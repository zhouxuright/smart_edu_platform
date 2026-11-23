"""统一 Agent 对话接口（页面级工具版）。

流程：
1. 前端带上「当前页面 + 资源 id」，服务端按页面挑读工具，自己去查真实数据；
2. 把查到的数据作为上下文交给模型，模型只能基于这些数据回答；
3. 模型如需修改，返回 action（工具名 + 参数），服务端校验后落成一条提案；
4. 用户点「应用」才真正执行，执行前再校验一次。

为什么不做逐字流式：
回答和动作必须在同一个 JSON 里返回，否则会出现「已经看到改好的文字，动作却被校验拒绝」。
所以这里是一次性返回，前端用 loading 态过渡；要流式得等升级成原生工具调用。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, UTC

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.agents.base import get_llm
from edu_app.agents.tools import REGISTRY, ToolError, tools_for
from edu_app.api.endpoints import get_current_user, require_llm_quota
from edu_app.core.config import settings
from edu_app.core.database import get_db
from edu_app.models.agent import AgentProposal
from edu_app.models.user import User
from edu_app.services.llm_gate import llm_slot

router = APIRouter(tags=["agent"])
logger = logging.getLogger(__name__)

PAGE_LABEL = {
    "resume": "简历工作台",
    "applications": "投递看板",
    "plan": "我的计划",
    "mistake-book": "错题本",
    "parent": "家长门户",
    "knowledge": "知识图谱",
    "dashboard": "仪表盘",
    "other": "启程 AI",
}

# 提案超过这个时间就作废：用户半小时后再点「应用」，上下文早变了
PROPOSAL_TTL = timedelta(minutes=30)
MAX_HISTORY_TURNS = 6


def _llm_ready() -> bool:
    base = (settings.LLM_API_BASE or "").strip()
    key = (settings.LLM_API_KEY or "").strip()
    return bool(
        not getattr(settings, "LLM_DISABLE", False)
        and base
        and key
        and key.lower() != "dummy"
    )


def _build_system_prompt(page: str, reads: dict[str, dict]) -> str:
    label = PAGE_LABEL.get(page, PAGE_LABEL["other"])
    sections = []
    for name, data in reads.items():
        sections.append(f"### {name}\n{json.dumps(data, ensure_ascii=False)}")
    data_block = "\n\n".join(sections) or "（没有查到数据）"

    write_tools = tools_for(page, "write")
    if write_tools:
        tool_block = "\n".join(
            f"- {spec.name}：{spec.description}；参数 {spec.params}" for spec in write_tools
        )
    else:
        tool_block = "（当前页面没有可执行的写操作，action 必须为 null）"

    return f"""你是「启程 AI」的页面助手，用户当前在「{label}」页面。

下面是你唯一可信的数据来源，全部由系统从数据库查得：
{data_block}

你可以提议的写操作（只是提议，用户确认后才会执行）：
{tool_block}

输出要求：
只输出一个 JSON 对象，不要 Markdown 代码块，不要任何解释性文字。
{{"reply": "给用户看的中文回答", "action": null 或 {{"tool": "工具名", "args": {{...}}}}}}

规则：
1. 只依据上面的数据回答。数据里没有的信息，直说「这份数据里没有」，不要编造公司、数字、时间。
2. 用户没明确要求修改，action 一律为 null，只给建议。
3. 用户要求修改时，reply 用一句话说明打算怎么改，然后给出 action；不要在 reply 里说「已经改好了」。
4. 改写要点时：每条以动词开头，保留原文的数字与结果，不新增原文没有的事实，每条不超过 60 字。
5. 推进投递状态时，只能选数据里 can_move_to 列出的状态。
6. 回答控制在 150 字以内，除非用户要求详细展开。
"""


def _history_block(history: list) -> str:
    lines = []
    for item in (history or [])[-MAX_HISTORY_TURNS:]:
        if not isinstance(item, dict):
            continue
        role = "用户" if item.get("role") == "user" else "助手"
        text = str(item.get("content") or "").strip()[:200]
        if text:
            lines.append(f"{role}：{text}")
    if not lines:
        return ""
    return "此前的对话（仅供参考，不要照抄）：\n" + "\n".join(lines) + "\n\n"


def _parse_decision(content: str) -> tuple[str, dict | None]:
    text = (content or "").strip()
    if not text:
        return "", None
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return text, None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return text, None
    if not isinstance(payload, dict):
        return text, None
    reply = str(payload.get("reply") or "").strip() or "我没太理解，可以说得更具体一点吗？"
    action = payload.get("action")
    return reply, action if isinstance(action, dict) else None


async def _run_reads(page: str, page_id, db: AsyncSession, user: User) -> dict[str, dict]:
    reads: dict[str, dict] = {}
    for spec in tools_for(page, "read"):
        try:
            reads[spec.name] = await spec.reader(db, user, page_id)
        except Exception:
            # 单个读工具失败不该让整轮对话挂掉，缺了数据模型会说「没查到」
            logger.exception("agent read tool failed: %s", spec.name)
            reads[spec.name] = {"error": "读取失败"}
    return reads


async def _create_proposal(
    db: AsyncSession, user: User, page: str, action: dict
) -> tuple[dict | None, str | None]:
    """校验模型给的动作并落成提案。返回 (提案, 拒绝原因)。"""
    name = str(action.get("tool") or "").strip()
    spec = REGISTRY.get(name)
    if not spec or spec.kind != "write" or page not in spec.pages:
        logger.warning("agent rejected tool=%s page=%s", name, page)
        return None, "这个操作在当前页面不可用"

    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    try:
        ctx = await spec.validate(db, user, args)
    except ToolError as error:
        return None, str(error)
    except Exception:
        logger.exception("agent validate failed: %s", name)
        return None, "参数校验失败，请换个说法再试"

    preview = spec.preview(ctx) if spec.preview else {}
    proposal = AgentProposal(
        id=str(uuid.uuid4()),
        user_id=user.id,
        page=page,
        tool=name,
        args=args,
        summary=str(preview.get("title") or spec.description)[:200],
        preview=preview,
        status="pending",
    )
    db.add(proposal)
    await db.commit()
    return {
        "id": proposal.id,
        "tool": name,
        "summary": proposal.summary,
        "preview": preview,
    }, None


@router.post("/agent/chat")
async def agent_chat(
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _quota: bool = Depends(require_llm_quota),
):
    body = payload or {}
    page = str(body.get("page") or "other")[:40]
    if page not in PAGE_LABEL:
        page = "other"
    message = str(body.get("message") or "").strip()[:2000]
    if len(message) < 2:
        raise HTTPException(status_code=400, detail="请把问题说完整一点")
    if not _llm_ready():
        raise HTTPException(status_code=503, detail="AI 暂时不可用，请稍后再试")

    reads = await _run_reads(page, body.get("page_id"), db, current_user)

    system_prompt = _build_system_prompt(page, reads)
    human = f"{_history_block(body.get('history'))}用户现在说：{message}"

    try:
        llm = get_llm(temperature=0.2, max_tokens=900)
        # 走 SDK 的调用也要占同一个额度池，否则闸门只管住 httpx 那几条路径
        async with llm_slot():
            result = await llm.ainvoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=human)]
            )
        content = getattr(result, "content", "")
        if isinstance(content, list):
            content = "".join(str(part) for part in content)
    except Exception:
        logger.exception("agent chat llm failed")
        raise HTTPException(status_code=503, detail="AI 暂时不可用，请稍后再试")

    reply, action = _parse_decision(str(content or ""))
    if not reply:
        raise HTTPException(status_code=503, detail="AI 没有返回内容，请重试")

    proposal, notice = (None, None)
    if action:
        proposal, notice = await _create_proposal(db, current_user, page, action)

    return {
        "reply": reply,
        "proposal": proposal,
        "notice": notice,
        "page": page,
        "context_used": list(reads.keys()),
    }


@router.post("/agent/chat/confirm")
async def agent_chat_confirm(
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    body = payload or {}
    proposal_id = str(body.get("proposal_id") or "").strip()
    accept = bool(body.get("accept"))
    if not proposal_id:
        raise HTTPException(status_code=400, detail="缺少提案 id")

    proposal = (
        await db.execute(
            select(AgentProposal).where(
                AgentProposal.id == proposal_id,
                AgentProposal.user_id == current_user.id,
            )
        )
    ).scalars().first()
    if not proposal:
        raise HTTPException(status_code=404, detail="提案不存在")
    if proposal.status != "pending":
        raise HTTPException(status_code=409, detail="这个提案已经处理过了")

    # 先取出来再动，避免 commit 后 ORM 实例过期、再读字段触发懒加载
    tool_name = proposal.tool
    args = proposal.args or {}
    created_at = proposal.created_at

    if created_at and datetime.now(UTC) - created_at > PROPOSAL_TTL:
        await db.execute(
            update(AgentProposal).where(AgentProposal.id == proposal_id).values(status="expired")
        )
        await db.commit()
        raise HTTPException(status_code=409, detail="提案已过期，请重新生成")

    if not accept:
        await db.execute(
            update(AgentProposal).where(AgentProposal.id == proposal_id).values(status="rejected")
        )
        await db.commit()
        return {"status": "rejected", "tool": tool_name}

    spec = REGISTRY.get(tool_name)
    if not spec or not spec.apply:
        raise HTTPException(status_code=400, detail="这个操作已下线")

    try:
        result = await spec.apply(db, current_user, args)
    except ToolError as error:
        await db.rollback()
        await db.execute(
            update(AgentProposal).where(AgentProposal.id == proposal_id).values(status="expired")
        )
        await db.commit()
        raise HTTPException(status_code=409, detail=str(error))
    except Exception:
        await db.rollback()
        logger.exception("agent apply failed: %s", tool_name)
        raise HTTPException(status_code=500, detail="执行失败，请重试")

    await db.execute(
        update(AgentProposal)
        .where(AgentProposal.id == proposal_id)
        .values(status="applied", applied_at=datetime.now(UTC))
    )
    await db.commit()
    return {"status": "applied", "tool": tool_name, "result": result}


@router.get("/agent/chat/tools")
async def agent_chat_tools(page: str = "other", current_user: User = Depends(get_current_user)):
    """列出某个页面对模型开放的工具，用于排查「为什么它不动手」。"""
    return {
        "page": page,
        "tools": [
            {"name": spec.name, "kind": spec.kind, "description": spec.description}
            for spec in tools_for(page)
        ],
    }
