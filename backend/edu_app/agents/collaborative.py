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
    except Exception as error:
        raise RuntimeError(f"讨论引导生成失败：{error}") from error

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
    except Exception as error:
        yield f"\n\n（讨论引导生成失败：{error}。请稍后重试。）"
