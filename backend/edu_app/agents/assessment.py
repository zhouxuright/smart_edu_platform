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
        # 原来这里返回一份模板评估。它读起来像分析结论，实际与这个学生无关，
        # 而家长/学生无法分辨——失败就该是失败。
        raise RuntimeError(f"学习评估生成失败：{e}") from e

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
    except Exception as error:
        # 流式已经发出 200 头，改不了状态码，只能把失败如实写进内容
        yield f"\n\n（评估生成失败：{error}。请稍后重试。）"
