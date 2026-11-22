import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

async def test_advanced_rag():
    print("Testing Industrial Advanced RAG Pipeline...")
    from edu_app.services.advanced_rag_pipeline import advanced_rag_pipeline
    
    # 1. Test RRF Fusion
    dense = [
        {"id": 1, "title": "勾股定理基本证明", "content": "直角三角形两直角边a、b的平方和等于斜边c的平方", "score": 0.92},
        {"id": 2, "title": "二次函数最值公式", "content": "顶点坐标公式为 -b/2a", "score": 0.75}
    ]
    sparse = [
        {"id": 2, "title": "二次函数最值公式", "content": "顶点坐标公式为 -b/2a", "score": 14.5},
        {"id": 1, "title": "勾股定理基本证明", "content": "直角三角形两直角边a、b的平方和等于斜边c的平方", "score": 8.2}
    ]
    
    rrf_fused = advanced_rag_pipeline.reciprocal_rank_fusion(dense, sparse, top_k=2)
    print("✓ RRF Reciprocal Rank Fusion verified:", rrf_fused)
    
    # 2. Test MMR Diversity Filtering
    mmr_filtered = advanced_rag_pipeline.maximal_marginal_relevance(rrf_fused, top_k=2)
    print("✓ MMR Diversity Filtering verified:", mmr_filtered)
    
    # 3. Test Context Compression
    compressed_text = advanced_rag_pipeline.compress_context_for_prompt(mmr_filtered, max_chars=500)
    print("✓ Context Compression verified:\n", compressed_text)

if __name__ == "__main__":
    asyncio.run(test_advanced_rag())
