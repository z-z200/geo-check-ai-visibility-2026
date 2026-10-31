"""体检分析编排：把采集、解析、指标、信源和补位建议串成一条链。"""

from dataclasses import replace
from typing import Callable

from metrics import compute_metrics
from models import AnswerRecord, BrandProfile, Question
from parser import merge_tracks, parse_answer, parse_rule_track, rule_check
from sources import attribute_sources, find_gaps
from suggest import recommend

__all__ = ["analyze_records"]

_PARSE_MODES = {"stored", "rule", "dual"}


def _is_api_error(record: AnswerRecord) -> bool:
    """仅把调用层写入的错误记录排除出有效样本，空回答仍计入。"""
    return record.answer_text.lstrip().startswith("[ERROR]")


def _category_map(questions: list[Question] | None) -> dict[str, str]:
    """建立 qid 到问题类别的映射，供按类别统计使用。"""
    return {question.qid: question.category for question in questions or []}


def _parse_one(record: AnswerRecord, profile: BrandProfile, parse_mode: str,
               parser_engine=None) -> AnswerRecord:
    """按指定模式解析单条记录；stored 模式保留已有解析结果。"""
    if parse_mode == "stored":
        return record
    if parse_mode == "rule":
        return parse_rule_track(record, profile)
    model_record = parse_answer(record, profile, engine=parser_engine)
    return merge_tracks(model_record, rule_check(record, profile))


def analyze_records(
    records: list[AnswerRecord],
    profile: BrandProfile,
    questions: list[Question] | None = None,
    parse_mode: str = "stored",
    parser_engine=None,
    covered_domains: list[str] | None = None,
    is_mock: bool = False,
    progress_callback: Callable[[int, int, AnswerRecord], None] | None = None,
) -> dict:
    """分析一批回答，返回记录、指标、信源缺口与补位建议。

    parse_mode:
    - stored：直接使用记录中已有的解析字段，适合加载离线演示结果；
    - rule：只用本地规则，适合 Mock 和没有 API 的演示；
    - dual：模型轨加规则轨，适合已配置 API 的真实体检。
    """
    normalized_mode = str(parse_mode).strip().lower()
    if normalized_mode not in _PARSE_MODES:
        raise ValueError(f"不支持的解析模式：{parse_mode!r}")

    categories = _category_map(questions)
    total = len(records)
    analyzed: list[AnswerRecord] = []
    for index, record in enumerate(records, start=1):
        if _is_api_error(record):
            parsed = replace(record, needs_review=True)
        else:
            parsed = _parse_one(record, profile, normalized_mode, parser_engine)
        # AnswerRecord 不持久化问题类别；运行时挂载即可供 metrics 分组。
        setattr(parsed, "category", categories.get(parsed.qid, profile.category or "未分类"))
        analyzed.append(parsed)
        if progress_callback is not None:
            progress_callback(index, total, parsed)

    valid_records = [record for record in analyzed if not _is_api_error(record)]
    metrics = compute_metrics(valid_records, profile)
    metrics["is_mock"] = bool(is_mock)
    metrics["failed_records"] = len(analyzed) - len(valid_records)
    source_stats = attribute_sources(valid_records)
    gaps = find_gaps(source_stats, covered_domains or [])
    suggestions = recommend(profile, metrics, gaps)

    return {
        "records": analyzed,
        "valid_records": valid_records,
        "metrics": metrics,
        "source_stats": source_stats,
        "gaps": gaps,
        "suggestions": suggestions,
        "parse_mode": normalized_mode,
        "is_mock": bool(is_mock),
    }