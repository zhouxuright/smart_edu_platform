"""LLM 输出文本清洗。

模型（DeepSeek / GPT）默认按 Markdown 组织回答，但历史代码把它当纯文本渲染，
于是 `**`、`###`、`---`、`--` 这些标记原样暴露给学生。

这里做两件事：
1. `sanitize_markdown()` —— 把模型写坏的标记修回合法 Markdown，而不是把所有标记
   都删掉。删掉会丢掉结构（标题、列表、加粗），排版反而更差；修好后交给前端
   react-markdown 渲染，用户看到的就只有排版结果，不会看到任何符号。
2. `MarkdownStreamFilter` —— SSE 流式场景下扣住行尾可能还没写完的标记
   （如刚吐出 `*`、下一个 chunk 才是 `*`），避免前端闪出半截 `**`。
"""
import re

# 行尾可能是「还没写完的标记」的字符：等下一个 chunk 再决定
_TAIL_MARKER = re.compile(r"[*_`#>\-+~|]+$")

# 独立成行的分隔线：--- / *** / ___ （3 个以上）
_HR_LINE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")

# 代码围栏
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")

# 句中用于停顿的 `--` / `---`（两侧是中文），模型想写破折号却写成了减号
_MID_DASH = re.compile(r"(?<=[\u4e00-\u9fff])\s*-{2,}\s*(?=[\u4e00-\u9fff])")

# 空加粗：** ** / ****
_EMPTY_BOLD = re.compile(r"\*{2}\s*\*{2,}")

# 标题井号后缺空格：##标题 -> ## 标题
_HEADING_TIGHT = re.compile(r"^(#{1,6})(?=[^\s#])")

# 星号/圆点弹珠统一成 Markdown 无序列表
_BULLET = re.compile(r"^(\s*)[•·▪●○]\s*")
_STAR_BULLET = re.compile(r"^(\s*)\*\s+(?=\S)")

# 多个空行压成一个
_BLANK_RUN = re.compile(r"\n{3,}")

# 零宽字符 / BOM
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)


def _fix_bold_balance(line: str) -> str:
    """单行内 `**` 个数为奇数时，去掉最后一个孤立的 `**`。

    模型常在列表项里只写开头忘了结尾，不处理的话 react-markdown 会把
    `**` 原样显示出来，这正是用户反馈的「多余标点」。
    """
    if line.count("**") % 2 == 0:
        return line
    idx = line.rfind("**")
    return line[:idx] + line[idx + 2:]


def sanitize_markdown(text: str) -> str:
    """把模型输出修成干净、可渲染的 Markdown。"""
    if not text:
        return ""
    s = text.replace("\r\n", "\n").replace("\r", "\n").translate(_INVISIBLE)
    s = s.replace("\u3000", " ")

    out = []
    in_fence = False
    for raw in s.split("\n"):
        line = raw.rstrip()

        fence = _FENCE.match(line)
        if fence:
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(raw)
            continue

        if _HR_LINE.match(line):
            out.append("---")
            continue

        line = _EMPTY_BOLD.sub("", line)
        line = _HEADING_TIGHT.sub(r"\1 ", line)
        line = _BULLET.sub(r"\1- ", line)
        line = _STAR_BULLET.sub(r"\1- ", line)
        line = _MID_DASH.sub("——", line)
        line = _fix_bold_balance(line)

        # 只剩标记、没有内容的行直接丢掉
        if line.strip() and not re.search(r"[^\s*_`#>\-+~|=]", line):
            continue
        out.append(line)

    return _BLANK_RUN.sub("\n\n", "\n".join(out)).strip()


class MarkdownStreamFilter:
    """流式增量清洗：扣住行尾未完成的标记，其余立刻放行。

    只做「尾部延迟」，不改动已确认的内容，因此不会造成整段延迟。
    """

    def __init__(self) -> None:
        self._pending = ""

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        self._pending += chunk
        match = _TAIL_MARKER.search(self._pending)
        if not match:
            out, self._pending = self._pending, ""
            return out
        # 整段都是标记 → 继续等；否则把标记前的正文放出去
        cut = match.start()
        out, self._pending = self._pending[:cut], self._pending[cut:]
        # 标记已经长到不可能是合法前缀（例如 `****`），直接放行避免卡住
        if len(self._pending) > 4:
            out, self._pending = out + self._pending, ""
        return out

    def flush(self) -> str:
        tail, self._pending = self._pending, ""
        return tail


def strip_inline_markers(text: str) -> str:
    """给「一行一条」的短文本用（仪表盘推荐、计划反馈）。

    这类场景不渲染 Markdown，只需要把标记去掉。
    """
    if not text:
        return ""
    s = sanitize_markdown(text)
    s = re.sub(r"^[#>\-\s]+", "", s)
    s = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", s)
    s = s.replace("**", "").replace("`", "")
    s = re.sub(r"\s*-{2,}\s*", "——", s)
    return s.strip()
