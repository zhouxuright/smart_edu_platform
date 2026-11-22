"""简历工作台接口。

设计取舍：
- 正文用 JSONB 整存整取，字段数量不定，拆表收益为零。
- 版本快照显式触发（`snapshot=1`），不是每次自动保存都留档，
  否则用户改十个字就多十条历史，版本列表会失去可读性。
- 导出 PDF 走服务端 reportlab，用户点一下就落文件。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, UTC
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.api.endpoints import get_current_user, require_llm_quota
from edu_app.core.config import settings
from edu_app.core.database import get_db
from edu_app.models.resume import Resume, ResumeAttachment, ResumeVersion
from edu_app.models.user import User
from edu_app.services import resume_files
from edu_app.services.document_pipeline import ParseError, extract_text
from edu_app.services.llm_text import strip_inline_markers
from edu_app.services.resume_pdf import build_resume_pdf
from edu_app.services.resume_store import snapshot as _snapshot

logger = logging.getLogger(__name__)
router = APIRouter(tags=["resumes"])

# 附件限制：一份简历最多挂 20 个文件，单次最多传 10 个，单文件 10MB、单批 30MB。
# 简历是几页纸，给到 10MB 已经能容下扫描件；再大基本是传错了文件。
MAX_ATTACHMENTS = 20
MAX_UPLOAD_FILES = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 30 * 1024 * 1024
ATTACHMENT_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md"}
PARSEABLE_EXTS = {".pdf", ".docx", ".txt", ".md"}
ATTACHMENT_TEXT_LIMIT = 200_000
EXCERPT_CHARS = 400


def _text(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def _lines(value: Any, limit: int = 8) -> list[str]:
    if isinstance(value, str):
        value = value.split("\n")
    if not isinstance(value, list):
        return []
    return [t for t in (_text(v, 400) for v in value[:limit]) if t]


def _normalize(raw: Any) -> dict:
    """把前端传来的任意结构收敛成固定形状。

    收敛比校验更重要：前端漏传一个字段不该让整份简历 500，
    缺的就是空数组/空串。
    """
    raw = raw if isinstance(raw, dict) else {}
    basics = raw.get("basics") if isinstance(raw.get("basics"), dict) else {}

    def group(items: Any, fields: tuple[str, ...]) -> list[dict]:
        out = []
        for item in (items or [])[:10]:
            if not isinstance(item, dict):
                continue
            entry = {f: _text(item.get(f), 120) for f in fields}
            if any(entry.values()):
                entry["bullets" if "bullets" in fields else "highlights"] = _lines(
                    item.get("bullets") if "bullets" in fields else item.get("highlights")
                )
                out.append(entry)
        return out

    return {
        "basics": {
            "name": _text(basics.get("name"), 40),
            "phone": _text(basics.get("phone"), 40),
            "email": _text(basics.get("email"), 80),
            "city": _text(basics.get("city"), 40),
            "github": _text(basics.get("github"), 120),
            "summary": _text(basics.get("summary"), 600),
        },
        "education": group(
            raw.get("education"), ("school", "major", "degree", "start", "end", "gpa", "highlights")
        ),
        "experience": group(
            raw.get("experience"), ("company", "role", "start", "end", "bullets")
        ),
        "projects": group(
            raw.get("projects"), ("name", "role", "start", "end", "bullets")
        ),
        "skills": _lines(raw.get("skills"), 30),
        "awards": _lines(raw.get("awards"), 12),
    }


def _completeness(data: dict) -> int:
    """完整度：只数「招聘方真的会看」的项，避免用空列表刷分。"""
    checks = [
        bool(data["basics"]["name"]),
        bool(data["basics"]["email"] or data["basics"]["phone"]),
        bool(data["basics"]["summary"]),
        bool(data["education"]),
        bool(data["experience"] or data["projects"]),
        any(item.get("bullets") or item.get("highlights") for item in data["experience"] + data["projects"] + data["education"]),
        bool(data["skills"]),
    ]
    return round(sum(1 for c in checks if c) / len(checks) * 100)


def _payload(resume: Resume, with_data: bool = True) -> dict:
    data = resume.data or {}
    body = {
        "id": resume.id,
        "title": resume.title,
        "target_role": resume.target_role,
        "version": resume.version,
        "updated_at": resume.updated_at.isoformat() if resume.updated_at else None,
        "created_at": resume.created_at.isoformat() if resume.created_at else None,
    }
    if with_data:
        body["data"] = data
        body["completeness"] = _completeness(_normalize(data))
    return body


async def _own(db: AsyncSession, resume_id: int, user: User) -> Resume:
    result = await db.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user.id)
    )
    resume = result.scalars().first()
    if not resume:
        # 不存在和越权返回同一个 404，避免泄露「这个 id 存在」
        raise HTTPException(status_code=404, detail="简历不存在")
    return resume


@router.get("/resumes")
async def list_resumes(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(Resume).where(Resume.user_id == current_user.id).order_by(Resume.updated_at.desc())
    )
    return {"items": [_payload(r, with_data=False) for r in result.scalars().all()]}


@router.post("/resumes")
async def create_resume(
    body: dict | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    body = body or {}
    resume = Resume(
        user_id=current_user.id,
        title=_text(body.get("title"), 80) or "我的简历",
        target_role=_text(body.get("target_role"), 80) or None,
        data=_normalize(body.get("data")),
        version=0,
    )
    db.add(resume)
    await db.flush()
    await _snapshot(db, resume, "创建简历")
    await db.commit()
    await db.refresh(resume)
    return _payload(resume)


@router.get("/resumes/{resume_id}")
async def get_resume(resume_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _payload(await _own(db, resume_id, current_user))


@router.patch("/resumes/{resume_id}")
async def update_resume(
    resume_id: int,
    body: dict,
    snapshot: bool = Query(False, description="是否留一个可回滚的版本快照"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resume = await _own(db, resume_id, current_user)
    if "title" in body:
        resume.title = _text(body.get("title"), 80) or resume.title
    if "target_role" in body:
        resume.target_role = _text(body.get("target_role"), 80) or None
    if "data" in body:
        resume.data = _normalize(body.get("data"))
    if snapshot:
        await _snapshot(db, resume, _text(body.get("note"), 120) or "手动保存")
    await db.commit()
    await db.refresh(resume)
    return _payload(resume)


@router.delete("/resumes/{resume_id}")
async def delete_resume(resume_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    resume = await _own(db, resume_id, current_user)
    # 先把磁盘文件名记下来：删完库就查不到了，附件文件会永远留在磁盘上
    stored = (
        await db.execute(
            select(ResumeAttachment.stored_name).where(ResumeAttachment.resume_id == resume.id)
        )
    ).scalars().all()
    await db.delete(resume)
    await db.commit()
    for name in stored:
        resume_files.remove(current_user.id, name)
    return {"deleted": resume_id}


@router.get("/resumes/{resume_id}/versions")
async def list_versions(resume_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    resume = await _own(db, resume_id, current_user)
    rows = (
        await db.execute(
            select(ResumeVersion)
            .where(ResumeVersion.resume_id == resume.id)
            .order_by(ResumeVersion.version.desc())
        )
    ).scalars().all()
    return {
        "current": resume.version,
        "items": [
            {
                "id": r.id,
                "version": r.version,
                "note": r.note,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "is_current": r.version == resume.version,
            }
            for r in rows
        ],
    }


@router.post("/resumes/{resume_id}/versions")
async def create_version(
    resume_id: int,
    body: dict | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resume = await _own(db, resume_id, current_user)
    note = _text((body or {}).get("note"), 120) or "手动留档"
    await _snapshot(db, resume, note)
    await db.commit()
    return {"version": resume.version}


@router.post("/resumes/{resume_id}/versions/{version}/restore")
async def restore_version(
    resume_id: int,
    version: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resume = await _own(db, resume_id, current_user)
    row = (
        await db.execute(
            select(ResumeVersion).where(
                ResumeVersion.resume_id == resume.id, ResumeVersion.version == version
            )
        )
    ).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="版本不存在")
    # 恢复前先把当前内容存一份，否则「恢复错了」就再也回不去
    await _snapshot(db, resume, f"恢复前备份（v{resume.version}）")
    resume.data = row.data
    await _snapshot(db, resume, f"恢复到 v{version}")
    await db.commit()
    await db.refresh(resume)
    return _payload(resume)


@router.get("/resumes/{resume_id}/pdf")
async def export_pdf(resume_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    resume = await _own(db, resume_id, current_user)
    try:
        pdf = build_resume_pdf(_normalize(resume.data))
    except RuntimeError:
        logger.error("Resume PDF export failed: no CJK font available")
        raise HTTPException(status_code=503, detail="服务器缺少中文字体，暂时无法导出 PDF")
    name = (resume.data or {}).get("basics", {}).get("name") or "resume"
    # HTTP 头只能是 latin-1，中文文件名必须按 RFC 5987 百分号编码，
    # 否则 Starlette 在写 header 时直接抛 UnicodeEncodeError（500）。
    filename = quote(f"{name}-简历.pdf")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=resume.pdf; filename*=UTF-8''{filename}"},
    )


# ---------------------------------------------------------------- 附件简历

def _attachment_payload(row: ResumeAttachment) -> dict:
    text = row.text or ""
    return {
        "id": row.id,
        "filename": row.filename,
        "ext": row.ext,
        "mime": row.mime,
        "size_bytes": row.size_bytes,
        "chars": row.chars,
        "pages": row.pages,
        "has_text": bool(text.strip()),
        "excerpt": text[:EXCERPT_CHARS],
        "parse_error": row.parse_error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _own_attachment(db: AsyncSession, resume: Resume, attachment_id: int) -> ResumeAttachment:
    row = (
        await db.execute(
            select(ResumeAttachment).where(
                ResumeAttachment.id == attachment_id,
                ResumeAttachment.resume_id == resume.id,
                ResumeAttachment.user_id == resume.user_id,
            )
        )
    ).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="附件不存在")
    return row


def _parse_attachment(filename: str, data: bytes, ext: str) -> tuple[str | None, int | None, int | None, str | None]:
    """返回 (正文, 字数, 页数, 解析错误)。解析失败不阻断上传——文件本身仍然可用。"""
    if ext not in PARSEABLE_EXTS:
        return None, None, None, "旧版 .doc 无法提取文字，建议用 Word 另存为 .docx 后再上传"
    try:
        parsed = extract_text(filename, data)
    except ParseError as error:
        return None, None, None, str(error)[:200]
    except Exception:
        logger.exception("Attachment parse failed: %s", filename)
        return None, None, None, "文件已保存，但无法提取文字"

    raw = parsed["text"]
    chars = len(raw)
    if chars > ATTACHMENT_TEXT_LIMIT:
        return raw[:ATTACHMENT_TEXT_LIMIT], chars, parsed.get("pages"), "正文过长，仅解析了前 20 万字"
    return raw, chars, parsed.get("pages"), None


@router.post("/resumes/{resume_id}/attachments")
async def upload_attachments(
    resume_id: int,
    files: list[UploadFile] = File(...),
    # 小程序 wx.uploadFile 无法指定 multipart 的文件名，服务端拿到的常是临时文件名；
    # 客户端可以用这个字段按顺序传原始文件名（JSON 字符串数组）
    filenames: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """单传与批量上传共用同一个入口：传 1 个就是单传，传多个就是批传。

    逐文件返回结果而不是整批成败：10 个文件里 1 个格式不对，
    不该让另外 9 个白传。同一份简历里内容相同的文件按 sha256 去重。
    """
    resume = await _own(db, resume_id, current_user)
    if not files:
        raise HTTPException(status_code=400, detail="没有收到文件")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多上传 {MAX_UPLOAD_FILES} 个文件，当前 {len(files)} 个",
        )

    display_names: list[str] = []
    if filenames:
        try:
            parsed_names = json.loads(filenames)
            if isinstance(parsed_names, list):
                display_names = [str(name or "").strip() for name in parsed_names]
        except json.JSONDecodeError:
            logger.warning("Ignoring malformed filenames payload")

    known = set(
        (
            await db.execute(
                select(ResumeAttachment.sha256).where(ResumeAttachment.resume_id == resume.id)
            )
        ).scalars().all()
    )
    if len(known) >= MAX_ATTACHMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"一份简历最多挂 {MAX_ATTACHMENTS} 个附件，请先删掉不用的",
        )

    results: list[dict] = []
    total_bytes = 0
    saved_any = False

    for index, upload in enumerate(files):
        filename = (upload.filename or "未命名文件").strip()[:200]
        if index < len(display_names) and display_names[index]:
            filename = display_names[index][:200]
        ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        try:
            data = await upload.read()
            total_bytes += len(data)

            if not data:
                results.append({"filename": filename, "status": "failed", "error": "文件是空的（0 字节）"})
                continue
            if len(data) > MAX_ATTACHMENT_BYTES:
                results.append({"filename": filename, "status": "failed", "error": "单文件超过 10MB"})
                continue
            if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
                results.append({"filename": filename, "status": "failed", "error": "本批文件总大小超过 30MB"})
                continue
            if ext not in ATTACHMENT_EXTS:
                results.append({
                    "filename": filename,
                    "status": "failed",
                    "error": f"不支持的格式 {ext or '（无扩展名）'}，请上传 pdf / docx / doc / txt / md",
                })
                continue

            sha = resume_files.digest(data)
            if sha in known:
                dup_id = (
                    await db.execute(
                        select(ResumeAttachment.id).where(
                            ResumeAttachment.resume_id == resume.id,
                            ResumeAttachment.sha256 == sha,
                        )
                    )
                ).scalars().first()
                results.append({
                    "filename": filename,
                    "status": "duplicated",
                    "attachment_id": dup_id,
                    "error": "这份简历里已有相同文件，已跳过",
                })
                continue
            if len(known) >= MAX_ATTACHMENTS:
                results.append({
                    "filename": filename,
                    "status": "failed",
                    "error": f"一份简历最多挂 {MAX_ATTACHMENTS} 个附件",
                })
                continue

            text, chars, pages, parse_error = _parse_attachment(filename, data, ext)
            stored_name = resume_files.save(current_user.id, data, ext)
            row = ResumeAttachment(
                resume_id=resume.id,
                user_id=current_user.id,
                filename=filename,
                stored_name=stored_name,
                ext=ext,
                mime=resume_files.mime_of(ext),
                size_bytes=len(data),
                sha256=sha,
                chars=chars,
                pages=pages,
                text=text,
                parse_error=parse_error,
            )
            try:
                # SAVEPOINT：并发上传同一文件时唯一约束会冲突，只回滚这一条
                async with db.begin_nested():
                    db.add(row)
                    await db.flush()
            except IntegrityError:
                resume_files.remove(current_user.id, stored_name)
                results.append({"filename": filename, "status": "duplicated", "error": "这份简历里已有相同文件，已跳过"})
                continue

            known.add(sha)
            saved_any = True
            # 必须在 commit 之前取字段：commit 后 ORM 实例过期，再读会触发懒加载
            results.append({**_attachment_payload(row), "status": "ok"})
        except Exception as error:
            logger.exception("Attachment upload failed: %s", filename)
            results.append({"filename": filename, "status": "failed", "error": f"上传失败：{type(error).__name__}"})
        finally:
            await upload.close()

    if saved_any:
        resume.updated_at = datetime.now(UTC)
    await db.commit()

    return {
        "items": results,
        "summary": {
            "total": len(results),
            "ok": sum(1 for r in results if r["status"] == "ok"),
            "duplicated": sum(1 for r in results if r["status"] == "duplicated"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
        },
    }


@router.get("/resumes/{resume_id}/attachments")
async def list_attachments(
    resume_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resume = await _own(db, resume_id, current_user)
    rows = (
        await db.execute(
            select(ResumeAttachment)
            .where(ResumeAttachment.resume_id == resume.id)
            .order_by(ResumeAttachment.created_at.desc())
        )
    ).scalars().all()
    items = [_attachment_payload(r) for r in rows]
    return {
        "items": items,
        "limit": MAX_ATTACHMENTS,
        "max_file_bytes": MAX_ATTACHMENT_BYTES,
        "max_batch_bytes": MAX_UPLOAD_TOTAL_BYTES,
        "max_batch_files": MAX_UPLOAD_FILES,
        "allowed_exts": sorted(ATTACHMENT_EXTS),
        "total_bytes": sum(i["size_bytes"] or 0 for i in items),
    }


@router.get("/resumes/{resume_id}/attachments/{attachment_id}/text")
async def attachment_text(
    resume_id: int,
    attachment_id: int,
    limit: int = Query(20000, ge=500, le=ATTACHMENT_TEXT_LIMIT),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resume = await _own(db, resume_id, current_user)
    row = await _own_attachment(db, resume, attachment_id)
    text = row.text or ""
    return {
        "id": row.id,
        "filename": row.filename,
        "chars": row.chars or 0,
        "text": text[:limit],
        "truncated": len(text) > limit,
        "parse_error": row.parse_error,
    }


@router.get("/resumes/{resume_id}/attachments/{attachment_id}/download")
async def download_attachment(
    resume_id: int,
    attachment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resume = await _own(db, resume_id, current_user)
    row = await _own_attachment(db, resume, attachment_id)
    data = resume_files.read(current_user.id, row.stored_name)
    if data is None:
        raise HTTPException(status_code=404, detail="文件已不在服务器上，请重新上传")
    fallback = f"attachment{row.ext or '.bin'}"
    encoded = quote(row.filename or fallback)
    return Response(
        content=data,
        media_type=row.mime or resume_files.mime_of(row.ext),
        headers={
            "Content-Disposition": f"attachment; filename={fallback}; filename*=UTF-8''{encoded}",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/resumes/{resume_id}/attachments/{attachment_id}")
async def delete_attachment(
    resume_id: int,
    attachment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resume = await _own(db, resume_id, current_user)
    row = await _own_attachment(db, resume, attachment_id)
    stored_name = row.stored_name
    await db.delete(row)
    await db.commit()
    # 先删库再删盘：库删成功而文件删失败只会留个孤儿文件，不影响用户；
    # 反过来会造成「记录还在、文件没了」的坏状态。
    resume_files.remove(current_user.id, stored_name)
    return {"deleted": attachment_id}


async def _chat_completion(prompt: str, max_tokens: int = 900, temperature: float = 0.3) -> str | None:
    base = (settings.LLM_API_BASE or "").strip()
    key = (settings.LLM_API_KEY or "").strip()
    if getattr(settings, "LLM_DISABLE", False) or not base or not key or key.lower() == "dummy":
        return None
    model_name = getattr(settings, "LLM_MODEL", None) or (
        "deepseek-chat" if "deepseek.com" in base else "gpt-4o"
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0), base_url=base) as client:
            response = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model_name,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            return (response.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except Exception as error:
        logger.warning("LLM call failed: %s", type(error).__name__)
        return None


async def _structure_with_model(text: str, target_role: str) -> dict | None:
    prompt = (
        "你是简历信息抽取器。把下面这份简历原文抽取成结构化 JSON。\n"
        "输出格式（严格照这个结构）：\n"
        '{"basics":{"name":"","phone":"","email":"","city":"","github":"","summary":""},'
        '"education":[{"school":"","major":"","degree":"","start":"","end":"","gpa":"","highlights":[""]}],'
        '"experience":[{"company":"","role":"","start":"","end":"","bullets":[""]}],'
        '"projects":[{"name":"","role":"","start":"","end":"","bullets":[""]}],'
        '"skills":[""],"awards":[""]}\n'
        "规则：\n"
        "1. 只抽取原文明确写出的信息，原文没有的字段留空字符串或空数组，绝对不要编造学校、公司、数字、奖项。\n"
        "2. 时间统一写成 YYYY.MM，只有年份就写 YYYY。\n"
        "3. bullets / highlights 每条不超过 60 字，保留原文里的数字和结果。\n"
        "4. summary 用两句话概括，不超过 80 字；原文没有自我描述时按经历客观归纳。\n"
        "5. 不要输出 Markdown 代码块，不要解释，只输出 JSON 对象。\n"
        f"目标岗位（仅供理解语境）：{target_role or '未指定'}\n"
        f"简历原文：\n{text[:6000]}"
    )
    content = await _chat_completion(prompt, max_tokens=1600, temperature=0.1)
    if not content:
        return None
    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


@router.post("/resumes/{resume_id}/attachments/{attachment_id}/import")
async def import_attachment(
    resume_id: int,
    attachment_id: int,
    apply: bool = Query(False, description="true 时直接覆盖当前结构化简历，并自动留档"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _quota: bool = Depends(require_llm_quota),
):
    """把附件正文解析成结构化字段。

    默认只返回解析结果给前端预览（apply=false），用户确认后再落库；
    直接覆盖用户手填的内容是不可接受的，哪怕解析得再准。
    """
    resume = await _own(db, resume_id, current_user)
    row = await _own_attachment(db, resume, attachment_id)
    text = (row.text or "").strip()
    if len(text) < 80:
        raise HTTPException(
            status_code=400,
            detail="这份附件里没有可解析的文字（可能是扫描件或图片），请改用左侧结构化填写",
        )

    parsed = await _structure_with_model(text, resume.target_role or "")
    if not parsed:
        raise HTTPException(status_code=503, detail="AI 暂时不可用，请稍后再试或手动填写")
    data = _normalize(parsed)
    if not any([
        data["basics"]["name"] or data["basics"]["email"] or data["basics"]["phone"],
        data["education"], data["experience"], data["projects"], data["skills"],
    ]):
        raise HTTPException(status_code=422, detail="没能从附件里识别出结构化信息，请手动填写")

    if not apply:
        return {
            "applied": False,
            "data": data,
            "completeness": _completeness(data),
            "source": {"attachment_id": row.id, "filename": row.filename, "chars": row.chars},
        }

    # 覆盖前先留一份，导入错了还能一键恢复
    await _snapshot(db, resume, "导入附件前备份")
    resume.data = data
    await _snapshot(db, resume, f"从附件导入：{row.filename[:40]}")
    await db.commit()
    await db.refresh(resume)
    return {"applied": True, **_payload(resume)}


async def _polish_with_model(section: str, text: str, target_role: str) -> list[str] | None:
    prompt = (
        "你是简历顾问。把下面这段经历改写成 2-3 条简历要点。\n"
        f"目标岗位：{target_role or '未指定'}\n"
        f"板块：{section}\n"
        f"原始内容：{text}\n"
        "要求：每条以动词开头，写清做了什么、怎么做、结果如何；"
        "只使用原文已有的事实，绝对不要编造数字、公司、奖项或时间；"
        "原文缺少量化结果时，用可核查的定性表述，不要虚构指标；"
        "每条不超过 60 字，不要 Markdown 符号，不要编号。"
        '只输出 JSON 数组，例如 ["...","..."]'
    )
    content = await _chat_completion(prompt, max_tokens=600)
    if not content:
        return None

    match = re.search(r"\[[\s\S]*\]", content)
    if not match:
        return None
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    cleaned = []
    for item in items if isinstance(items, list) else []:
        line = strip_inline_markers(str(item or ""))
        line = re.sub(r"^\s*(?:[-*•]|\d+[.、)])\s*", "", line).strip()
        if line:
            cleaned.append(line[:120])
    return cleaned[:3] or None


@router.post("/resumes/{resume_id}/polish")
async def polish(
    resume_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _quota: bool = Depends(require_llm_quota),
):
    resume = await _own(db, resume_id, current_user)
    text = _text((body or {}).get("text"), 800)
    if len(text) < 8:
        raise HTTPException(status_code=400, detail="内容太短，至少写 8 个字再让 AI 润色")
    section = _text((body or {}).get("section"), 20) or "经历"
    lines = await _polish_with_model(section, text, resume.target_role or "")
    if not lines:
        raise HTTPException(status_code=503, detail="AI 暂时不可用，请稍后再试或手动修改")
    return {"lines": lines, "model_used": True}
