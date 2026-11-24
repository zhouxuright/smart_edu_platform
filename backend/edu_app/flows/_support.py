"""流程之间共用的几件小事：取简历、拍平文本、调模型、抠 JSON。

抽出来的理由很实际：第二个流程（模拟面试）要的正是第一版里那几段
「从模型回复里抠出 JSON」「把简历拍成可搜索的文本」。复制一遍的话，
下次改解析规则就会漏掉其中一处，两个流程对同一段回复给出不同结论。
"""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.models.resume import Resume
from edu_app.models.user import User
from edu_app.services.llm_gate import llm_slot

MAX_RESUME_CHARS = 6000


def json_block(text: str) -> dict | None:
    """从模型回复里抠出第一个 JSON 对象。抠不出来返回 None，由调用方报错。"""
    match = re.search(r"\{[\s\S]*\}", text or "")
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def flatten(node: Any, out: list[str]) -> None:
    """把简历 JSON 拍成纯文本：字段名不重要，能搜到内容就行。"""
    if isinstance(node, str):
        value = node.strip()
        if value:
            out.append(value)
    elif isinstance(node, (int, float)):
        out.append(str(node))
    elif isinstance(node, list):
        for item in node:
            flatten(item, out)
    elif isinstance(node, dict):
        for value in node.values():
            flatten(value, out)


def resume_to_text(data: dict | None) -> str:
    out: list[str] = []
    flatten(data or {}, out)
    return "\n".join(out)[:MAX_RESUME_CHARS]


async def load_resume(
    db: AsyncSession, user: User, resume_id: Any = None
) -> Resume | None:
    """按 id 取简历；没给 id 就用最近编辑的那份（和简历工作台默认打开的同一份）。"""
    stmt = select(Resume).where(Resume.user_id == user.id)
    if resume_id:
        stmt = stmt.where(Resume.id == int(resume_id))
    else:
        stmt = stmt.order_by(Resume.updated_at.desc())
    return (await db.execute(stmt.limit(1))).scalars().first()


async def ask(llm, system: str, human: str) -> str:
    """一次模型调用。占用共享闸门，否则并发会绕过 `llm_gate` 的上限。"""
    async with llm_slot():
        result = await llm.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=human)]
        )
    content = getattr(result, "content", "")
    if isinstance(content, list):
        content = "".join(str(part) for part in content)
    return str(content or "")
