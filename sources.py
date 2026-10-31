"""信源归因与占位缺口：统计 AI 回答引用域名，并找出高频但品牌尚未覆盖的信源。"""

from statistics import median
from urllib.parse import urlsplit

from models import AnswerRecord

__all__ = ["attribute_sources", "find_gaps"]

_PLATFORM_BY_DOMAIN = {
    "baidu.com": "百度",
    "zhihu.com": "知乎",
    "xiaohongshu.com": "小红书",
    "weixin.qq.com": "微信",
    "bilibili.com": "B站",
}


def _normalize_domain(value: str) -> str:
    """把域名或 URL 统一为小写、无 www. 前缀且无路径的主机名。"""
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip().lower()
    candidate = text if "://" in text or text.startswith("//") else f"//{text}"
    try:
        host = urlsplit(candidate).hostname or ""
    except ValueError:
        return ""
    return host.rstrip(".").removeprefix("www.")


def _platform_name(domain: str) -> str:
    """把已知域名及其子域名映射为平台名称，未知域名保留原域名。"""
    if domain in _PLATFORM_BY_DOMAIN:
        return _PLATFORM_BY_DOMAIN[domain]
    for known_domain, platform in _PLATFORM_BY_DOMAIN.items():
        if domain.endswith(f".{known_domain}"):
            return platform
    return domain


def _median_text(value: int | float) -> str:
    """中位数可能是整数或 .5，去掉无意义的尾随零。"""
    return f"{value:g}"


def attribute_sources(records: list[AnswerRecord]) -> dict:
    """按域名汇总全部回答中的引用次数，并附带平台名称。"""
    stats: dict[str, dict] = {}
    for record in records:
        for raw_domain in getattr(record, "source_domains", []) or []:
            domain = _normalize_domain(raw_domain)
            if not domain:
                continue
            if domain not in stats:
                stats[domain] = {"platform": _platform_name(domain), "count": 0}
            stats[domain]["count"] += 1
    return stats


def find_gaps(source_stats: dict, covered_domains: list[str]) -> list[dict]:
    """筛出引用频次不低于中位数、且未被品牌覆盖的域名，按频次降序返回。"""
    if not source_stats:
        return []
    counts = [item["count"] for item in source_stats.values() if isinstance(item, dict)]
    if not counts:
        return []
    threshold = median(counts)
    covered = {_normalize_domain(domain) for domain in covered_domains}
    gaps = []
    for raw_domain, item in source_stats.items():
        domain = _normalize_domain(raw_domain)
        if not domain or domain in covered or not isinstance(item, dict):
            continue
        count = item.get("count", 0)
        if isinstance(count, (int, float)) and not isinstance(count, bool) and count >= threshold:
            gaps.append({
                "platform": item.get("platform") or _platform_name(domain),
                "domain": domain,
                "count": count,
                "gap_reason": f"引用频次 {count:g} 不低于中位数 {_median_text(threshold)}，但品牌尚未覆盖",
            })
    return sorted(gaps, key=lambda gap: (-gap["count"], gap["domain"]))