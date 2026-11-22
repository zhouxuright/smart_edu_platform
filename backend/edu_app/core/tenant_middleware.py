from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from jose import jwt, JWTError
import logging
from edu_app.core.config import settings

logger = logging.getLogger(__name__)

class TenantContextMiddleware(BaseHTTPMiddleware):
    """
    V2.0.0 商业级多租户上下文隔离中间件
    自动解析 JWT Token，抽取 tenant_id、user_id，注入到 Request State 中
    """
    async def dispatch(self, request: Request, call_next):
        tenant_id = None
        user_email = None
        
        # 1. 尝试从 Authorization Header 解析 Token
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization") or ""
        token = None
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
        elif "token" in request.query_params:
            token = request.query_params.get("token")

        if token:
            try:
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
                user_email = payload.get("sub")
                tenant_id = payload.get("tenant_id")
            except JWTError as e:
                logger.warning(f"TenantMiddleware: Invalid token provided - {e}")
            except Exception as e:
                logger.error(f"TenantMiddleware: Error parsing token - {e}")

        # 2. 将解析结果挂载到 request.state
        request.state.tenant_id = tenant_id or "default_tenant"
        request.state.user_email = user_email
        
        response = await call_next(request)
        return response
