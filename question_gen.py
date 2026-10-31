"""模板式问题生成器。

设计要点：
1. 40 条问题按 7 类固定配比生成，全部来自模板，可复现、可审校。
2. 其中 20 条包含品牌名（测"用户点名后 AI 怎么说"），
   20 条不含品牌名（测"用户泛问时 AI 会不会主动想到你"）。
   后 20 条才是"AI 可见度"的核心测量对象。
3. 生成结果可落 CSV，运营同学能用 Excel 审校，历史版本也便于对比。
"""

import csv
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


def generate_questions(profile: BrandProfile, use_llm: bool = False) -> list[Question]:
    """按固定配比生成 40 个问题，按完整文本去重后从 Q01 开始编号。

    use_llm 为后续接入大模型保留；当前阶段统一使用模板，离线也能稳定复现。
    """
    _ = use_llm  # 现阶段不发起任何网络请求

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
