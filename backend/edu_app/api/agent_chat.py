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

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.agents.base import get_llm
from edu_app.agents.tools import tools_for
from edu_app.api.endpoints import get_current_user, require_llm_quota
from edu_app.core.config import settings
from edu_app.core.database import get_db
from edu_app.models.agent import AgentProposal
from edu_app.models.memory import AgentSession
from edu_app.models.user import User
from edu_app.services import memory, proposals, workflow
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
    "jd-match": "JD 匹配",
    "mock-interview": "模拟面试",
    "dashboard": "仪表盘",
    "other": "启程 AI",
}

# 提案的有效期与执行逻辑统一在 services/proposals.py，这里不再重复定义
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


def _build_system_prompt(
    page: str, reads: dict[str, dict], memory_block: str = "", prev_summary: str = ""
) -> str:
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

    # 长期记忆与上一次会话摘要都是「背景」，不是用户这一轮说的话。
    # 位置放在数据之前、规则之后，并明确标注来源，避免模型把它们当成当前事实。
    background = ""
    if prev_summary:
        background += f"\n{prev_summary}\n"
    if memory_block:
        background += f"\n{memory_block}\n"

    return f"""你是「启程 AI」的页面助手，用户当前在「{label}」页面。

下面是你唯一可信的数据来源，全部由系统从数据库查得：
{data_block}
{background}
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
    """校验模型给的动作并落成提案。返回 (提案, 拒绝原因)。

    真正的校验、预览、落库都在 `services/proposals.py`：工作流里的确认点
    也走同一份实现，否则两条路径迟早会分叉。
    """
    return await proposals.create(
        db,
        user,
        page,
        str(action.get("tool") or ""),
        action.get("args") if isinstance(action.get("args"), dict) else {},
    )


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

    # 会话落库：刷新页面、换设备都能接上，前端不再需要每轮传整段历史
    session = await memory.get_or_create_session(
        db, current_user, page, str(body.get("session_id") or "").strip() or None
    )
    history = await memory.load_history(db, session.id)
    if not history and isinstance(body.get("history"), list):
        # 兼容还没更新的前端：这一段只用一次、不落库，下一轮起只认服务端
        history = body["history"]

    reads = await _run_reads(page, body.get("page_id"), db, current_user)
    memory_block = await memory.render_memories(db, current_user.id)
    prev_summary = await memory.render_previous_summary(
        db, current_user.id, page, exclude_session_id=session.id
    )

    system_prompt = _build_system_prompt(page, reads, memory_block, prev_summary)
    human = f"{_history_block(history)}用户现在说：{message}"

    tokens = 0
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
        usage = getattr(result, "usage_metadata", None) or {}
        tokens = int(usage.get("total_tokens") or 0)
    except Exception:
        logger.exception("agent chat llm failed")
        raise HTTPException(status_code=503, detail="AI 暂时不可用，请稍后再试")

    reply, action = _parse_decision(str(content or ""))
    if not reply:
        raise HTTPException(status_code=503, detail="AI 没有返回内容，请重试")

    proposal, notice = (None, None)
    if action:
        proposal, notice = await _create_proposal(db, current_user, page, action)

    await memory.append_turn(
        db,
        session,
        message,
        reply,
        proposal_id=(proposal or {}).get("id"),
        tool_name=(proposal or {}).get("tool"),
        tokens=tokens,
    )
    await db.commit()

    # 用户明确说「记住…」才抽记忆，且放后台：抽取失败不影响这轮回答
    if memory.looks_like_memory_request(message):
        memory.schedule_extraction(current_user.id, message, session.id)

    return {
        "reply": reply,
        "proposal": proposal,
        "notice": notice,
        "page": page,
        "session_id": session.id,
        "context_used": list(reads.keys()),
        "memory_used": bool(memory_block or prev_summary),
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

    try:
        if accept:
            proposal, result = await proposals.apply(db, current_user, proposal_id)
        else:
            proposal = await proposals.reject(db, current_user, proposal_id)
            result = None
    except proposals.ProposalError as error:
        raise HTTPException(status_code=error.status, detail=str(error))

    response: dict = {"status": proposal.status, "tool": proposal.tool, "result": result}

    # 提案挂在工作流上：确认完必须把流程推下去。
    # 少了这一步，用户点完「应用」简历改了，但面试流程永远停在等待确认——
    # 而且没有任何提示，因为写操作本身是成功的。
    if proposal.run_id:
        try:
            run = await workflow.resume_run(db, current_user, proposal.run_id, accept)
            if accept:
                run = await workflow.advance(db, current_user, run.id)
            steps = await workflow.load_steps(db, run.id)
            response["run"] = workflow.run_payload(run, steps)
        except Exception as error:  # noqa: BLE001 - 写操作已成功，不能因此报错
            logger.warning("advance run %s after proposal failed: %s", proposal.run_id, error)
            response["run_error"] = str(error)

    return response


@router.get("/agent/chat/history")
async def agent_chat_history(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """恢复一个会话。前端刷新或换设备后靠它把对话接回来。

    提案状态一并带回去：已经应用/拒绝的卡片要置灰，否则用户会重复点。
    """
    session = (
        await db.execute(
            select(AgentSession).where(
                AgentSession.id == session_id,
                AgentSession.user_id == current_user.id,
            )
        )
    ).scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    rows = await memory.load_session_messages(db, session.id)
    proposal_ids = [row.proposal_id for row in rows if row.proposal_id]
    # 注意别把局部变量叫 proposals：会遮住上面导入的 services.proposals
    by_id: dict[str, AgentProposal] = {}
    if proposal_ids:
        found = (
            await db.execute(
                select(AgentProposal).where(AgentProposal.id.in_(proposal_ids))
            )
        ).scalars().all()
        by_id = {item.id: item for item in found}

    messages = []
    for row in rows:
        item: dict = {"role": row.role, "content": row.content or ""}
        proposal = by_id.get(row.proposal_id or "")
        if proposal:
            item["proposal"] = {
                "id": proposal.id,
                "tool": proposal.tool,
                "summary": proposal.summary,
                "preview": proposal.preview,
            }
            item["decided"] = None if proposal.status == "pending" else proposal.status
        messages.append(item)

    return {
        "session_id": session.id,
        "page": session.page,
        "title": session.title,
        "status": session.status,
        "messages": messages,
    }


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
