"""工作流状态机回归（直接打服务层，不依赖前端）。

覆盖四件最容易做错的事：
1. **失败要落库**：输入不合法时 run 变 failed，而不是抛异常就没了。
2. **断点恢复**：把 current_step 改回中间某步，能接着走，不重跑前面的。
3. **幂等**：重复执行同一步不新增步骤记录、不重复扣额度。
4. **并发**：两个请求同时推进，只有一个成功，另一个拿冲突。

外加 HITL 的同意/拒绝两条路径。

跑法（后端可以开着，脚本自己连库）：
    python scripts/agent_workflow_check.py

脚本会消耗演示账号的 3 次模型额度（2 次完整流程 + 1 次断点恢复），
结束后删掉自己创建的 run，不影响简历。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from edu_app.core.database import AsyncSessionLocal, engine
from edu_app.flows.jd_match import resume_to_text
from edu_app.models.resume import Resume
from edu_app.models.user import User
from edu_app.models.workflow import AgentRun, AgentStep
from edu_app.services import workflow
from edu_app.services.workflow import (
    FLOWS,
    Flow,
    FlowError,
    RunConflict,
    RunNotFound,
    Step,
    StepOutcome,
    register_flow,
)

engine.echo = False

EMAIL = "demo@example.com"
PROBE_KINDS = ("jd_match", "__probe_pause")

passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if detail else ""))


# ---------------- HITL 探针流程（只在本脚本里注册） ----------------

async def _p_first(db, run, user):
    return StepOutcome(state={"a": 1}, output={"a": 1})


async def _p_pause(db, run, user):
    return StepOutcome(
        state={"b": 2},
        output={"b": 2},
        awaiting={"kind": "confirm", "summary": "这一步需要人确认"},
    )


async def _p_last(db, run, user):
    return StepOutcome(state={"c": 3}, output={"c": 3})


if "__probe_pause" not in FLOWS:
    register_flow(
        Flow(
            kind="__probe_pause",
            title="HITL 探针",
            steps=[
                Step(name="first", handler=_p_first),
                Step(name="pause", handler=_p_pause),
                Step(name="last", handler=_p_last),
            ],
        )
    )


async def cleanup(db, user_id: int):
    run_ids = (
        await db.execute(
            select(AgentRun.id).where(
                AgentRun.user_id == user_id, AgentRun.kind.in_(PROBE_KINDS)
            )
        )
    ).scalars().all()
    if run_ids:
        await db.execute(delete(AgentStep).where(AgentStep.run_id.in_(run_ids)))
        await db.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
    await db.commit()


async def main() -> int:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalars().first()
        if not user:
            print(f"找不到账号 {EMAIL}")
            return 2
        other = (await db.execute(select(User).where(User.id != user.id).limit(1))).scalars().first()

        await cleanup(db, user.id)

        resume = None
        for row in (
            await db.execute(
                select(Resume).where(Resume.user_id == user.id).order_by(Resume.updated_at.desc())
            )
        ).scalars().all():
            if len(resume_to_text(row.data)) >= 50:
                resume = row
                break
        if not resume:
            print("演示账号没有可用简历，先跑一次 seed 或手动建一份")
            return 2

        # 1. 未知流程
        try:
            await workflow.start_run(db, user, "no_such_flow", {})
            check("未知流程被拒", False, "居然创建成功了")
        except FlowError:
            check("未知流程被拒", True)

        # 2. 输入不合法：失败要落库
        bad = await workflow.start_run(db, user, "jd_match", {"jd_text": "太短"})
        bad = await workflow.advance(db, user, bad.id, 6)
        check(
            "输入不合法时 run 变 failed 且带原因",
            bad.status == "failed" and "JD" in (bad.error or ""),
            f"status={bad.status} error={bad.error}",
        )
        bad_steps = await workflow.load_steps(db, bad.id)
        check(
            "失败步骤留痕",
            len(bad_steps) == 1 and bad_steps[0].status == "failed",
            f"steps={[(s.step, s.status) for s in bad_steps]}",
        )

        # 3. 完整流程
        jd = (
            "岗位：B 端产品经理（SaaS 客服/工单方向）\n"
            "职责：负责工单系统的需求分析与 PRD 撰写；与研发、设计协作推动上线；"
            "通过数据分析持续优化流程。\n"
            "要求：本科及以上学历；2 年以上 B 端产品经验；熟练使用 Axure 或 Figma；"
            "熟练使用 SQL 做数据分析；有可量化的流程优化案例。"
        )
        run = await workflow.start_run(
            db, user, "jd_match", {"jd_text": jd, "resume_id": resume.id}
        )
        run = await workflow.advance(db, user, run.id, 6)
        steps = await workflow.load_steps(db, run.id)
        check(
            "四步全部执行完",
            run.status == "succeeded" and len(steps) == 4,
            f"status={run.status} steps={[s.step for s in steps]}",
        )
        check(
            "每步都标记成功",
            all(s.status == "succeeded" for s in steps),
            str([(s.step, s.status) for s in steps]),
        )
        check("流程结束后 current_step 清空", run.current_step is None)

        state = run.state or {}
        match = state.get("match") or {}
        check(
            "覆盖率是 0~1 的数",
            isinstance(match.get("coverage"), (int, float))
            and 0 <= float(match["coverage"]) <= 1,
            str(match.get("coverage")),
        )
        check(
            "核对结果三分：命中 / 缺失 / 无法验证",
            all(k in match for k in ("matched", "missing", "unverified")),
            str({k: len(match.get(k) or []) for k in ("matched", "missing", "unverified")}),
        )
        check(
            "建议非空且不超过 5 条",
            1 <= len(state.get("advice") or []) <= 5,
            str(len(state.get("advice") or [])),
        )
        check(
            "拆 JD 得到岗位名",
            bool((state.get("requirements") or {}).get("role")),
            str((state.get("requirements") or {}).get("role")),
        )

        # 4. 幂等：回退到已完成的一步，不应新增记录、不应重调模型
        before = len(steps)
        run.current_step = "extract_jd"
        run.status = "running"
        await db.commit()
        run = await workflow.advance(db, user, run.id, 1)
        after = await workflow.load_steps(db, run.id)
        check(
            "重复执行已完成步骤不新增记录",
            len(after) == before,
            f"{before} -> {len(after)}",
        )
        check(
            "复用旧输出继续推进",
            run.current_step == "match_requirements",
            str(run.current_step),
        )

        # 5. 断点恢复：只跑最后一步
        run.current_step = "advise"
        run.status = "running"
        await db.commit()
        run = await workflow.advance(db, user, run.id, 1)
        check(
            "断点恢复只跑指定步",
            run.status == "succeeded" and len(await workflow.load_steps(db, run.id)) == before,
            f"status={run.status}",
        )

        # 6. 并发推进：只有一个能拿到锁
        conc = await workflow.start_run(db, user, "jd_match", {"jd_text": jd})
        conc.state = {**(run.state or {}), "input": {"jd_text": jd}}
        conc.current_step = "match_requirements"
        conc.status = "running"
        await db.commit()

        async def advance_once():
            async with AsyncSessionLocal() as db2:
                return await workflow.run_once(db2, user, conc.id)

        results = await asyncio.gather(advance_once(), advance_once(), return_exceptions=True)
        conflicts = [r for r in results if isinstance(r, RunConflict)]
        others = [r for r in results if isinstance(r, Exception) and not isinstance(r, RunConflict)]
        check(
            "并发推进只有一个成功、另一个冲突",
            len(conflicts) == 1 and not others,
            f"conflicts={len(conflicts)} other={[type(o).__name__ for o in others]}",
        )

        # 7. HITL：拒绝
        p1 = await workflow.start_run(db, user, "__probe_pause", {})
        p1 = await workflow.advance(db, user, p1.id, 6)
        check(
            "需要确认时停在 awaiting_human",
            p1.status == "awaiting_human" and p1.current_step == "pause",
            f"status={p1.status} step={p1.current_step}",
        )
        check(
            "待确认内容写进 state",
            (p1.state or {}).get("_pending", {}).get("next_step") == "last",
            str((p1.state or {}).get("_pending")),
        )
        p1 = await workflow.resume_run(db, user, p1.id, accept=False)
        check("拒绝后流程取消", p1.status == "cancelled" and p1.current_step is None)

        # 8. HITL：同意并跑完
        p2 = await workflow.start_run(db, user, "__probe_pause", {})
        p2 = await workflow.advance(db, user, p2.id, 6)
        p2 = await workflow.resume_run(db, user, p2.id, accept=True)
        p2 = await workflow.advance(db, user, p2.id, 6)
        check(
            "同意后从下一步继续到结束",
            p2.status == "succeeded" and (p2.state or {}).get("c") == 3,
            f"status={p2.status} state={p2.state}",
        )

        # 9. 非等待状态不能 resume
        try:
            await workflow.resume_run(db, user, p2.id, accept=True)
            check("非等待状态 resume 被拒", False, "居然通过了")
        except RunConflict:
            check("非等待状态 resume 被拒", True)

        # 10. 越权
        if other:
            try:
                await workflow.get_run(db, other, run.id)
                check("别人的 run 取不到", False, "居然取到了")
            except RunNotFound:
                check("别人的 run 取不到", True)
        else:
            print("SKIP  别人的 run 取不到（库里只有一个用户）")

        await cleanup(db, user.id)

    print()
    print(f"通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
