"""模板式问题生成器。

设计要点：
1. 40 条问题按 7 类固定配比生成，全部来自模板，可复现、可审校。
2. 其中 20 条包含品牌名（测"用户点名后 AI 怎么说"），
   20 条不含品牌名（测"用户泛问时 AI 会不会主动想到你"）。
   后 20 条才是"AI 可见度"的核心测量对象。
3. 生成结果可落 CSV，运营同学能用 Excel 审校，历史版本也便于对比。
"""

import csv
import json
import re
from pathlib import Path

from models import BrandProfile, Question

# 含品牌名的类别：品牌词 6 + 竞品对比词 6 + 决策长尾词 5 + 风险词 3 = 20
# 不含品牌名的类别：品类词 6 + 场景需求词 8 + 地域词 6 = 20
_TEMPLATE_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("品牌词", (
        "{brand}这个牌子怎么样",
        "{brand}到底靠不靠谱",
        "有人用过{brand}吗，体验如何",
        "{brand}是什么来头",
        "想了解{brand}，值得关注吗",
        "{brand}在同类里口碑怎么样",
    )),
    ("品类词", (
        "{category}哪个牌子比较好",
        "{category}怎么选不容易踩坑",
        "想买{category}，有什么推荐",
        "{category}有哪些口碑好的品牌",
        "第一次买{category}，要注意什么",
        "{category}里哪些品牌比较靠谱",
    )),
    ("场景需求词", (
        "送礼选{category}，有什么拿得出手的牌子",
        "自己用的话，{category}哪个牌子划算",
        "家里日常用{category}，选什么品牌合适",
        "节日送{category}，有什么靠谱选择",
        "预算有限想买{category}，推荐哪个牌子",
        "给长辈买{category}，哪个品牌比较体面",
        "年轻人日常用{category}，哪个品牌更合适",
        "春节送礼想选{category}，有什么推荐",
    )),
    ("竞品对比词", (
        "{brand}和{competitor}哪个更值得买",
        "同样都是{category}，{brand}比{competitor}好在哪",
        "选{brand}还是{competitor}，怎么判断",
        "{brand}跟{competitor}相比有什么区别",
        "{competitor}和{brand}，普通人更该选哪个",
        "用过{brand}和{competitor}的人更推荐哪一个",
    )),
    ("地域词", (
        "{region}{city}买{category}，哪个牌子更常见",
        "{city}本地人买{category}一般会选什么品牌",
        "在{city}想买{category}，有什么本地推荐",
        "{region}{city}的{category}市场里，哪些品牌口碑好",
        "{city}附近选{category}，本地品牌和外地品牌怎么比",
        "住在{city}，买{category}推荐哪个品牌",
    )),
    ("决策长尾词", (
        "{brand}和同价位{category}比，长期用哪个省心",
        "第一次买{category}，预算中等选{brand}会不会后悔",
        "给公司采购{category}，{brand}适合吗",
        "{brand}用两年后质量还稳定吗",
        "家里有老人小孩，选{brand}的{category}要注意什么",
    )),
    ("风险词", (
        "{brand}有没有什么负面评价",
        "{brand}出过质量问题吗",
        "买{brand}之前有哪些坑要避开",
    )),
]


QUESTION_GENERATOR_VERSION = "question-rewrite-v1"
QUESTION_GENERATOR_SYSTEM_PROMPT = """你是中文用户问题改写器，只负责把给定问题意图改写成真实用户可能向 AI 提出的自然问题。

必须遵守：
1. 一个意图对应一个问题，qid 不得改变；
2. 不得改变问题意图或测试目标；
3. contains_brand=true 时问题必须包含给定品牌名；false 时不得出现品牌名；
4. 不得添加品牌档案之外的事实、历史、数字、价格、销量、奖项、资质、地址、原料、工艺或负面事件；
5. 不得把品牌优势、缺点或未经证实的事件写进问题前提；
6. 不得出现“评测、测试、GEO、可见度、提示词、排名算法”等内部词语；
7. 使用真实用户的口语化问法，避免宣传语、研究报告口吻和复合长问句；
8. 每个问题只问一件事，通常不超过40个汉字；
9. 不得重复问题；
10. 只输出 JSON，不输出解释或 Markdown。

输出格式：
{"questions":[{"qid":"Q01","question":"问题文本"}]}
"""

_BRANDED_CATEGORIES = {"品牌词", "竞品对比词", "决策长尾词", "风险词"}


def build_question_generation_prompt(profile: BrandProfile, seeds: list[Question]) -> str:
    """构造问题改写输入；不传入事实库、别名或已覆盖信源。"""
    payload = {
        "brand_profile": {
            "brand_name": profile.brand_name.strip(),
            "category": profile.category.strip(),
            "region": profile.region.strip(),
            "city": profile.city.strip(),
            "competitors": [item.strip() for item in profile.competitors if item.strip()],
        },
        "intents": [
            {
                "qid": item.qid,
                "category": item.category,
                "contains_brand": item.category in _BRANDED_CATEGORIES,
                "seed_question": item.text,
            }
            for item in seeds
        ],
    }
    return "请改写以下固定问题意图，只返回 JSON：\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


def _extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI 未返回 JSON 对象")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("AI 返回内容不是 JSON 对象")
    return data


def _normalize_question(text: str) -> str:
    value = " ".join(str(text or "").split())
    if value.endswith("?"):
        value = value[:-1] + "？"
    elif not value.endswith("？"):
        value += "？"
    return value


def validate_generated_questions(profile: BrandProfile, seeds: list[Question], payload: dict) -> list[Question]:
    """用固定意图和规则校验 AI 改写结果，不依赖事实库。"""
    rows = payload.get("questions")
    if not isinstance(rows, list):
        raise ValueError("AI 返回缺少 questions 数组")
    expected = {item.qid: item for item in seeds}
    by_qid: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        qid = str(row.get("qid", "")).strip().upper()
        if qid in by_qid:
            raise ValueError(f"AI 返回重复 qid：{qid}")
        by_qid[qid] = row
    if set(by_qid) != set(expected):
        missing = sorted(set(expected) - set(by_qid))
        extra = sorted(set(by_qid) - set(expected))
        raise ValueError(f"问题编号不完整，缺少={missing}，多出={extra}")

    brand = profile.brand_name.strip().casefold()
    entity_terms = [profile.brand_name.strip(), profile.category.strip(), *[item.strip() for item in profile.competitors if item.strip()]]
    general_forbidden = ("GEO", "可见度", "提示词", "评测", "测试", "排名算法")
    claim_patterns = (
        r"创立于", r"成立于", r"获得(?:了|过)?(?:认证|奖项|专利)",
        r"(?:行业|全国|销量|排名)第一", r"唯一(?:品牌|选择)",
        r"最好(?:的|品牌|选择)", r"最差(?:的|品牌|选择)",
        r"(?:价格|售价).{0,4}(?:便宜|昂贵|高|低)",
    )
    specific_allegations = ("被处罚", "违法", "致癌", "添加剂", "事故", "曝光", "倒闭")
    negative_terms = ("骗", "处罚", "违法", "致癌", "添加剂", "事故", "曝光", "倒闭")

    validated: list[Question] = []
    seen: set[str] = set()
    for qid in sorted(expected):
        seed = expected[qid]
        question = _normalize_question(by_qid[qid].get("question", ""))
        if not (5 <= len(question) <= 50):
            raise ValueError(f"{qid} 问题长度异常：{question}")
        if question.casefold() in seen:
            raise ValueError(f"AI 返回重复问题：{question}")
        seen.add(question.casefold())
        folded = question.casefold()
        contains_brand = seed.category in _BRANDED_CATEGORIES
        has_brand = bool(brand and brand in folded)
        if contains_brand != has_brand:
            raise ValueError(f"{qid} 品牌出现规则不符合意图")
        if any(term.casefold() in folded for term in general_forbidden):
            raise ValueError(f"{qid} 包含内部术语")
        sanitized = question
        for term in entity_terms:
            if term:
                sanitized = sanitized.replace(term, "")
        if re.search(r"[0-9０-９]", sanitized):
            raise ValueError(f"{qid} 包含未经允许的数字")
        if any(re.search(pattern, question) for pattern in claim_patterns):
            raise ValueError(f"{qid} 包含预设事实性断言")
        if seed.category == "风险词":
            if any(term in question for term in specific_allegations):
                raise ValueError(f"{qid} 包含具体负面指控")
        elif any(term in question for term in negative_terms):
            raise ValueError(f"{qid} 包含强负面前提")
        validated.append(Question(qid=qid, category=seed.category, text=question))
    return validated


def generate_questions_with_ai(profile: BrandProfile, engine) -> list[Question]:
    """固定意图 + AI 结合结构字段改写 + 规则校验。事实库不参与。"""
    seeds = generate_questions(profile)
    if hasattr(engine, "system_prompt"):
        engine.system_prompt = QUESTION_GENERATOR_SYSTEM_PROMPT
    prompt = build_question_generation_prompt(profile, seeds)
    response = engine.ask(prompt)
    payload = _extract_json_object(response)
    return validate_generated_questions(profile, seeds, payload)


def generate_questions(profile: BrandProfile, use_llm: bool = False) -> list[Question]:
    """按固定配比生成 40 个问题，按完整文本去重后从 Q01 开始编号。

    该函数始终生成固定模板，供 Mock、离线演示和 Live 失败回退使用。
    """
    _ = use_llm

    brand = profile.brand_name.strip() or "这个品牌"
    category = profile.category.strip() or "这类产品"
    region = profile.region.strip() or "山东"

    competitors = [item.strip() for item in profile.competitors if item.strip()]
    competitors = competitors or ["同类品牌"]

    # 保留用户填写的城市，再补充山东城市，确保地域题覆盖至少 3 个城市
    city_candidates = ([profile.city.strip()] if profile.city.strip() else []) + [
        "青岛", "济南", "烟台", "潍坊", "威海", "临沂",
    ]
    cities: list[str] = []
    for city in city_candidates:
        if city not in cities:
            cities.append(city)

    questions: list[Question] = []
    seen_texts: set[str] = set()

    for category_name, templates in _TEMPLATE_GROUPS:
        for index, template in enumerate(templates):
            text = template.format(
                brand=brand,
                category=category,
                region=region,
                city=cities[index % len(cities)],
                competitor=competitors[index % len(competitors)],
            )
            if text in seen_texts:
                continue
            seen_texts.add(text)
            questions.append(
                Question(
                    qid=f"Q{len(questions) + 1:02d}",
                    category=category_name,
                    text=text,
                )
            )

    if len(questions) != 40:
        raise ValueError(f"问题集应生成 40 条，实际生成 {len(questions)} 条")
    return questions


def save_questions_csv(questions: list[Question], path: str = "data/questions.csv") -> None:
    """把问题集写成 CSV（utf-8-sig，Excel 打开不乱码）。"""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=("qid", "category", "text"))
        writer.writeheader()
        writer.writerows(
            {"qid": q.qid, "category": q.category, "text": q.text} for q in questions
        )


if __name__ == "__main__":
    sample_profile = BrandProfile(
        brand_name="某绿茶",
        aliases=["某茶"],
        category="绿茶",
        region="山东",
        city="日照",
        competitors=["甲牌绿茶", "乙牌绿茶"],
        facts=["产自山东日照", "国家地理标志产品", "成立于1985年"],
    )
    sample_questions = generate_questions(sample_profile)
    for question in sample_questions:
        print(f"{question.qid} [{question.category}] {question.text}")
    save_questions_csv(sample_questions)
    print(f"\n共生成 {len(sample_questions)} 条，已写入 data/questions.csv")
