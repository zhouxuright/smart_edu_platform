"""工作流接口：启动、推进、确认、查询。

刻意做成「一步一个请求」的形状：每次调用最多推进 `max_steps` 步，
前端可以按步骤显示进度，进程被杀掉也不会丢中间结果。

不要在这里写业务逻辑——流程定义在 `edu_app/flows/` 里，这里只做
鉴权、错误码映射和序列化。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.api.endpoints import get_current_user
from edu_app.core.database import get_db
from edu_app.models.user import User
from edu_app.models.workflow import AgentRun
from edu_app.services import workflow

router = APIRouter(tags=["agent-workflow"])


class StartRunBody(BaseModel):
    kind: str
    input: dict | None = None
    # 一次请求最多推进几步。4 步的流程填 6 就一次走完
    max_steps: int = Field(default=6, ge=1, le=10)


class ResumeBody(BaseModel):
    accept: bool
    # 多轮流程（模拟面试）把用户这一轮的回答随确认一起提交，写进 state["_human_input"]；
    # 单步确认（JD 匹配、改简历提案）不需要，留空即可
    payload: dict | None = None


def _map_error(error: Exception) -> HTTPException:
    if isinstance(error, workflow.RunNotFound):
        return HTTPException(status_code=404, detail="流程不存在")
    if isinstance(error, workflow.RunConflict):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, workflow.FlowError):
        return HTTPException(status_code=400, detail=str(error))
    return HTTPException(status_code=500, detail="流程执行失败")


@router.get("/agent/flows")
async def list_flows(current_user: User = Depends(get_current_user)):
    """可用的流程类型，前端据此渲染入口，不用把 kind 硬编码在页面里。"""
    return {"flows": workflow.list_flows()}


@router.post("/agent/runs")
async def start_run(
    body: StartRunBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        run = await workflow.start_run(db, current_user, body.kind, body.input or {})
        run = await workflow.advance(db, current_user, run.id, body.max_steps)
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001 - 统一映射成 HTTP 错误
        raise _map_error(error)

    steps = await workflow.load_steps(db, run.id)
    return workflow.run_payload(run, steps)


@router.get("/agent/runs")
async def list_runs(
    kind: str | None = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(AgentRun).where(AgentRun.user_id == current_user.id)
    if kind:
        stmt = stmt.where(AgentRun.kind == kind)
    rows = (
        await db.execute(
            stmt.order_by(AgentRun.created_at.desc()).limit(max(1, min(limit, 50)))
        )
    ).scalars().all()
    return {"runs": [workflow.run_payload(r, [], include_state=False) for r in rows]}


@router.get("/agent/runs/{run_id}")
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        run = await workflow.get_run(db, current_user, run_id)
    except Exception as error:  # noqa: BLE001
        raise _map_error(error)
    steps = await workflow.load_steps(db, run.id)
    return workflow.run_payload(run, steps)


@router.post("/agent/runs/{run_id}/advance")
async def advance_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """推进一步。前端按步骤展示进度时用；一次走完用 POST /agent/runs。"""
    try:
        run = await workflow.advance(db, current_user, run_id, max_steps=1)
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001
        raise _map_error(error)
    steps = await workflow.load_steps(db, run.id)
    return workflow.run_payload(run, steps)


@router.post("/agent/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    body: ResumeBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """人机协同的确认点：同意继续（可带这一轮的输入），拒绝则整条流程取消。"""
    try:
        run = await workflow.resume_run(
            db, current_user, run_id, body.accept, body.payload
        )
        if body.accept:
            run = await workflow.advance(db, current_user, run_id, max_steps=6)
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001
        raise _map_error(error)
    steps = await workflow.load_steps(db, run.id)
    return workflow.run_payload(run, steps)
