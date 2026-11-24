"""可恢复的 Agent 工作流执行器。

执行模型是 step-driven：**一次调用只推进一个步骤**，每一步单独提交。

为什么不用 Celery / Temporal：这里的流程是「用户点一下，走 3~5 步，中间可能要确认」，
不是每天几十万任务的调度。状态已经在 Postgres 里，进程重启不丢、可以重放，
不需要 broker、worker、另一个要监控的组件。

为什么不用 LangGraph：它解决「图 + 分支 + checkpoint 后端」。现在只有线性 3~5 步，
引入图框架等于为未来的分支付现在的调试成本。等单条流程超过 8 步且出现真分支再换，
届时表结构不用动（checkpointer 可以写同一套表）。

三个不变量，改代码时别破坏：

1. **额度在拿到锁之后、执行之前扣。** 扣不到就回滚锁，run 停在原地等明天。
   放在锁之前会让「重复执行已完成步骤」也扣一次额度——那一步根本没调模型。
2. **抢锁靠 `version`。** `UPDATE ... WHERE version = :v` 影响 0 行就是有人抢先了，
   抛 409，不要重试——重试会把两步的副作用叠在一起。
3. **`(run_id, step)` 唯一。** 同一流程的同一个步骤永远只可能有一条成功记录，
   重复调用直接复用它的输出。
4. **挂提案的确认点，先应用再推进。** 应用失败就不推进，run 留在
   `awaiting_human`。反过来的话，界面显示流程成功、数据其实没改。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from typing import Any, Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.core.database import AsyncSessionLocal
from edu_app.models.agent import AgentProposal
from edu_app.models.user import User
from edu_app.models.workflow import AgentRun, AgentStep
from edu_app.services import llm_usage, proposals, quota as quota_service

logger = logging.getLogger(__name__)

# 未完成的流程超过这个时间就作废，避免列表里堆着上个月的半截任务
RUN_TTL = timedelta(hours=24)
# 单次 HTTP 调用最多推进几步：4 步的流程一次走完，同时给长流程留刹车
MAX_STEPS_PER_CALL = 6


class FlowError(Exception):
    """流程定义或输入有问题，属于 400。"""


class RunNotFound(Exception):
    """流程不存在或不属于当前用户，属于 404。"""


class RunConflict(Exception):
    """并发推进或状态不允许，属于 409。"""


@dataclass
class StepOutcome:
    """一个步骤的产出。

    - `state`：要合并进 run.state 的增量（不是整体覆盖）
    - `next_step`：跳转目标，不填就按流程顺序走下一步
    - `awaiting`：需要人确认，run 会停在 awaiting_human
    - `done`：显式结束（即使后面还有步骤）
    - `drop`：要从 state 里移除的键。人工输入（`_human_input`）被下一步
      消费掉之后必须删，否则重跑时会拿旧答案当新答案
    """

    state: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    next_step: str | None = None
    awaiting: dict[str, Any] | None = None
    done: bool = False
    drop: tuple[str, ...] = ()


StepHandler = Callable[[AsyncSession, AgentRun, User], Awaitable[StepOutcome]]


@dataclass
class Step:
    name: str
    handler: StepHandler
    # 会调模型的步骤要扣额度、要标 token 归属；纯计算的步骤不扣
    llm: bool = False
    label: str = ""


@dataclass
class Flow:
    kind: str
    steps: list[Step]
    title: str = ""

    def __post_init__(self):
        if not self.steps:
            raise ValueError(f"flow {self.kind} 没有步骤")
        names = [s.name for s in self.steps]
        if len(names) != len(set(names)):
            raise ValueError(f"flow {self.kind} 步骤名重复")

    @property
    def first(self) -> str:
        return self.steps[0].name

    def step(self, name: str) -> Step | None:
        return next((s for s in self.steps if s.name == name), None)

    def next_after(self, name: str) -> str | None:
        for index, step in enumerate(self.steps):
            if step.name == name:
                return self.steps[index + 1].name if index + 1 < len(self.steps) else None
        return None


FLOWS: dict[str, Flow] = {}


def register_flow(flow: Flow) -> Flow:
    if flow.kind in FLOWS:
        raise ValueError(f"flow {flow.kind} 已注册")
    FLOWS[flow.kind] = flow
    return flow


def _ensure_flows_loaded() -> None:
    """flows 包在导入时注册；延迟导入避免 services → flows → services 的循环。"""
    from edu_app import flows  # noqa: F401


def get_flow(kind: str) -> Flow:
    _ensure_flows_loaded()
    flow = FLOWS.get(kind)
    if not flow:
        raise FlowError(f"未知的流程类型：{kind}")
    return flow


def list_flows() -> list[dict[str, str]]:
    _ensure_flows_loaded()
    return [
        {"kind": f.kind, "title": f.title, "steps": len(f.steps)} for f in FLOWS.values()
    ]


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

async def get_run(db: AsyncSession, user: User, run_id: str) -> AgentRun:
    run = (
        await db.execute(
            select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user.id)
        )
    ).scalars().first()
    if not run:
        # 别人的 run 一律当不存在，报 403 等于确认这个 id 是真的
        raise RunNotFound(run_id)
    return run


async def load_steps(db: AsyncSession, run_id: str) -> list[AgentStep]:
    return (
        await db.execute(
            select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.id)
        )
    ).scalars().all()


def run_payload(run: AgentRun, steps: list[AgentStep], *, include_state: bool = True) -> dict:
    payload = {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "current_step": run.current_step,
        "version": run.version,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "steps": [
            {
                "step": s.step,
                "status": s.status,
                "output": s.output_data,
                "error": s.error,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            }
            for s in steps
        ],
    }
    if include_state:
        payload["state"] = run.state or {}
    return payload


# ---------------------------------------------------------------------------
# 启动 / 推进
# ---------------------------------------------------------------------------

async def start_run(
    db: AsyncSession, user: User, kind: str, payload: dict[str, Any] | None = None
) -> AgentRun:
    flow = get_flow(kind)
    now = datetime.now(UTC)
    run = AgentRun(
        id=str(uuid.uuid4()),
        user_id=user.id,
        kind=flow.kind,
        status="running",
        current_step=flow.first,
        state={"input": payload or {}},
        version=0,
        created_at=now,
        updated_at=now,
        expires_at=now + RUN_TTL,
    )
    db.add(run)
    await db.commit()
    return run


async def run_once(db: AsyncSession, user: User, run_id: str) -> AgentRun:
    """推进一个步骤。调用方负责决定要不要循环。"""
    run = await get_run(db, user, run_id)
    if run.status != "running":
        return run

    flow = get_flow(run.kind)
    step_name = run.current_step or flow.first
    step = flow.step(step_name)
    if not step:
        raise FlowError(f"流程 {run.kind} 没有步骤 {step_name}")

    # 抢锁。rowcount=0 说明另一个请求已经把 version 推进了，或者状态变了
    claimed = await db.execute(
        update(AgentRun)
        .where(
            AgentRun.id == run.id,
            AgentRun.version == run.version,
            AgentRun.status == "running",
        )
        .values(version=AgentRun.version + 1)
    )
    if claimed.rowcount == 0:
        await db.rollback()
        raise RunConflict("这个流程正在被另一个请求推进，请稍后刷新")

    # 幂等：这一步成功过就直接用它的输出，不再执行、也不再扣额度
    existing = (
        await db.execute(
            select(AgentStep).where(AgentStep.run_id == run.id, AgentStep.step == step_name)
        )
    ).scalars().first()

    if existing and existing.status == "succeeded":
        # 上一步已经完整提交（步骤记录 + state + current_step 在同一个事务里），
        # 这里只是防御性重入：直接按它的输出往下走，不再执行
        return await _apply_and_advance(
            db, run, flow, step_name, StepOutcome(output=existing.output_data)
        )

    # 额度在锁内扣：扣不到就回滚锁，run 回到「未推进」，用户明天还能接着走
    if step.llm:
        try:
            llm_usage.set_current_user(user.id)
            await quota_service.consume_quota(user)
        except Exception:
            await db.rollback()
            raise

    now = datetime.now(UTC)
    if existing:
        # 上次执行到一半崩了（事务回滚的话不会留下记录，这里是极端情况），重跑
        step_row = existing
        step_row.status = "running"
        step_row.started_at = now
        step_row.ended_at = None
        step_row.error = None
    else:
        step_row = AgentStep(
            run_id=run.id,
            step=step_name,
            status="running",
            input_data=run.state or {},
            started_at=now,
        )
        db.add(step_row)

    try:
        outcome = await step.handler(db, run, user)
    except Exception as error:  # noqa: BLE001 - 步骤失败要落库，不能只写日志
        logger.exception("workflow step failed: run=%s step=%s", run.id, step_name)
        step_row.status = "failed"
        step_row.error = str(error)[:500]
        step_row.ended_at = datetime.now(UTC)
        run.status = "failed"
        run.error = f"{step_name}: {str(error)[:400]}"
        run.updated_at = datetime.now(UTC)
        await db.commit()
        return run

    step_row.status = "succeeded"
    step_row.output_data = outcome.output or {}
    step_row.ended_at = datetime.now(UTC)
    # 不在这里单独 commit：步骤记录与 state / current_step 必须同一个事务落库，
    # 否则崩在两次提交之间会留下「步骤成功但状态没合并」的半截流程
    return await _apply_and_advance(db, run, flow, step_name, outcome)


async def _apply_and_advance(
    db: AsyncSession, run: AgentRun, flow: Flow, step_name: str, outcome: StepOutcome
) -> AgentRun:
    state = dict(run.state or {})
    for key in outcome.drop:
        state.pop(key, None)
    if outcome.state:
        state.update(outcome.state)
    if outcome.awaiting:
        state["_pending"] = outcome.awaiting
    else:
        state.pop("_pending", None)
    run.state = state
    run.updated_at = datetime.now(UTC)

    next_name = outcome.next_step or flow.next_after(step_name)

    if outcome.awaiting:
        # 停在原地等确认；current_step 不前进，恢复时从 _pending.next_step 走
        run.status = "awaiting_human"
        state["_pending"] = {**outcome.awaiting, "next_step": next_name}
        run.state = state
    elif outcome.done or not next_name:
        run.status = "succeeded"
        run.current_step = None
    else:
        run.current_step = next_name

    await db.commit()
    return run


async def advance(
    db: AsyncSession, user: User, run_id: str, max_steps: int = MAX_STEPS_PER_CALL
) -> AgentRun:
    """连续推进，直到流程停下、需要人确认、或达到步数上限。"""
    run = await get_run(db, user, run_id)
    for _ in range(max(1, max_steps)):
        if run.status != "running":
            break
        run = await run_once(db, user, run_id)
    return run


async def resume_run(
    db: AsyncSession,
    user: User,
    run_id: str,
    accept: bool,
    payload: dict[str, Any] | None = None,
) -> AgentRun:
    """人机协同的恢复点。

    两类确认点走同一个入口，区别只在 `_pending` 里带了什么：

    - **回答型**（模拟面试的每一题）：`payload` 是用户这一轮的输入，
      写进 `state["_human_input"]`，下一步负责消费并 `drop` 掉。
    - **写操作型**（改简历提案）：`_pending.proposal_id` 指向一条
      `AgentProposal`，同意就执行，执行不成功不推进流程。

    拒绝一律取消整条流程。中途放弃比留下半截状态干净。
    """
    run = await get_run(db, user, run_id)
    if run.status != "awaiting_human":
        raise RunConflict("这个流程当前不在等待确认")

    state = dict(run.state or {})
    pending = dict(state.get("_pending") or {})
    state.pop("_pending", None)
    proposal_id = pending.get("proposal_id")

    if not accept:
        if proposal_id:
            try:
                await proposals.reject(db, user, proposal_id)
            except proposals.ProposalError:
                # 提案已经过期/被处理过，不该拦住「放弃流程」这个动作
                logger.info("reject proposal %s skipped: already settled", proposal_id)
        run.status = "cancelled"
        run.current_step = None
        run.state = state
        run.updated_at = datetime.now(UTC)
        await db.commit()
        return run

    # 顺序不能反：必须**先应用再推进**。反过来（先推进、后应用）一旦应用失败，
    # 就会留下「流程往下走了但数据没改」的状态，而且用户看不出问题——
    # 界面显示成功，简历其实没动。
    if proposal_id:
        try:
            await proposals.apply(db, user, proposal_id, expected_run_id=run.id)
        except proposals.ProposalError as error:
            # run 留在 awaiting_human：用户可以重试，也可以选择放弃
            raise RunConflict(f"提案执行失败：{error}") from error

    if payload:
        state["_human_input"] = payload

    next_step = pending.get("next_step")
    if not next_step:
        # 最后一步的确认点：同意即结束。
        # 这里如果把 status 设成 running、current_step 设成 None，下一次
        # run_once 会回落到 flow.first，整条流程从头再跑一遍。
        run.status = "succeeded"
        run.current_step = None
    else:
        run.status = "running"
        run.current_step = next_step

    run.state = state
    run.updated_at = datetime.now(UTC)
    await db.commit()
    return run


# ---------------------------------------------------------------------------
# 过期清扫
# ---------------------------------------------------------------------------

# 十分钟扫一次。量级是「每天几十条」，不需要更密
SWEEP_INTERVAL = timedelta(minutes=10)


async def sweep_expired(db: AsyncSession) -> dict[str, int]:
    """作废过期的流程与提案，返回各自影响的行数。

    为什么必须有这一步：`expires_at` 和提案 TTL 都只是「写在字段里」，
    没人扫的话它们只是装饰——作废的流程会一直躺在列表里，超时提案会一直
    显示「待确认」，用户点下去才报「已过期」。这类「状态字段写了但没人执行」
    是最容易在演示时被看穿的一类问题。

    两件事一起做，顺序不能反：先把挂在过期 run 上的 pending 提案作废，
    再取消 run。反过来会留下「流程已取消、提案还能点」的孤儿。
    """
    now = datetime.now(UTC)

    stale_run_ids = (
        await db.execute(
            select(AgentRun.id).where(
                AgentRun.status.in_(("running", "awaiting_human")),
                AgentRun.expires_at.is_not(None),
                AgentRun.expires_at < now,
            )
        )
    ).scalars().all()

    proposals_expired = 0
    runs_cancelled = 0

    if stale_run_ids:
        result = await db.execute(
            update(AgentProposal)
            .where(
                AgentProposal.run_id.in_(stale_run_ids),
                AgentProposal.status == "pending",
            )
            .values(status="expired")
        )
        proposals_expired += result.rowcount

        result = await db.execute(
            update(AgentRun)
            .where(AgentRun.id.in_(stale_run_ids))
            .values(
                status="cancelled",
                current_step=None,
                updated_at=now,
                error=f"超时未完成（超过 {int(RUN_TTL.total_seconds() // 3600)} 小时），已自动作废",
            )
        )
        runs_cancelled += result.rowcount

    # 没挂在流程上的提案（聊天里生成的）只看 TTL
    result = await db.execute(
        update(AgentProposal)
        .where(
            AgentProposal.status == "pending",
            AgentProposal.created_at < now - proposals.PROPOSAL_TTL,
        )
        .values(status="expired")
    )
    proposals_expired += result.rowcount

    await db.commit()
    return {"runs": runs_cancelled, "proposals": proposals_expired}


async def sweep_loop(stop_event: asyncio.Event) -> None:
    """进程内的清扫循环。和 `reminder_loop` 同一个模式：不引 broker。

    单副本时它就是唯一执行者；将来多副本，`UPDATE ... WHERE status = ...`
    天然幂等，两个副本同时扫也只会有一个真的改到行。
    """
    while not stop_event.is_set():
        try:
            async with AsyncSessionLocal() as db:
                stats = await sweep_expired(db)
            if stats["runs"] or stats["proposals"]:
                logger.info(
                    "清扫：作废 %d 个流程、%d 条提案",
                    stats["runs"],
                    stats["proposals"],
                )
        except Exception:  # noqa: BLE001 - 循环不能因为一次失败就退出
            logger.exception("清扫过期流程失败")

        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=SWEEP_INTERVAL.total_seconds()
            )
        except asyncio.TimeoutError:
            continue
