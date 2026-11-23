"""大模型调用的并发闸门。

为什么需要：每个请求都直接往供应商打，服务器本身不慢，但供应商的并发额度
先撑不住——表现为大量请求同时超时，用户看到「转圈很久然后失败」，比直接
告诉他「排队中」体验更差。

闸门放在「建立连接」这一层，而不是每个业务函数里，这样所有走 httpx 的调用点
（聊天、简历润色、计划生成、评估）自动共享同一个额度池。

为什么用组合上下文管理器：调用点原本是

    async with httpx.AsyncClient(timeout=timeout, base_url=base) as client:

换成

    async with llm_client(timeout, base) as client:

一行替换，后面的缩进和逻辑都不用动。想改成每个调用点手写 try/finally 的话，
8 处里漏一处就等于没限流。

注意：流式响应会把额度占满整个流的生命周期（可能 10-30 秒），这是对的——
那段时间供应商那边的连接确实还开着。
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import HTTPException

from edu_app.core.config import settings
from edu_app.services.llm_usage import record_from_http_response

logger = logging.getLogger(__name__)

_semaphore: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    """懒创建。

    模块级直接建 Semaphore 会在 import 时尝试拿事件循环，测试脚本里多次
    asyncio.run 会踩到「绑定到已关闭的 loop」。放到首次调用时创建就没这个问题。
    """
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, settings.LLM_MAX_CONCURRENCY))
    return _semaphore


@asynccontextmanager
async def llm_slot():
    """只占额度，不建客户端。

    给走 SDK 的调用点用（LangChain 的 `ainvoke` 自己管连接），
    httpx 直连的调用点用下面的 `llm_client`。
    """
    sem = _sem()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=settings.LLM_QUEUE_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(
            "llm.gate.rejected concurrency=%s timeout=%ss",
            settings.LLM_MAX_CONCURRENCY,
            settings.LLM_QUEUE_TIMEOUT,
        )
        raise HTTPException(
            status_code=503,
            detail="当前使用人数较多，请稍后重试。",
            headers={"Retry-After": "5"},
        )

    try:
        yield
    finally:
        sem.release()


@asynccontextmanager
async def llm_client(timeout: httpx.Timeout | float, base_url: str):
    """占用一个模型并发额度，并在额度内建立 HTTP 客户端。

    排队超过 LLM_QUEUE_TIMEOUT 秒直接返回 503：与其让用户等 60 秒后超时，
    不如立刻告诉他稍后再试。Retry-After 给前端一个可展示的倒计时。

    event hook 顺带做 token 计量：所有走 httpx 的调用点（8 处）都不用改代码，
    响应体里的 usage 会被自动累加。这是唯一不漏的做法——逐个调用点手写，
    漏一处成本就少算一块，以后新增的调用点还会继续漏。
    """
    async with llm_slot():
        async with httpx.AsyncClient(
            timeout=timeout,
            base_url=base_url,
            event_hooks={"response": [record_from_http_response]},
        ) as client:
            yield client
