"""线上冒烟：对已部署的后端跑一遍关键路径。

本地能跑 ≠ 线上能跑。部署后最容易踩的四类问题，这个脚本一次全查：
1. 健康检查不通（进程没起来 / 端口绑错 / 健康检查路径写错）
2. CORS 没配对（浏览器报跨域，curl 却一切正常，最难查）
3. 数据库没连上（表没建出来，注册 500）
4. 静态目录没挂（图片 404）

用法：
    python scripts/smoke_remote.py https://qicheng-api.up.railway.app
    python scripts/smoke_remote.py https://xxx.onrender.com --origin https://qicheng.vercel.app
"""
from __future__ import annotations

import argparse
import json
import secrets
import string
import sys
import urllib.error
import urllib.request

PASS, FAIL = 0, 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS  {name}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"FAIL  {name}" + (f"  [{detail}]" if detail else ""))


def call(base: str, method: str, path: str, token: str | None = None,
         body: dict | None = None, extra_headers: dict | None = None,
         timeout: int = 60) -> tuple[int, object, dict]:
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for key, val in (extra_headers or {}).items():
        req.add_header(key, val)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                payload = json.loads(raw.decode())
            except Exception:
                payload = raw[:200]
            return resp.status, payload, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode())
        except Exception:
            payload = raw[:200]
        return exc.code, payload, dict(exc.headers or {})
    except Exception as exc:  # noqa: BLE001 - 连接失败要打印原因，不能吞
        return 0, str(exc), {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base", help="后端根地址，如 https://qicheng-api.up.railway.app")
    parser.add_argument("--origin", default="", help="前端地址，用于校验 CORS")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    print(f"目标：{base}\n")

    # ---- 1. 健康检查
    status, payload, _ = call(base, "GET", "/", timeout=90)
    check("根路径可访问", status == 200, f"HTTP {status}")

    status, payload, _ = call(base, "GET", "/openapi.json")
    paths = list((payload or {}).get("paths", {}).keys()) if isinstance(payload, dict) else []
    check("OpenAPI 可读", status == 200 and len(paths) > 20, f"{len(paths)} 个接口")
    check("Agent 接口已挂载", "/api/agent/chat" in paths, "/api/agent/chat")
    check("简历接口已挂载", any(p.startswith("/api/resumes") for p in paths), "")

    # ---- 2. CORS 预检（浏览器视角）
    if args.origin:
        status, _, headers = call(
            base, "OPTIONS", "/api/resumes",
            extra_headers={
                "Origin": args.origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        allow = headers.get("access-control-allow-origin", "")
        check("CORS 预检放行该前端", status in (200, 204) and allow in (args.origin, "*"),
              f"HTTP {status} ACAO={allow or '缺失'}")

    # ---- 3. 注册新用户（顺带验证数据库真的连上了、表建出来了）
    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    email = f"smoke_{suffix}@example.com"
    password = "Smoke" + secrets.token_urlsafe(9) + "1!"
    status, payload, _ = call(base, "POST", "/api/register", body={
        "email": email, "password": password, "full_name": "线上冒烟",
    })
    check("注册可用（数据库已建表）", status in (200, 201),
          f"HTTP {status} {str(payload)[:120]}")

    status, payload, _ = call(base, "POST", "/api/login", body={"email": email, "password": password})
    token = (payload or {}).get("access_token") if isinstance(payload, dict) else None
    check("登录拿到 token", bool(token), f"HTTP {status}")

    if token:
        status, payload, _ = call(base, "GET", "/api/resumes", token=token)
        check("带 token 读简历列表", status == 200, f"HTTP {status}")

        status, payload, _ = call(base, "GET", "/api/agent/chat/tools?page=resume", token=token)
        names = [t.get("name") for t in (payload or {}).get("tools", [])] if isinstance(payload, dict) else []
        check("Agent 工具表可读", status == 200 and "get_resume" in names, ",".join(names))

        status, payload, _ = call(base, "GET", "/api/llm/quota", token=token)
        check("额度接口可读", status == 200, f"HTTP {status}")

        status, payload, _ = call(base, "GET", "/api/internship-plans/current", token=token)
        check("计划接口可读（新用户应为空）", status in (200, 204, 404), f"HTTP {status}")

    # ---- 4. 静态目录挂载
    status, _, _ = call(base, "GET", "/static/__probe__")
    check("/static 已挂载（404 正常，500 说明目录没建）", status == 404, f"HTTP {status}")

    print(f"\n通过 {PASS}，失败 {FAIL}")
    if FAIL:
        print("失败项按上面顺序对：健康检查→CORS→数据库→静态目录。")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
