import logging
from typing import List, Dict, Any
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from edu_app.models.learning import KnowledgeNode

logger = logging.getLogger(__name__)

class GraphRAGSearcher:
    """
    V3.0.0 Graph RAG (图谱增强检索)
    在传统向量/文本检索基础之上，沿前置依赖边与衍生边展开二阶网络扩展
    """
    
    @staticmethod
    async def expand_graph_context(db: AsyncSession, subject: str, query_keywords: List[str]) -> Dict[str, Any]:
        """
        基于图形链表扩展上下文概念
        """
        # 1. 找到与关键词匹配的核心节点
        res = await db.execute(select(KnowledgeNode).where(KnowledgeNode.subject == subject))
        nodes = res.scalars().all()
        
        matched_nodes = []
        for n in nodes:
            for kw in query_keywords:
                if kw and (kw.lower() in n.name.lower() or (n.description and kw.lower() in n.description.lower())):
                    matched_nodes.append(n)
                    break
                    
        if not matched_nodes:
            matched_nodes = nodes[:3]  # 回退：取前 3 个节点
            
        # 2. 沿着前置/相关依赖边做二阶关联扩展 (Graph Traversal)
        expanded_nodes = {}
        relationships = []
        
        node_map = {n.name: n for n in nodes}
        
        for root in matched_nodes:
            expanded_nodes[root.name] = {
                "name": root.name,
                "description": root.description or "",
                "type": "core"
            }
            
            # 2.1 扩展前置知识 (Prerequisites)
            pres = getattr(root, "prerequisites", None) or []
            for pre_name in pres:
                relationships.append({"source": pre_name, "target": root.name, "relation": "前置知识"})
                if pre_name in node_map and pre_name not in expanded_nodes:
                    pre_node = node_map[pre_name]
                    expanded_nodes[pre_name] = {
                        "name": pre_node.name,
                        "description": pre_node.description or "",
                        "type": "prerequisite"
                    }
                    
            # 2.2 扩展衍生/相关知识 (Related Nodes)
            rels = getattr(root, "related_nodes", None) or []
            for rel_name in rels:
                relationships.append({"source": root.name, "target": rel_name, "relation": "衍生知识"})
                if rel_name in node_map and rel_name not in expanded_nodes:
                    rel_node = node_map[rel_name]
                    expanded_nodes[rel_name] = {
                        "name": rel_node.name,
                        "description": rel_node.description or "",
                        "type": "derived"
                    }
                    
        # 3. 格式化为 Graph RAG 富 Prompt 上下文
        context_lines = ["【Graph RAG 学科拓扑结构与衍生网络】："]
        for name, info in expanded_nodes.items():
            context_lines.append(f"• 概念［{name}］({info['type']}): {info['description']}")
            
        context_lines.append("【概念演进依赖关系】：")
        for rel in relationships[:8]:
            context_lines.append(f"  {rel['source']} ──({rel['relation']})──> {rel['target']}")
            
        return {
            "expanded_nodes": list(expanded_nodes.values()),
            "relationships": relationships,
            "graph_prompt_context": "\n".join(context_lines)
        }
