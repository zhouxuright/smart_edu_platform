"""只读统计「第一份实习 7 天计划」的试用漏斗。

用法（在 backend 目录）：
    python scripts/trial_report.py
    python scripts/trial_report.py --db-url sqlite+aiosqlite:///./trial.db
    python scripts/trial_report.py --include-test-accounts   # 连自测账号一起算

只执行 SELECT，不修改任何数据。表不存在时输出提示而不是报错。
默认排除 @example.com / e2e_ 等自测账号，避免开发联调数据混进试用结果。
"""
import argparse
import asyncio
import statistics
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from edu_app.core.config import settings

# 自测账号特征：注册时用这些域名/前缀，统计时自动剔除
TEST_ACCOUNT_PATTERNS = ("%@example.com", "%@example.test", "%@test.local", "e2e%", "probe%")


async def scalar(conn, sql: str) -> int:
    result = await conn.execute(text(sql))
    return int(result.scalar() or 0)


async def rows(conn, sql: str):
    result = await conn.execute(text(sql))
    return result.fetchall()


async def _has_table(conn, name: str) -> bool:
    try:
        await conn.execute(text(f"SELECT 1 FROM {name} LIMIT 1"))
        return True
    except Exception:
        await conn.rollback()
        return False


async def _test_account_ids(conn) -> list[int]:
    """找出注册时使用的自测账号，统计时剔除，避免联调数据混进试用结果。"""
    if not await _has_table(conn, "users"):
        return []
    clause = " OR ".join(f"email LIKE '{pattern}'" for pattern in TEST_ACCOUNT_PATTERNS)
    try:
        result = await conn.execute(text(f"SELECT id FROM users WHERE {clause}"))
        return [row[0] for row in result.fetchall()]
    except Exception:
        await conn.rollback()
        return []


def pct(numerator: int, denominator: int) -> str:
    if not denominator:
        return "—"
    return f"{numerator / denominator * 100:.0f}%"


def describe_url(db_url: str) -> str:
    parts = urlsplit(db_url)
    database = (parts.path or "").lstrip("/") or "(default)"
    if parts.scheme.startswith("sqlite"):
        return f"SQLite {database}"
    return f"{parts.scheme.split('+')[0]} {parts.hostname or '(unknown host)'}:{parts.port or '(default port)'} / {database}"


async def report(db_url: str, include_test_accounts: bool = False) -> int:
    engine = create_async_engine(db_url)
    try:
        try:
            conn = await engine.connect()
        except Exception as error:
            print(f"无法连接数据库：{describe_url(db_url)}")
            print(f"原因：{type(error).__name__}")
            print("请确认数据库服务已启动、网络可达，或用 --db-url 指向本地库，例如：")
            print('  python scripts/trial_report.py --db-url "sqlite+aiosqlite:///../app.db"')
            return 3
        try:
            return await _report(conn, include_test_accounts)
        finally:
            await conn.close()
    finally:
        await engine.dispose()


async def _report(conn, include_test_accounts: bool = False) -> int:
    if not await _has_table(conn, "plan_events"):
        if await _has_table(conn, "internship_plans"):
            print("plan_events 表还不存在（埋点表未建），暂时无法统计漏斗。")
        else:
            print("这个库还没有建过计划相关表：应用还没连过这个库。")
            print("先启动一次后端让它自动建表（或用 --db-url 指向已有数据的库），再回来统计。")
        return 2

    excluded = [] if include_test_accounts else await _test_account_ids(conn)
    if excluded:
        ids = ", ".join(str(i) for i in excluded)
        event_filter = f" AND user_id NOT IN ({ids})"
        alias_filter = f" AND g.user_id NOT IN ({ids})"
        plan_filter = f" AND p.user_id NOT IN ({ids})"
    else:
        event_filter = alias_filter = plan_filter = ""

    opened = await scalar(conn, f"SELECT COUNT(DISTINCT user_id) FROM plan_events WHERE event='plan_opened'{event_filter}")
    generated = await scalar(conn, f"SELECT COUNT(DISTINCT user_id) FROM plan_events WHERE event='plan_generated'{event_filter}")
    first_done = await scalar(conn, f"SELECT COUNT(DISTINCT user_id) FROM plan_events WHERE event='first_task_completed'{event_filter}")
    completed = await scalar(conn, f"SELECT COUNT(DISTINCT user_id) FROM plan_events WHERE event='plan_completed'{event_filter}")
    reminder = await scalar(conn, f"SELECT COUNT(DISTINCT user_id) FROM plan_events WHERE event='reminder_enabled'{event_filter}")
    tasks_done = await scalar(
        conn,
        "SELECT COUNT(*) FROM internship_plan_tasks t JOIN internship_plans p ON p.id = t.plan_id "
        f"WHERE t.completed = TRUE{plan_filter}",
    )
    tasks_total = await scalar(
        conn,
        f"SELECT COUNT(*) FROM internship_plan_tasks t JOIN internship_plans p ON p.id = t.plan_id WHERE 1=1{plan_filter}",
    )

    timings = await rows(conn, f"""
        SELECT g.user_id,
               MIN(g.created_at) AS generated_at,
               MIN(f.created_at) AS first_at
        FROM plan_events g
        JOIN plan_events f ON f.user_id = g.user_id AND f.event = 'first_task_completed'
        WHERE g.event = 'plan_generated'{alias_filter}
        GROUP BY g.user_id
    """)
    minutes = []
    for _, generated_at, first_at in timings:
        if isinstance(generated_at, str):
            generated_at = datetime.fromisoformat(generated_at)
        if isinstance(first_at, str):
            first_at = datetime.fromisoformat(first_at)
        if generated_at and first_at:
            delta = (first_at - generated_at).total_seconds() / 60
            if delta >= 0:
                minutes.append(delta)

    print("第一份实习 · 7 天计划 — 试用漏斗（只读）")
    print("-" * 46)
    print(f"打开计划页的用户        {opened}")
    print(f"生成计划的用户          {generated}")
    print(f"完成至少一项的用户      {first_done}")
    print(f"完成全部 7 项的用户     {completed}")
    print(f"开启提醒的用户          {reminder}")
    print("-" * 46)
    print(f"保存率（生成 / 打开）    {pct(generated, opened)}")
    print(f"首项完成率（首项 / 生成）{pct(first_done, generated)}")
    print(f"全计划完成率            {pct(completed, generated)}")
    print(f"任务完成数              {tasks_done} / {tasks_total}")
    if minutes:
        print(f"生成到首项完成中位时长  {statistics.median(minutes):.0f} 分钟")
    else:
        print("生成到首项完成中位时长  —（还没有人完成首项）")
    print("-" * 46)
    print("口径：按用户去重；plan_opened 每天只记一次。")
    if excluded:
        print(f"已排除 {len(excluded)} 个自测账号（@example.com / e2e / probe 等）。")
    elif include_test_accounts:
        print("注意：当前包含自测账号，数字不可当作试用结果。")
    print("样本小于 20 人时只看方向，不要当作稳定转化率对外引用。")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="只读统计实习计划试用漏斗")
    parser.add_argument("--db-url", default=settings.DATABASE_URL, help="数据库连接串，默认读取项目配置")
    parser.add_argument("--include-test-accounts", action="store_true", help="连自测账号一起统计（默认剔除）")
    args = parser.parse_args()
    sys.exit(asyncio.run(report(args.db_url, args.include_test_accounts)))
