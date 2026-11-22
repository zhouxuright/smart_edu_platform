"""计划提醒的实际投递。

只做一件事：把「用户开了提醒、到点了、今天还没提醒过」的计划，
写成一条站内通知。不依赖外部服务，进程内定时循环即可。

设计取舍：
- 不用 Celery：为了一个每天跑几次的任务引入 broker 不划算，
  且当前是单进程部署，asyncio 循环足够。
- 幂等靠数据库唯一约束（dedupe_key），不靠内存标记：
  进程重启后内存标记会丢，唯一约束不会。
- 时区用服务器本地时间，与 internship_plans.py 里 `datetime.now()` 的口径一致。
"""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from edu_app.core.database import AsyncSessionLocal
from edu_app.models.notification import Notification
from edu_app.models.plan import InternshipPlan, InternshipPlanTask, PlanReminder

logger = logging.getLogger(__name__)

# 扫描间隔：提醒精度到小时，10 分钟扫一次足够，也不会给数据库压力
SCAN_INTERVAL_SECONDS = 600


async def dispatch_due_reminders(now: datetime | None = None) -> int:
    """把到点未提醒的计划转成站内通知，返回新增条数。"""
    local_now = now or datetime.now()
    today = local_now.strftime("%Y-%m-%d")
    created = 0

    async with AsyncSessionLocal() as db:
        reminders = (
            await db.execute(
                select(PlanReminder).where(
                    PlanReminder.enabled.is_(True), PlanReminder.hour <= local_now.hour
                )
            )
        ).scalars().all()

        for reminder in reminders:
            plan = await db.scalar(
                select(InternshipPlan).where(InternshipPlan.id == reminder.plan_id)
            )
            if not plan:
                continue

            pending = await db.scalar(
                select(InternshipPlanTask)
                .where(
                    InternshipPlanTask.plan_id == plan.id,
                    InternshipPlanTask.completed.is_(False),
                )
                .order_by(InternshipPlanTask.day_index)
            )
            # 全部完成就不打扰了
            if not pending:
                continue

            dedupe_key = f"plan_reminder:{plan.id}:{today}"
            exists = await db.scalar(
                select(Notification.id).where(Notification.dedupe_key == dedupe_key)
            )
            if exists:
                continue

            db.add(
                Notification(
                    user_id=reminder.user_id,
                    type="plan_reminder",
                    title=f"今天这一步还没做：{pending.title}",
                    body=(
                        f"{plan.target_role} · 第 {pending.day_index + 1} 天\n"
                        f"{pending.action}\n交付物：{pending.deliverable}"
                    ),
                    link="/plan/internship",
                    dedupe_key=dedupe_key,
                )
            )
            try:
                await db.commit()
                created += 1
            except IntegrityError:
                # 并发下另一个循环已经写过同一条，忽略即可
                await db.rollback()

    if created:
        logger.info("已生成 %s 条计划提醒", created)
    return created


async def reminder_loop(stop_event: asyncio.Event) -> None:
    """常驻循环。启动时先等一会儿，避免和建表/预热抢资源。"""
    await asyncio.sleep(15)
    while not stop_event.is_set():
        try:
            await dispatch_due_reminders()
        except Exception as exc:  # 单次失败不能让循环退出
            logger.warning("提醒扫描失败：%s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=SCAN_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue
