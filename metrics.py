"""回答记录指标汇总：为市场部提供品牌提及、占位、引用和口碑指标。"""

import pandas as pd

from models import AnswerRecord, BrandProfile


def _record_category(record: AnswerRecord, profile: BrandProfile) -> str:
    """优先使用可关联到问题记录的类别，缺失时回退到品牌品类。"""
    category = getattr(record, "category", "") or getattr(record, "question_category", "")
    return str(category or profile.category or "未分类")


def _mention_rates(frame: pd.DataFrame, column: str) -> dict[str, float]:
    """按指定维度计算提及比例，并统一保留两位小数。"""
    rates = frame.groupby(column, dropna=False)["mentioned"].mean()
    return {str(key): round(float(value), 2) for key, value in rates.items()}


def compute_metrics(records: list[AnswerRecord], profile: BrandProfile) -> dict:
    """汇总全部回答记录；空数据返回结构完整且可安全展示的零值结果。"""
    if not records:
        return {"mention_rate": 0.0, "top1_rate": 0.0, "avg_position": 0,
                "citation_rate": 0.0, "sentiment_score": 0.0,
                "by_engine": {}, "by_category": {}, "n_records": 0}

    # 先转 DataFrame，后续所有汇总都基于同一份数据，避免口径漂移。
    rows = [
        {
            "engine": record.engine or "未知引擎",
            "category": _record_category(record, profile),
            "mentioned": bool(record.mentioned),
            "position": record.position,
            "source_mode": record.source_mode,
            "sentiment": record.sentiment,
        }
        for record in records
    ]
    frame = pd.DataFrame(rows)
    frame["position"] = pd.to_numeric(frame["position"], errors="coerce")
    total = len(frame)

    # 提及、榜首和引用均以全部回答数为分母。
    mention_rate = round(float(frame["mentioned"].mean()), 2)
    top1_rate = round(float((frame["position"] == 1).mean()), 2)
    citation_rate = round(float((frame["source_mode"] == "带链接").mean()), 2)

    # 平均位次只统计明确提及且位置为正数的记录。
    valid_positions = frame.loc[frame["mentioned"] & frame["position"].gt(0), "position"]
    avg_position = round(float(valid_positions.mean()), 2) if not valid_positions.empty else None

    # 正面记 1 分、负面记 -1 分、中性记 0 分，再除以全部回答数。
    sentiment_values = frame["sentiment"].map({"正": 1, "中": 0, "负": -1}).fillna(0)
    sentiment_score = round(float(sentiment_values.mean()), 2)

    return {
        "mention_rate": mention_rate,
        "top1_rate": top1_rate,
        "avg_position": avg_position,
        "citation_rate": citation_rate,
        "sentiment_score": sentiment_score,
        "by_engine": _mention_rates(frame, "engine"),
        "by_category": _mention_rates(frame, "category"),
        "n_records": total,
    }


def _demo() -> None:
    """用 10 条假数据展示指标结果，并覆盖不同引擎、类别和情感。"""
    engines = ["DeepSeek"] * 5 + ["豆包"] * 5
    categories = ["推荐", "推荐", "推荐", "对比", "对比", "推荐", "推荐", "对比", "对比", "地域"]
    mentioned = [True, True, False, True, False, True, False, True, False, True]
    positions = [1, 2, None, 3, None, 1, None, 2, None, 4]
    sources = ["带链接", "无来源", "仅文字提及", "带链接", "无来源", "仅文字提及", "带链接", "无来源", "无来源", "仅文字提及"]
    sentiments = ["正", "中", "负", "正", "中", "中", "中", "负", "正", "中"]
    records = []
    for index in range(10):
        record = AnswerRecord(
            record_id=f"R{index + 1:02d}", qid=f"Q{index + 1:02d}", engine=engines[index],
            round=1, question_text="示例问题", answer_text="示例回答", mentioned=mentioned[index],
            position=positions[index], source_mode=sources[index], sentiment=sentiments[index],
        )
        record.category = categories[index]  # 演示记录关联的问题类别
        records.append(record)
    print(compute_metrics(records, BrandProfile(brand_name="示例品牌", category="茶饮")))


if __name__ == "__main__":
    _demo()