"""大模型成本报表：按天、按用户看 token 消耗。

用法：

    python scripts/llm_cost_report.py              # 近 7 天
    python scripts/llm_cost_report.py --days 30    # 近 30 天
    python scripts/llm_cost_report.py --top 5      # 只看前 5 个用户

价格从配置读（`LLM_PRICE_INPUT_PER_M` / `LLM_PRICE_OUTPUT_PER_M`，单位元/百万 token）。
**没配价格时只报 token 数，不报金额**——宁可少给一个数，也不要拿一个
编出来的单价去算成本，那比没有更危险。

顺带做一个自检：`count > 0` 但 `total_tokens = 0` 的行，说明那几次调用没被
计量到（回调没触发、响应不是 JSON、或走了未覆盖的路径）。这个数应该是 0，
不是 0 就说明有漏记，需要查。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from edu_app.core.config import settings  # noqa: E402
from edu_app.core.database import AsyncSessionLocal, engine  # noqa: E402
from edu_app.models.learning import LlmUsage  # noqa: E402
from edu_app.models.user import User  # noqa: E402


def _money(prompt_tokens: int, completion_tokens: int) -> float | None:
    """按配置单价算钱。未配置价格返回 None。"""
    price_in = float(getattr(settings, "LLM_PRICE_INPUT_PER_M", 0) or 0)
    price_out = float(getattr(settings, "LLM_PRICE_OUTPUT_PER_M", 0) or 0)
    if price_in <= 0 and price_out <= 0:
        return None
    return prompt_tokens / 1_000_000 * price_in + completion_tokens / 1_000_000 * price_out


def _fmt_money(value: float | None) -> str:
    return "—" if value is None else f"¥{value:.2f}"


async def main(days: int, top: int) -> int:
    since = date.today() - timedelta(days=days - 1)

    async with AsyncSessionLocal() as db:
        daily = (
            await db.execute(
                select(
                    LlmUsage.day,
                    func.sum(LlmUsage.count),
                    func.sum(LlmUsage.prompt_tokens),
                    func.sum(LlmUsage.completion_tokens),
                    func.sum(LlmUsage.total_tokens),
                )
                .where(LlmUsage.day >= since)
                .group_by(LlmUsage.day)
                .order_by(LlmUsage.day)
            )
        ).all()

        by_user = (
            await db.execute(
                select(
                    User.email,
                    func.sum(LlmUsage.count),
                    func.sum(LlmUsage.total_tokens),
                )
                .join(User, User.id == LlmUsage.user_id)
                .where(LlmUsage.day >= since)
                .group_by(User.email)
                .order_by(func.sum(LlmUsage.total_tokens).desc())
                .limit(top)
            )
        ).all()

        # 自检：有调用次数但没记到 token 的行
        missing = (
            await db.execute(
                select(func.count(), func.sum(LlmUsage.count)).where(
                    LlmUsage.day >= since,
                    LlmUsage.count > 0,
                    LlmUsage.total_tokens == 0,
                )
            )
        ).one()

    if not daily:
        print(f"近 {days} 天没有任何模型调用记录。")
        return 0

    price_in = float(getattr(settings, "LLM_PRICE_INPUT_PER_M", 0) or 0)
    price_out = float(getattr(settings, "LLM_PRICE_OUTPUT_PER_M", 0) or 0)
    priced = price_in > 0 or price_out > 0

    print(f"\n=== 近 {days} 天 token 消耗 ===")
    header = f"{'日期':<12}{'次数':>7}{'输入':>11}{'输出':>10}{'合计':>11}"
    print(header + (f"{'成本':>10}" if priced else ""))
    print("-" * (len(header) + (10 if priced else 0)))

    total_calls = total_in = total_out = total_all = 0
    for day, calls, p_tok, c_tok, all_tok in daily:
        calls = int(calls or 0)
        p_tok = int(p_tok or 0)
        c_tok = int(c_tok or 0)
        all_tok = int(all_tok or 0)
        total_calls += calls
        total_in += p_tok
        total_out += c_tok
        total_all += all_tok
        line = f"{str(day):<12}{calls:>7}{p_tok:>11}{c_tok:>10}{all_tok:>11}"
        if priced:
            line += f"{_fmt_money(_money(p_tok, c_tok)):>10}"
        print(line)

    print("-" * (len(header) + (10 if priced else 0)))
    footer = f"{'合计':<12}{total_calls:>7}{total_in:>11}{total_out:>10}{total_all:>11}"
    if priced:
        footer += f"{_fmt_money(_money(total_in, total_out)):>10}"
    print(footer)

    if not priced:
        print(
            "\n未配置单价，只报 token 数。要算金额，在环境变量里设：\n"
            "  LLM_PRICE_INPUT_PER_M=2.0    # 元 / 百万输入 token\n"
            "  LLM_PRICE_OUTPUT_PER_M=8.0   # 元 / 百万输出 token\n"
            "（数值按你实际合同价填，不要照抄这里）"
        )
    else:
        avg = _money(total_in, total_out) / total_calls if total_calls else 0
        print(f"\n单次对话平均成本：{_fmt_money(avg)}")

    if by_user:
        print(f"\n=== 消耗最多的 {len(by_user)} 个用户 ===")
        print(f"{'邮箱':<34}{'次数':>7}{'token':>11}{'占比':>8}")
        for email, calls, all_tok in by_user:
            all_tok = int(all_tok or 0)
            share = all_tok / total_all * 100 if total_all else 0
            print(f"{str(email)[:33]:<34}{int(calls or 0):>7}{all_tok:>11}{share:>7.1f}%")

    rows, calls = int(missing[0] or 0), int(missing[1] or 0)
    print("\n=== 计量自检 ===")
    if rows:
        print(
            f"⚠ {rows} 个「用户-天」共 {calls} 次调用没有记录 token。\n"
            "  两种情况都会命中这里：\n"
            "  (a) 这条记录早于 token 计量上线——历史数据，属正常，不用管；\n"
            "  (b) 上线之后新产生的记录——说明有漏记（响应非 JSON、调用路径未覆盖），\n"
            "      这会让成本被低估，需要抽查对应接口。\n"
            "  看日期就能区分：早于上线日的归 (a)，之后的是 (b)。"
        )
    else:
        print("✓ 所有调用都记到了 token。")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="大模型成本报表")
    parser.add_argument("--days", type=int, default=7, help="统计最近多少天，默认 7")
    parser.add_argument("--top", type=int, default=10, help="用户榜取前几名，默认 10")
    args = parser.parse_args()

    async def run():
        try:
            return await main(args.days, args.top)
        finally:
            await engine.dispose()

    raise SystemExit(asyncio.run(run()))
