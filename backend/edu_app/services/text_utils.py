"""中文分词：优先 jieba，缺失时退化为字 + 双字组合。

BM25 依赖分词质量。jieba 未安装时如果直接抛 ImportError，检索会整体 500，
因此这里必须自带兜底，保证知识库在最小环境下仍可检索。

虚词必须过滤掉：「怎样才能让我的履历更容易被挑中」里的「怎样/才能/让/我/的/更/被」
对区分文档毫无贡献，却会让 BM25 累积出一个和主题无关的高分，把语义排序顶翻。
"""
import re

# 中文功能词/疑问词/程度副词：只贡献噪声，不贡献主题
_STOP = set(
    "的了和是在有就不都一也这那与及等"
    "我你他她它们咱您"
    "怎样怎么如何什么为什么哪些哪个多少是否能否可以能否"
    "才能可以可能应该需要想要希望打算准备"
    "让把被给对从向往于因为所以但是如果虽然然后而且并且"
    "更最很太非常比较稍微更加特别十分相当"
    "的了吗呢吧啊呀哦嗯着过地得"
    "一个一些一种一样一起一下一定"
    "中上下来去出去进来回来"
)

# 兜底模式下双字组合里同样需要剔除的高频噪声
_NOISE_PAIRS = {"怎样", "怎么", "如何", "什么", "可以", "才能", "容易", "我的", "我们"}


def _fallback_tokens(text: str) -> list[str]:
    """中文按「单字 + 相邻双字」切，英文数字按单词切。

    双字组合能覆盖大部分中文词，虽不如 jieba 精准，但足以支撑 BM25 排序。
    """
    text = (text or "").lower()
    tokens: list[str] = []
    for piece in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text):
        if piece[0].isascii():
            tokens.append(piece)
            continue
        if len(piece) == 1:
            tokens.append(piece)
            continue
        tokens.extend(piece[i:i + 2] for i in range(len(piece) - 1))
        tokens.extend(piece[i] for i in range(len(piece)))
    return [t for t in tokens if t.strip() and t not in _STOP and t not in _NOISE_PAIRS]


def tokenize(text: str) -> list[str]:
    try:
        import jieba
    except ImportError:
        return _fallback_tokens(text)
    return [
        t for t in jieba.lcut((text or "").lower())
        if t.strip() and t not in _STOP
    ]
