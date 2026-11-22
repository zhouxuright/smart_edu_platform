"""文档解析与分块：批量入库的唯一入口。

支持格式：pdf / docx / txt / md / csv / json / log。
解析失败会抛出 ParseError，由调用方转成逐文件失败原因，而不是静默存空文档。
"""
import io
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_DOC_CHARS = 2_000_000
CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".text"}
SUPPORTED_EXTS = TEXT_EXTS | {".pdf", ".docx"}


class ParseError(Exception):
    """解析失败，message 直接展示给用户。"""


def decode_bytes(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="ignore"), "utf-8(忽略异常字节)"


def _extract_pdf(data: bytes) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError as error:  # pragma: no cover
        raise ParseError("服务端缺少 PDF 解析依赖 pypdf") from error

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as error:
        raise ParseError("PDF 无法打开，可能已损坏或加密") from error

    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as error:
            raise ParseError("PDF 已加密，请先解除密码再上传") from error

    pages = list(getattr(reader, "pages", []) or [])
    if not pages:
        raise ParseError("PDF 没有任何页面")

    texts, empty_pages = [], []
    for index, page in enumerate(pages, 1):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        page_text = page_text.strip()
        if not page_text:
            empty_pages.append(index)
        texts.append(f"[第 {index} 页]\n{page_text}" if page_text else "")

    text = "\n\n".join(t for t in texts if t).strip()
    warnings = []
    if not text:
        raise ParseError("PDF 里没有可提取的文字（可能是扫描件，需要先 OCR）")
    if empty_pages:
        preview = ", ".join(str(p) for p in empty_pages[:5])
        warnings.append(f"{len(empty_pages)} 页没有文字（第 {preview} 页等），可能是扫描页")
    return {"text": text, "pages": len(pages), "warnings": warnings, "format": "pdf"}


def _extract_docx(data: bytes) -> dict:
    try:
        import docx
    except ImportError as error:  # pragma: no cover
        raise ParseError("服务端缺少 DOCX 解析依赖 python-docx") from error

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as error:
        raise ParseError("DOCX 无法打开，可能已损坏或实际是旧版 .doc") from error

    parts = [p.text.strip() for p in document.paragraphs if p.text and p.text.strip()]
    for table in getattr(document, "tables", []) or []:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))

    text = "\n".join(parts).strip()
    if not text:
        raise ParseError("DOCX 里没有文字内容")
    return {"text": text, "pages": None, "warnings": [], "format": "docx"}


def _extract_text_file(data: bytes, ext: str) -> dict:
    text, encoding = decode_bytes(data)
    text = text.strip()
    if not text:
        raise ParseError("文件内容为空")
    warnings = []
    if encoding.startswith("utf-8(忽略"):
        warnings.append("文件编码无法识别，已按 UTF-8 强制读取，可能出现乱码")
    if ext == ".json":
        try:
            json.loads(text)
        except Exception:
            warnings.append("内容不是合法 JSON，已按纯文本入库")
    return {"text": text, "pages": None, "warnings": warnings, "format": ext.lstrip(".")}


def extract_text(filename: str, data: bytes) -> dict:
    """按扩展名解析文件，返回 {text, pages, warnings, format}。"""
    if not data:
        raise ParseError("文件是空的（0 字节）")
    ext = Path(filename or "").suffix.lower()
    if ext == ".pdf":
        result = _extract_pdf(data)
    elif ext == ".docx":
        result = _extract_docx(data)
    elif ext == ".doc":
        raise ParseError("不支持旧版 .doc，请用 Word 另存为 .docx 后再上传")
    elif ext in TEXT_EXTS or not ext:
        result = _extract_text_file(data, ext or ".txt")
    else:
        raise ParseError(f"不支持的格式 {ext}，目前支持 pdf/docx/txt/md/csv/json")

    text = result["text"]
    if len(text) > MAX_DOC_CHARS:
        raise ParseError(f"文档过长（{len(text)} 字，上限 {MAX_DOC_CHARS} 字），请拆分后上传")
    result["chars"] = len(text)
    return result


_SENTENCE_END = "。！？；!?;\n"


def _split_long(block: str, size: int) -> list[str]:
    parts, start = [], 0
    while start < len(block):
        end = min(start + size, len(block))
        if end < len(block):
            window = block[start:end]
            cut = max(window.rfind(ch) for ch in _SENTENCE_END)
            if cut > size * 0.5:
                end = start + cut + 1
        parts.append(block[start:end])
        start = end
    return [p for p in parts if p.strip()]


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """按段落与句子边界切块，块间保留少量重叠，避免答案被切断。"""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    if not normalized:
        return []
    if len(normalized) <= size:
        return [{"seq": 0, "content": normalized, "char_start": 0, "char_end": len(normalized)}]

    paragraphs = [p for p in re.split(r"\n\s*\n", normalized) if p.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= size:
            units.append(paragraph)
        else:
            units.extend(_split_long(paragraph, size))

    chunks, buffer, buffer_start, cursor = [], "", 0, 0
    for unit in units:
        position = normalized.find(unit, cursor)
        if position < 0:
            position = cursor
        if buffer and len(buffer) + len(unit) + 2 > size:
            chunks.append({"content": buffer.strip(), "char_start": buffer_start, "char_end": position})
            tail = buffer[-overlap:] if overlap > 0 else ""
            buffer = f"{tail}\n\n{unit}" if tail else unit
            buffer_start = max(0, position - len(tail) - 2)
        else:
            if not buffer:
                buffer_start = position
            buffer = f"{buffer}\n\n{unit}" if buffer else unit
        cursor = position + len(unit)

    if buffer.strip():
        chunks.append({"content": buffer.strip(), "char_start": buffer_start, "char_end": min(len(normalized), cursor)})

    return [{"seq": index, **chunk} for index, chunk in enumerate(chunks)]
