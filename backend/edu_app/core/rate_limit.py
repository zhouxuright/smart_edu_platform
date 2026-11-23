"""全局限流。

为什么不用 Redis：当前是单副本，进程内滑动窗口就够，引入 Redis 只会多一个
故障点和一个网络往返。等真上多副本时，把 SlidingWindow 换成 Redis 的
INCR + EXPIRE 即可，中间件本身不用动。

为什么不用 BaseHTTPMiddleware：它会给每个请求包一层 anyio 任务组，高并发下
开销明显。限流是所有请求的必经之路，用纯 ASGI 更轻。
"""
from __future__ import annotations

import hashlib
import time
from collections import deque

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from edu_app.core.config import settings

# 登录/注册单独收紧，防撞库。只匹配精确路径，子路径走通用阈值。
AUTH_PATHS = frozenset({"/api/login", "/api/register"})


class SlidingWindow:
    """按 key 的滑动窗口计数。

    用时间戳队列而不是固定窗口计数器：固定窗口在边界处会放过 2 倍流量
    （59 秒和 61 秒各打满一次）。
    """

    def __init__(self, limit: int, window: float = 60.0):
        self.limit = max(1, limit)
        self.window = window
        self._hits: dict[str, deque[float]] = {}
        self._last_gc = time.monotonic()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self.window

        queue = self._hits.get(key)
        if queue is None:
            queue = deque()
            self._hits[key] = queue

        while queue and queue[0] <= cutoff:
            queue.popleft()

        if len(queue) >= self.limit:
            # 最早那次请求滑出窗口时才有位置，给客户端一个准确的等待时间
            retry_after = int(self.window - (now - queue[0])) + 1
            return False, retry_after

        queue.append(now)
        self._gc(now, cutoff)
        return True, 0

    def _gc(self, now: float, cutoff: float) -> None:
        """清掉整个窗口内都没动静的 key，否则字典会随用户数无限增长。"""
        if now - self._last_gc < 300:
            return
        self._last_gc = now
        stale = [key for key, queue in self._hits.items() if not queue or queue[-1] <= cutoff]
        for key in stale:
            self._hits.pop(key, None)


def _client_key(scope: Scope) -> str:
    """已登录用户按 token 计数，未登录按 IP。

    刻意不解 JWT：限流在鉴权之前，解 token 等于给每个请求加一次签名校验；
    取 token 的哈希已经能区分用户，且同一账号换 token 后重新计数也无害。
    """
    headers = dict(scope.get("headers") or [])
    auth = headers.get(b"authorization", b"").decode("latin-1")
    if auth.startswith("Bearer "):
        digest = hashlib.sha1(auth[7:].encode("utf-8")).hexdigest()[:16]
        return f"u:{digest}"
    client = scope.get("client") or ("unknown", 0)
    return f"ip:{client[0]}"


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.general = SlidingWindow(settings.RATE_LIMIT_PER_MINUTE)
        self.auth = SlidingWindow(settings.RATE_LIMIT_AUTH_PER_MINUTE)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # 静态资源、文档、健康检查不限流；CORS 预检也不占额度，
        # 否则一个页面打开时的几十条预检就把额度吃光了。
        if scope.get("method") == "OPTIONS" or not scope.get("path", "").startswith("/api"):
            return await self.app(scope, receive, send)

        limiter = self.auth if scope["path"] in AUTH_PATHS else self.general
        allowed, retry_after = limiter.allow(_client_key(scope))

        if not allowed:
            response = JSONResponse(
                {"detail": "请求过于频繁，请稍后再试。"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
            return await response(scope, receive, send)

        return await self.app(scope, receive, send)
