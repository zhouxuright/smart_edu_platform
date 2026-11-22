"""简历版本快照。

原来这段逻辑写在 `api/resumes.py` 里，Agent 改写简历要点时也要留档，
两边各写一份迟早会跑偏（比如一边裁到 30 版、另一边忘了裁）。
所以抽成服务层，API 和 Agent 共用同一条路径。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.models.resume import Resume, ResumeVersion

# 每个简历最多保留的版本数。历史版本是「反悔用」的，不是归档系统。
MAX_VERSIONS = 30


async def snapshot(
    db: AsyncSession,
    resume: Resume,
    note: str,
    data: dict | None = None,
) -> ResumeVersion:
    """把当前内容存为一个版本，返回新建的版本行。

    `data` 不传就存 `resume.data`；恢复历史版本时传旧数据。
    调用方负责 commit。
    """
    resume.version = (resume.version or 0) + 1
    row = ResumeVersion(
        resume_id=resume.id,
        version=resume.version,
        note=(note or "")[:120],
        data=data if data is not None else (resume.data or {}),
    )
    db.add(row)

    stale = (
        await db.execute(
            select(ResumeVersion.id)
            .where(ResumeVersion.resume_id == resume.id)
            .order_by(ResumeVersion.version.desc())
            .offset(MAX_VERSIONS)
        )
    ).scalars().all()
    if stale:
        await db.execute(delete(ResumeVersion).where(ResumeVersion.id.in_(stale)))
    return row


def clone(data: Any) -> dict:
    """JSONB 字段做浅层深拷贝，避免直接改到 ORM 里那份引用。"""
    import copy

    return copy.deepcopy(data) if isinstance(data, (dict, list)) else {}
