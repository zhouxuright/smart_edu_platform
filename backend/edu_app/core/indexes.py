"""启动时补齐 create_all 覆盖不到的 schema 变更。

`create_all` 有两个盲区，本文件两个都要兜：

1. **不给已存在的表补索引。** 它只负责建表。线上库是历史建出来的，
   所以哪怕在模型里加了 `index=True`，跑起来也不会生效——查询照样全表扫描。
2. **不给已存在的表补列。** 同理，新加一个字段，模型改了、代码上线了，
   但线上那张表还是旧结构，一查就报 `UndefinedColumn`。

两件事都用幂等 DDL 显式补一遍：`CREATE INDEX IF NOT EXISTS` /
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`。可重复执行，对新库和老库都有效，
不需要引入 Alembic 也能先把最痛的地方止住。

边界要说清楚：这只覆盖**加列**。改类型、加约束、数据迁移仍然需要真正的迁移工具，
别把它当 Alembic 用。

**执行顺序是「先列后索引」**，不能反过来：新加的索引可能引用新加的列，
先建索引会报 `column does not exist`。

**每条 DDL 各自包一层 savepoint。** PostgreSQL 里一条语句失败会把整个事务
标记为 aborted，之后所有语句都报 `InFailedSQLTransactionError`——不包
savepoint 的话，第一条失败就等于剩下全部静默失败，而日志里只有一行 warning，
看起来像「只有那一条出问题」。

表名、列名、索引名全是本文件里的常量，不来自外部输入，不存在注入风险。
"""
from __future__ import annotations

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

INDEXES: tuple[tuple[str, str, str], ...] = (
    # 学情聚合、仪表盘：WHERE user_id = ? ORDER BY timestamp DESC
    ("ix_learning_records_user_time", "learning_records", "(user_id, timestamp DESC)"),
    # 错题列表：WHERE user_id = ? ORDER BY created_at DESC
    ("ix_mistake_records_user_created", "mistake_records", "(user_id, created_at DESC)"),
    # 薄弱点、Agent 读错题：WHERE user_id = ? AND is_resolved = 0
    ("ix_mistake_records_user_resolved", "mistake_records", "(user_id, is_resolved)"),
    # 工作流恢复：按 run_id 找回挂在这个流程上的提案
    ("ix_agent_proposals_run_id", "agent_proposals", "(run_id)"),
)

# (表, 列, 列定义)。只用于「新增字段」，已有字段的默认值回填靠 DEFAULT 子句。
COLUMNS: tuple[tuple[str, str, str], ...] = (
    # 成本核算：只记次数无法区分「一句短问答」和「长简历润色」
    ("llm_usage", "prompt_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("llm_usage", "completion_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("llm_usage", "total_tokens", "INTEGER NOT NULL DEFAULT 0"),
    # 多租户字段（原先散落在 main.py 里手写，统一收到这里）
    ("users", "tenant_id", "VARCHAR"),
    ("documents", "tenant_id", "VARCHAR"),
    ("documents", "owner_id", "INTEGER"),
    # 提案挂到工作流上：没有这两列，流程里的确认点就只能当独立提案处理，
    # 用户点完「应用」流程永远停在 awaiting_human
    ("agent_proposals", "run_id", "VARCHAR(36)"),
    ("agent_proposals", "step", "VARCHAR(40)"),
)


async def ensure_indexes(conn) -> list[str]:
    """在已建好的连接上补索引，返回成功执行的索引名。

    单个索引失败不该拖垮启动：线上锁表、权限不足都可能让 CREATE INDEX 报错，
    而索引缺失只会慢，不会让服务不可用。每条各自包 savepoint，
    避免一条失败连带后面全部失败（见文件头）。
    """
    done: list[str] = []
    for name, table, columns in INDEXES:
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {columns}")
                )
            done.append(name)
        except Exception as error:  # noqa: BLE001 - 启动阶段不能因为索引失败就崩
            logger.warning("补索引 %s 失败（表 %s）：%s", name, table, error)
    if done:
        logger.info("已确保 %d 个索引存在：%s", len(done), ", ".join(done))
    return done


async def ensure_columns(conn) -> list[str]:
    """补缺失的列，返回成功执行的「表.列」。

    加列失败同样不拦启动：缺列的接口会报错，但整个服务不该因此起不来，
    否则一次 schema 问题就变成全站不可用。
    """
    done: list[str] = []
    for table, column, ddl in COLUMNS:
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
                )
            done.append(f"{table}.{column}")
        except Exception as error:  # noqa: BLE001
            logger.warning("补列 %s.%s 失败：%s", table, column, error)
    if done:
        logger.info("已确保 %d 个字段存在：%s", len(done), ", ".join(done))
    return done
