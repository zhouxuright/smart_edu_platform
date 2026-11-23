"""启动时补齐缺失的索引。

为什么不靠 `create_all`：它只负责**建表**，不会给已经存在的表补索引。
线上库是历史 `create_all` 建出来的，所以哪怕在模型里加了 `index=True`，
跑起来也不会生效——索引永远是缺的，查询照样全表扫描。

这里用 `CREATE INDEX IF NOT EXISTS` 显式补一遍：幂等、可重复执行、
对新库和老库都有效，不需要引入 Alembic 也能先把最痛的地方止住。

只补「按 user_id 过滤 + 排序或过滤」这一类热查询用到的复合索引。
单列 `user_id` 索引被复合索引的最左前缀覆盖，不必单独建——多建一个只会
拖慢写入，还占空间。

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
)


async def ensure_indexes(conn) -> list[str]:
    """在已建好的连接上补索引，返回成功执行的索引名。

    单个索引失败不该拖垮启动：线上锁表、权限不足都可能让 CREATE INDEX 报错，
    而索引缺失只会慢，不会让服务不可用。
    """
    done: list[str] = []
    for name, table, columns in INDEXES:
        try:
            await conn.execute(
                text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {columns}")
            )
            done.append(name)
        except Exception as error:  # noqa: BLE001 - 启动阶段不能因为索引失败就崩
            logger.warning("补索引 %s 失败（表 %s）：%s", name, table, error)
    if done:
        logger.info("已确保 %d 个索引存在：%s", len(done), ", ".join(done))
    return done
