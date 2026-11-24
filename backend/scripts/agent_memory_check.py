"""会话与记忆的回归（直接打服务层，不过 HTTP、不依赖前端）。

覆盖三件事：
1. 会话：同一 id 能取回、别人的 id 取不到、历史按时间正序、超限只留最近 N 条。
2. 记忆：同键同值只刷新、值变化走版本链、部分唯一索引真的生效、过期不注入。
3. 归档：摘要能生成、能被下一条会话读到、失败时不归档而是延后重试。

跑法（后端可以开着，脚本自己连库）：
    python scripts/agent_memory_check.py

脚本用 `page='__probe__'` 和 `mem_key like 'probe_%'` 隔离数据，
结束时全部删掉，可以反复执行。
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from edu_app.core.database import AsyncSessionLocal, engine
from edu_app.models.memory import AgentMessage, AgentSession, UserMemory
from edu_app.models.user import User
from edu_app.services import memory

# 引擎是 echo=True 建的，SQL 会把断言结果冲散；这个脚本只看 PASS/FAIL
engine.echo = False

PROBE_PAGE = "__probe__"
SUFFIX = uuid.uuid4().hex[:8]
MEM_PREFIX = f"probe_{SUFFIX}_"

passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if detail else ""))


async def cleanup(db, user_id: int):
    session_ids = (
        await db.execute(
            select(AgentSession.id).where(
                AgentSession.user_id == user_id, AgentSession.page == PROBE_PAGE
            )
        )
    ).scalars().all()
    if session_ids:
        await db.execute(
            delete(AgentMessage).where(AgentMessage.session_id.in_(session_ids))
        )
    await db.execute(
        delete(AgentSession).where(
            AgentSession.user_id == user_id, AgentSession.page == PROBE_PAGE
        )
    )
    await db.execute(
        delete(UserMemory).where(
            UserMemory.user_id == user_id, UserMemory.mem_key.like("probe_%")
        )
    )
    await db.commit()


async def main() -> int:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).order_by(User.id))).scalars().first()
        if not user:
            print("库里没有用户，先注册一个再跑")
            return 2
        other = (
            await db.execute(select(User).where(User.id != user.id).limit(1))
        ).scalars().first()

        await cleanup(db, user.id)

        # ---------------- 会话 ----------------
        s1 = await memory.get_or_create_session(db, user, PROBE_PAGE, None)
        await db.commit()
        check("新会话 id 是 36 位 uuid", len(s1.id) == 36, s1.id)
        check("新会话初始为 active", s1.status == "active" and s1.turn_count == 0)

        again = await memory.get_or_create_session(db, user, PROBE_PAGE, s1.id)
        check("同一 id 取回同一会话", again.id == s1.id)

        if other:
            stolen = await memory.get_or_create_session(db, other, PROBE_PAGE, s1.id)
            check(
                "别人的 session_id 取不到原会话（新建）",
                stolen.id != s1.id and stolen.user_id == other.id,
            )
            await db.execute(
                delete(AgentMessage).where(AgentMessage.session_id == stolen.id)
            )
            await db.execute(delete(AgentSession).where(AgentSession.id == stolen.id))
            await db.commit()
        else:
            print("SKIP  别人的 session_id 取不到原会话（库里只有一个用户）")

        await memory.append_turn(db, s1, "第一句", "第一答", tokens=10)
        await db.commit()
        history = await memory.load_history(db, s1.id)
        check(
            "一轮落两条且顺序为 user→assistant",
            [h["role"] for h in history] == ["user", "assistant"],
            str([h["role"] for h in history]),
        )
        check("turn_count 累加", s1.turn_count == 1, str(s1.turn_count))
        check("token_total 累加", s1.token_total == 10, str(s1.token_total))
        check("会话标题取首条用户消息", s1.title == "第一句", str(s1.title))

        for i in range(2, 9):
            await memory.append_turn(db, s1, f"第{i}句", f"第{i}答")
        await db.commit()
        trimmed = await memory.load_history(db, s1.id, limit=4)
        check(
            "超限时只返回最近 4 条且正序",
            len(trimmed) == 4 and trimmed[-1]["content"] == "第8答",
            str([t["content"] for t in trimmed]),
        )

        # ---------------- 记忆 ----------------
        key = f"{MEM_PREFIX}target_city"
        m1 = await memory.upsert_memory(
            db, user.id, "fact", key, "杭州", source="user_stated", confidence=0.9
        )
        await db.commit()
        check("首次写入记忆", m1 is not None and m1.value == "杭州")

        before = m1.last_seen_at
        m2 = await memory.upsert_memory(
            db, user.id, "fact", key, "杭州", source="user_stated", confidence=0.9
        )
        await db.commit()
        count_same = (
            await db.execute(
                select(UserMemory).where(
                    UserMemory.user_id == user.id, UserMemory.mem_key == key
                )
            )
        ).scalars().all()
        check(
            "同键同值只刷新不新增",
            len(count_same) == 1 and m2.id == m1.id and m2.last_seen_at >= before,
            f"rows={len(count_same)}",
        )

        m3 = await memory.upsert_memory(
            db, user.id, "fact", key, "上海", source="user_stated", confidence=0.9
        )
        await db.commit()
        all_rows = (
            await db.execute(
                select(UserMemory)
                .where(UserMemory.user_id == user.id, UserMemory.mem_key == key)
                .order_by(UserMemory.id)
            )
        ).scalars().all()
        current = [r for r in all_rows if r.superseded_by is None]
        check("值变化后保留旧行", len(all_rows) == 2, f"rows={len(all_rows)}")
        check(
            "旧行指向新行（版本链）",
            all_rows[0].superseded_by == m3.id and all_rows[1].superseded_by is None,
            f"old->{all_rows[0].superseded_by} new_id={m3.id}",
        )
        check("当前有效只有一条", len(current) == 1 and current[0].value == "上海")

        # 部分唯一索引是否真的建了：直接插一条重复的「当前有效」行，必须被库挡住
        async with AsyncSessionLocal() as db2:
            db2.add(
                UserMemory(
                    user_id=user.id,
                    kind="fact",
                    mem_key=key,
                    value="北京",
                    confidence=0.5,
                    source="user_stated",
                    created_at=datetime.now(UTC),
                    last_seen_at=datetime.now(UTC),
                )
            )
            try:
                await db2.flush()
                check("部分唯一索引生效", False, "重复的当前行居然插进去了")
            except IntegrityError:
                check("部分唯一索引生效", True)
            finally:
                await db2.rollback()

        rendered = await memory.render_memories(db, user.id)
        check("注入用当前值", "上海" in rendered and "杭州" not in rendered)
        check("注入带类型与键", "[fact]" in rendered and "target_city" in rendered)

        # 过期记忆不进 prompt
        expired_key = f"{MEM_PREFIX}expired_note"
        await memory.upsert_memory(
            db,
            user.id,
            "goal",
            expired_key,
            "这是一条过期记忆",
            source="summary",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        await db.commit()
        rendered2 = await memory.render_memories(db, user.id)
        check("过期记忆不注入", "这是一条过期记忆" not in rendered2)

        check(
            "键归一化稳定",
            memory.normalize_key("Target City") == "target_city"
            and memory.normalize_key("target-city") == "target_city",
            memory.normalize_key("Target-City"),
        )

        # ---------------- 归档 ----------------
        archive_ok = await memory.archive_session(db, s1)
        await db.commit()
        if archive_ok:
            check("归档成功", s1.status == "archived" and bool(s1.summary), s1.status)
            check("摘要长度合理", 5 <= len(s1.summary or "") <= 800, str(len(s1.summary or "")))
            prev = await memory.render_previous_summary(db, user.id, PROBE_PAGE)
            check("下一条会话能读到上次摘要", bool(prev) and s1.summary[:10] in prev)
        else:
            check(
                "模型不可用时不归档、只延后重试",
                s1.status == "active" and s1.last_active_at > datetime.now(UTC) - timedelta(minutes=1),
                f"status={s1.status}",
            )

        # 强制「模型不可用」的路径：不归档 + 推后 last_active_at
        s2 = await memory.get_or_create_session(db, user, PROBE_PAGE, None)
        await memory.append_turn(db, s2, "再试一次", "好")
        await db.commit()
        original_ready = memory._llm_ready
        memory._llm_ready = lambda: False
        try:
            ok = await memory.archive_session(db, s2)
        finally:
            memory._llm_ready = original_ready
        await db.commit()
        check(
            "摘要失败不归档且推后重试时间",
            ok is False and s2.status == "active" and s2.summary is None,
            f"ok={ok} status={s2.status}",
        )

        await cleanup(db, user.id)

    print()
    print(f"通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
