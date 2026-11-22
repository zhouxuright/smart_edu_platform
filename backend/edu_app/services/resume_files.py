"""附件简历的文件存储。

为什么不用 `backend/uploads`：
那个目录被 main.py 挂到了 `/static`，任何人拿到路径就能下载。简历附件里
有手机号、邮箱、住址，属于个人敏感信息，必须走带鉴权的接口读，
所以单独落在 `backend/storage/resumes`，只由 `/api/resumes/.../download` 吐出。

目录按 user_id 分片：同一目录下文件数可控，删用户数据时也能整目录清理。
磁盘名用 uuid，不用原始文件名——中文名、重名、路径穿越都能一次绕开。
"""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from edu_app.core.config import settings

# 跟着 DATA_DIR 走。本地默认 backend/storage/resumes，线上指向挂载卷
BASE_DIR = settings.data_root / "storage" / "resumes"

# 只接受我们自己生成的磁盘名，任何外部传入的 stored_name 都要过这一关
_SAFE_NAME = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{1,8}$")

MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _user_dir(user_id: int) -> Path:
    path = BASE_DIR / str(int(user_id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(user_id: int, data: bytes, ext: str) -> str:
    """写入文件，返回磁盘名。ext 需带点，如 '.pdf'。"""
    stored_name = f"{uuid.uuid4().hex}{ext}"
    _user_dir(user_id).joinpath(stored_name).write_bytes(data)
    return stored_name


def path_of(user_id: int, stored_name: str) -> Path | None:
    if not _SAFE_NAME.match(stored_name or ""):
        return None
    path = BASE_DIR / str(int(user_id)) / stored_name
    return path if path.is_file() else None


def read(user_id: int, stored_name: str) -> bytes | None:
    path = path_of(user_id, stored_name)
    return path.read_bytes() if path else None


def remove(user_id: int, stored_name: str) -> bool:
    path = path_of(user_id, stored_name)
    if not path:
        return False
    try:
        path.unlink()
        return True
    except OSError:
        # 文件已不在磁盘上不算失败：数据库记录该删还是要删
        return False


def mime_of(ext: str) -> str:
    return MIME_BY_EXT.get((ext or "").lower(), "application/octet-stream")
