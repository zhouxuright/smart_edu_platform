from langchain_core.prompts import ChatPromptTemplate
from edu_app.agents.base import get_llm
import asyncio

async def generate_parent_report(student_name: str, records: list, feedback: str):
    llm = get_llm(temperature=0.6)
    records_text = "\n".join([f"- {r.subject}: {r.score}" for r in records])
    system_prompt = """你是一个家长沟通Agent。
    目标是为家长生成清晰的孩子学习进度周报，同时给出可执行的家庭辅导计划。
    输出必须遵循「目标→思考→行动步骤→结果/下一步」结构，每个行动包含负责人（家长/孩子）与时间安排。
    语气专业、积极、关怀。
    请务必使用简体中文回复。
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Student: {name}\nRecent Activity:\n{records}\nTeacher/System Feedback: {feedback}")
    ])
    chain = prompt | llm
    try:
        response = await asyncio.wait_for(chain.ainvoke({
            "name": student_name,
            "records": records_text,
            "feedback": feedback
        }), timeout=12)
        return response.content
    except Exception as error:
        # 不返回模板报告。原来这里兜一段「目标：…行动步骤：…」，
        # 家长拿到的是**看起来像分析**的假内容，而他没有任何办法分辨。
        # 没配模型时 `get_llm()` 已经会返回本地回退，能走到这里的都是真失败。
        raise RuntimeError(f"家长报告生成失败：{error}") from error

async def generate_parent_report_stream(student_name: str, records: list, feedback: str):
    llm = get_llm(temperature=0.6)
    records_text = "\n".join([f"- {r.subject}: {r.score}" for r in records])
    system_prompt = """你是一个家长沟通Agent。
    目标是为家长生成清晰的孩子学习进度周报，同时给出可执行的家庭辅导计划。
    输出必须遵循「目标→思考→行动步骤→结果/下一步」结构，每个行动包含负责人（家长/孩子）与时间安排。
    语气专业、积极、关怀。
    请务必使用简体中文回复。
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Student: {name}\nRecent Activity:\n{records}\nTeacher/System Feedback: {feedback}")
    ])
    chain = prompt | llm
    try:
        async for chunk in chain.astream({
            "name": student_name,
            "records": records_text,
            "feedback": feedback
        }):
            yield chunk.content
    except Exception as error:
        # 流式已经发出了 200 响应头，没法再改成错误码。这里只能把「失败」
        # 如实写进内容里，绝不能拿一段模板报告冒充分析结果。
        yield f"\n\n（报告生成失败：{error}。请稍后重试。）"
