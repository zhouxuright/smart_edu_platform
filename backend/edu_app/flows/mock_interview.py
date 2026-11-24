"""模拟面试：真正需要「中断 + 恢复」的流程。

和 JD 匹配的区别在于它不是一次跑完的：每一题都要停下来等用户回答，
中途可能关掉页面、第二天再回来。这正是状态机存在的理由——
如果只在内存里跑，刷新一次这场面试就没了。

十二步的分工：

1. `prepare`（不调模型）：取简历、定岗位、把简历整理成带 index 的提纲。
2. `plan_questions`（调模型）：一次生成 4 道题。**不逐题调模型**——
   4 题各调一次规划，成本翻 4 倍，题目质量并不会更好。
3. `ask_1`…`ask_4`（不调模型）：出题并停在 `awaiting_human`。
4. `record_1`…`record_4`（不调模型）：把用户上一轮的回答归档进 `answers`。
   之所以单独一步，是为了让「用户输入」和「流程状态」的边界清楚：
   恢复接口只负责把输入塞进 `_human_input`，归档由流程自己决定。
5. `summary`（调模型）：**一次性**评估全部回答。逐题点评看着热闹，
   但用户真正要看的是「这场面试整体怎么样、下一场怎么改」。
6. `propose_improvement`（调模型 + 写操作提案）：针对暴露的问题给出
   一段简历要点的改写，落成 `AgentProposal` 挂在 run 上。
   用户确认后才真正写库，确认完流程自动结束。

为什么第 6 步要做成提案而不是直接改：模型改写简历属于写操作，
用户没看过就不该落库。这条规则在 `agents/tools.py` 里已经定了，
这里只是让工作流复用它，而不是另开一条后门。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.agents.base import get_llm
from edu_app.flows._support import ask, json_block, load_resume, resume_to_text
from edu_app.models.user import User
from edu_app.models.workflow import AgentRun
from edu_app.services import proposals
from edu_app.services.workflow import Flow, FlowError, Step, StepOutcome, register_flow

logger = logging.getLogger(__name__)

# 固定 4 轮。轮数写死是有意的：步骤名要在 `agent_steps` 里唯一，
# 动态轮数会让 `(run_id, step)` 约束失效，得再加一列 attempt 才行。
# 4 轮已经够暴露问题，再多用户就答不动了。
ROUNDS = 4

MAX_ANSWER_CHARS = 1200
MAX_BRIEF_CHARS = 2600

# 只把「能改写要点」的两段拿出来：教育、基本信息不交给模型碰
REWRITABLE = ("experience", "projects")


# ---------------------------------------------------------------------------
# 简历提纲：带 index，模型据此指定要改哪一段
# ---------------------------------------------------------------------------

def _clip(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _outline(data: dict | None) -> list[dict]:
    """把简历里可改写的段落列成带 index 的清单。

    index 必须和 `tools._validate_update_bullets` 里的语义一致——
    那边是 `resume.data[section][index]`，所以这里也按同样的顺序编号。
    """
    out: list[dict] = []
    payload = data or {}
    for section in REWRITABLE:
        for index, item in enumerate((payload.get(section) or [])[:6]):
            if not isinstance(item, dict):
                continue
            out.append({
                "section": section,
                "index": index,
                "name": _clip(item.get("company") or item.get("name"), 40),
                "role": _clip(item.get("role"), 40),
                "period": f"{_clip(item.get('start'), 12)}-{_clip(item.get('end'), 12)}",
                "bullets": [_clip(b, 120) for b in (item.get("bullets") or [])][:5],
            })
    return out


def _brief(data: dict | None) -> str:
    payload = data or {}
    basics = payload.get("basics") or {}
    lines = [
        f"姓名：{_clip(basics.get('name'), 20) or '未填'}",
        f"城市：{_clip(basics.get('city'), 20) or '未填'}",
        f"自我介绍：{_clip(basics.get('summary'), 300) or '未填'}",
        f"技能：{'、'.join(_clip(s, 20) for s in (payload.get('skills') or [])[:12]) or '未填'}",
    ]
    for item in _outline(payload):
        bullets = "；".join(item["bullets"]) or "（没有要点）"
        lines.append(
            f"[{item['section']}#{item['index']}] {item['name']} {item['role']} "
            f"{item['period']}：{bullets}"
        )
    return "\n".join(lines)[:MAX_BRIEF_CHARS]


# ---------------------------------------------------------------------------
# 步骤 1：准备
# ---------------------------------------------------------------------------

async def prepare(db: AsyncSession, run: AgentRun, user: User) -> StepOutcome:
    payload = (run.state or {}).get("input") or {}
    resume = await load_resume(db, user, payload.get("resume_id"))
    if not resume:
        raise FlowError("还没有简历，先到简历工作台建一份再来模拟面试")

    text = resume_to_text(resume.data)
    if len(text) < 50:
        raise FlowError("简历内容太少，先补充经历再模拟面试")

    role = _clip(payload.get("target_role") or resume.target_role, 60) or "目标岗位"
    jd = _clip(payload.get("jd_text"), 2000)

    return StepOutcome(
        state={
            "resume_id": resume.id,
            "resume_title": resume.title,
            "role": role,
            "jd_text": jd,
            "resume_brief": _brief(resume.data),
            "outline": _outline(resume.data),
        },
        output={"resume_id": resume.id, "role": role, "rounds": ROUNDS},
    )


# ---------------------------------------------------------------------------
# 步骤 2：出题（调模型，一次生成全部）
# ---------------------------------------------------------------------------

PLAN_PROMPT = """你是资深面试官，正在面试一位应聘「{role}」岗位的候选人。

根据下面的简历，出 {rounds} 道面试题，按顺序覆盖：
1. 自我介绍与求职动机
2. 简历里最值得深挖的一段经历（问具体做法、取舍、结果）
3. 岗位匹配度与短板（问不会的部分怎么补）
4. 行为题或压力题（冲突、失败、赶 deadline 之类）

规则：
1. 每题一句话，不超过 60 字，用面试官口吻直接问，不要写「请问您能…」这种客套。
2. 必须基于简历里真实存在的内容提问，不要编造简历里没有的项目或公司。
3. 简历内容单薄时，问通用能力，不要硬编项目细节。
4. 只输出 JSON：{{"questions":[{{"q":"问题","focus":"考察点，不超过 15 字"}}]}}
"""


async def plan_questions(db: AsyncSession, run: AgentRun, user: User) -> StepOutcome:
    state = run.state or {}
    brief = state.get("resume_brief") or ""
    role = state.get("role") or "目标岗位"
    jd = state.get("jd_text") or ""

    human = f"简历：\n{brief}"
    if jd:
        human += f"\n\n岗位要求：\n{jd}"

    llm = get_llm(temperature=0.4, max_tokens=700)
    raw = await ask(llm, PLAN_PROMPT.format(role=role, rounds=ROUNDS), human)
    payload = json_block(raw)
    if not payload:
        raise FlowError("出题失败，请重试")

    items = payload.get("questions") if isinstance(payload.get("questions"), list) else []
    questions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = _clip(item.get("q"), 120)
        if text:
            questions.append({"q": text, "focus": _clip(item.get("focus"), 30)})
    questions = questions[:ROUNDS]

    if len(questions) < 2:
        raise FlowError("没能生成足够的面试题，换个岗位名或补一下简历再试")

    return StepOutcome(
        state={"questions": questions},
        output={"questions": len(questions)},
    )


# ---------------------------------------------------------------------------
# 步骤 3 / 4：出题与归档（都不调模型）
# ---------------------------------------------------------------------------

def _make_ask(round_index: int):
    """`ask_n`：把第 n 题抛给用户，停在 awaiting_human。

    题目数量少于 ROUNDS 时直接跳到总评，不要让流程空转在 `ask_4` 上。
    """

    async def handler(db: AsyncSession, run: AgentRun, user: User) -> StepOutcome:
        state = run.state or {}
        questions = state.get("questions") or []
        if round_index > len(questions):
            return StepOutcome(next_step="summary", output={"skipped": True})

        item = questions[round_index - 1]
        return StepOutcome(
            output={"index": round_index, "question": item["q"]},
            awaiting={
                "kind": "answer",
                "index": round_index,
                "total": len(questions),
                "question": item["q"],
                "focus": item.get("focus") or "",
                "hint": "用 3~6 句话说清背景、你做了什么、结果如何",
            },
        )

    return handler


def _make_record(round_index: int):
    """`record_n`：把用户上一轮的回答归档，并清掉 `_human_input`。

    空回答也要记一条：总评里要能看出「这题没答」，而不是把它当成不存在。
    """

    async def handler(db: AsyncSession, run: AgentRun, user: User) -> StepOutcome:
        state = run.state or {}
        human = state.get("_human_input") or {}
        answer = _clip(human.get("answer") if isinstance(human, dict) else "", MAX_ANSWER_CHARS)

        questions = state.get("questions") or []
        question = questions[round_index - 1]["q"] if round_index <= len(questions) else ""

        answers = list(state.get("answers") or [])
        answers.append({"index": round_index, "question": question, "answer": answer})

        return StepOutcome(
            state={"answers": answers},
            output={"index": round_index, "answered": bool(answer), "chars": len(answer)},
            drop=("_human_input",),
        )

    return handler


# ---------------------------------------------------------------------------
# 步骤 5：总评（调模型，一次评全部回答）
# ---------------------------------------------------------------------------

SUMMARY_PROMPT = """你是面试官，刚结束一场面试。下面是完整问答记录。

请给出评估，只输出 JSON：
{"score": 0到100的整数,
 "verdict": "一句话结论，不超过 40 字",
 "strengths": ["做得好的地方，2~3 条"],
 "issues": [{"index": 1, "issue": "这题的问题", "better": "更好的答法，一句话"}],
 "next_actions": ["下一场面试前该做的 2~3 件事"]}

规则：
1. score 只看回答质量：结构是否清楚、有没有具体做法和结果、和岗位是否相关。
   回答为空的那一题按 0 分计，并在 issues 里写明「未作答」。
2. 不要编造候选人没说过的事实，也不要替他补充经历。
3. issues 覆盖所有答得不好的题，最多 4 条；全都答得好时给空数组。
4. 每条不超过 60 字，动词开头。
"""


async def summary(db: AsyncSession, run: AgentRun, user: User) -> StepOutcome:
    state = run.state or {}
    answers = state.get("answers") or []

    if not any(item.get("answer") for item in answers):
        # 一句没答就调模型评分，等于花钱买一段「你没回答」的废话
        return StepOutcome(
            state={
                "review": {
                    "score": 0,
                    "verdict": "这场面试没有作答，无法评估",
                    "strengths": [],
                    "issues": [],
                    "next_actions": ["找一段安静的 20 分钟，重新完整答一遍"],
                }
            },
            output={"score": 0, "skipped": True},
        )

    payload = {
        "岗位": state.get("role"),
        "简历摘要": (state.get("resume_brief") or "")[:1500],
        "问答": [
            {"index": item["index"], "问": item["question"], "答": item["answer"]}
            for item in answers
        ],
    }
    llm = get_llm(temperature=0.2, max_tokens=900)
    raw = await ask(llm, SUMMARY_PROMPT, json.dumps(payload, ensure_ascii=False))
    parsed = json_block(raw)
    if not parsed:
        raise FlowError("总评生成失败，请重试")

    try:
        score = int(parsed.get("score"))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))

    issues = []
    for item in parsed.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issue = _clip(item.get("issue"), 120)
        if issue:
            issues.append({
                "index": item.get("index"),
                "issue": issue,
                "better": _clip(item.get("better"), 160),
            })

    review = {
        "score": score,
        "verdict": _clip(parsed.get("verdict"), 60),
        "strengths": [_clip(x, 100) for x in (parsed.get("strengths") or []) if _clip(x, 100)][:3],
        "issues": issues[:4],
        "next_actions": [
            _clip(x, 100) for x in (parsed.get("next_actions") or []) if _clip(x, 100)
        ][:3],
    }

    return StepOutcome(
        state={"review": review},
        output={"score": score, "issues": len(issues)},
    )


# ---------------------------------------------------------------------------
# 步骤 6：提议改简历（调模型 + 落提案，等人确认）
# ---------------------------------------------------------------------------

IMPROVE_PROMPT = """你是简历教练。刚才这场面试暴露了一些问题，请据此改写简历里
**一段已有经历**的要点，让它在下一场面试里更难被问倒。

只输出 JSON：
{"section": "experience 或 projects", "index": 整数, "why": "一句话说明为什么改这段",
 "bullets": ["改写后的要点"]}

规则：
1. 只能改下面清单里已有的条目，section 和 index 必须来自清单。
2. 3~5 条要点，每条动词开头，不超过 60 字，突出做法与结果。
3. 保留原文里的数字；原文没有数字就不要凭空编一个。
4. 不允许新增经历、虚构项目或夸大职级。
5. 如果面试没有暴露出简历要点的问题，输出 {"skip": true}。
"""


async def propose_improvement(db: AsyncSession, run: AgentRun, user: User) -> StepOutcome:
    state = run.state or {}
    outline = state.get("outline") or []
    review = state.get("review") or {}

    if not outline:
        return StepOutcome(
            state={"improvement_note": "这份简历还没有可改写的实习或项目经历，先补一段再来"},
            output={"skipped": True},
            done=True,
        )

    payload = {
        "岗位": state.get("role"),
        "面试问题": [item.get("issue") for item in (review.get("issues") or [])],
        "下一场该做的": review.get("next_actions") or [],
        "可改写的条目": outline,
    }
    llm = get_llm(temperature=0.3, max_tokens=700)
    raw = await ask(llm, IMPROVE_PROMPT, json.dumps(payload, ensure_ascii=False))
    parsed = json_block(raw)

    if not parsed or parsed.get("skip"):
        return StepOutcome(
            state={"improvement_note": "这场面试没有暴露出需要改写的简历要点"},
            output={"skipped": True},
            done=True,
        )

    args = {
        "resume_id": state.get("resume_id"),
        "section": str(parsed.get("section") or "").strip(),
        "index": parsed.get("index"),
        "bullets": parsed.get("bullets"),
    }

    # 走统一入口：校验、预览、落库都在 services/proposals.py，
    # 和工作流之外的聊天提案完全同一套规则
    proposal, notice = await proposals.create(
        db,
        user,
        "resume",
        "update_bullets",
        args,
        run_id=run.id,
        step="propose_improvement",
    )

    if not proposal:
        # 模型给的段落/序号对不上，不该让整条流程失败——面试记录本身是有价值的
        logger.info("mock interview improvement skipped: %s", notice)
        return StepOutcome(
            state={"improvement_note": notice or "没能生成改写建议"},
            output={"skipped": True, "notice": notice},
            done=True,
        )

    return StepOutcome(
        state={
            "improvement": {
                "proposal_id": proposal["id"],
                "why": _clip(parsed.get("why"), 120),
            }
        },
        output={"proposal_id": proposal["id"]},
        awaiting={
            "kind": "proposal",
            "proposal_id": proposal["id"],
            "title": proposal["summary"],
            "preview": proposal["preview"],
            "why": _clip(parsed.get("why"), 120),
        },
        done=True,
    )


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

def _build_steps() -> list[Step]:
    steps = [
        Step(name="prepare", handler=prepare, label="读取简历"),
        Step(name="plan_questions", handler=plan_questions, llm=True, label="设计问题"),
    ]
    for index in range(1, ROUNDS + 1):
        steps.append(Step(name=f"ask_{index}", handler=_make_ask(index), label=f"第 {index} 题"))
        steps.append(
            Step(name=f"record_{index}", handler=_make_record(index), label=f"记录第 {index} 题")
        )
    steps.append(Step(name="summary", handler=summary, llm=True, label="总评"))
    steps.append(
        Step(name="propose_improvement", handler=propose_improvement, llm=True, label="提议改进")
    )
    return steps


register_flow(
    Flow(kind="mock_interview", title="模拟面试", steps=_build_steps())
)
