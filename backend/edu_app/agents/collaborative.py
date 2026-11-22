from langchain_core.prompts import ChatPromptTemplate
from edu_app.agents.base import get_llm
import asyncio

async def facilitate_discussion(topic: str, current_discussion: list):
    llm = get_llm(temperature=0.8)
    
    system_prompt = """你是一个协作学习Agent。
    你的角色是促进学生之间的小组讨论。
    你应该：
    1. 鼓励参与。
    2. 提出与主题相关的发人深省的问题。
    3. 综合不同的观点并形成清晰结论。
    4. 保持讨论聚焦并产出可执行行动项（包含负责人与截止时间）。
    输出结构为「思考→行动建议→角色分配→决定与下一步」。
    请务必使用简体中文回复。
    """
    
    # 格式化讨论历史
    history_text = "\n".join([f"{msg['user']}: {msg['content']}" for msg in current_discussion])
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Topic: {topic}\nCurrent Discussion:\n{history}\n\nWhat is your contribution as the facilitator?")
    ])
    
    chain = prompt | llm
    
    try:
        response = await asyncio.wait_for(chain.ainvoke({
            "topic": topic,
            "history": history_text
        }), timeout=15)
        return response.content
    except Exception:
        return (
            "思考：促进讨论聚焦，综合观点并形成清晰结论与行动。\n"
            "行动建议：\n"
            "1）围绕主题提出两个关键问题，鼓励每人给出例证。\n"
            "2）提炼共识与分歧，形成3条可执行任务与截止时间。\n"
            "角色分配：明确负责人与协作方式，记录检查点。\n"
            "决定与下一步：设定复盘时间与产出要求，跟踪完成度。"
        )

async def facilitate_discussion_stream(topic: str, current_discussion: list):
    llm = get_llm(temperature=0.8)
    system_prompt = """你是一个协作学习Agent。
    你的角色是促进学生之间的小组讨论。
    你应该：
    1. 鼓励参与。
    2. 提出与主题相关的发人深省的问题。
    3. 综合不同的观点并形成清晰结论。
    4. 保持讨论聚焦并产出可执行行动项（包含负责人与截止时间）。
    输出结构为「思考→行动建议→角色分配→决定与下一步」。
    请务必使用简体中文回复。
    """
    history_text = "\n".join([f"{msg['user']}: {msg['content']}" for msg in current_discussion])
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Topic: {topic}\nCurrent Discussion:\n{history}\n\nWhat is your contribution as the facilitator?")
    ])
    chain = prompt | llm
    try:
        async for chunk in chain.astream({
            "topic": topic,
            "history": history_text
        }):
            yield chunk.content
    except Exception:
        fallback = (
            "思考：促进讨论聚焦，综合观点并形成清晰结论与行动。\n"
            "行动建议：\n"
            "1）围绕主题提出两个关键问题，鼓励每人给出例证。\n"
            "2）提炼共识与分歧，形成3条可执行任务与截止时间。\n"
            "角色分配：明确负责人与协作方式，记录检查点。\n"
            "决定与下一步：设定复盘时间与产出要求，跟踪完成度。"
        )
        yield fallback
