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
    except Exception:
        return (
            "目标：为家长生成孩子的学习进度周报与家庭辅导计划。\n"
            "思考：结合近期记录，保持积极与关怀，突出可执行安排。\n"
            "行动步骤：\n"
            "1）家长与孩子共同复盘一次近期活动，形成3条改进要点。\n"
            "2）本周安排两次专项练习与一次讨论，明确负责人与时间。\n"
            "3）每天10分钟阅读或错题整理，周末进行总结与反馈。\n"
            "结果/下一步：记录完成度与收获，下周调整计划与重点。"
        )

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
    except Exception:
        fallback = (
            "目标：为家长生成孩子的学习进度周报与家庭辅导计划。\n"
            "思考：结合近期记录，保持积极与关怀，突出可执行安排。\n"
            "行动步骤：\n"
            "1）家长与孩子共同复盘一次近期活动，形成3条改进要点。\n"
            "2）本周安排两次专项练习与一次讨论，明确负责人与时间。\n"
            "3）每天10分钟阅读或错题整理，周末进行总结与反馈。\n"
            "结果/下一步：记录完成度与收获，下周调整计划与重点。"
        )
        yield fallback
