"""规则型补位建议：根据信源缺口或最弱指标生成可执行内容方案。"""
from models import BrandProfile

__all__ = ["recommend"]

_COST = {"问答": 0.6, "评测": 0.8, "百科": 1.0, "长文": 1.2, "短视频脚本": 1.5}
_INFO = {"mention_rate": ("品牌提及可见度", "品牌提及率", "知乎"),
         "top1_rate": ("首位占位", "首位提及率", "百度"),
         "citation_rate": ("权威信源引用", "引用率", "知乎"),
         "sentiment_score": ("品牌口碑", "情感得分", "小红书"),
         "avg_position": ("推荐排序占位", "平均提及位次", "百度")}
_LEVELS = {"高": 1.0, "high": 1.0, "严重": 1.0, "critical": 1.0,
           "中": 0.7, "medium": 0.7, "一般": 0.7, "低": 0.4, "low": 0.4, "轻微": 0.4}

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

def _num(value, default: float = 0.0) -> float:
    if isinstance(value, bool): return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result and abs(result) != float("inf") else default

def _rate(value) -> float:
    result = _num(value)
    return _clamp(result / 100 if result > 1 else result, 0.0, 1.0)

def _degree(gap: dict) -> float:
    value = next((gap.get(key) for key in
                  ("gap_degree", "degree", "severity", "gap_level", "weight", "importance")
                  if gap.get(key) is not None), None)
    if isinstance(value, str) and value.strip().lower() in _LEVELS:
        return _LEVELS[value.strip().lower()]
    degree = _num(value, 0.8)
    return _clamp(degree / 5 if degree > 1 else degree, 0.1, 1.0)

def _priority(count: float, degree: float, cost: float) -> int:
    """引用频次 × 缺口程度 ÷ 预估成本，再线性映射到 1-5。"""
    raw = max(count, 1.0) * degree / max(cost, 0.1)
    return int(round(1 + 4 * min(raw / 4, 1.0)))

def _content(platform: str, gap_text: str, index: int) -> str:
    text = f"{platform}{gap_text}".lower()
    rules = (("百科", ("百科", "baike", "baidu")), ("评测", ("小红书", "xiaohongshu")),
             ("短视频脚本", ("b站", "bilibili", "视频", "抖音", "douyin")),
             ("长文", ("微信", "公众号", "weixin")), ("问答", ("知乎", "问答", "zhihu")))
    for content_type, words in rules:
        if any(word in text for word in words):
            return content_type
    return ("问答", "评测", "长文")[index % 3]

def _facts(source: list[str], offset: int) -> list[str]:
    """从原始事实库轮换取 2-4 条，确保不引入外部事实。"""
    clean = list(dict.fromkeys(fact for fact in source
                               if isinstance(fact, str) and fact.strip()))
    if len(clean) < 2:
        return []
    count = min(3, len(clean))
    return [clean[(offset + i) % len(clean)] for i in range(count)]

def _gap_text(gap: dict, profile: BrandProfile) -> str:
    dimension = next((str(gap.get(key)).strip() for key in
                      ("dimension", "category", "topic", "question_category")
                      if gap.get(key)), "")
    if dimension:
        return f"AI 对“{dimension}”维度说清不足，需补足可验证的品牌信息"
    platform = str(gap.get("platform") or "目标平台")
    return f"AI 对“{profile.category or '品牌'}权威信源引用”维度说清不足：{platform} 上缺少可引用内容"

def _expected(gap: dict) -> str:
    explicit = gap.get("expected_metric") or gap.get("metric")
    if explicit:
        return str(explicit)
    text = " ".join(str(gap.get(key, "")) for key in
                    ("dimension", "category", "topic", "gap_reason")).lower()
    rules = (("情感得分", ("情感", "口碑", "sentiment")),
             ("平均提及位次", ("首位", "top1", "排序", "位次", "position")),
             ("品牌提及率", ("提及", "mention", "可见")))
    return next((metric for metric, words in rules if any(word in text for word in words)), "引用率")

def _weakest(metrics: dict) -> tuple[str, float]:
    """把不同量纲指标转为“越弱越高”的分数，取最高者。"""
    scores = {"mention_rate": 1 - _rate(metrics.get("mention_rate")),
              "top1_rate": 1 - _rate(metrics.get("top1_rate")),
              "citation_rate": 1 - _rate(metrics.get("citation_rate")),
              "sentiment_score": 1 - _clamp((_num(metrics.get("sentiment_score")) + 1) / 2, 0, 1)}
    if metrics.get("avg_position") is not None:
        scores["avg_position"] = _clamp((_num(metrics.get("avg_position"), 1) - 1) / 4, 0, 1)
    key = max(scores, key=scores.get)
    return key, scores[key]

def _fact_check(suggestion: dict, profile: BrandProfile) -> bool:
    """生成后二次核验：删除非 profile.facts 原文，空则整条作废。"""
    allowed = {fact for fact in profile.facts if isinstance(fact, str)}
    suggestion["must_cover_facts"] = [f for f in suggestion["must_cover_facts"] if f in allowed]
    return bool(suggestion["must_cover_facts"])

def recommend(profile: BrandProfile, metrics: dict, gaps: list[dict]) -> list[dict]:
    """规则生成补位建议；最多 8 条，不调用任何大模型。"""
    if not _facts(profile.facts, 0):
        return []
    suggestions = []
    for index, gap in enumerate(gaps or [None]):
        if gap is None:
            key, weakness = _weakest(metrics)
            dimension, expected, platform = _INFO[key]
            value = metrics.get(key)
            shown = "暂无有效提及" if value is None else f"{_num(value):g}"
            gap_text = f"AI 对“{dimension}”维度说清不足：当前{expected}为 {shown}"
            count, degree = max(1, round(weakness * 5)), 1.0
        else:
            gap = gap if isinstance(gap, dict) else {}
            platform = str(gap.get("platform") or "目标平台")
            gap_text = _gap_text(gap, profile)
            expected = _expected(gap)
            count = _num(gap.get("count", gap.get("frequency", gap.get("citation_count"))), 1)
            degree = _degree(gap)
        content_type = _content(platform, gap_text, index)
        suggestion = {
            "gap": gap_text, "platform": platform, "content_type": content_type,
            "must_cover_facts": _facts(profile.facts, index),
            "priority": _priority(count, degree, _COST[content_type]),
            "expected_metric": expected,
        }
        if _fact_check(suggestion, profile):
            suggestions.append(suggestion)
    suggestions.sort(key=lambda item: item["priority"], reverse=True)
    return suggestions[:8]