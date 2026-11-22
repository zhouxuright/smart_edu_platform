from langchain_core.prompts import ChatPromptTemplate
from edu_app.agents.base import get_llm
import asyncio

import logging

async def analyze_performance(student_name: str, records: list):
    llm = get_llm(temperature=0.5)
    
    # 格式化学习记录以用于提示词
    records_text = "\n".join([f"- {r.subject} ({r.topic}): {r.score}" for r in records])
    if not records_text.strip():
        records_text = "（暂无具体分数记录）"
    
    system_prompt = """你是一个评估分析Agent。
    分析学生的学习记录并提供全面的评估。
    内容包括：
    1. 优势 (Strengths)
    2. 不足 (Weaknesses)
    3. 建议改进计划 (Suggested Improvement Plan)
    4. 推荐学习路径 (Recommended Learning Path)
    
    以「目标→分析→行动步骤→评估与下一步」输出，并提供每个行动的预期结果与检查点。
    保持语气建设性和鼓励性。
    请务必使用简体中文回复。
    """
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Student: {name}\nRecords:\n{records}")
    ])
    
    chain = prompt | llm
    
    try:
        response = await asyncio.wait_for(chain.ainvoke({
            "name": student_name,
            "records": records_text
        }), timeout=30)
        return response.content
    except Exception as e:
        logging.error(f"Assessment agent error: {e}")
        return (
            "目标：总结当前学习表现并给出改进与行动计划。\n"
            "分析：基于最近记录，识别优势与薄弱主题，明确提升方向。\n"
            "行动步骤：\n"
            "1）为薄弱主题安排两次专项练习并整理错题本。\n"
            "2）针对最近一次活动完成复盘，记录要点与检查点。\n"
            "3）制定一周计划：每天10-15分钟巩固练习与笔记整理。\n"
            "评估与下一步：跟踪分数变化与完成度，下周复盘并更新计划。"
        )

async def analyze_performance_stream(student_name: str, records: list):
    llm = get_llm(temperature=0.5)
    records_text = "\n".join([f"- {r.subject} ({r.topic}): {r.score}" for r in records])
    system_prompt = """你是一个评估分析Agent。
    分析学生的学习记录并提供全面的评估。
    内容包括：
    1. 优势 (Strengths)
    2. 不足 (Weaknesses)
    3. 建议改进计划 (Suggested Improvement Plan)
    4. 推荐学习路径 (Recommended Learning Path)
    
    以「目标→分析→行动步骤→评估与下一步」输出，并提供每个行动的预期结果与检查点。
    保持语气建设性和鼓励性。
    请务必使用简体中文回复。
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Student: {name}\nRecords:\n{records}")
    ])
    chain = prompt | llm
    
    try:
        async for chunk in chain.astream({
            "name": student_name,
            "records": records_text
        }):
            yield chunk.content
    except Exception:
        fallback = (
            "目标：总结当前学习表现并给出改进与行动计划。\n"
            "分析：基于最近记录，识别优势与薄弱主题，明确提升方向。\n"
            "行动步骤：\n"
            "1）为薄弱主题安排两次专项练习并整理错题本。\n"
            "2）针对最近一次活动完成复盘，记录要点与检查点。\n"
            "3）制定一周计划：每天10-15分钟巩固练习与笔记整理。\n"
            "评估与下一步：跟踪分数变化与完成度，下周复盘并更新计划。"
        )
        yield fallback
