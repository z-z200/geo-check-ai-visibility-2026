"""阶段五核心链路的最小回归测试，不依赖 API。"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis import analyze_records  # noqa: E402
from models import AnswerRecord, BrandProfile  # noqa: E402
from parser import parse_rule_track  # noqa: E402
from question_gen import generate_questions  # noqa: E402
from report import render_html_report  # noqa: E402


def make_record(record_id: str, qid: str, answer: str) -> AnswerRecord:
    return AnswerRecord(
        record_id=record_id,
        qid=qid,
        engine="mock",
        round=1,
        question_text="test question",
        answer_text=answer,
    )


class StageFiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = BrandProfile(
            brand_name="Alpha",
            aliases=["AlphaTea"],
            category="tea",
            competitors=["Beta"],
            facts=["fact one", "fact two"],
        )

    def test_rule_track_parses_listing_and_source(self) -> None:
        record = make_record(
            "R1",
            "Q1",
            "1. Beta-brand introduction\n2. Alpha comes from Shandong\nhttps://www.zhihu.com/question/1",
        )
        parsed = parse_rule_track(record, self.profile)
        self.assertTrue(parsed.mentioned)
        self.assertEqual(parsed.position, 2)
        self.assertEqual(parsed.mention_type, "清单内")
        self.assertEqual(parsed.source_mode, "带链接")
        self.assertEqual(parsed.source_domains, ["zhihu.com"])

    def test_empty_answer_is_kept_and_marked_for_review(self) -> None:
        record = make_record("R1", "Q1", "")
        result = analyze_records([record], self.profile, parse_mode="rule")
        self.assertEqual(result["metrics"]["n_records"], 1)
        self.assertTrue(result["records"][0].needs_review)
        self.assertFalse(result["records"][0].mentioned)

    def test_api_error_is_excluded_from_metrics(self) -> None:
        record = make_record("R1", "Q1", "[ERROR] HTTP 401: invalid key")
        result = analyze_records([record], self.profile, parse_mode="rule")
        self.assertEqual(result["metrics"]["n_records"], 0)
        self.assertEqual(result["metrics"]["failed_records"], 1)
        self.assertTrue(result["records"][0].needs_review)

    def test_demo_bundle_builds_metrics_sources_and_suggestions(self) -> None:
        payload = json.loads((ROOT / "data/demo_results.json").read_text(encoding="utf-8"))
        profile = BrandProfile(**payload["profile"])
        questions = generate_questions(profile)
        records = [
            AnswerRecord(**{key: value for key, value in row.items() if key in AnswerRecord.__dataclass_fields__})
            for row in payload["records"]
        ]
        result = analyze_records(
            records,
            profile,
            questions=questions,
            parse_mode="stored",
            covered_domains=payload["covered_domains"],
            is_mock=True,
        )
        self.assertEqual(result["metrics"]["n_records"], 120)
        self.assertEqual(result["metrics"]["failed_records"], 0)
        self.assertTrue(result["source_stats"])
        self.assertTrue(result["suggestions"])
        html = render_html_report(profile, result["metrics"], result["gaps"], result["suggestions"])
        self.assertIn("fff3cd", html)  # 模拟数据角标


if __name__ == "__main__":
    unittest.main()