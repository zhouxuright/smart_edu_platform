"""写操作提案的唯一执行入口。

为什么必须收口成一个文件：提案现在有两条确认路径——

- 聊天卡片：`POST /agent/chat/confirm`
- 工作流恢复：`POST /agent/runs/{id}/resume`

两条路径各写一遍 apply，很快就会出现「聊天里能应用、流程里应用不了」，
或者更糟：一边执行了、另一边的状态没改。所有校验、TTL、二次校验、
状态流转都放在这里，接口层只做错误码映射。

四条不变量：

1. **执行前抢占状态。** `UPDATE ... WHERE status='pending'` 影响 0 行就是
   别人先点了，直接 409。没有这一步，两个标签页同时点「应用」会把同一段
   简历改写执行两遍——第二遍的 before 已经是第一遍的 after 了。
2. **执行时重新校验。** 从生成提案到用户点确认之间，简历可能被改过、
   投递可能已经流转。`spec.apply` 内部会再 validate 一次。
3. **创建时只 flush 不 commit。** 工作流步骤里创建提案必须和步骤记录、
   run.state 同一个事务提交，否则崩在中间会留下「提案在、步骤没成功」，
   重跑就会多出一条重复提案。调用方负责 commit。
4. **失败不静默。** 校验失败把提案标成 expired 并抛出原因；执行期异常
   退回 pending，让用户可以重试。两种情况都不能让调用方以为成功了。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, UTC

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.agents.tools import REGISTRY, ToolError
from edu_app.models.agent import AgentProposal
from edu_app.models.user import User

logger = logging.getLogger(__name__)

# 用户半小时后再点「应用」，上下文早变了：简历可能改过、投递可能已流转
PROPOSAL_TTL = timedelta(minutes=30)


class ProposalError(Exception):
    """提案不可用。`status` 直接映射成 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


async def get(db: AsyncSession, user: User, proposal_id: str) -> AgentProposal:
    proposal = (
        await db.execute(
            select(AgentProposal).where(
                AgentProposal.id == proposal_id,
                AgentProposal.user_id == user.id,
            )
        )
    ).scalars().first()
    if not proposal:
        # 别人的提案一律当不存在，报 403 等于确认这个 id 是真的
        raise ProposalError("提案不存在", 404)
    return proposal


def _payload(proposal: AgentProposal) -> dict:
    return {
        "id": proposal.id,
        "tool": proposal.tool,
        "summary": proposal.summary,
        "preview": proposal.preview,
        "status": proposal.status,
        "run_id": proposal.run_id,
        "step": proposal.step,
    }


# ---------------------------------------------------------------------------
# 创建
# ---------------------------------------------------------------------------

async def create(
    db: AsyncSession,
    user: User,
    page: str,
    tool_name: str,
    args: dict | None,
    *,
    run_id: str | None = None,
    step: str | None = None,
) -> tuple[dict | None, str | None]:
    """校验模型给的动作并落成提案。返回 (提案, 拒绝原因)。

    **不 commit**：调用方决定事务边界（见文件头第 3 条）。
    """
    name = str(tool_name or "").strip()
    spec = REGISTRY.get(name)
    if not spec or spec.kind != "write" or page not in spec.pages:
        logger.warning("proposal rejected tool=%s page=%s", name, page)
        return None, "这个操作在当前页面不可用"

    payload = args if isinstance(args, dict) else {}
    try:
        ctx = await spec.validate(db, user, payload)
    except ToolError as error:
        return None, str(error)
    except Exception:
        logger.exception("proposal validate failed: %s", name)
        return None, "参数校验失败，请换个说法再试"

    preview = spec.preview(ctx) if spec.preview else {}
    proposal = AgentProposal(
        id=str(uuid.uuid4()),
        user_id=user.id,
        page=page,
        tool=name,
        args=payload,
        summary=str(preview.get("title") or spec.description)[:200],
        preview=preview,
        status="pending",
        run_id=run_id,
        step=step,
    )
    db.add(proposal)
    await db.flush()
    return _payload(proposal), None


# ---------------------------------------------------------------------------
# 确认 / 拒绝
# ---------------------------------------------------------------------------

async def _force_status(db: AsyncSession, proposal_id: str, status: str) -> None:
    """绕过 ORM 直接改状态。只在 rollback 之后用——那时 ORM 实例已经过期。"""
    await db.execute(
        update(AgentProposal).where(AgentProposal.id == proposal_id).values(status=status)
    )
    await db.commit()


async def apply(
    db: AsyncSession,
    user: User,
    proposal_id: str,
    *,
    expected_run_id: str | None = None,
) -> tuple[AgentProposal, dict]:
    """执行提案。已经执行过的直接返回，不重复执行。"""
    proposal = await get(db, user, proposal_id)

    # 幂等：聊天确认已经应用过，工作流恢复时又调一次，不应该再执行一遍
    if proposal.status == "applied":
        return proposal, {"status": "applied", "tool": proposal.tool, "already": True}

    if proposal.status != "pending":
        raise ProposalError("这个提案已经处理过了", 409)

    if expected_run_id and proposal.run_id and proposal.run_id != expected_run_id:
        raise ProposalError("这个提案不属于当前流程", 409)

    if proposal.created_at and datetime.now(UTC) - proposal.created_at > PROPOSAL_TTL:
        await _force_status(db, proposal_id, "expired")
        raise ProposalError("提案已过期，请重新生成", 409)

    # 先取出来再动：执行过程中会 commit，之后 ORM 属性可能被刷新
    tool_name = proposal.tool
    args = dict(proposal.args or {})

    # 抢占：只有把 pending 改成 applying 的那个请求有权执行
    claimed = await db.execute(
        update(AgentProposal)
        .where(AgentProposal.id == proposal_id, AgentProposal.status == "pending")
        .values(status="applying")
    )
    if claimed.rowcount == 0:
        await db.rollback()
        raise ProposalError("这个提案正在被处理，请稍后刷新", 409)
    await db.commit()

    spec = REGISTRY.get(tool_name)
    if not spec or not spec.apply:
        await _force_status(db, proposal_id, "expired")
        raise ProposalError("这个操作已下线", 400)

    try:
        result = await spec.apply(db, user, args)
    except ToolError as error:
        # 参数已经不符合当前数据（简历改过、投递已流转）：重试也没用，作废
        await db.rollback()
        await _force_status(db, proposal_id, "expired")
        raise ProposalError(str(error), 409)
    except Exception:
        # 执行期异常（数据库抖动之类）：退回 pending，用户还能再点一次
        await db.rollback()
        await _force_status(db, proposal_id, "pending")
        logger.exception("proposal apply failed: %s", tool_name)
        raise ProposalError("执行失败，请重试", 500)

    proposal.status = "applied"
    proposal.applied_at = datetime.now(UTC)
    await db.commit()
    return proposal, {"status": "applied", "tool": tool_name, "result": result}


async def reject(db: AsyncSession, user: User, proposal_id: str) -> AgentProposal:
    proposal = await get(db, user, proposal_id)
    if proposal.status == "pending":
        proposal.status = "rejected"
        await db.commit()
    return proposal
