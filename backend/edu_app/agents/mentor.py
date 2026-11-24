from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from edu_app.agents.base import get_llm
import asyncio

async def get_mentor_response(user_profile: dict, message: str, chat_history: list):
    llm = get_llm(temperature=0.5, max_tokens=512)
    
    # 使用普通字符串编写系统提示，并通过变量插入
    system_prompt = """你是一个学生的AI学习导师。
    学生档案：
    - 年级：{grade}
    - 兴趣：{interests}
    - 学习风格：{learning_style}
    
    相关知识库上下文（可参考，但需结合学生情况）：
    {context}
    
    你的目标是指导学生，根据他们的学习风格解释概念，并鼓励他们。
    你必须具备思考、行动、推理与决策能力，按照「目标→思考→行动步骤→结果/下一步」结构输出，给出可执行的学习行动与决策。
    若问题属于知识问答（如“为什么/原理/推导/对比/作用”），先用1-3句话给出直接、正确、可核查的结论，再给出结构化说明与关键公式或例子。
    若信息不足，请先声明合理假设并继续完成任务。
    请务必使用简体中文回复。
    """
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder(variable_name="history"),
        ("human", "{input}")
    ])
    
    chain = prompt | llm
    
    # 将历史消息转换为 LangChain 可用的格式
    formatted_history = []
    for msg in chat_history:
        if msg['role'] == 'user':
            formatted_history.append(HumanMessage(content=msg['content']))
        elif msg['role'] == 'assistant':
            formatted_history.append(AIMessage(content=msg['content']))
            
    short_input = message[:800] + ("..." if len(message) > 800 else "")
    try:
        response = await asyncio.wait_for(chain.ainvoke({
            "history": formatted_history,
            "input": short_input,
            "grade": user_profile.get('grade', '未知'),
            "interests": user_profile.get('interests', '未知'),
            "learning_style": user_profile.get('learning_style', '未知'),
            "context": user_profile.get('context', '暂无相关上下文')
        }), timeout=12)
        content = response.content if hasattr(response, "content") else str(response or "")
        if not content or len(content.strip()) < 20:
            alt_llm = get_llm(temperature=0.2, max_tokens=384)
            alt_chain = prompt | alt_llm
            alt_resp = await asyncio.wait_for(alt_chain.ainvoke({
                "history": formatted_history,
                "input": short_input,
                "grade": user_profile.get('grade', '未知'),
                "interests": user_profile.get('interests', '未知'),
                "learning_style": user_profile.get('learning_style', '未知'),
                "context": user_profile.get('context', '暂无相关上下文')
            }), timeout=10)
            return alt_resp.content if hasattr(alt_resp, "content") else str(alt_resp or "")
        return content
    except Exception as error:
        # 不再返回「看起来像回答」的模板文本。没配模型时 get_llm() 会返回
        # 本地回退并正常返回，能走到这里的都是真失败，应该让调用方知道。
        raise RuntimeError(f"导师回复生成失败：{error}") from error
