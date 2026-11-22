import math
import json
import logging
import asyncio
from typing import List, Dict, Any, Optional
from langchain_core.prompts import ChatPromptTemplate
from edu_app.agents.base import get_llm

logger = logging.getLogger(__name__)

class AdvancedRAGPipeline:
    """
    工业级 Advanced RAG 架构实现
    三阶段处理流：
    1. Pre-Retrieval (检索前处理): 多查询扩展 (Multi-Query Expansion) + 假设性文档嵌入 (HyDE)
    2. Retrieval (检索阶段): 向量(Dense) + BM25(Sparse) 双路检索与硬性元数据过滤 (Tenant/Owner)
    3. Post-Retrieval (检索后处理): 倒数排名融合 (RRF) + 最大边际相关性 (MMR) 去重去冗余 + Context 重排与压缩
    """
    
    def __init__(self, rrf_k: int = 60, lambda_mult: float = 0.7):
        self.rrf_k = rrf_k
        self.lambda_mult = lambda_mult  # MMR 多样性与相关性配比系数

    async def generate_hyde_and_multi_query(self, original_query: str) -> Dict[str, Any]:
        """
        检索前优化 (Pre-Retrieval)：生成 HyDE 假想文档与 3 个变体查询词
        """
        llm = get_llm(temperature=0.3, max_tokens=300)
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是一个高级 RAG 查询优化专家。
请针对用户的原始问题，完成两项任务：
1. 生成 3 个不同表述的搜索子查询 (Multi-Query Expansion)，捕获不同侧面的关键词。
2. 生成一段 2-3 句的“假设性理想回答段落”(HyDE)，用以进行文档-文档级向量匹配。

输出必须严格为 JSON 格式：
{
    "expanded_queries": ["变体1", "变体2", "变体3"],
    "hypothetical_doc": "假想回答段落..."
}
"""),
            ("human", "原始问题：{query}")
        ])
        
        chain = prompt | llm
        try:
            resp = await asyncio.wait_for(chain.ainvoke({"query": original_query}), timeout=6.0)
            content = resp.content if hasattr(resp, "content") else str(resp)
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            data = json.loads(content)
            return {
                "expanded_queries": data.get("expanded_queries", [original_query]),
                "hypothetical_doc": data.get("hypothetical_doc", original_query)
            }
        except Exception as e:
            logger.warning(f"HyDE & Multi-Query expansion fallback: {e}")
            return {
                "expanded_queries": [original_query],
                "hypothetical_doc": original_query
            }

    def reciprocal_rank_fusion(self, dense_rankings: List[Dict[str, Any]], sparse_rankings: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
        """
        检索后优化 (Post-Retrieval)：倒数排名融合算法 (RRF)
        RRF_Score(d) = sum(1 / (k + rank_dense(d))) + sum(1 / (k + rank_sparse(d)))
        """
        rrf_scores = {}
        doc_map = {}

        # 处理 Dense 向量结果
        for rank, doc in enumerate(dense_rankings):
            doc_id = doc["id"]
            doc_map[doc_id] = doc
            if doc_id not in rrf_scores:
                rrf_scores[doc_id] = 0.0
            rrf_scores[doc_id] += 1.0 / (self.rrf_k + rank + 1)

        # 处理 Sparse BM25 结果
        for rank, doc in enumerate(sparse_rankings):
            doc_id = doc["id"]
            doc_map[doc_id] = doc
            if doc_id not in rrf_scores:
                rrf_scores[doc_id] = 0.0
            rrf_scores[doc_id] += 1.0 / (self.rrf_k + rank + 1)

        # 按 RRF 得分降序排序
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        
        fused_results = []
        for doc_id in sorted_ids[:top_k]:
            item = dict(doc_map[doc_id])
            item["rrf_score"] = round(rrf_scores[doc_id], 5)
            fused_results.append(item)
            
        return fused_results

    def maximal_marginal_relevance(self, candidate_docs: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
        """
        检索后优化 (Post-Retrieval)：最大边际相关性 (MMR) 去重与多样性筛选
        防止向 LLM 提供语义重复/冗余的切片
        """
        if not candidate_docs:
            return []

        # RRF 分数是 1/(k+rank) 量级（约 0.016），而 Jaccard 相似度是 0-1。
        # 直接加权会让「相似度」项压过「相关性」项，把排序彻底打乱，
        # 因此先把相关性归一到 0-1 再参与 MMR。
        raw_scores = [float(d.get("rrf_score", d.get("score", 0.0))) for d in candidate_docs]
        max_score = max(raw_scores) or 1.0
        for doc, raw in zip(candidate_docs, raw_scores):
            doc["_rel_norm"] = raw / max_score

        selected_docs = [candidate_docs[0]]
        unselected_docs = candidate_docs[1:]

        while len(selected_docs) < top_k and unselected_docs:
            best_doc = None
            best_mmr = -float("inf")

            for doc in unselected_docs:
                rel = doc.get("_rel_norm", 0.5)
                # 简单重叠度估计 (Jaccard Distance) 评估冗余度
                doc_words = set(doc.get("content", ""))
                max_sim = max([
                    len(doc_words.intersection(set(s.get("content", "")))) / max(1, len(doc_words.union(set(s.get("content", "")))))
                    for s in selected_docs
                ]) if selected_docs else 0.0

                mmr_score = self.lambda_mult * rel - (1 - self.lambda_mult) * max_sim
                if mmr_score > best_mmr:
                    best_mmr = mmr_score
                    best_doc = doc

            if best_doc:
                selected_docs.append(best_doc)
                unselected_docs.remove(best_doc)
            else:
                break

        return selected_docs

    def compress_context_for_prompt(self, docs: List[Dict[str, Any]], max_chars: int = 2500) -> str:
        """
        检索后优化：Context 自动压缩与结构化排版
        """
        formatted = ["【工业级 Advanced RAG 检索上下文 (已做 RRF 融合与 MMR 去重)】："]
        total_len = 0

        for i, doc in enumerate(docs, 1):
            title = doc.get("title", f"参考文档_{i}")
            content = doc.get("content", "").strip()
            score = doc.get("rrf_score", doc.get("score", 0.0))
            
            chunk_str = f"--- [片段 {i}] 标题：《{title}》 (相关度: {score}) ---\n{content}\n"
            if total_len + len(chunk_str) > max_chars:
                break
            formatted.append(chunk_str)
            total_len += len(chunk_str)

        return "\n".join(formatted)

advanced_rag_pipeline = AdvancedRAGPipeline()
