"""模拟面试 + 提案 HITL 回归（直接打服务层，不依赖前端）。

这个脚本专门验证「多轮中断」这件事，因为它最容易做出假象：

1. **每一题都要真的停下来。** 如果 `ask_n` 没有返回 `awaiting`，
   流程会在一次请求里跑完 12 步，用户什么都看不到。
2. **人工输入不能残留。** `_human_input` 被 `record_n` 消费后必须删掉，
   否则重跑会拿上一题的答案当这一题的。
3. **提案要挂在 run 上。** 没有 `run_id`，用户点完「应用」流程永远停在
   等待确认，而界面显示一切正常。
4. **确认完流程要结束。** 最后一步的 `next_step` 是 None，如果恢复时把
   status 设成 running，下一次推进会把整场面试从头再跑一遍。
5. **一次面试只扣 3 次额度。** 出题 / 总评 / 提议各一次，逐题调模型
   会让成本翻四倍。

跑法：
    python scripts/agent_mock_interview_check.py

脚本自建探针账号与简历，结束后全部删除，不碰演示账号的数据。
会消耗 6 次模型额度（两场面试，各 3 次）。
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from edu_app.core.database import AsyncSessionLocal, engine
from edu_app.models.agent import AgentProposal
from edu_app.models.learning import LlmUsage
from edu_app.models.resume import Resume, ResumeVersion
from edu_app.models.user import User
from edu_app.models.workflow import AgentRun, AgentStep
from edu_app.services import proposals, workflow
from edu_app.services.workflow import RunConflict

engine.echo = False

passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if detail else ""))


RESUME_DATA = {
    "basics": {
        "name": "林一",
        "city": "杭州",
        "summary": "3 年 B 端产品经验，负责客服工单系统，主导需求分析与 PRD 撰写，上线后处理时长下降 30%。",
    },
    "skills": ["Axure", "Figma", "SQL", "数据分析"],
    "experience": [{
        "company": "某 SaaS 公司",
        "role": "产品经理",
        "start": "2022.03",
        "end": "至今",
        "bullets": [
            "负责工单系统需求分析与 PRD 撰写",
            "用 SQL 做漏斗分析，推动流程优化，处理时长下降 30%",
        ],
    }],
    "education": [{
        "school": "某某大学", "major": "信息管理", "degree": "本科",
        "start": "2018.09", "end": "2022.06",
    }],
    "projects": [{
        "name": "客服知识库重构",
        "role": "产品负责人",
        "start": "2023.05",
        "end": "2023.09",
        "bullets": ["梳理 200 条高频问题，重写检索规则，首次解决率从 42% 提到 61%"],
    }],
    "awards": [],
}

ANSWERS = [
    "我是林一，做了三年 B 端产品，主要在做客服工单系统。选这个方向是因为我喜欢把模糊的流程问题拆成可执行的需求。",
    "工单系统里最值得说的是处理时长优化：我先用 SQL 拉了三年的工单数据做漏斗，发现 60% 的时间耗在分派环节，"
    "于是把按人分派改成按技能标签分派，上线后平均处理时长下降 30%。",
    "岗位要求数据分析能力，我平时用 SQL 和简单的看板就够用；短板是没有做过机器学习相关的策略，"
    "我的补法是先把手上的分派规则做成可量化的实验，再逐步引入。",
    "有一次研发排期和上线时间冲突，我把需求拆成必须上线和可以延后两部分，拿数据说服业务方先上核心链路，"
    "最终按时上线，延后部分在下一个迭代补齐。",
]


async def make_probe(db) -> tuple[User, Resume]:
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"interview_probe_{suffix}@example.com",
        hashed_password="not-a-real-hash",
        full_name="面试探针",
        role="student",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    resume = Resume(
        user_id=user.id,
        title="面试探针简历",
        target_role="B 端产品经理",
        data=RESUME_DATA,
        version=0,
    )
    db.add(resume)
    await db.commit()
    await db.refresh(resume)
    return user, resume


async def cleanup(db, user_id: int, resume_id: int):
    run_ids = (
        await db.execute(select(AgentRun.id).where(AgentRun.user_id == user_id))
    ).scalars().all()
    if run_ids:
        await db.execute(delete(AgentStep).where(AgentStep.run_id.in_(run_ids)))
        await db.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
    await db.execute(delete(AgentProposal).where(AgentProposal.user_id == user_id))
    await db.execute(delete(ResumeVersion).where(ResumeVersion.resume_id == resume_id))
    await db.execute(delete(Resume).where(Resume.id == resume_id))
    await db.execute(delete(LlmUsage).where(LlmUsage.user_id == user_id))
    await db.execute(delete(User).where(User.id == user_id))
    await db.commit()


async def answer_all(db, user, run):
    """按 `_pending` 一路答下去，直到不再等回答。返回 (run, 答了几轮)。"""
    rounds = 0
    while run.status == "awaiting_human" and rounds < 8:
        pending = (run.state or {}).get("_pending") or {}
        if pending.get("kind") != "answer":
            break
        index = int(pending.get("index") or 1)
        answer = ANSWERS[min(index - 1, len(ANSWERS) - 1)]
        run = await workflow.resume_run(db, user, run.id, True, {"answer": answer})
        run = await workflow.advance(db, user, run.id, 6)
        rounds += 1
    return run, rounds


async def main() -> int:
    async with AsyncSessionLocal() as db:
        user, resume = await make_probe(db)
        print(f"探针账号 {user.email} / 简历 #{resume.id}")

        # ---------------- 第一场：完整答完并应用提案 ----------------
        run = await workflow.start_run(
            db, user, "mock_interview", {"resume_id": resume.id, "target_role": "B 端产品经理"}
        )
        run = await workflow.advance(db, user, run.id, 6)

        check(
            "第一题就停下来等人回答",
            run.status == "awaiting_human" and run.current_step == "ask_1",
            f"status={run.status} step={run.current_step}",
        )
        pending = (run.state or {}).get("_pending") or {}
        check(
            "待回答内容带题号与题干",
            pending.get("kind") == "answer" and pending.get("index") == 1 and bool(pending.get("question")),
            f"kind={pending.get('kind')} q={(pending.get('question') or '')[:24]}",
        )
        check(
            "出题阶段没走完全部步骤",
            len(await workflow.load_steps(db, run.id)) == 3,
            str([s.step for s in await workflow.load_steps(db, run.id)]),
        )

        run, rounds = await answer_all(db, user, run)
        state = run.state or {}
        answers = state.get("answers") or []
        check("四轮都答上了", rounds == 4 and len(answers) == 4, f"rounds={rounds} answers={len(answers)}")
        check(
            "每条回答都带着对应题目",
            all(item.get("question") for item in answers),
            str([(item["index"], bool(item["question"])) for item in answers]),
        )
        check(
            "人工输入已被消费，没有残留在 state 里",
            "_human_input" not in state,
            str(sorted(k for k in state if k.startswith("_"))),
        )

        review = state.get("review") or {}
        check(
            "总评给出 0~100 的分数",
            isinstance(review.get("score"), int) and 0 <= review["score"] <= 100,
            f"score={review.get('score')} verdict={(review.get('verdict') or '')[:20]}",
        )

        proposal_id = ((state.get("_pending") or {}).get("proposal_id")) or (
            (state.get("improvement") or {}).get("proposal_id")
        )
        proposal = None
        if proposal_id:
            proposal = (
                await db.execute(select(AgentProposal).where(AgentProposal.id == proposal_id))
            ).scalars().first()

        check(
            "最后停在「是否应用简历改写」上",
            run.status == "awaiting_human"
            and (state.get("_pending") or {}).get("kind") == "proposal",
            f"status={run.status} kind={(state.get('_pending') or {}).get('kind')} note={state.get('improvement_note')}",
        )
        check(
            "提案挂在这个 run 的 propose_improvement 步骤上",
            bool(proposal) and proposal.run_id == run.id and proposal.step == "propose_improvement",
            f"run_id={getattr(proposal, 'run_id', None)} step={getattr(proposal, 'step', None)}",
        )

        before_version = resume.version
        run = await workflow.resume_run(db, user, run.id, True)
        check(
            "确认后流程直接结束，不会从头再跑",
            run.status == "succeeded" and run.current_step is None,
            f"status={run.status} step={run.current_step}",
        )
        check(
            "提案被标记为已应用",
            (await db.execute(select(AgentProposal.status).where(AgentProposal.id == proposal_id))).scalar()
            == "applied",
            str((await db.execute(select(AgentProposal.status).where(AgentProposal.id == proposal_id))).scalar()),
        )

        await db.refresh(resume)
        check(
            "简历真的被改了（版本号前进）",
            resume.version > before_version,
            f"{before_version} -> {resume.version}",
        )

        # 幂等：聊天确认已经应用过，流程恢复又调一次，不该再写一遍
        again, payload = await proposals.apply(db, user, proposal_id, expected_run_id=run.id)
        check(
            "重复应用不产生第二次写入",
            payload.get("already") is True and again.status == "applied",
            str(payload.get("already")),
        )

        used = (
            await db.execute(
                select(LlmUsage.count).where(LlmUsage.user_id == user.id)
            )
        ).scalar()
        check("一场面试只扣 3 次额度", int(used or 0) == 3, f"used={used}")

        # ---------------- 第二场：拒绝提案 ----------------
        run2 = await workflow.start_run(
            db, user, "mock_interview", {"resume_id": resume.id, "target_role": "B 端产品经理"}
        )
        run2 = await workflow.advance(db, user, run2.id, 6)
        run2, _ = await answer_all(db, user, run2)
        pending2 = (run2.state or {}).get("_pending") or {}

        if pending2.get("kind") == "proposal":
            pid2 = pending2.get("proposal_id")
            run2 = await workflow.resume_run(db, user, run2.id, False)
            check(
                "拒绝后流程取消",
                run2.status == "cancelled" and run2.current_step is None,
                f"status={run2.status}",
            )
            status2 = (
                await db.execute(select(AgentProposal.status).where(AgentProposal.id == pid2))
            ).scalar()
            check("拒绝时提案一并标记为 rejected", status2 == "rejected", str(status2))
        else:
            print(f"SKIP  拒绝路径（这场没有生成提案：{run2.status} / {run2.state.get('improvement_note')}）")

        # ---------------- 边界 ----------------
        try:
            await workflow.resume_run(db, user, run.id, True)
            check("非等待状态 resume 被拒", False, "居然通过了")
        except RunConflict:
            check("非等待状态 resume 被拒", True)

        try:
            await proposals.apply(db, user, "no-such-proposal")
            check("不存在的提案被拒", False, "居然通过了")
        except proposals.ProposalError as error:
            check("不存在的提案被拒", error.status == 404, f"status={error.status}")

        # 越权：别的账号拿不到这条提案
        other = (
            await db.execute(select(User).where(User.id != user.id).limit(1))
        ).scalars().first()
        if other and proposal_id:
            try:
                await proposals.apply(db, other, proposal_id)
                check("别人的提案取不到", False, "居然取到了")
            except proposals.ProposalError as error:
                check("别人的提案取不到", error.status == 404, f"status={error.status}")
        else:
            print("SKIP  别人的提案取不到（库里只有一个用户）")

        await cleanup(db, user.id, resume.id)
        print("探针数据已清理")

    print()
    print(f"通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
