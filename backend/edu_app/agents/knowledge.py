from langchain_core.prompts import ChatPromptTemplate
from edu_app.agents.base import get_llm
import json
import asyncio

async def extract_knowledge_from_text(text: str, subject: str = None):
    llm = get_llm(temperature=0.2)
    
    system_prompt = """你是一个专业的NLP知识抽取和多学科关联分析专家。
    你的任务是从给定的文本中抽取核心知识点，并分析这些知识点与其他学科的潜在关联。

    请输出严格的 JSON 格式，包含以下字段：
    - main_subject: 文本所属的主要学科 (如: 历史, 物理)
    - knowledge_points: 抽取的知识点列表 (List[dict])
      - name: 知识点名称
      - description: 简短描述
      - category: 类别 (如: 事件, 人物, 定理)
    - cross_disciplinary_links: 跨学科关联 (List[dict])
      - source_point: 源知识点 (来自上面的列表)
      - target_subject: 目标学科 (如: 地理, 数学)
      - target_concept: 目标学科的相关概念
      - relationship: 关联描述 (如: "受...影响", "基于...原理")
      
    Example output format:
    {{
        "main_subject": "历史",
        "knowledge_points": [
            {{"name": "赤壁之战", "description": "三国时期关键战役", "category": "战役"}}
        ],
        "cross_disciplinary_links": [
            {{"source_point": "赤壁之战", "target_subject": "地理", "target_concept": "季风气候", "relationship": "利用东南风火攻"}}
        ]
    }}

    请务必使用简体中文回复。
    """
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "请分析以下文本 (预设学科: {subject}):\n{text}")
    ])
    
    try:
        response = await prompt.ainvoke({"subject": subject or "自动识别", "text": text})
        content_str = response.content
        
        if "```json" in content_str:
            content_str = content_str.split("```json")[1].split("```")[0]
        elif "```" in content_str:
            content_str = content_str.split("```")[1].split("```")[0]
            
        return json.loads(content_str.strip())
    except Exception as e:
        return {
            "main_subject": "Unknown",
            "knowledge_points": [],
            "cross_disciplinary_links": [],
            "error": str(e)
        }

async def get_knowledge_graph_data(subject: str, topic: str, existing_nodes: list = None):
    llm = get_llm(temperature=0.3)
    
    context_str = ""
    if existing_nodes:
        # 将 KnowledgeNode 对象转换为字典
        nodes_data = []
        for node in existing_nodes[:30]:
            nodes_data.append({
                "name": node.name,
                "description": node.description,
                "prerequisites": node.prerequisites,
                "related_nodes": node.related_nodes
            })
        blob = json.dumps(nodes_data, ensure_ascii=False, separators=(',', ':'))
        context_str = f"参考以下现有的真实数据库数据来构建图谱:\n{blob[:5000]}\n请确保生成的图谱包含这些真实数据节点。"

    # 使用普通字符串作为模板而非 f-string
    # {context_str} 将由 LangChain 进行替换
    # JSON 花括号 {{ 和 }} 需要为 LangChain 进行转义
    system_prompt = """你是一个知识图谱Agent。
    你的任务是为给定的学科和主题生成一个结构化的知识图谱。
    {context_str}
    输出必须是一个包含 'nodes' 和 'edges' 的有效 JSON 对象。
    Nodes (节点) 应该包含 'id', 'label' (中文), 'description' (中文)。
    Edges (边) 应该包含 'source', 'target', 'relationship' (中文)。

    Example output format:
    {{
        "nodes": [
            {{"id": "1", "label": "概念 A", "description": "..."}},
            {{"id": "2", "label": "概念 B", "description": "..."}}
        ],
        "edges": [
            {{"source": "1", "target": "2", "relationship": "导致"}}
        ]
    }}
    请务必使用简体中文回复内容。
    """
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Create a knowledge graph for Subject: {subject}, Topic: {topic}")
    ])
    
    # 从数据库构建可确定的图作为回退/基础
    deterministic_graph = {"nodes": [], "edges": []}
    id_map = {}
    if existing_nodes:
        idx = 1
        for node in existing_nodes:
            nid = str(idx)
            id_map[node.name] = nid
            deterministic_graph["nodes"].append({
                "id": nid,
                "label": node.name,
                "description": node.description or ""
            })
            idx += 1
        # 前置知识边
        for node in existing_nodes:
            src_id = id_map.get(node.name)
            # 边从前置节点指向当前节点
            if node.prerequisites:
                for pre in node.prerequisites:
                    pre_id = id_map.get(pre)
                    if pre_id:
                        deterministic_graph["edges"].append({
                            "source": pre_id,
                            "target": src_id,
                            "relationship": "前置知识"
                        })
            # 相关知识边
            if node.related_nodes:
                for rel in node.related_nodes:
                    rel_id = id_map.get(rel)
                    if rel_id:
                        deterministic_graph["edges"].append({
                            "source": src_id,
                            "target": rel_id,
                            "relationship": "相关知识"
                        })

        # 若提供了主题，则围绕匹配项过滤子图
        if topic:
            matched_ids = {n["id"] for n in deterministic_graph["nodes"] if topic in n["label"] or topic in n["description"]}
            neighbor_ids = set()
            for e in deterministic_graph["edges"]:
                if e["source"] in matched_ids or e["target"] in matched_ids:
                    neighbor_ids.add(e["source"])
                    neighbor_ids.add(e["target"])
            keep_ids = matched_ids | neighbor_ids
            deterministic_graph["nodes"] = [n for n in deterministic_graph["nodes"] if n["id"] in keep_ids] or deterministic_graph["nodes"]
            deterministic_graph["edges"] = [e for e in deterministic_graph["edges"] if e["source"] in keep_ids and e["target"] in keep_ids] or deterministic_graph["edges"]

    # 尝试使用大模型生成，能合并则合并，否则返回确定性结果
    try:
        response = await asyncio.wait_for((prompt | llm).ainvoke({
            "subject": subject,
            "topic": topic,
            "context_str": context_str
        }), timeout=12)
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        llm_graph = json.loads(content.strip())

        # 合并：追加不重复的 LLM 边和节点
        if isinstance(llm_graph, dict):
            existing_pairs = {(e["source"], e["target"], e["relationship"]) for e in deterministic_graph.get("edges", [])}
            existing_labels = {n["label"] for n in deterministic_graph.get("nodes", [])}
            for n in llm_graph.get("nodes", []):
                if n.get("label") and n["label"] not in existing_labels:
                    deterministic_graph["nodes"].append(n)
            for e in llm_graph.get("edges", []):
                key = (e.get("source"), e.get("target"), e.get("relationship"))
                if all(key) and key not in existing_pairs:
                    deterministic_graph["edges"].append(e)
        return deterministic_graph
    except Exception:
        return deterministic_graph
