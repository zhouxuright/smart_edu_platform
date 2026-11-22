# RAG 检索链路运维手册

本文档说明知识库检索的真实实现、可调参数，以及改动 embedding 模型后必须做的迁移动作。

## 一、链路全貌

```
上传文档 → 解析(pdf/docx/txt/md/csv/json) → 分块(500字/80重叠)
        → BGE-M3 向量化(1024维) 存 document_chunks.vector
        → 同时保留原文供 BM25 使用

检索 query → BGE-M3 向量化
          → ① 语义：与每个分块向量做余弦相似度
          → ② 词法：jieba 分词后算 BM25(k1=1.5, b=0.75)
          → ③ 融合：0.65×语义分 + 0.35×词法分
          → ④ MMR 去重(λ=0.7，只重排第 2 名之后)
          → ⑤ 上下文压缩到 2500 字后交给 LLM
```

确实用到了向量（BGE-M3 本地推理，1024 维）、文档分块、混合检索（语义 + 关键词）、重排序（MMR 多样性去重）。向量存在 Postgres 的 `document_chunks.vector` 字段（JSON 数组），未引入独立向量库。

## 二、关键设计：分数不能用「候选集最大值」归一

早期实现是 `dense/max_dense` 和 `bm25/max_bm25`，这在候选分数彼此接近时会**把噪声放大成满分**：
当所有分块的 BM25 都只有 0.3 左右时，相对最大值的那一条也会拿到 1.0 的归一值，直接盖过真正的语义匹配。

现在的做法是让每个信号都有绝对意义：

| 信号 | 处理方式 | 参数 |
|---|---|---|
| 语义 | 余弦线性平移缩放到 0–1 | `SEMANTIC_FLOOR=0.30`、`SEMANTIC_SPAN=0.55` |
| 词法 | BM25 饱和函数 `s/(s+3)` | `BM25_SATURATION=3.0` |
| 融合 | 加权和 | `SEMANTIC_WEIGHT=0.65`、`KEYWORD_WEIGHT=0.35` |

参数定义在 `edu_app/api/endpoints.py` 的 `chunk_level_search` 上方。

**展示分**额外做了一次开方（`score ** 0.5`）。原因是加权融合在「只有语义命中、零词面重叠」时上限只有 0.65，直接乘 100 会让一个正确的 Top-1 看起来只有 30 多分。开方是严格单调变换，不影响排序，因此不会出现「排第一但分数更低」。

## 三、虚词必须过滤

`edu_app/services/text_utils.py` 维护中文虚词表。像「怎样才能让我的履历更容易被挑中」里的
「怎样/才能/让/我/的/更/被」对区分文档毫无贡献，却会让 BM25 累积出一个和主题无关的高分。
实测不过滤时该查询的 Top-1 会错排到「薪资谈判」，过滤后正确命中「简历撰写」。

## 四、改 embedding 模型后的必做步骤

不同模型的向量不在同一空间，维度也可能不同（哈希兜底 1536 维 vs BGE-M3 1024 维）。
换模型后**必须重建向量**，否则语义检索会静默失效（旧向量与新查询向量点积无意义）。

库里有两张表存向量，都要重建：

| 表 | 用途 | 不重建的后果 |
|---|---|---|
| `document_chunks.vector` | 分块级检索（主链路） | 语义分恒为 0，退化成纯 BM25 |
| `document_embeddings.vector` | 文档级回退链路（无分块的历史数据） | 这些文档被按维度过滤掉，检索不到 |

```bash
cd backend

# 1. 看统计，确认待处理数量与现有维度
HF_HUB_OFFLINE=1 python scripts/reindex_embeddings.py --dry-run

# 2. 实际重建（默认两张表都重建；分块 90 条约 2 分钟，CPU 推理）
HF_HUB_OFFLINE=1 python scripts/reindex_embeddings.py

#    只重建其中一张：
HF_HUB_OFFLINE=1 python scripts/reindex_embeddings.py --target chunks
HF_HUB_OFFLINE=1 python scripts/reindex_embeddings.py --target documents

# 3. 确认维度已统一（两张表都应只剩一个维度）
python -c "
import asyncio, asyncpg
async def m():
    c = await asyncpg.connect('postgresql://postgres:你的密码@localhost:5432/smart_edu')
    for t in ('document_chunks', 'document_embeddings'):
        rows = await c.fetch(f'select json_array_length(vector) d, count(*) n from {t} group by 1 order by 1')
        print(t, [dict(r) for r in rows])
    await c.close()
asyncio.run(m())
"
```

## 五、改检索逻辑后的验收

任何动到排序、权重、分词、embedding 的改动，都必须复跑 Top-1 评估：

```bash
cd backend
python scripts/eval_retrieval.py
```

评估集是 8 篇彼此独立的短文档，每篇只讲一个主题，查询句**刻意不与文档标题共享词面**，
逼检索器真正用语义而不是关键词重叠来命中。当前基线：**8/8 = 100%**。

需要看某条查询的原始分数时：

```bash
HF_HUB_OFFLINE=1 python scripts/diag_scores.py
```

会逐条打印每个候选块的余弦分、BM25 分和命中的词，用于判断是语义分还是词法分在主导。

## 六、常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| 语义分全是 0 | 分块向量维度与查询向量不一致（换过模型没重建） | 跑第四节的重建流程 |
| 检索 500，日志报 `No module named 'jieba'` | 依赖未装 | `pip install jieba`（约 12 分钟，务必后台跑） |
| 正确的文档排到第二 | 虚词把 BM25 顶高了 | 检查 `text_utils._STOP` 是否漏词 |
| 分数全是 1.000 | 又用回了「除以候选集最大值」的归一化 | 改回绝对分口径，见第二节 |
| `MissingGreenlet` | 异步会话里访问了懒加载关系或过期实例 | 显式 join 出关联表；`AsyncSession(engine, expire_on_commit=False)` |
| 老文档检索不到 | `document_embeddings` 还是旧维度，被过滤掉了 | `--target documents` 重建 |

## 七、回退链路的回归测试

文档级回退链路容易被忽略，因为它只在「有文档但没有分块」时才触发。
`scripts/test_legacy_fallback.py` 会临时造一个「2 篇无分块文档、向量维度 1024/1536 混杂」的探针用户，
验证不会 500 且能正确跳过维度不匹配的旧向量，跑完自动清理数据。

```bash
cd backend
python scripts/test_legacy_fallback.py
```
