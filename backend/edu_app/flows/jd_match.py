"""JD 匹配：第一个真正跑在状态机上的流程。

选它做第一个，是因为它**纯只读、无写操作**——流程跑到一半失败也不会脏数据，
可以放心用来验证「中断能恢复」这件事。

四步的分工刻意做成「模型只做语义、代码做判定」：

1. `load_resume`（不调模型）：校验 JD 长度、取简历、拍平成文本。
2. `extract_jd`（调模型）：把 JD 拆成结构化要求，每条带上能在简历里搜到的 probes。
3. `match_requirements`（**不调模型**）：拿 probes 做字符串命中，算覆盖率。
   判定必须由代码做——同一份 JD 问两次要给同一个结论，模型打分做不到。
4. `advise`（调模型）：只针对缺失项给补强动作，不允许编造经历。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.agents.base import get_llm
from edu_app.flows._support import ask, json_block, load_resume, resume_to_text
from edu_app.models.user import User
from edu_app.models.workflow import AgentRun
from edu_app.services.workflow import Flow, FlowError, Step, StepOutcome, register_flow

logger = logging.getLogger(__name__)

MAX_JD_CHARS = 8000


# ---------------------------------------------------------------------------
# 步骤 1：取数据（不调模型）
# ---------------------------------------------------------------------------

async def load_resume_step(db: AsyncSession, run: AgentRun, user: User) -> StepOutcome:
    payload = (run.state or {}).get("input") or {}
    jd = str(payload.get("jd_text") or "").strip()
    if len(jd) < 40:
        raise FlowError("JD 太短了，至少粘 40 个字，否则拆不出有效要求")
    jd = jd[:MAX_JD_CHARS]

    resume = await load_resume(db, user, payload.get("resume_id"))
    if not resume:
        raise FlowError("还没有简历，先到简历工作台建一份再来匹配")

    text = resume_to_text(resume.data)
    if len(text) < 50:
        raise FlowError("简历内容太少，先补充经历再做匹配")

    return StepOutcome(
        state={
            "jd_text": jd,
            "resume_id": resume.id,
            "resume_title": resume.title,
            "resume_text": text,
        },
        output={
            "resume_id": resume.id,
            "resume_title": resume.title,
            "resume_chars": len(text),
            "jd_chars": len(jd),
        },
    )


# ---------------------------------------------------------------------------
# 步骤 2：拆 JD（调模型）
# ---------------------------------------------------------------------------

EXTRACT_PROMPT = """你是招聘信息解析器。把这段 JD 拆成可核对的要求。

只输出一个 JSON 对象，不要解释：
{"role":"岗位名",
 "must_have":[{"text":"硬性要求，一句话","probes":["能在简历里搜到的词"]}],
 "nice_to_have":[{"text":"加分项，一句话","probes":["..."]}]}

规则：
1. must_have 只放硬性条件（学历、年限、技术栈、必须的项目经验），4~8 条。
2. probes 是能直接在简历文本里搜到的具体词：技术名、工具名、证书名、量化指标。
   每条 1~3 个，用 JD 里的原词，不要改写。
3. 「沟通能力强」「有责任心」这类无法检索的要求放 nice_to_have，probes 留空。
4. 不要编造 JD 里没有的要求。JD 写得含糊的，宁可少列。
"""


async def extract_jd(db: AsyncSession, run: AgentRun, user: User) -> StepOutcome:
    state = run.state or {}
    jd = state.get("jd_text") or ""

    llm = get_llm(temperature=0, max_tokens=800)
    raw = await ask(llm, EXTRACT_PROMPT, f"JD：\n{jd}")
    payload = json_block(raw)
    if not payload:
        raise FlowError("JD 解析失败，换一段格式更清晰的 JD 再试")

    must = payload.get("must_have") if isinstance(payload.get("must_have"), list) else []
    nice = (
        payload.get("nice_to_have") if isinstance(payload.get("nice_to_have"), list) else []
    )

    def clean(items: list) -> list[dict]:
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            probes = [
                str(p).strip()
                for p in (item.get("probes") or [])
                if str(p).strip()
            ][:3]
            if text:
                out.append({"text": text[:120], "probes": probes})
        return out

    must, nice = clean(must)[:8], clean(nice)[:6]
    if not must:
        raise FlowError("这段 JD 里没找到硬性要求，可能不是招聘信息")

    return StepOutcome(
        state={
            "requirements": {
                "role": str(payload.get("role") or "")[:80],
                "must_have": must,
                "nice_to_have": nice,
            }
        },
        output={"role": str(payload.get("role") or "")[:80], "must_have": len(must)},
    )


# ---------------------------------------------------------------------------
# 步骤 3：核对（不调模型，纯代码判定）
# ---------------------------------------------------------------------------

async def match_requirements(db: AsyncSession, run: AgentRun, user: User) -> StepOutcome:
    state = run.state or {}
    requirements = state.get("requirements") or {}
    resume_text = (state.get("resume_text") or "").lower()

    matched, missing, unverified = [], [], []
    for item in requirements.get("must_have") or []:
        probes = [p for p in (item.get("probes") or []) if p]
        if not probes:
            unverified.append({"text": item.get("text"), "reason": "这条要求没有可检索的关键词"})
            continue
        hits = [p for p in probes if p.lower() in resume_text]
        if hits:
            matched.append({"text": item.get("text"), "hit": hits})
        else:
            missing.append({"text": item.get("text"), "probes": probes})

    total = len(matched) + len(missing)
    coverage = round(len(matched) / total, 2) if total else 0.0

    return StepOutcome(
        state={
            "match": {
                "matched": matched,
                "missing": missing,
                "unverified": unverified,
                "coverage": coverage,
            }
        },
        output={
            "coverage": coverage,
            "matched": len(matched),
            "missing": len(missing),
            "unverified": len(unverified),
        },
    )


# ---------------------------------------------------------------------------
# 步骤 4：给补强建议（调模型）
# ---------------------------------------------------------------------------

ADVISE_PROMPT = """你是求职教练。下面给出简历与岗位硬性要求的核对结果。

只针对「缺失项」给 3~5 条可执行的补强动作。

规则：
1. 每条一句话，动词开头，不超过 60 字，说清「做什么 + 补到哪」。
2. 不允许建议编造经历、夸大职级或虚构项目。没有相关经历时，建议用课程、
   个人项目、开源贡献去补，并写明怎么补。
3. 不要重复简历里已经有的内容。
4. 只输出 JSON：{"advice":["...","..."]}
"""


async def advise(db: AsyncSession, run: AgentRun, user: User) -> StepOutcome:
    state = run.state or {}
    match = state.get("match") or {}
    missing = match.get("missing") or []

    if not missing:
        return StepOutcome(
            state={"advice": ["硬性要求已全部命中，重点转为准备面试案例与项目复盘。"]},
            output={"advice": 1, "skipped": True},
            done=True,
        )

    payload = {
        "岗位": (state.get("requirements") or {}).get("role"),
        "缺失项": [m.get("text") for m in missing],
        "简历摘要": (state.get("resume_text") or "")[:2500],
    }
    llm = get_llm(temperature=0.2, max_tokens=600)
    raw = await ask(llm, ADVISE_PROMPT, json.dumps(payload, ensure_ascii=False))
    parsed = json_block(raw)
    items = parsed.get("advice") if isinstance(parsed, dict) else None
    advice = [str(x).strip()[:120] for x in (items or []) if str(x).strip()][:5]
    if not advice:
        raise FlowError("建议生成失败，请重试")

    return StepOutcome(
        state={"advice": advice},
        output={"advice": len(advice)},
    )


register_flow(
    Flow(
        kind="jd_match",
        title="JD 匹配",
        steps=[
            Step(name="load_resume", handler=load_resume_step, label="读取简历"),
            Step(name="extract_jd", handler=extract_jd, llm=True, label="拆解 JD"),
            Step(name="match_requirements", handler=match_requirements, label="核对要求"),
            Step(name="advise", handler=advise, llm=True, label="生成建议"),
        ],
    )
)
