import json
import unittest
from types import SimpleNamespace

from engines import OpenAICompatEngine
from models import BrandProfile
from question_gen import (
    QUESTION_GENERATOR_SYSTEM_PROMPT,
    build_question_generation_prompt,
    generate_questions,
    generate_questions_with_ai,
    validate_generated_questions,
)


class FakeQuestionEngine:
    def __init__(self):
        self.system_prompt = ""
        self.prompt = ""

    def ask(self, prompt):
        self.prompt = prompt
        payload = json.loads(prompt.split("\n", 1)[1])
        return json.dumps(
            {
                "questions": [
                    {"qid": item["qid"], "question": item["seed_question"]}
                    for item in payload["intents"]
                ]
            },
            ensure_ascii=False,
        )


class FakeCompletions:
    def __init__(self):
        self.messages = None

    def create(self, **kwargs):
        self.messages = kwargs["messages"]
        message = SimpleNamespace(content="回答")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class QuestionGenerationTests(unittest.TestCase):
    def setUp(self):
        self.profile = BrandProfile(
            brand_name="超意兴",
            aliases=["超意兴快餐"],
            category="中式快餐",
            region="山东",
            city="济南",
            competitors=["老济南把子肉"],
            facts=["FACT_SENTINEL_1993", "FACT_SENTINEL_SECRET"],
        )

    def test_prompt_excludes_facts_aliases_and_sources(self):
        seeds = generate_questions(self.profile)
        prompt = build_question_generation_prompt(self.profile, seeds)
        self.assertNotIn("FACT_SENTINEL", prompt)
        self.assertNotIn("超意兴快餐", prompt)
        self.assertNotIn('"facts"', prompt)

    def test_ai_generation_keeps_qids_and_brand_ratio(self):
        engine = FakeQuestionEngine()
        questions = generate_questions_with_ai(self.profile, engine)
        self.assertEqual(len(questions), 40)
        self.assertEqual([q.qid for q in questions], [f"Q{i:02d}" for i in range(1, 41)])
        branded = [q for q in questions if "超意兴" in q.text]
        self.assertEqual(len(branded), 20)
        self.assertEqual(engine.system_prompt, QUESTION_GENERATOR_SYSTEM_PROMPT)

    def test_rule_validator_rejects_injected_fact(self):
        seeds = generate_questions(self.profile)
        payload = {"questions": [{"qid": q.qid, "question": q.text} for q in seeds]}
        payload["questions"][0]["question"] = "超意兴创立于1993年，靠谱吗？"
        with self.assertRaises(ValueError):
            validate_generated_questions(self.profile, seeds, payload)

    def test_answer_request_has_no_default_system_prompt(self):
        engine = OpenAICompatEngine("https://example.invalid", "test-key", "test-model")
        fake = FakeCompletions()
        engine.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
        self.assertEqual(engine._request("济南哪家快餐值得推荐？"), "回答")
        self.assertEqual(
            fake.messages,
            [{"role": "user", "content": "济南哪家快餐值得推荐？"}],
        )

    def test_parser_engine_still_accepts_system_prompt(self):
        engine = OpenAICompatEngine(
            "https://example.invalid",
            "test-key",
            "test-model",
            system_prompt="解析规则",
        )
        fake = FakeCompletions()
        engine.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
        engine._request("解析这条回答")
        self.assertEqual(fake.messages[0], {"role": "system", "content": "解析规则"})


if __name__ == "__main__":
    unittest.main()
