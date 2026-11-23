"""错题本 Agent 工具的验收（不过大模型，直接打注册表）。

覆盖 list_mistakes / add_mistake / resolve_mistake 的
validate → preview → apply 三段，以及各种拒绝路径。

跑法（后端可开着，脚本自己连库）：
    python scripts/agent_tools_mistake.py

脚本自建临时账号与错题，结束时全部删除，可以反复执行。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from edu_app.agents.tools import REGISTRY, ToolError, tools_for
from edu_app.core.database import AsyncSessionLocal, engine
from edu_app.models.learning import MistakeRecord
from edu_app.models.user import User

# 引擎是 echo=True 建的，SQL 会把断言结果冲散；这个脚本只看 PASS/FAIL
engine.echo = False

EMAIL = "mistake_probe@example.com"
passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if detail else ""))


async def expect_error(name: str, coro, keyword: str = "") -> None:
    try:
        await coro
    except ToolError as error:
        ok = keyword in str(error) if keyword else True
        check(name, ok, str(error)[:60])
        return
    except Exception as error:  # noqa: BLE001
        check(name, False, f"抛了非 ToolError: {type(error).__name__}: {error}")
        return
    check(name, False, "本该报错却通过了")


async def main() -> int:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalars().first()
        if not user:
            user = User(email=EMAIL, hashed_password="x", full_name="错题探针", role="student")
            db.add(user)
            await db.commit()
            await db.refresh(user)

        # 清掉上一轮残留
        await db.execute(delete(MistakeRecord).where(MistakeRecord.user_id == user.id))
        await db.commit()
        uid = user.id

        # 造一个「别人的」错题，用来验证越权被拦
        other = (await db.execute(select(User).where(User.email == "smoke_probe_other@example.com"))).scalars().first()
        if not other:
            other = User(email="smoke_probe_other@example.com", hashed_password="x", full_name="别人", role="student")
            db.add(other)
            await db.commit()
            await db.refresh(other)
        foreign = MistakeRecord(user_id=other.id, subject="数学", question_content="别人的题，不该被看到", is_resolved=0)
        db.add(foreign)
        await db.commit()
        await db.refresh(foreign)
        foreign_id = foreign.id

        print("—— 注册表 ——")
        read_tools = [spec.name for spec in tools_for("mistake-book", "read")]
        write_tools = [spec.name for spec in tools_for("mistake-book", "write")]
        check("错题本有读工具", read_tools == ["list_mistakes"], str(read_tools))
        check("错题本有写工具", sorted(write_tools) == ["add_mistake", "resolve_mistake"], str(sorted(write_tools)))
        check("简历页拿不到错题本工具", all(spec.name not in {"list_mistakes", "add_mistake", "resolve_mistake"}
                                       for spec in tools_for("resume")))
        # 原来这里断言「注册表总数为 11」，每加一个工具就要改一次数字，
        # 改数字又变成无脑操作——换成不会随工具增减漂移的完整性检查。
        check(
            "注册表完整（每个工具都有页面、描述与实现）",
            all(
                spec.pages and spec.description and (spec.reader or spec.validate)
                for spec in REGISTRY.values()
            ),
            f"{len(REGISTRY)} 个工具",
        )
        check(
            "错题本工具不会漏到别的页面",
            all(
                "mistake-book" in spec.pages
                for spec in REGISTRY.values()
                if spec.name in {"list_mistakes", "add_mistake", "resolve_mistake"}
            ),
        )

        print("—— list_mistakes（空）——")
        reader = REGISTRY["list_mistakes"].reader
        empty = await reader(db, user, None)
        check("空错题本返回 exists=False", empty.get("exists") is False, str(empty.get("hint", ""))[:40])

        print("—— add_mistake ——")
        spec = REGISTRY["add_mistake"]
        good = {"subject": "数学", "topic": "极限", "question_content": "求 lim(x→0) (sin x)/x 的值，并说明理由。"}

        await expect_error("题干太短被拒", spec.validate(db, user, {"subject": "数学", "question_content": "求"}), "太短")
        await expect_error("非法科目被拒", spec.validate(db, user, {"subject": "数序", "question_content": good["question_content"]}), "科目")
        await expect_error("缺少科目被拒", spec.validate(db, user, {"question_content": good["question_content"]}), "科目")
        await expect_error(
            "超长题干被拒",
            spec.validate(db, user, {"subject": "数学", "question_content": "长" * 2001}),
            "超过",
        )

        ctx = await spec.validate(db, user, good)
        preview = spec.preview(ctx)
        check("预览是 item 类型", preview["kind"] == "item", preview["kind"])
        check("预览带学科", preview["meta"] == "数学", preview["meta"])

        result = await spec.apply(db, user, good)
        new_id = result["mistake_id"]
        check("错题已落库", bool(new_id), f"id={new_id}")

        row = await db.scalar(select(MistakeRecord).where(MistakeRecord.id == new_id))
        check("落库默认待巩固", row is not None and row.is_resolved == 0, f"is_resolved={getattr(row, 'is_resolved', None)}")

        await expect_error("重复收录被拒", spec.validate(db, user, good), "已经在错题本")

        print("—— list_mistakes（有数据）——")
        listed = await reader(db, user, None)
        check("读到 1 条错题", listed.get("total") == 1, str(listed.get("total")))
        check("按学科统计正确", listed.get("by_subject") == {"数学": 1}, str(listed.get("by_subject")))
        check("未掌握数为 1", listed.get("unresolved") == 1, str(listed.get("unresolved")))
        check("最近记录带内容摘要", bool(listed.get("recent")), str(listed.get("recent", [{}])[0].get("content", ""))[:30])

        print("—— resolve_mistake ——")
        rspec = REGISTRY["resolve_mistake"]
        await expect_error("缺 id 被拒", rspec.validate(db, user, {"resolved": True}), "缺少错题 id")
        await expect_error("resolved 类型不对被拒", rspec.validate(db, user, {"mistake_id": new_id, "resolved": "yes"}), "只能是")
        await expect_error("越权错题被拒", rspec.validate(db, user, {"mistake_id": foreign_id, "resolved": True}), "找不到")

        rctx = await rspec.validate(db, user, {"mistake_id": new_id, "resolved": True})
        rpreview = rspec.preview(rctx)
        check("预览是 status 类型", rpreview["kind"] == "status", rpreview["kind"])
        check("状态从待巩固到已掌握", (rpreview["from_label"], rpreview["to_label"]) == ("待巩固", "已掌握"),
              f"{rpreview['from_label']} -> {rpreview['to_label']}")

        await rspec.apply(db, user, {"mistake_id": new_id, "resolved": True})
        row = await db.scalar(select(MistakeRecord).where(MistakeRecord.id == new_id))
        check("已标记为掌握", row is not None and row.is_resolved == 1, f"is_resolved={getattr(row, 'is_resolved', None)}")

        await expect_error("重复标记被拒", rspec.validate(db, user, {"mistake_id": new_id, "resolved": True}), "已经是")

        await rspec.apply(db, user, {"mistake_id": new_id, "resolved": False})
        row = await db.scalar(select(MistakeRecord).where(MistakeRecord.id == new_id))
        check("可以改回待巩固", row is not None and row.is_resolved == 0, f"is_resolved={getattr(row, 'is_resolved', None)}")

        print("—— 清理 ——")
        await db.execute(delete(MistakeRecord).where(MistakeRecord.user_id == uid))
        await db.execute(delete(MistakeRecord).where(MistakeRecord.id == foreign_id))
        await db.execute(delete(User).where(User.id == uid))
        await db.execute(delete(User).where(User.id == other.id))
        await db.commit()
        check("临时数据已清理", True)

    print(f"\n通过 {len(passed)} 项，失败 {len(failed)} 项")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
