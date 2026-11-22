# LLM 输出渲染：从「露出 `**`」到正常排版

## 问题

DeepSeek 返回的是标准 Markdown，但前端一直用 `<p>{content}</p>` 当纯文本渲染，
学生看到的回答里带着 `**目标**：`、`### 第2–3周`、`---`、`--` 这些标记。

根因不是模型输出脏，是**渲染链路缺了一环**。

## 方案

三层，各管一段：

### 1. 后端清洗（`backend/edu_app/services/llm_text.py`）

`sanitize_markdown()` 的取向是**把标记修回合法**，不是删掉。
全删会丢掉标题、列表、加粗的结构，排版反而更差。

处理项：

| 现象 | 处理 |
| --- | --- |
| 单行 `**` 个数为奇数 | 摘掉最后一个孤立 `**`（模型常忘闭合） |
| `****` / `** **` 空加粗 | 删除 |
| `##标题` 缺空格 | 补成 `## 标题` |
| `• 条目` / `* 条目` | 统一成 `- 条目` |
| 句中 `--`（两侧是中文） | 换成破折号 `——` |
| 独立成行的 `---`/`***` | 归一成 `---`（合法分隔线，渲染后不可见） |
| 只有标记没有内容的行 | 丢弃 |
| 零宽字符、全角空格 | 清除 |

`strip_inline_markers()` 给「一行一条、不渲染 Markdown」的场景用（仪表盘推荐、计划反馈）。

### 2. 流式防抖（`MarkdownStreamFilter`）

SSE 逐 token 推，行尾经常停在半个标记上（刚吐出 `*`，下一个 chunk 才是 `*`）。
过滤器**扣住行尾的标记串**，等下一个 chunk 到齐再放行，其余正文立刻下发，
所以不会造成整段延迟。

`/agent/mentor/stream` 的 `[DONE]` 分支从 `return` 改成 `break`，
确保 `flush()` 把尾巴发完再结束。

### 3. 前端渲染（`frontend/src/components/MarkdownView.jsx`）

`react-markdown@10` + `remark-gfm`（表格、任务列表、删除线）。
CSS 全部走 `--plan-*` 变量，浅蓝主题下标题/列表/引用/代码块都不跑版。

流式期间额外做一次 `trimDanglingMarkers()`：摘掉未闭合的尾部 `*`/`_`/`` ` ``，
避免加粗闭合前闪一下符号。

## 覆盖范围

| 位置 | 处理 |
| --- | --- |
| `/agent/mentor` | `sanitize_markdown` |
| `/agent/mentor/stream` | `MarkdownStreamFilter` + 前端流式裁剪 |
| `/dashboard/summary` 推荐 | `strip_inline_markers` |
| 协作学习 AI 助教 | 前端 `MarkdownView` |

## 验收

```
POST /api/agent/mentor/stream 实测：
  chars 608 / 残留 ** 10（5 对，合法） / -- 0 / --- 0 / 行首 # 0

浏览器（scripts/md_check.cjs）：
  strong 7 / ul 1 / li 3 / 裸露标记 0 / 控制台错误 0
```

脚本：`frontend/scripts/md_check.cjs`（`UI_EMAIL=... UI_PASSWORD=... node scripts/md_check.cjs`）。
