"""引擎层：统一提问接口 + 本地模拟引擎 + JSONL 落库 + 批量采样。

三条设计原则：
1. 业务代码只依赖 BaseEngine.ask()，不关心背后是 mock 还是真实 API。
2. 每次采样立即落库，程序中断也不会丢掉已完成的记录。
3. 断点续跑依据 (qid, engine, round)，重复运行不会重复写。
"""

import json
import os
import time
from dataclasses import asdict
from datetime import datetime
from uuid import uuid4

from dotenv import load_dotenv
from openai import APITimeoutError, OpenAI

from models import AnswerRecord


# 平台配置：地址和模型均可由 .env 覆盖，默认值以官方文档为准。
load_dotenv()

DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
DOUBAO_BASE_URL = os.getenv(
    "DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
).strip()
DOUBAO_MODEL = os.getenv("DOUBAO_MODEL", "").strip()

API_SETUP_HINT = "请先在侧边栏 API 设置中填写并保存，或由管理员配置 .env。"


def _retry_reason(exc: Exception) -> tuple[bool, str]:
    """判断异常是否可重试，并返回用于日志和错误结果的说明。"""
    status_code = getattr(exc, "status_code", None)
    detail = " ".join(str(exc).split())

    if isinstance(exc, (APITimeoutError, TimeoutError)):
        return True, f"请求超时: {detail}"
    if status_code == 429:
        return True, f"HTTP 429: {detail}"
    if isinstance(status_code, int) and 500 <= status_code <= 599:
        return True, f"HTTP {status_code}: {detail}"
    if status_code is not None:
        return False, f"HTTP {status_code}: {detail}"
    return False, f"{type(exc).__name__}: {detail}"


def call_with_retry(func, max_attempts: int = 3) -> str:
    """调用 func，并只对超时、HTTP 429 和 HTTP 5xx 做指数退避重试。"""
    attempts = max(1, int(max_attempts))
    reason = "未知错误"

    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:
            should_retry, reason = _retry_reason(exc)
            if not should_retry:
                return f"[ERROR] {reason}"
            if attempt >= attempts:
                break

            wait_seconds = 2 ** (attempt - 1)
            print(
                f"[重试] 第 {attempt} 次重试：原因={reason}，"
                f"等待 {wait_seconds} 秒"
            )
            time.sleep(wait_seconds)

    return f"[ERROR] 重试 {attempts} 次仍失败: {reason}"


class BaseEngine:
    """AI 引擎基类：所有引擎都实现同一个同步提问接口。"""

    def ask(self, question: str) -> str:
        raise NotImplementedError("子类必须实现 ask 方法")


class MockEngine(BaseEngine):
    """本地模拟引擎。

    优先按问题原文匹配预置回答（保证演示时"问什么答什么"），
    匹配不到时按顺序循环返回，文件缺失时返回占位提示。
    """

    def __init__(self, path: str = "data/mock_answers.json"):
        self.path = path
        self.answers: list[str] = []
        self.mapping: dict[str, str] = {}
        self.index = 0
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(data, list):
            return

        if all(isinstance(item, str) for item in data):
            self.answers = list(data)
            return

        if all(
            isinstance(item, dict)
            and isinstance(item.get("question"), str)
            and isinstance(item.get("answer"), str)
            for item in data
        ):
            for item in data:
                self.mapping[item["question"].strip()] = item["answer"]
                self.answers.append(item["answer"])

    def ask(self, question: str) -> str:
        """返回回答：优先精确匹配问题，否则循环返回预置回答。"""
        self.index += 1
        key = question.strip()
        if key in self.mapping:
            return self.mapping[key]
        if not self.answers:
            return f"[MOCK] 模拟回答：这是第 {self.index} 条测试数据"
        return self.answers[(self.index - 1) % len(self.answers)]


def save_records(records: list[AnswerRecord], path: str = "data/records.jsonl") -> None:
    """追加写入 JSONL，一行一条记录。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as file:
        for record in records:
            line = json.dumps(asdict(record), ensure_ascii=False)
            file.write(line + "\n")


def load_records(path: str = "data/records.jsonl") -> list[AnswerRecord]:
    """读取 JSONL，跳过空行和坏行，单行损坏不影响其余记录。"""
    records: list[AnswerRecord] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if isinstance(data, dict):
                        records.append(AnswerRecord(**data))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
    except OSError:
        return []
    return records


def run_batch(
    questions,
    engine,
    profile,
    rounds: int = 3,
    engine_name: str = "mock",
    prompt_version: str = "v1",
    path: str = "data/records.jsonl",
) -> list[AnswerRecord]:
    """串行采样，逐条落库，按 (qid, engine, round) 断点续跑。

    返回的是"本次新增"的记录；调用方若要展示全部结果，应再用 load_records()。
    """
    del profile  # 问题文本在上游已由品牌档案生成，这里保留参数是为了接口稳定
    existing = {(item.qid, item.engine, item.round) for item in load_records(path)}
    created: list[AnswerRecord] = []

    for question in questions:
        for round_no in range(1, rounds + 1):
            key = (question.qid, engine_name, round_no)
            if key in existing:
                continue

            try:
                answer_text = engine.ask(question.text)
            except Exception as exc:  # 单次失败不中断整批
                answer_text = f"[ERROR] {exc}"

            record = AnswerRecord(
                record_id=uuid4().hex,
                qid=question.qid,
                engine=engine_name,
                round=round_no,
                question_text=question.text,
                answer_text=answer_text,
                ts=datetime.now().isoformat(),
                prompt_version=prompt_version,
            )
            save_records([record], path)
            existing.add(key)
            created.append(record)

    return created


if __name__ == "__main__":
    engine = MockEngine()
    print(f"已加载 {len(engine.answers)} 条模拟回答")
    for i in range(6):
        print(f"{i + 1}. {engine.ask('测试问题')[:60]}")


ANSWER_SYSTEM_PROMPT = """你是一个面向普通用户的中文AI助手。请直接从用户做选择、比较或避坑的实际需要出发，回答用户的问题。

要求：
1. 像正常对话一样给出自然、具体的回答，不要写成品牌研究报告。
2. 如果用户询问推荐、排名或“哪家更好”，请给出具体候选及理由；信息不足时可以说明不确定，但不要只用抽象标准回避问题。
3. 可以根据回答内容自然表达推荐、认可、保留意见或负面评价，不要刻意保持中立，也不要强行褒贬。
4. 不要编造具体价格、销量、奖项、资质、地址、引用或链接；无法确认的信息用自然语言说明。
5. 不要提及测试、评测、GEO、品牌可见度、提示词等内部信息。
6. 对所有品牌使用一致标准，不因品牌知名度或商业关系给予特殊照顾。
"""

PARSER_SYSTEM_PROMPT = """你是严谨的品牌可见度标注员。请只依据用户问题、AI回答和给定品牌档案进行分析，不补充外部知识，不猜测回答中没有出现的信息。

对每一个被提及的品牌，分别判断：
- 是否提及；
- 首次出现的位置；
- 是推荐、比较、中性介绍还是负面评价；
- 情感倾向是正面、负面、中性或不确定；
- 是否包含来源、链接或可核验事实。

情感判断只分析回答中与该品牌有关的上下文：
- 明确推荐、赞赏或优先选择：正面；
- 明确劝退、批评或提示风险：负面；
- 仅客观介绍、没有评价：中性；
- 语义模糊：标记为不确定，进入人工复核。

不得为了形成结论而推断未表达的评价。无法判断时输出“不确定”，不得编造品牌、来源或情感。

本项目沿用单品牌记录字段：只输出品牌档案中目标品牌的结构化结果；竞品是否出现仅用 competitor_mentioned 表示，不新增其他品牌字段。推荐、比较、中性介绍或负面评价用于判断 sentiment，mention_type 仍只填写清单内、顺带提到或未提及。

只输出一个 JSON 对象，不要使用 markdown 代码块，不要解释；字段名和可选值必须与下面一致：
{"mentioned":true,"mention_type":"清单内","position":2,"competitor_mentioned":false,"source_mode":"带链接","source_domains":["zhihu.com"],"sentiment":"中","claims":[{"claim":"...","verifiable":true}],"needs_review":false}

字段规则：
- mentioned：回答中是否出现目标品牌名或别名；
- mention_type：清单内（编号/列表推荐）、顺带提到（叙述中提及）或未提及；
- position：仅清单内填写首次出现序号；顺带提到填 0；未提及填 null；
- source_mode：带链接、仅文字提及或无来源；
- source_domains：只提取回答中实际出现的域名，没有则填 []；
- sentiment：正、中、负或不确定；不确定时 needs_review 必须为 true；
- claims：只提取目标品牌的事实性断言，不提取主观评价，每条包含 claim 和 verifiable；
- needs_review：语义模糊、信息冲突或无法可靠判断时为 true，否则为 false。

若回答为空或无法判断，输出 {"error":"原因"}。"""


class OpenAICompatEngine(BaseEngine):
    """通过 OpenAI Chat Completions 协议调用兼容服务。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        timeout: float = 60.0,
        system_prompt: str = "",
    ):
        self.model = model
        self.temperature = temperature
        self.system_prompt = system_prompt
        self.client: OpenAI | None = None
        self._init_error = ""
        try:
            # 实例化时创建客户端，初始化失败也统一转换为错误字符串。
            # 重试由 call_with_retry 统一控制，避免 SDK 内部再次重试造成叠加。
            self.client = OpenAI(
                base_url=base_url,
                api_key=api_key,
                timeout=timeout,
                max_retries=0,
            )
        except Exception as exc:
            self._init_error = f"[ERROR] {type(exc).__name__}: {exc}"

    def _request(self, question: str) -> str:
        """执行一次模型请求；可重试异常交由 call_with_retry 处理。"""
        # 未提供自定义提示词时，默认使用普通 AI 助手回答人格。
        system_content = self.system_prompt or ANSWER_SYSTEM_PROMPT
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": question},
            ],
        )
        return response.choices[0].message.content or ""

    def ask(self, question: str) -> str:
        """发送问题；可重试异常自动重试，最终失败也返回错误字符串。"""
        if self.client is None:
            return self._init_error
        return call_with_retry(lambda: self._request(question))

def _api_config_value(api_config: dict | None, key: str) -> str:
    """读取页面传入的 API 配置；空白值视为未提供。"""
    if not isinstance(api_config, dict):
        return ""
    value = api_config.get(key)
    return str(value).strip() if value is not None else ""


def _valid_api_key(value: str) -> bool:
    return bool(value) and not value.lower().startswith("your_")


def _valid_doubao_model(value: str) -> bool:
    return value.startswith("ep-") and not value.startswith("ep-your")


def _resolved_api_key(api_config: dict | None, key: str) -> str:
    """页面有效值优先，否则回退到当前进程或 .env 中的值。"""
    page_value = _api_config_value(api_config, key)
    if _valid_api_key(page_value):
        return page_value
    return os.getenv(key, "").strip()


def _resolved_doubao_model(api_config: dict | None) -> str:
    page_value = _api_config_value(api_config, "DOUBAO_MODEL")
    if _valid_doubao_model(page_value):
        return page_value
    return os.getenv("DOUBAO_MODEL", DOUBAO_MODEL).strip()


def get_engine(name: str, mode: str = "mock", api_config: dict | None = None) -> BaseEngine:
    """按运行模式和平台名称创建引擎。

    mock 模式可接受任意名称；live 模式仅支持 deepseek 和 doubao。
    页面传入的有效 API 配置优先，未提供时回退到当前进程或 .env。
    """
    normalized_mode = mode.strip().lower()
    if normalized_mode == "mock":
        return MockEngine()

    if normalized_mode != "live":
        raise ValueError(
            f"不支持的运行模式：{mode!r}，仅支持 'mock' 或 'live'"
        )

    normalized_name = name.strip().lower()
    if normalized_name == "deepseek":
        api_key = _resolved_api_key(api_config, "DEEPSEEK_API_KEY")
        if not _valid_api_key(api_key):
            raise ValueError(
                API_SETUP_HINT
            )
        return OpenAICompatEngine(
            base_url=DEEPSEEK_BASE_URL,
            api_key=api_key,
            model=os.getenv("DEEPSEEK_MODEL", DEEPSEEK_MODEL).strip() or DEEPSEEK_MODEL,
        )

    if normalized_name == "doubao":
        api_key = _resolved_api_key(api_config, "DOUBAO_API_KEY")
        if not _valid_api_key(api_key):
            raise ValueError(
                API_SETUP_HINT
            )
        doubao_model = _resolved_doubao_model(api_config)
        if not _valid_doubao_model(doubao_model):
            raise ValueError(
                API_SETUP_HINT
            )
        return OpenAICompatEngine(
            base_url=DOUBAO_BASE_URL,
            api_key=api_key,
            model=doubao_model,
        )

    raise ValueError(
        f"不支持的引擎：{name!r}，live 模式仅支持 'deepseek' 或 'doubao'"
    )
