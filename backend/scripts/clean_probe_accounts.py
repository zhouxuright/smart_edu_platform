"""清理自测账号，让试用数据干净。

为什么需要：开发联调、端到端脚本、Agent 探针都会往库里塞 `@example.com` 账号。
`scripts/trial_report.py` 统计时会自动剔除它们，但正式收数据前还是清掉更省心——
否则「注册用户数」这类原始口径永远对不上。

**默认只预览，不删任何东西。** 真要删必须显式加 `--apply`。

保留名单是硬编码的：`admin@` / `parent@` / `demo@` 三个演示账号即使命中
`@example.com` 也不会被删，它们是文档和端到端脚本依赖的固定账号。

跑法（在 backend 目录）：
    python scripts/clean_probe_accounts.py                # 只看会删谁
    python scripts/clean_probe_accounts.py --apply        # 真删
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from edu_app.core.config import settings

# 命中任意一条即视为自测账号
PROBE_PATTERNS = ("e2e\\_%", "smoke\\_%", "dash\\_%", "dash\\_perf\\_%",
                  "%probe%", "mdtest\\_%", "att\\_%")
# 命中 @example.com 的账号里，这三个必须保留
KEEP_EMAILS = ("admin@example.com", "parent@example.com", "demo@example.com")


async def business_tables(conn) -> list[tuple[str, str]]:
    """找出所有引用 users 的业务表及其列名，避免漏删留下孤儿行。"""
    rows = (await conn.execute(text("""
        SELECT c.table_name, c.column_name
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_name = c.table_name AND t.table_schema = c.table_schema
        WHERE c.table_schema = 'public'
          AND c.column_name IN ('user_id', 'owner_id')
          AND t.table_type = 'BASE TABLE'
          AND c.table_name <> 'users'
        ORDER BY c.table_name
    """))).all()
    return [(r[0], r[1]) for r in rows]


# 「孙表」：只靠 user_id / owner_id 的动态发现抓不到它们——它们通过父表主键
# 关联，自己身上没有 user 列。漏掉任何一张，父表就永远被外键挡住删不掉。
# 每条是 (子表, 子表外键列, 父表, 父表的用户列)。
CHILD_TABLES = (
    ("document_embeddings", "document_id", "documents", "owner_id"),
    ("document_chunks", "document_id", "documents", "owner_id"),
    # 工作流：agent_steps 只记 run_id，账号信息在 agent_runs 上
    ("agent_steps", "run_id", "agent_runs", "user_id"),
    # 会话：agent_messages 只记 session_id，账号信息在 agent_sessions 上
    ("agent_messages", "session_id", "agent_sessions", "user_id"),
)


async def purge(conn, ids: list[int], tables: list[tuple[str, str]]) -> tuple[int, list[str]]:
    """按「能删就删、删不动下一轮再试」的顺序清理，返回 (行数, 仍被挡住的表)。

    为什么不用固定的表顺序：外键是链式的（documents 被 document_embeddings 引用，
    后者可能又被别的表引用），写死顺序迟早漏。这里每张表用 savepoint 单独尝试，
    被外键挡住的留到下一轮——因为依赖它的子表在上一轮已经删掉了。
    """
    total = 0

    # 先清孙表，否则父表永远被外键挡住
    for child, child_col, parent, parent_user_col in CHILD_TABLES:
        try:
            async with conn.begin_nested():
                result = await conn.execute(
                    text(
                        f'DELETE FROM "{child}" WHERE "{child_col}" IN '
                        f'(SELECT id FROM "{parent}" WHERE "{parent_user_col}" = ANY(:ids))'
                    ),
                    {"ids": ids},
                )
            if result.rowcount:
                total += result.rowcount
                print(f"  清 {child}.{child_col}: {result.rowcount} 行")
        except IntegrityError:
            print(f"  {child} 仍被外键挡住，跳过")

    pending = list(tables) + [("audit_events", "user_id")]
    blocked: list[str] = []

    while pending:
        progressed = False
        retry: list[tuple[str, str]] = []
        for table, column in pending:
            try:
                async with conn.begin_nested():
                    result = await conn.execute(
                        text(f'DELETE FROM "{table}" WHERE {column} = ANY(:ids)'),
                        {"ids": ids},
                    )
                if result.rowcount:
                    total += result.rowcount
                    print(f"  清 {table}.{column}: {result.rowcount} 行")
                progressed = True
            except IntegrityError:
                retry.append((table, column))
        pending = retry
        if not progressed:
            blocked = [f"{table}.{column}" for table, column in pending]
            break
    return total, blocked


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正执行删除（默认只预览）")
    args = parser.parse_args()

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    try:
        async with engine.begin() as conn:
            # 保留名单要用 expanding 绑定：普通 :keep 传元组会被当成单个值，
            # 生成 `NOT IN $1` 这种非法语法
            targets = (await conn.execute(
                text("""
                    SELECT id, email FROM users
                    WHERE (email LIKE '%@example.com'
                           OR email LIKE 'e2e\\_%'
                           OR email LIKE '%probe%'
                           OR email LIKE 'smoke\\_%')
                      AND email NOT IN :keep
                    ORDER BY id
                """).bindparams(bindparam("keep", expanding=True)),
                {"keep": list(KEEP_EMAILS)},
            )).all()

            if not targets:
                print("没有需要清理的自测账号。")
                return 0

            print(f"命中 {len(targets)} 个自测账号：")
            for uid, email in targets:
                print(f"  {uid:4} {email}")

            tables = await business_tables(conn)
            ids = [uid for uid, _ in targets]

            if not args.apply:
                print(f"\n将清理 {len(tables)} 张业务表里这些账号的数据，然后删除账号本身。")
                print("这是预览。确认无误后加 --apply 执行。")
                return 0

            total, blocked = await purge(conn, ids, tables)
            if blocked:
                # 删不掉的表说明还有更深一层的依赖，直接报出来让人判断，
                # 不要在这里硬删——那可能连带删掉不该删的东西
                print(f"\n这些表仍被外键挡住，未清理：{'、'.join(blocked)}")
                print("账号未删除（整批已回滚）。请先处理上述依赖再重跑。")
                raise SystemExit(1)

            result = await conn.execute(
                text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": ids}
            )
            print(f"\n已删除 {result.rowcount} 个账号，业务数据共 {total} 行。")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
