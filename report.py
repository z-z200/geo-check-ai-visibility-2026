"""CSV 导出与离线 HTML 体检报告。"""
from __future__ import annotations
from html import escape
import pandas as pd
from models import BrandProfile
__all__ = ["export_metrics_csv", "export_suggestions_csv", "render_html_report"]

_SUGGESTION_COLUMNS = ("gap", "platform", "content_type", "must_cover_facts", "priority", "expected_metric")
_MOCK_KEYS = {"is_mock", "mock", "is_simulated", "simulated", "demo", "is_demo", "data_mode"}
_MOCK_BADGE = ('<span style="display:inline-block;padding:3px 9px;border-radius:999px;'
               'background:#fff3cd;color:#8a5a00;font-size:12px;font-weight:700;">模拟数据</span>')

def _csv_bytes(frame: pd.DataFrame) -> bytes:
    """to_csv 输出文本，再显式编码为带 BOM 的 UTF-8。"""
    return frame.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

def export_metrics_csv(metrics: dict) -> bytes:
    """把指标字典导出为单行 CSV。"""
    return _csv_bytes(pd.DataFrame([dict(metrics or {})]))

def export_suggestions_csv(suggestions: list[dict]) -> bytes:
    """把补位建议导出为 CSV；无建议时仍保留固定表头。"""
    items = list(suggestions or [])
    frame = pd.DataFrame(items) if items else pd.DataFrame(columns=_SUGGESTION_COLUMNS)
    return _csv_bytes(frame)


def _num(value, default: float = 0.0) -> float:
    """容错读取数值，并过滤布尔值、NaN 和无穷值。"""
    if isinstance(value, bool):
        return default
    try:
        result = float(str(value).strip().removesuffix("%"))
    except (TypeError, ValueError):
        return default
    return result if result == result and abs(result) != float("inf") else default


def _rate(value) -> float:
    """把 0-1 或 0-100 的比例统一到 0-1。"""
    result = _num(value)
    if "%" in str(value) or result > 1:
        result /= 100
    return max(0.0, min(1.0, result))


def _pct(value) -> str:
    return f"{_rate(value) * 100:.0f}%"


def _mock_text(value) -> bool:
    text = str(value).casefold()
    return "mock" in text or "模拟" in text


def _mock_marker(value) -> bool:
    """识别指标或建议中显式的模拟数据标记。"""
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).casefold()
            if key_text in {"by_engine", "engines"} and isinstance(item, dict):
                if any(_mock_text(engine) for engine in item):
                    return True
            if key_text in _MOCK_KEYS and item not in (None, False, "", 0):
                return True
            if key_text in {"mode", "engine", "source", "data_source"} and _mock_text(item):
                return True
            if _mock_marker(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_mock_marker(item) for item in value)
    return False


def _plain(value) -> str:
    if isinstance(value, (list, tuple)):
        return "；".join(str(item) for item in value)
    if isinstance(value, dict):
        return "；".join(f"{key}: {item}" for key, item in value.items())
    return "" if value is None else str(value)


def _total_score(metrics: dict) -> float:
    """优先使用已有总分，否则按五个指标等权折算为 100 分制。"""
    explicit = metrics.get("total_score", metrics.get("overall_score"))
    if explicit is not None:
        score = _num(explicit)
        return round(score * 100 if 0 < abs(score) <= 1 else score, 1)
    position = metrics.get("avg_position")
    position_score = 0.0 if position in (None, "") else max(0.0, min(1.0, 1 - (_num(position) - 1) / 4))
    sentiment_score = max(0.0, min(1.0, (_num(metrics.get("sentiment_score")) + 1) / 2))
    values = (_rate(metrics.get("mention_rate")), _rate(metrics.get("top1_rate")),
              position_score, _rate(metrics.get("citation_rate")), sentiment_score)
    return round(sum(values) / len(values) * 100, 1)


def render_html_report(profile: BrandProfile, metrics: dict, gaps: list[dict],
                       suggestions: list[dict]) -> str:
    """生成不依赖外部资源的单页 HTML 报告。"""
    metrics, gaps, suggestions = metrics or {}, gaps or [], suggestions or []
    brand = escape(str(profile.get("brand_name", "") if isinstance(profile, dict)
                       else getattr(profile, "brand_name", "")) or "未命名品牌")
    mocked = _mock_marker(metrics) or _mock_marker(suggestions)
    score, badge = _total_score(metrics), _MOCK_BADGE if mocked else ""
    cards = (
        ("品牌提及率", _pct(metrics.get("mention_rate")), "目标品牌出现在回答中的比例"),
        ("首位提及率", _pct(metrics.get("top1_rate")), "品牌进入推荐首位的比例"),
        ("平均提及位次", "暂无" if metrics.get("avg_position") in (None, "", 0) else f"第 {_num(metrics.get('avg_position')):g} 位", "明确提及时的平均排序"),
        ("来源引用率", _pct(metrics.get("citation_rate")), "回答包含可点击来源链接的比例"),
        ("情感得分", f"{_num(metrics.get('sentiment_score')):+.2f}", "负面 -1 至正面 +1"),
    )
    card_html = "".join(
        f'<div style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:16px;">'
        f'<div style="color:#6b7280;font-size:13px;">{label}</div>'
        f'<div style="font-size:26px;font-weight:750;margin:8px 0;color:#111827;">{value}</div>'
        f'<div style="color:#9ca3af;font-size:12px;line-height:1.45;">{note}</div></div>'
        for label, value, note in cards)
    engines = metrics.get("by_engine") if isinstance(metrics.get("by_engine"), dict) else {}
    engine_rows = "".join(
        f'<tr><td style="padding:10px;border-bottom:1px solid #eee;">{escape(str(engine))}{_MOCK_BADGE if _mock_text(engine) else ""}</td>'
        f'<td style="padding:10px;border-bottom:1px solid #eee;font-weight:700;">{_pct(rate)}</td></tr>'
        for engine, rate in engines.items()) or (
        '<tr><td colspan="2" style="padding:14px;color:#9ca3af;">暂无引擎数据</td></tr>')
    suggestion_rows = "".join(
        f'<tr><td style="padding:10px;border-bottom:1px solid #eee;text-align:center;">{escape(_plain(item.get("priority")))}</td>'
        f'<td style="padding:10px;border-bottom:1px solid #eee;">{escape(_plain(item.get("gap")))}</td>'
        f'<td style="padding:10px;border-bottom:1px solid #eee;">{escape(_plain(item.get("platform")))}</td>'
        f'<td style="padding:10px;border-bottom:1px solid #eee;">{escape(_plain(item.get("content_type")))}</td>'
        f'<td style="padding:10px;border-bottom:1px solid #eee;">{escape(_plain(item.get("must_cover_facts")))}</td>'
        f'<td style="padding:10px;border-bottom:1px solid #eee;">{escape(_plain(item.get("expected_metric")))}</td></tr>'
        for item in suggestions if isinstance(item, dict)) or (
        '<tr><td colspan="6" style="padding:14px;color:#9ca3af;">暂无补位建议</td></tr>')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{brand} AI 可见度体检报告</title></head>
<body style="margin:0;background:#f3f4f6;color:#111827;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;">
<main style="max-width:1080px;margin:0 auto;padding:32px 20px;">
<header style="background:linear-gradient(135deg,#111827,#374151);color:#fff;border-radius:16px;padding:28px 30px;display:flex;justify-content:space-between;gap:24px;align-items:center;">
<div><div style="font-size:13px;letter-spacing:1px;color:#d1d5db;">齐鲁品牌 AI 可见度体检</div>
<h1 style="margin:8px 0 6px;font-size:30px;">{brand}</h1>
<div style="color:#d1d5db;font-size:13px;">有效回答 {escape(str(metrics.get("n_records", 0)))} 条 · 发现 {len(gaps)} 个信源缺口 {badge}</div></div>
<div style="min-width:105px;text-align:center;background:rgba(255,255,255,.1);border-radius:14px;padding:16px;"><div style="font-size:12px;color:#d1d5db;">总分</div>
<div style="font-size:38px;font-weight:800;">{score:.1f}</div><div style="font-size:12px;color:#d1d5db;">/ 100</div></div></header>
<section style="margin-top:18px;display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;">{card_html}</section>
<section style="margin-top:22px;display:grid;grid-template-columns:minmax(240px,.75fr) minmax(0,1.6fr);gap:18px;">
<div style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:18px;"><h2 style="font-size:17px;margin:0 0 10px;">按引擎的提及率 {badge}</h2>
<table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr><th style="text-align:left;padding:8px;color:#6b7280;">引擎</th><th style="text-align:left;padding:8px;color:#6b7280;">提及率</th></tr></thead><tbody>{engine_rows}</tbody></table></div>
<div style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:18px;overflow:auto;"><h2 style="font-size:17px;margin:0 0 10px;">补位建议 ({len(suggestions)}) {badge}</h2>
<table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr><th style="padding:8px;color:#6b7280;">优先级</th><th style="text-align:left;padding:8px;color:#6b7280;">缺口</th><th style="text-align:left;padding:8px;color:#6b7280;">平台</th><th style="text-align:left;padding:8px;color:#6b7280;">内容类型</th><th style="text-align:left;padding:8px;color:#6b7280;">必须覆盖事实</th><th style="text-align:left;padding:8px;color:#6b7280;">预期指标</th></tr></thead><tbody>{suggestion_rows}</tbody></table></div></section>
<footer style="padding:18px 4px;color:#9ca3af;font-size:12px;">本报告由本地规则生成，仅用于品牌 AI 可见度自查。</footer></main></body></html>'''
