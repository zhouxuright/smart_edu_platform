import logging
import json
from typing import List, Dict, Any
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from edu_app.models.learning import StudentProfile, LearningRecord, MistakeRecord

logger = logging.getLogger(__name__)

class MemoryLayerEngine:
    """
    V3.0.0 长期层级记忆引擎 (Long-Term Memory Layer)
    用于提取、维护和注入学生在长期交互中沉淀的特质与弱点上下文
    """
    
    @staticmethod
    async def extract_and_enrich_context(db: AsyncSession, user_id: int) -> Dict[str, Any]:
        """
        从 Profile、错题库和学习历史中智能抽取层级记忆
        """
        # 1. 查询基础画像
        p_res = await db.execute(select(StudentProfile).where(StudentProfile.user_id == user_id))
        profile = p_res.scalars().first()
        
        grade = getattr(profile, "grade", "高一")
        interests = getattr(profile, "interests", "数理化")
        learning_style = getattr(profile, "learning_style", "混合型")
        
        # 2. 查询近期未解决错题核心点 (Episodic Memory)
        m_res = await db.execute(
            select(MistakeRecord)
            .where(MistakeRecord.user_id == user_id, MistakeRecord.is_resolved == 0)
            .order_by(MistakeRecord.created_at.desc())
            .limit(5)
        )
        recent_mistakes = m_res.scalars().all()
        
        weakness_topics = []
        for m in recent_mistakes:
            topic = m.topic or m.subject
            if topic and topic not in weakness_topics:
                weakness_topics.append(topic)
                
        # 3. 统计平均学习得分 (Semantic Memory Summary)
        r_res = await db.execute(
            select(LearningRecord)
            .where(LearningRecord.user_id == user_id)
            .order_by(LearningRecord.timestamp.desc())
            .limit(10)
        )
        records = r_res.scalars().all()
        scores = [r.score for r in records if r.score is not None]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 85.0
        
        # 4. 组装强化的长期记忆 Prompt 上下文
        memory_prompt_context = (
            f"【学生长期特质记忆】：\n"
            f"- 年级与兴趣：{grade} | 偏好：{interests}\n"
            f"- 学习风格：{learning_style}（请针对此风格多使用对应的方法解释）\n"
            f"- 近期平均表现得分：{avg_score} 分\n"
            f"- 核心薄弱知识点（需特别注意引导）：{', '.join(weakness_topics) if weakness_topics else '暂无明显薄弱项'}\n"
        )
        
        return {
            "grade": grade,
            "interests": interests,
            "learning_style": learning_style,
            "avg_score": avg_score,
            "weakness_topics": weakness_topics,
            "memory_context": memory_prompt_context
        }
