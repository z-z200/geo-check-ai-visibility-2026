"""模型轨回答解析器：调用抽取模型，并把结构化 JSON 写回 AnswerRecord。"""

import json
import re
from dataclasses import replace

from engines import PARSER_SYSTEM_PROMPT, get_engine
from models import AnswerRecord, BrandProfile

# 任务提示词只负责注入本次记录并约束输出格式，人格与标注原则由 PARSER_SYSTEM_PROMPT 提供。
_EXTRACTION_PROMPT = """请从下面这条 AI 回答中抽取结构化信息，并严格按系统提示中的标注规则和 JSON 格式输出。

品牌名：{brand_name}
别名：{aliases}
竞品：{competitors}

问题：{question}
回答：{answer}

抽取规则：
1. mentioned：回答中是否出现品牌名或别名，只要出现即 true
2. mention_type：清单内（AI 给出编号/列表推荐）｜顺带提到（仅叙述中提及）｜未提及
3. position：仅当 mention_type 为"清单内"时填首次出现序号；顺带提到填 0；未提及填 null
4. competitor_mentioned：竞品是否被提及
5. source_mode：带链接（有可点击链接或参考资料区块）｜仅文字提及（提到来源但无链接）｜无来源
6. source_domains：从回答中提取的域名数组，如 ["zhihu.com"]，无则 []
7. sentiment：正｜中｜负｜不确定；语义模糊时必须填“不确定”，不得推测未表达的评价
8. claims：关于目标品牌的事实性断言数组，每条为 {"claim":"原文","verifiable":true|false}，
   只抽可验证事实（产地、成立时间、产品线、资质、销量、奖项），不抽主观评价
9. needs_review：情感为不确定、信息冲突或无法可靠判断时为 true，否则为 false

严格输出以下 JSON，不要 markdown 代码块，不要解释：
{"mentioned":true,"mention_type":"清单内","position":2,"competitor_mentioned":false,
 "source_mode":"带链接","source_domains":["zhihu.com"],"sentiment":"中",
 "claims":[{"claim":"...","verifiable":true}],"needs_review":false}

若回答为空或无法判断，输出 {"error":"原因"}。"""

_PLACEHOLDER = re.compile(r"\{(brand_name|aliases|competitors|question|answer)\}")


def _build_prompt(record: AnswerRecord, profile: BrandProfile) -> str:
    """只替换模板占位符，回答中的花括号不会再次参与替换。"""
    values = {"brand_name": profile.brand_name, "aliases": "、".join(profile.aliases) or "无",
              "competitors": "、".join(profile.competitors) or "无",
              "question": record.question_text, "answer": record.answer_text}
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], _EXTRACTION_PROMPT)


def _parse_json(text: str) -> tuple[dict | None, str]:
    """依次尝试原文、去代码块文本、首尾花括号文本；每次失败记录原因。"""
    if not isinstance(text, str):
        return None, "模型输出不是文本"
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    matched = re.search(r"\{.*\}", text, flags=re.DOTALL)
    reason = "未找到 JSON 对象"
    for candidate in (text, cleaned, matched.group(0) if matched else cleaned):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError) as exc:
            reason = f"JSON 解析失败：{exc}"
            continue
        if isinstance(data, dict):
            return data, ""
        reason = "JSON 顶层不是对象"
    return None, reason


def _failed(record: AnswerRecord, prompt_version: str, reason: str) -> AnswerRecord:
    """失败时保留记录元数据，业务字段恢复默认值并标记人工复核。"""
    return replace(record, mentioned=False, mention_type="未提及", position=None,
                   competitor_mentioned=False, source_mode="无来源", source_domains=[],
                   sentiment="不确定", claims=[{"parse_error": reason}], needs_review=True,
                   prompt_version=prompt_version)


def parse_answer(record: AnswerRecord, profile: BrandProfile, engine=None,
                 prompt_version: str = "v1") -> AnswerRecord:
    """调用模型抽取单条回答；任何无法形成有效 JSON 的情况都安全降级。"""
    if not record.answer_text.strip():
        return _failed(record, prompt_version, "回答为空，无法抽取")
    active_engine = engine
    try:
        # 未注入引擎时按原回答平台创建 live 引擎；OpenAICompatEngine 默认温度为 0。
        active_engine = active_engine or get_engine(record.engine, mode="live")
        if hasattr(active_engine, "system_prompt"):
            active_engine.system_prompt = PARSER_SYSTEM_PROMPT
        if hasattr(active_engine, "temperature"):
            active_engine.temperature = 0
        output = active_engine.ask(_build_prompt(record, profile))
    except Exception as exc:
        return _failed(record, prompt_version, f"抽取调用失败：{type(exc).__name__}: {exc}")

    data, reason = _parse_json(output)
    if data is None:
        return _failed(record, prompt_version, reason)
    if data.get("error"):
        return _failed(record, prompt_version, str(data["error"]))

    mention_type = data.get("mention_type")
    mention_type = mention_type if mention_type in {"清单内", "顺带提到", "未提及"} else "未提及"
    position = data.get("position")
    if mention_type == "清单内":
        position = position if isinstance(position, int) and not isinstance(position, bool) and position > 0 else None
    else:
        position = 0 if mention_type == "顺带提到" else None
    source_mode = data.get("source_mode")
    source_mode = source_mode if source_mode in {"带链接", "仅文字提及", "无来源"} else "无来源"
    sentiment = data.get("sentiment")
    sentiment = sentiment if sentiment in {"正", "中", "负", "不确定"} else "不确定"
    needs_review = data.get("needs_review") is True or sentiment == "不确定"
    raw_domains = data.get("source_domains")
    domains = [item.strip() for item in raw_domains if isinstance(item, str) and item.strip()] if isinstance(raw_domains, list) else []
    raw_claims = data.get("claims")
    claims = [{"claim": item["claim"].strip(), "verifiable": item.get("verifiable") is True}
              for item in raw_claims
              if isinstance(item, dict) and isinstance(item.get("claim"), str) and item["claim"].strip()] if isinstance(raw_claims, list) else []

    return replace(record, mentioned=data.get("mentioned") is True, mention_type=mention_type,
                   position=position, competitor_mentioned=data.get("competitor_mentioned") is True,
                   source_mode=source_mode, source_domains=domains, sentiment=sentiment,
                   claims=claims, needs_review=needs_review, prompt_version=prompt_version)
def rule_check(record: AnswerRecord, profile: BrandProfile) -> dict: answer = record.answer_text; folded = answer.casefold(); names = [name.strip() for name in [profile.brand_name, *profile.aliases] if isinstance(name, str) and name.strip()]; mentioned = any(name.casefold() in folded for name in names); markers = list(re.finditer(r"(?m)^[ \t]*(?:\d+[.、)）]|[（(]\d+[）)]|[①-⑳])[ \t]*", answer)); spans = [(marker.start(), markers[index + 1].start() if index + 1 < len(markers) else len(answer)) for index, marker in enumerate(markers)]; position = next((index for index, (start, end) in enumerate(spans, 1) if any(name.casefold() in answer[start:end].casefold() for name in names)), None); hosts = re.findall(r"https?://(?:www\.)?([^/\s:?#<>'）】]+)", answer, flags=re.IGNORECASE); domains = list(dict.fromkeys(host.strip(".,;!?)]}，。；！？）】").lower() for host in hosts if host.strip(".,;!?)]}，。；！？）】"))); source_mode = "带链接" if hosts else "仅文字提及" if re.search(r"根据|依据|(?<![数证根])据", answer) else "无来源"; return {"mentioned": mentioned, "position": position, "source_mode": source_mode, "source_domains": domains}
def merge_tracks(record: AnswerRecord, rule_result: dict) -> AnswerRecord: model_position = record.position; rule_position = rule_result.get("position"); parse_error = any(isinstance(item, dict) and "parse_error" in item for item in record.claims); mentioned_conflict = record.mentioned != bool(rule_result.get("mentioned")); position_conflict = model_position is not None and rule_position is not None and abs(model_position - rule_position) >= 2; conflict = mentioned_conflict or position_conflict or parse_error; use_rule = parse_error; position = rule_position if use_rule else (model_position if model_position is not None else (rule_position if not conflict else model_position)); return replace(record, mentioned=bool(rule_result.get("mentioned")) if use_rule else record.mentioned, position=position, source_mode=rule_result.get("source_mode", record.source_mode) if use_rule else (record.source_mode or rule_result.get("source_mode", "无来源")), source_domains=list(rule_result.get("source_domains", [])) if use_rule else (record.source_domains or list(rule_result.get("source_domains", []))), needs_review=record.needs_review or conflict)
def parse_rule_track(record: AnswerRecord, profile: BrandProfile,
                     prompt_version: str = "rule-v1") -> AnswerRecord:
    """只使用本地规则解析一条回答，供 Mock 与离线演示使用。"""
    result = rule_check(record, profile)
    mentioned = bool(result.get("mentioned"))
    position = result.get("position")
    if mentioned and isinstance(position, int) and position > 0:
        mention_type = "清单内"
    elif mentioned:
        mention_type = "顺带提到"
        position = 0
    else:
        mention_type = "未提及"
        position = None

    names = [profile.brand_name, *profile.aliases]
    competitors = [name for name in profile.competitors if isinstance(name, str) and name.strip()]
    folded = record.answer_text.casefold()
    competitor_mentioned = any(
        name.strip().casefold() in folded for name in competitors if name.strip()
    )
    del names  # 品牌提及已由 rule_check 统一判断，这里不重复计算。

    return replace(
        record,
        mentioned=mentioned,
        mention_type=mention_type,
        position=position,
        competitor_mentioned=competitor_mentioned,
        source_mode=result.get("source_mode", "无来源"),
        source_domains=list(result.get("source_domains", [])),
        needs_review=not bool(record.answer_text.strip()),
        prompt_version=prompt_version,
    )
