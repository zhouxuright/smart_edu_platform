"""大模型 token 用量计量：按用户、按天累加。

与 `quota.py` 的分工，两者不要混：

- `quota.py` 管「**能不能**调」——次数配额，超限返回 429；
- 本模块管「**花了多少**」——token 数，用于成本核算与定价。

为什么必须记 token 而不只记次数：一次长简历润色可能是 3000 prompt tokens，
一句「今天该做什么」可能只有 300。都算 1 次的话，成本差十倍，
也就无法回答「这个用户是赚钱的还是亏钱的」。

采集方式刻意做成**非侵入式**，业务代码不需要知道计量的存在：

- 走 httpx 直连的调用点（简历润色、计划生成、评估等 8 处），
  由 `llm_gate.llm_client` 挂的 event hook 解析响应体里的 `usage`；
- 走 LangChain 的调用点（Agent 对话、导师建议），
  由 `agents.base.get_llm` 注入的 callback 解析 `LLMResult`。

为什么不逐个调用点手写：调用点有十来处，漏一处就等于成本少算一块，
而且以后新增调用点还会继续漏。挂在闸门层是唯一不漏的做法。

写入用 fire-and-forget：计量失败绝不能影响用户正在等的回复，
所以独立 session、异常只 warning、任务持有强引用防 GC。
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextvars import ContextVar
from datetime import date

from langchain_core.callbacks import AsyncCallbackHandler
from sqlalchemy.dialects.postgresql import insert as pg_insert

from edu_app.core.database import AsyncSessionLocal
from edu_app.models.learning import LlmUsage

logger = logging.getLogger(__name__)

# 当前请求属于哪个用户。由 require_llm_quota 在路由执行前设置，
# 后台任务（如仪表盘推荐）通过 asyncio.create_task 继承 context 也能读到。
_current_user_id: ContextVar[int | None] = ContextVar("llm_usage_user_id", default=None)

# 持有后台任务强引用：create_task 的返回值如果没人引用，可能在执行途中被 GC，
# 表现为「有时候记上了有时候没记上」。
_pending: set[asyncio.Task] = set()


def set_current_user(user_id: int | None) -> None:
    """标记当前请求的用户。不 reset 也没问题：每个请求是独立 task，context 不复用。"""
    _current_user_id.set(user_id)


def current_user_id() -> int | None:
    return _current_user_id.get()


def _spawn(coro) -> None:
    """在当前事件循环里起一个后台任务，失败不影响主流程。"""
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:
        # 没有运行中的事件循环。必须 close()，否则协程从未 await 会抛
        # RuntimeWarning，而这个警告在生产日志里等于噪音。
        logger.warning("llm.usage.skip no running loop")
        coro.close()
        return
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def record_usage(prompt_tokens: int, completion_tokens: int) -> None:
    """把一次调用的 token 数累加到当天的行上。

    与 quota 共用 (user_id, day) 这一行，但只动 token 三列，不碰 count——
    count 归 quota.py 管，两边都改会互相覆盖。
    """
    user_id = current_user_id()
    if user_id is None:
        return
    prompt_tokens = max(0, int(prompt_tokens or 0))
    completion_tokens = max(0, int(completion_tokens or 0))
    if not prompt_tokens and not completion_tokens:
        return

    table = LlmUsage.__table__
    stmt = (
        pg_insert(LlmUsage)
        .values(
            user_id=user_id,
            day=date.today(),
            count=0,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        .on_conflict_do_update(
            index_elements=["user_id", "day"],
            set_={
                "prompt_tokens": table.c.prompt_tokens + prompt_tokens,
                "completion_tokens": table.c.completion_tokens + completion_tokens,
                "total_tokens": table.c.total_tokens + prompt_tokens + completion_tokens,
            },
        )
    )

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(stmt)
            await db.commit()
    except Exception as error:  # noqa: BLE001 - 计量失败不能影响业务
        logger.warning("记录 token 用量失败（user=%s）：%s", user_id, error)


def usage_from_openai(payload) -> tuple[int, int] | None:
    """从 OpenAI 兼容响应体里取 (prompt_tokens, completion_tokens)。

    DeepSeek 也返回这个结构。取不到就返回 None，由调用方跳过。
    """
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    try:
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        return None
    return (prompt, completion) if (prompt or completion) else None


def stream_options() -> dict:
    """流式请求要带上的参数，让供应商在最后一个 chunk 里回 usage。

    不带这个，SSE 流里就没有 usage 字段，流式接口的成本只能靠猜。
    OpenAI 标准参数，DeepSeek 支持。
    """
    return {"stream_options": {"include_usage": True}}


def note_usage(prompt_tokens: int, completion_tokens: int) -> None:
    """供流式调用点在解析到 usage 时调用。

    流式不能走 httpx 的 response hook（那会把流读完才返回），
    所以由调用点在 `aiter_lines` 循环里自己发现 usage 再交过来。
    """
    if prompt_tokens or completion_tokens:
        _spawn(record_usage(prompt_tokens, completion_tokens))


def parse_chunk(raw: str):
    """流式解析入口：json 解析的同时，发现 usage 就顺手记一笔。

    5 处流式循环原本都是 `obj = json.loads(data)`，换成这个函数即可，
    不必每处手写检测——那种改法漏一处就少算一块成本，而且以后新增的还会漏。

    解析失败照常抛异常，由调用方原有的 try/except 处理，语义不变。
    """
    obj = json.loads(raw)
    try:
        found = usage_from_openai(obj)
        if found:
            note_usage(*found)
    except Exception:  # noqa: BLE001 - 计量失败不能影响流式输出
        pass
    return obj


def usage_from_llm_result(result) -> tuple[int, int] | None:
    """从 LangChain 的 LLMResult 里取 token 数。

    两条路径都要试：不同版本、不同 provider 放的位置不一样。
    """
    # 路径一：llm_output["token_usage"]（ChatOpenAI 走这条）
    output = getattr(result, "llm_output", None) or {}
    if isinstance(output, dict):
        found = usage_from_openai({"usage": output.get("token_usage") or output.get("usage")})
        if found:
            return found

    # 路径二：generations[0][0].message.usage_metadata（langchain-core 新接口）
    generations = getattr(result, "generations", None) or []
    if generations and generations[0]:
        message = getattr(generations[0][0], "message", None)
        meta = getattr(message, "usage_metadata", None)
        if isinstance(meta, dict):
            try:
                prompt = int(meta.get("input_tokens") or 0)
                completion = int(meta.get("output_tokens") or 0)
            except (TypeError, ValueError):
                return None
            if prompt or completion:
                return (prompt, completion)
    return None


class UsageCallback(AsyncCallbackHandler):
    """挂在 ChatOpenAI 上，每次调用结束自动记一笔。

    用 callback 而不是包装 llm 对象：包装会破坏 `prompt | llm` 这类链式组合，
    而 callback 是 LangChain 的原生扩展点，对业务代码完全透明。

    **必须是 AsyncCallbackHandler，不能用同步的 BaseCallbackHandler。**
    同步版本的 on_llm_end 会被 LangChain 丢到 executor 线程里跑，那里没有
    事件循环，asyncio.create_task 直接抛 RuntimeError——表现为计量静默失效，
    日志里只有一行 "coroutine was never awaited"，很容易被忽略。
    """

    async def on_llm_end(self, response, **kwargs):  # noqa: ANN001 - 父类签名如此
        try:
            usage = usage_from_llm_result(response)
        except Exception:  # noqa: BLE001
            return
        if usage:
            _spawn(record_usage(*usage))


async def record_from_http_response(response) -> None:
    """httpx 的 response hook 入口：解析并记录，失败静默。

    **关键细节：hook 在 body 读取之前触发。** 实测此时
    `response.is_stream_consumed` 是 False，直接 `response.json()` 会抛
    `Attempted to access streaming response content`。所以必须先 `aread()`。

    但 `aread()` 对 SSE 流式响应会一直阻塞到流结束——那会把流式变成非流式，
    用户看到的「逐字输出」就没了。所以先看 content-type：

    - `text/event-stream`：流式，跳过（token 由调用点自己解析，见下）
    - `application/json`：非流式，读 body 再解析
    - 其他：跳过

    流式路径的 token 目前**没有**自动采集，这类调用会表现为
    「有 count、没有 token」，由 `llm_cost_report.py` 的自检报出来。
    这不是遗漏而是取舍：为了不破坏流式体验，宁可少记。
    """
    try:
        if not getattr(response, "is_success", False):
            return
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return
        if "application/json" not in content_type:
            return
        if not response.is_stream_consumed:
            await response.aread()
        payload = response.json()
    except Exception:  # noqa: BLE001 - 解析失败不影响业务
        return

    usage = usage_from_openai(payload)
    if usage:
        _spawn(record_usage(*usage))
