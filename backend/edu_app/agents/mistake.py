from langchain_core.prompts import ChatPromptTemplate
from edu_app.agents.base import get_llm
import json
import asyncio

import logging

async def analyze_mistake(content: str, subject: str = "General"):
    logging.info(f"Analyzing mistake: {content[:50]}...")
    llm = get_llm(temperature=0.4)
    
    system_prompt = """你是一个专业的AI错题分析助手。
    你的任务是分析学生提交的错题，并提供详细的反馈报告。
    
    请输出严格的 JSON 格式，包含以下字段：
    - knowledge_points: 题目涉及的知识点列表 (List[str])
    - difficulty: 难度等级 (1-5)
    - error_cause_analysis: 可能的错误原因分析 (str)
    - correct_solution: 正确的解题步骤 (str)
    - learning_suggestion: 针对该知识点的学习建议 (str)
    - similar_problem: 一个类似的练习题 (包含题目和答案) (dict: {{"question": "...", "answer": "..."}})
    
    注意：如果包含 LaTeX 公式，请务必使用双反斜杠转义。
    请务必使用简体中文回复。
    """
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "请分析以下错题 (学科: {subject}):\n{content}")
    ])
    
    try:
        response = await prompt.ainvoke({"subject": subject, "content": content})
        logging.info("Calling LLM for analysis...")
        result = await llm.ainvoke(response)
        logging.info(f"LLM response received: {result.content[:100]}...")
        
        # Parse JSON
        content_str = result.content
        if "```json" in content_str:
            content_str = content_str.split("```json")[1].split("```")[0]
        elif "```" in content_str:
            content_str = content_str.split("```")[1].split("```")[0]
            
        parsed_result = json.loads(content_str.strip())
        logging.info("Successfully parsed analysis result.")
        return parsed_result
    except Exception as e:
        logging.error(f"Error analyzing mistake: {e}")
        # Fallback in case of parsing error or LLM failure
        return {
            "knowledge_points": ["未知"],
            "difficulty": 1,
            "error_cause_analysis": "无法自动分析，请稍后重试。",
            "correct_solution": "暂无",
            "learning_suggestion": "请检查题目内容是否清晰。",
            "similar_problem": {"question": "", "answer": ""},
            "raw_error": str(e)
        }
