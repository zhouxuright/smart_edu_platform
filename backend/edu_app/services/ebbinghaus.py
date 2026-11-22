import math
import logging
from datetime import datetime, UTC
from typing import List, Dict, Any
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from edu_app.models.learning import MistakeRecord

logger = logging.getLogger(__name__)

def calculate_retention_rate(created_at: datetime, stability_factor: float = 1.0) -> float:
    """
    基于赫尔曼·艾宾浩斯遗忘曲线模型计算当前记忆保留率：
    R = exp(-t / (2.5 * S))
    - t: 距离上次出题/复习的天数
    - S: 记忆稳定因子 (例如解题难度大或重复错过的题目，S 值较小，衰减更快)
    """
    now = datetime.now(UTC)
    if not created_at:
        return 0.5
    
    # 确保时区一致
    if getattr(created_at, "tzinfo", None) is None:
        created_at = created_at.replace(tzinfo=UTC)
        
    delta_days = (now - created_at).total_seconds() / 86400.0
    if delta_days <= 0:
        return 1.0
    
    # 算式：R = exp(-t / (2.5 * S))
    effective_stability = max(0.2, stability_factor)
    retention = math.exp(-delta_days / (2.5 * effective_stability))
    return round(retention, 4)


async def get_due_reviews_for_user(db: AsyncSession, user_id: int, max_items: int = 5) -> List[Dict[str, Any]]:
    """
    自适应获取当前触发遗忘黄金点的待复练错题
    """
    result = await db.execute(
        select(MistakeRecord)
        .where(MistakeRecord.user_id == user_id, MistakeRecord.is_resolved == 0)
        .order_by(MistakeRecord.created_at.desc())
    )
    mistakes = result.scalars().all()
    
    due_list = []
    for m in mistakes:
        # 基于是否有分析结果估算稳定性 S
        analysis = m.analysis_result or {}
        difficulty = float(analysis.get("difficulty", 3))
        # 难度越高，稳定性越低 (1~5) -> S 在 0.6 ~ 1.4 之间
        stability = max(0.5, (6 - difficulty) * 0.25)
        
        retention = calculate_retention_rate(m.created_at, stability_factor=stability)
        
        # 当记忆保留率跌破 65% 或超过 1 天未巩固时触发提醒
        if retention <= 0.65:
            due_list.append({
                "mistake_id": m.id,
                "subject": m.subject,
                "topic": m.topic or "综合应用",
                "question_content": m.question_content[:60] + "...",
                "retention_rate": retention,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "recommendation_reason": f"记忆保留率已降至 {int(retention * 100)}%，处于艾宾浩斯黄金复练窗口"
            })
            
    # 按保留率升序排序（最容易忘记的优先练习）
    due_list.sort(key=lambda x: x["retention_rate"])
    return due_list[:max_items]


def generate_wechat_push_payload(user_name: str, due_reviews: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    生成微信小程序订阅消息推送模板 Payload
    """
    count = len(due_reviews)
    first_topic = due_reviews[0]["topic"] if due_reviews else "暂无"
    
    return {
        "touser": "WECHAT_OPENID_PLACEHOLDER",
        "template_id": "TEMPLATE_ID_EBBINGHAUS_REVIEW",
        "page": "pages/mistake/mistake",
        "data": {
            "thing1": {"value": f"黄金复习提醒 ({count}道题待巩固)"},
            "thing2": {"value": f"推荐首选：《{first_topic}》"},
            "phrase3": {"value": "自适应闭环"},
            "date4": {"value": datetime.now(UTC).strftime("%Y-%m-%d %H:%M")}
        }
    }
