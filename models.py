from dataclasses import dataclass, field


@dataclass
class BrandProfile:
    """品牌档案：位于体检流程的入口，承接用户输入并作为提问、核验和报告生成的基础信息。"""

    brand_name: str  # 品牌名：用户填写，后续问题生成与回答解析读取，用于识别目标品牌。
    aliases: list[str] = field(default_factory=list)  # 品牌别名/简称：用户填写，提及识别读取，用于覆盖品牌的不同叫法。
    category: str = ""  # 品牌品类：用户填写，问题生成与报告分析读取，用于限定品牌所处赛道。
    region: str = ""  # 省份：用户填写，问题生成与地域分析读取，用于构造地域相关问题。
    city: str = ""  # 城市：用户填写，问题生成与地域分析读取，用于细化地域范围。
    competitors: list[str] = field(default_factory=list)  # 主要竞品：用户填写，竞品提及统计读取，用于衡量竞争占位情况。
    facts: list[str] = field(default_factory=list)  # 品牌事实库：用户或资料整理者填写，描述准确率核验读取，用于核对模型回答是否准确。


@dataclass
class Question:
    """标准问题：位于品牌档案之后、模型提问之前，用于统一管理每轮体检所使用的问题。"""

    qid: str  # 问题编号：问题设计者填写，答题流程与报告组装读取，用于稳定关联问题及其回答记录。
    category: str  # 问题类别：问题设计者填写，指标汇总与报告分组读取，用于区分不同体检维度。
    text: str  # 问题原文：问题设计者填写，模型调用与答案展示读取，用于实际发送给 AI 并保留原始问法。


@dataclass
class AnswerRecord:
    """单次回答记录：位于模型回答之后、指标计算与报告生成之前，用于保存一条可分析、可复核的结构化结果。"""

    record_id: str  # 记录编号：采集程序填写，去重、追溯和报告读取，用于唯一定位一次回答。
    qid: str  # 问题编号：采集程序填写，指标计算读取，用于把该回答关联回对应问题。
    engine: str  # AI 引擎名称：采集程序填写，分引擎统计读取，用于区分 DeepSeek、豆包等回答来源。
    round: int  # 提问轮次：采集程序填写，稳定性与重复测试分析读取，用于识别同一问题的第几次回答。
    question_text: str  # 问题原文快照：采集程序填写，报告展示与复核读取，用于保留当次实际提问文本。
    answer_text: str  # 模型回答原文：采集程序填写，解析、展示和复核读取，用于作为所有判断的原始依据。
    mentioned: bool = False  # 是否提及品牌：解析程序填写，可见度指标读取，用于快速判断目标品牌是否出现在回答中。
    mention_type: str = "未提及"  # 提及类型：解析程序填写，可见度与占位质量分析读取；可选值为清单内、顺带提到、未提及。
    position: int | None = None  # 提及位置：解析程序填写，排序与占位分析读取，用于记录品牌出现在第几位。
    competitor_mentioned: bool = False  # 是否提及竞品：解析程序填写，竞争分析读取，用于判断回答是否被竞品占据。
    source_mode: str = "无来源"  # 来源模式：解析程序填写，可信度与引用分析读取；可选值为带链接、仅文字提及、无来源。
    source_domains: list[str] = field(default_factory=list)  # 来源域名列表：解析程序填写，来源结构分析读取，用于汇总回答引用的网站。
    sentiment: str = "中"  # 情感倾向：解析程序填写，品牌口碑分析读取；可选值为正、中、负。
    claims: list[dict] = field(default_factory=list)  # 描述主张列表：解析程序填写，事实核验与人工复核读取，用于逐条保存模型对品牌说法的核验结果。
    needs_review: bool = False  # 是否需要复核：解析程序或规则程序填写，人工质检与报告提示读取，用于标记低置信度或存在争议的记录。
    ts: str = ""  # 采集时间：采集程序填写，审计、排序和趋势分析读取，用于记录该回答产生或入库的时间。
    prompt_version: str = "v1"  # 提示词版本：采集程序填写，效果对比与审计读取，用于区分不同提问模板下的结果。