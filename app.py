"""齐鲁品牌 AI 可见度体检：Streamlit 交互与流程编排。"""

import hashlib
import json
import os
import secrets
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import dotenv_values, set_key, unset_key

# 在依赖模块加载 .env 之前读取，确保只有本地启动脚本能开启网页写配置。
PUBLIC_BYOK = (
    os.getenv("GEO_PUBLIC_BYOK", "").strip() == "1"
    or os.getenv("GEO_PUBLIC_DEMO", "").strip() == "1"
)
_ENV_EDIT_ENABLED_AT_STARTUP = os.getenv("GEO_ALLOW_ENV_EDIT", "").strip() == "1"
if "GEO_ALLOW_ENV_EDIT" not in os.environ:
    # 阻止 load_dotenv() 从 .env 中补入该开关，避免公开部署被配置文件误开启。
    os.environ["GEO_ALLOW_ENV_EDIT"] = "0"

from analysis import analyze_records
from engines import BaseEngine, PARSER_SYSTEM_PROMPT, get_engine, load_records, run_batch
from models import AnswerRecord, BrandProfile
from question_gen import generate_questions
from report import export_metrics_csv, export_suggestions_csv, render_html_report

DEMO_PATH = Path("data/demo_results.json")
RUN_DIR = Path("data/runs")
LAST_PROFILE_PATH = Path("data/last_profile.json")
ENV_PATH = Path(__file__).resolve().parent / ".env"
ENV_EDIT_ENABLED = _ENV_EDIT_ENABLED_AT_STARTUP and not PUBLIC_BYOK
API_ENV_KEYS = (
    "DEEPSEEK_API_KEY",
    "DOUBAO_API_KEY",
    "DOUBAO_MODEL",
)
API_WIDGET_KEYS = {
    "DEEPSEEK_API_KEY": "api_deepseek_key",
    "DOUBAO_API_KEY": "api_doubao_key",
    "DOUBAO_MODEL": "api_doubao_model",
}
API_PAGE_OVERRIDE_KEY = "api_page_override"
PROFILE_FIELDS = set(BrandProfile.__dataclass_fields__)
PROFILE_INPUT_KEYS = (
    "profile_brand_name",
    "profile_aliases",
    "profile_category",
    "profile_region",
    "profile_city",
    "profile_competitors",
    "profile_facts",
    "profile_covered_domains",
)
CURRENT_ANALYSIS_KEYS = (
    "profile",
    "covered_domains",
    "questions",
    "analysis",
    "records",
    "engine_label",
    "review_log",
)


def profile_input_keys(version: int) -> tuple[str, ...]:
    """为每轮表单生成唯一键，确保清空后前端旧值不会回填。"""
    return tuple(f"{key}_{version}" for key in PROFILE_INPUT_KEYS)


class ProgressEngine(BaseEngine):
    """把引擎逐条调用同步到 Streamlit 进度条。"""

    def __init__(self, engine: BaseEngine, total: int):
        self.engine = engine
        self.total = max(total, 1)
        self.done = 0
        self.bar = st.progress(0, text=f"正在采集回答：0 / {self.total}")

    def ask(self, question: str) -> str:
        try:
            return self.engine.ask(question)
        finally:
            self.done += 1
            self.bar.progress(
                min(self.done / self.total, 1.0),
                text=f"正在采集回答：{self.done} / {self.total}",
            )


def flash(message: str, level: str = "success") -> None:
    """跨 st.rerun 保留一条操作提示。"""
    st.session_state["flash"] = {"message": message, "level": level}


def show_flash() -> None:
    item = st.session_state.pop("flash", None)
    if not item:
        return
    renderer = getattr(st, item.get("level", "info"), st.info)
    renderer(item.get("message", ""))


def inject_ui_styles() -> None:
    """统一侧边栏标题、隐私说明和控件间距。"""
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] [data-testid="stSidebarContent"] {
            padding-top: 1.1rem;
        }
        .geo-sidebar-heading {
            display: flex;
            align-items: center;
            gap: .65rem;
            margin: .1rem 0 .9rem;
            color: #17324d;
            font-size: 1.2rem;
            font-weight: 800;
            letter-spacing: -.01em;
        }
        .geo-sidebar-heading::before {
            content: "";
            width: .32rem;
            height: 1.35rem;
            border-radius: 999px;
            background: linear-gradient(180deg, #2563eb, #60a5fa);
            box-shadow: 0 2px 8px rgba(37, 99, 235, .28);
        }
        .geo-byok-card {
            position: relative;
            overflow: hidden;
            margin: .15rem 0 .95rem;
            padding: .9rem .95rem .85rem 1.05rem;
            border: 1px solid #d7e7fb;
            border-radius: .95rem;
            background:
                radial-gradient(circle at 100% 0%, rgba(147, 197, 253, .34), transparent 42%),
                linear-gradient(145deg, #fbfdff 0%, #eef6ff 100%);
            box-shadow: 0 7px 20px rgba(37, 99, 235, .08);
        }
        .geo-byok-card::before {
            content: "";
            position: absolute;
            top: .75rem;
            bottom: .75rem;
            left: 0;
            width: .22rem;
            border-radius: 0 999px 999px 0;
            background: linear-gradient(180deg, #2563eb, #7dd3fc);
        }
        .geo-byok-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: .5rem;
            margin-bottom: .55rem;
            color: #15385f;
            font-size: .88rem;
            font-weight: 800;
        }
        .geo-byok-badge {
            flex: 0 0 auto;
            padding: .18rem .45rem;
            border: 1px solid #bfdbfe;
            border-radius: 999px;
            background: rgba(255, 255, 255, .8);
            color: #2563eb;
            font-size: .63rem;
            font-weight: 700;
            letter-spacing: .02em;
        }
        .geo-byok-line {
            display: flex;
            align-items: flex-start;
            gap: .48rem;
            margin-top: .3rem;
            color: #48627d;
            font-size: .76rem;
            line-height: 1.45;
        }
        .geo-byok-dot {
            flex: 0 0 auto;
            width: .38rem;
            height: .38rem;
            margin-top: .31rem;
            border-radius: 50%;
            background: #60a5fa;
            box-shadow: 0 0 0 3px rgba(96, 165, 250, .13);
        }
        [data-testid="stSidebar"] [data-testid="stExpander"] details {
            border-color: #dbe7f3;
            border-radius: .8rem;
            background: rgba(255, 255, 255, .58);
        }
        [data-testid="stSidebar"] .stRadio > label,
        [data-testid="stSidebar"] .stSelectbox > label,
        [data-testid="stSidebar"] .stNumberInput > label {
            color: #29445f;
            font-weight: 650;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def empty_api_config() -> dict[str, str]:
    """返回字段完整的空 API 配置，避免 UI 和引擎读到缺失键。"""
    return {key: "" for key in API_ENV_KEYS}


def api_widget_keys_for_current_form() -> dict[str, str]:
    """为 API 输入控件生成版本化 key，确保清除后前端不会残留旧值。"""
    try:
        version = int(st.session_state.get("api_form_version", 0))
    except (TypeError, ValueError):
        version = 0
    return {key: f"{base}_{version}" for key, base in API_WIDGET_KEYS.items()}


def reset_api_inputs() -> None:
    """丢弃当前版本 API 控件并切到一组全新的空输入框。"""
    for widget_key in api_widget_keys_for_current_form().values():
        st.session_state.pop(widget_key, None)
    try:
        version = int(st.session_state.get("api_form_version", 0))
    except (TypeError, ValueError):
        version = 0
    st.session_state["api_form_version"] = version + 1
    st.session_state["api_inputs_initialized"] = False
    st.session_state[API_PAGE_OVERRIDE_KEY] = False
    st.session_state["api_config"] = empty_api_config()


def read_saved_api_config() -> dict[str, str]:
    """仅在允许编辑本机配置时读取 .env；公开部署不回填服务器密钥。"""
    config = empty_api_config()
    if not ENV_EDIT_ENABLED or not ENV_PATH.exists():
        return config
    try:
        values = dotenv_values(ENV_PATH)
    except (OSError, ValueError):
        return config
    for key in API_ENV_KEYS:
        config[key] = str(values.get(key) or "").strip()
    return config


def ensure_api_input_defaults() -> None:
    """每个浏览器会话只初始化一次，避免后续输入被每次重跑覆盖。"""
    if st.session_state.get("api_inputs_initialized"):
        return
    defaults = read_saved_api_config()
    for env_key, widget_key in api_widget_keys_for_current_form().items():
        if widget_key not in st.session_state:
            st.session_state[widget_key] = defaults[env_key]
    st.session_state.setdefault(API_PAGE_OVERRIDE_KEY, False)
    st.session_state["api_inputs_initialized"] = True


def current_api_config() -> dict[str, str]:
    """读取当前页面输入，作为运行时最高优先级配置。"""
    return {
        key: str(st.session_state.get(widget_key, "") or "").strip()
        for key, widget_key in api_widget_keys_for_current_form().items()
    }


def mark_api_input_changed() -> None:
    """标记页面值已由用户调整，用于显示配置来源。"""
    st.session_state[API_PAGE_OVERRIDE_KEY] = True
    st.session_state["api_config"] = current_api_config()


def resolved_api_value(api_config: dict[str, str] | None, key: str) -> str:
    """本地模式回退到 .env；公网 BYOK 模式只认可当前页面会话值。"""
    page_value = str((api_config or {}).get(key, "") or "").strip()
    if page_value:
        return page_value
    if PUBLIC_BYOK:
        return ""
    return os.getenv(key, "").strip()


def api_source_for(engine_name: str, api_config: dict[str, str] | None) -> str:
    """显示当前引擎实际优先采用的配置来源。"""
    active_keys = (
        ("DEEPSEEK_API_KEY",)
        if engine_name == "deepseek"
        else ("DOUBAO_API_KEY", "DOUBAO_MODEL")
    )
    page_override = st.session_state.get(API_PAGE_OVERRIDE_KEY, False)
    page_values = [str((api_config or {}).get(key, "") or "").strip() for key in active_keys]
    if PUBLIC_BYOK:
        return "本次网页会话" if any(page_values) else "未配置"
    if page_override and any(page_values):
        return "网页配置"
    if any(os.getenv(key, "").strip() for key in active_keys):
        return "本机 .env"
    if any(page_values):
        return "网页配置"
    return "未配置"


def safe_api_error(exc: Exception, values: dict[str, str]) -> str:
    """生成可读错误，并确保异常文本中不会出现完整 API Key。"""
    message = f"{type(exc).__name__}: {exc}"
    for value in values.values():
        if value:
            message = message.replace(value, "***")
    return " ".join(message.split())[:300]


def save_api_settings() -> None:
    """把页面 API 配置写入项目根目录 .env，并立即刷新当前进程。"""
    values = current_api_config()
    try:
        ENV_PATH.touch(exist_ok=True)
        for key, value in values.items():
            set_key(str(ENV_PATH), key, value, quote_mode="always", encoding="utf-8")
    except Exception as exc:
        flash(f"保存本机 API 配置失败：{safe_api_error(exc, values)}", "error")
        return

    for key, value in values.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
    for key, widget_key in api_widget_keys_for_current_form().items():
        st.session_state[widget_key] = values[key]
    st.session_state[API_PAGE_OVERRIDE_KEY] = True
    st.session_state["api_config"] = values
    flash("已保存到本机，下次打开会自动读取。")


def clear_saved_api_settings() -> None:
    """从 .env 和当前进程中移除网页管理的 API 配置，保留其他配置项。"""
    values = current_api_config()
    secrets_for_error = {**read_saved_api_config(), **values}
    try:
        if ENV_PATH.exists():
            for key in API_ENV_KEYS:
                unset_key(str(ENV_PATH), key, encoding="utf-8")
    except Exception as exc:
        flash(f"清除本机 API 配置失败：{safe_api_error(exc, secrets_for_error)}", "error")
        return

    for key in API_ENV_KEYS:
        os.environ.pop(key, None)
    reset_api_inputs()
    flash("已清除本机 API 配置，其他 .env 内容未修改。")


def clear_page_api_settings() -> None:
    """只清空当前页面的 API 输入，不修改本机 .env。"""
    reset_api_inputs()
    flash("已清除本次 API 输入；本机 .env 未修改。")


def read_demo(path: Path = DEMO_PATH) -> tuple[BrandProfile, list[str], list[AnswerRecord]]:
    """读取离线演示包；演示包包含档案、已覆盖信源和回答记录。"""
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("演示文件顶层应为对象")

    profile_data = data.get("profile") or {}
    if not isinstance(profile_data, dict) or not profile_data.get("brand_name"):
        raise ValueError("演示文件缺少有效品牌档案")
    profile = BrandProfile(**{key: value for key, value in profile_data.items() if key in PROFILE_FIELDS})

    rows = data.get("records", [])
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("演示文件中的 records 应为记录对象数组")
    records = [
        AnswerRecord(**{key: value for key, value in row.items() if key in AnswerRecord.__dataclass_fields__})
        for row in rows
    ]
    covered = [str(item).strip() for item in data.get("covered_domains", []) if str(item).strip()]
    return profile, covered, records


def questions_for(profile: BrandProfile):
    """统一生成问题，保证问题编号和类别在所有模式下一致。"""
    return generate_questions(profile)


def load_last_profile() -> None:
    """浏览器会话丢失时，自动恢复上一次保存的品牌档案。"""
    if PUBLIC_BYOK:
        return
    if (
        st.session_state.get("skip_last_profile_restore")
        or st.session_state.get("profile")
        or not LAST_PROFILE_PATH.exists()
    ):
        return
    try:
        with LAST_PROFILE_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
        profile_data = data.get("profile") or {}
        profile = BrandProfile(
            **{key: value for key, value in profile_data.items() if key in PROFILE_FIELDS}
        )
        st.session_state["profile"] = profile
        st.session_state["covered_domains"] = [
            str(item).strip()
            for item in data.get("covered_domains", [])
            if str(item).strip()
        ]
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return


def save_last_profile(profile: BrandProfile, covered_domains: list[str]) -> None:
    """保存最近档案；公网演示仅保留在当前浏览器会话中。"""
    if PUBLIC_BYOK:
        return
    payload = {"profile": asdict(profile), "covered_domains": covered_domains}
    LAST_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_PROFILE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def clear_profile_inputs() -> None:
    """清空当前档案和分析结果，并停止下次自动恢复。"""
    try:
        version = int(st.session_state.get("profile_form_version", 0))
    except (TypeError, ValueError):
        version = 0
    st.session_state.pop("skip_last_profile_restore", None)
    for key in (*profile_input_keys(version), *CURRENT_ANALYSIS_KEYS):
        st.session_state.pop(key, None)
    st.session_state["profile_form_version"] = version + 1
    if not PUBLIC_BYOK:
        try:
            LAST_PROFILE_PATH.unlink(missing_ok=True)
        except OSError as exc:
            st.session_state["skip_last_profile_restore"] = True
            flash(f"页面输入已清空，但上次档案文件未能删除：{exc}", "warning")
            return
    flash("已清空当前品牌输入，可以开始新的体检。")


def run_path_for(profile: BrandProfile) -> Path:
    """同一会话内同一品牌复用记录；公网版写入随机会话临时文件。"""
    signature = json.dumps(asdict(profile), ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
    if PUBLIC_BYOK:
        session_id = st.session_state.setdefault("public_session_id", secrets.token_hex(12))
        path = Path(tempfile.gettempdir()) / "geo-check-public" / f"{session_id}-{digest}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return RUN_DIR / f"{digest}.jsonl"


def percent(value) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "0%"


def records_frame(records: list[AnswerRecord]) -> pd.DataFrame:
    """生成适合网页展示和下载的记录表。"""
    return pd.DataFrame(
        [
            {
                "qid": item.qid,
                "问题类别": getattr(item, "category", ""),
                "引擎": item.engine,
                "轮次": item.round,
                "问题": item.question_text,
                "回答": item.answer_text,
                "是否提及": item.mentioned,
                "提及类型": item.mention_type,
                "位次": item.position,
                "是否提竞品": item.competitor_mentioned,
                "来源模式": item.source_mode,
                "来源域名": "、".join(item.source_domains),
                "情感": item.sentiment,
                "需复核": item.needs_review,
            }
            for item in records
        ]
    )


def source_frame(source_stats: dict) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"平台": item.get("platform", domain), "域名": domain, "引用次数": item.get("count", 0)}
            for domain, item in source_stats.items()
        ]
    )


def suggestion_frame(suggestions: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "优先级": item.get("priority"),
                "缺口": item.get("gap"),
                "目标平台": item.get("platform"),
                "内容体裁": item.get("content_type"),
                "必须覆盖事实": "；".join(item.get("must_cover_facts", [])),
                "预期指标": item.get("expected_metric"),
            }
            for item in suggestions
        ]
    )


def store_analysis(result: dict, profile: BrandProfile, covered: list[str],
                   questions, engine_label: str) -> None:
    """把整条分析链的产物统一写进 session_state。"""
    st.session_state["records"] = result["records"]
    st.session_state["analysis"] = result
    st.session_state["profile"] = profile
    st.session_state["covered_domains"] = covered
    st.session_state["questions"] = questions
    st.session_state["engine_label"] = engine_label


def load_demo_into_session() -> None:
    profile, covered, records = read_demo()
    questions = questions_for(profile)
    st.session_state["review_log"] = []
    result = analyze_records(
        records,
        profile,
        questions=questions,
        parse_mode="stored",
        covered_domains=covered,
        is_mock=True,
    )
    store_analysis(result, profile, covered, questions, "离线演示")


def _valid_api_key(value: str) -> bool:
    return bool(value) and not value.lower().startswith("your_")


def _valid_doubao_model(value: str) -> bool:
    return value.startswith("ep-") and not value.startswith("ep-your")


def _status_value(api_config: dict[str, str] | None, key: str, validator) -> str:
    page_value = str((api_config or {}).get(key, "") or "").strip()
    if validator(page_value):
        return page_value
    if PUBLIC_BYOK:
        return ""
    return os.getenv(key, "").strip()


def load_api_status(api_config: dict[str, str] | None = None) -> tuple[bool, bool]:
    """同时考虑页面配置与本机 .env，只返回可用状态，不展示密钥。"""
    deepseek_key = _status_value(api_config, "DEEPSEEK_API_KEY", _valid_api_key)
    doubao_key = _status_value(api_config, "DOUBAO_API_KEY", _valid_api_key)
    doubao_model = _status_value(api_config, "DOUBAO_MODEL", _valid_doubao_model)
    deepseek_ready = _valid_api_key(deepseek_key)
    doubao_ready = _valid_api_key(doubao_key) and _valid_doubao_model(doubao_model)
    return deepseek_ready, doubao_ready


def render_sidebar() -> tuple[str, bool, str, str]:
    saved = st.session_state.get("profile")
    covered_saved = st.session_state.get("covered_domains", [])
    try:
        form_version = int(st.session_state.get("profile_form_version", 0))
    except (TypeError, ValueError):
        form_version = 0

    with st.sidebar:
        st.markdown('<div class="geo-sidebar-heading">运行设置</div>', unsafe_allow_html=True)
        if PUBLIC_BYOK:
            st.markdown(
                """
                <div class="geo-byok-card">
                    <div class="geo-byok-head">
                        <span>BYOK 隐私模式</span>
                        <span class="geo-byok-badge">会话隔离</span>
                    </div>
                    <div class="geo-byok-line"><span class="geo-byok-dot"></span><span>使用你自己的 API Key</span></div>
                    <div class="geo-byok-line"><span class="geo-byok-dot"></span><span>仅用于当前会话，支持一键清除</span></div>
                    <div class="geo-byok-line"><span class="geo-byok-dot"></span><span>不写入 .env、日志或报告</span></div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        mode = st.radio("运行模式", ("mock", "live"), horizontal=True, key="run_mode")
        offline = st.toggle(
            "离线演示",
            key="offline_demo",
            help="直接读取内置案例，不调用任何 API",
        )
        engine_options = ("mock",) if mode == "mock" else ("deepseek", "doubao")
        engine_name = st.selectbox("引擎", engine_options, key=f"engine_{mode}")

        parse_choice = "规则解析"
        if mode == "live" and not offline:
            parse_choice = st.selectbox(
                "解析方式",
                ("规则解析（无需额外调用）", "模型 + 规则双轨（额外 API 调用）"),
                help="双轨解析会为每条回答额外调用一次抽取模型，成本更高。",
            )
        if mode == "live" and not offline:
            ensure_api_input_defaults()
            api_keys = api_widget_keys_for_current_form()
            with st.expander("API 设置", expanded=PUBLIC_BYOK):
                st.text_input(
                    "DeepSeek API Key",
                    key=api_keys["DEEPSEEK_API_KEY"],
                    type="password",
                    on_change=mark_api_input_changed,
                    help="仅用于当前会话，不建议填写企业主账号或高额度 Key。",
                )
                st.text_input(
                    "豆包 API Key",
                    key=api_keys["DOUBAO_API_KEY"],
                    type="password",
                    on_change=mark_api_input_changed,
                    help="仅用于当前会话，不建议填写企业主账号或高额度 Key。",
                )
                st.text_input(
                    "豆包接入点 ID",
                    key=api_keys["DOUBAO_MODEL"],
                    placeholder="ep- 开头",
                    on_change=mark_api_input_changed,
                    help="豆包真实调用需要 ep- 开头的推理接入点 ID。",
                )
                if ENV_EDIT_ENABLED:
                    st.button(
                        "保存到本机",
                        type="primary",
                        width="stretch",
                        on_click=save_api_settings,
                    )
                    st.button(
                        "清除本机 API 配置",
                        width="stretch",
                        on_click=clear_saved_api_settings,
                    )
                else:
                    st.caption(
                        "仅当前会话使用，请求会经本服务转发；"
                        "点击下方按钮可立即清除。"
                    )
                st.button(
                    "清除本次 API 输入" if PUBLIC_BYOK else "仅清除页面输入",
                    width="stretch",
                    on_click=clear_page_api_settings,
                )

            api_config = current_api_config()
            st.session_state["api_config"] = api_config
            deepseek_ready, doubao_ready = load_api_status(api_config)
            current_ready = deepseek_ready if engine_name == "deepseek" else doubao_ready
            st.caption(f"当前来源：{api_source_for(engine_name, api_config)}")
            st.caption(f"当前状态：{'已填写' if current_ready else '未填写'}")
            st.caption(
                f"平台检查：DeepSeek {'已填写' if deepseek_ready else '未填写'}；"
                f"豆包 {'已填写' if doubao_ready else '未填写'}"
            )
            if PUBLIC_BYOK:
                st.warning("真实调用费用由填写 Key 的用户承担；公网版最多允许 3 轮。")
        if offline:
            st.info("离线演示不读取 API Key，报告会自动标注“模拟数据”。")

        st.divider()
        st.header("品牌档案")
        st.caption("带 * 为必填；标注‘建议填写’的信息越完整，体检和补位建议越准确。")
        with st.form(f"brand_profile_form_{form_version}"):
            brand_name = st.text_input(
                "品牌名 *",
                key=f"profile_brand_name_{form_version}",
                value=saved.brand_name if saved else "",
                placeholder="例如：超意兴",
                help="唯一必填项，用于生成问题、识别 AI 提及和区分体检记录。",
            )
            aliases_text = st.text_input(
                "别名（选填）",
                key=f"profile_aliases_{form_version}",
                value=", ".join(saved.aliases) if saved else "",
                placeholder="例如：超意兴把子肉、超意兴快餐",
                help="填写常见简称、门店名或用户常用叫法，多个用逗号分隔。",
            )
            category = st.text_input(
                "品类（建议填写）",
                key=f"profile_category_{form_version}",
                value=saved.category if saved else "",
                placeholder="例如：中式快餐、把子肉",
                help="影响品类、场景和竞品问题的生成。",
            )
            region = st.text_input(
                "地域（建议填写）",
                key=f"profile_region_{form_version}",
                value=saved.region if saved else "山东",
                placeholder="例如：山东",
                help="用于生成地域相关问题和判断本地信源覆盖。",
            )
            city = st.text_input(
                "城市（选填）",
                key=f"profile_city_{form_version}",
                value=saved.city if saved else "",
                placeholder="例如：济南",
                help="填写主要经营城市，可提高地域问题与信源分析的针对性。",
            )
            competitors_text = st.text_input(
                "竞品（建议填写）",
                key=f"profile_competitors_{form_version}",
                value=", ".join(saved.competitors) if saved else "",
                placeholder="例如：老济南把子肉、金德利民",
                help="填写主要竞争对手，多个用逗号分隔。",
            )
            facts_text = st.text_area(
                "事实库（建议填写）",
                key=f"profile_facts_{form_version}",
                value="\n".join(saved.facts) if saved else "",
                placeholder="每行一条，例如：\n创始于1993年\n主营把子肉和米饭套餐\n在济南设有多家门店",
                help="每行一条，建议至少填写 2 条可公开核实的信息。",
            )
            covered_text = st.text_input(
                "已覆盖信源（选填）",
                key=f"profile_covered_domains_{form_version}",
                value=", ".join(covered_saved),
                placeholder="例如：zhihu.com, baidu.com",
                help="填写品牌已运营或覆盖的网站、平台域名；不确定可以留空。",
            )
            st.caption("提示：品牌名必填；事实库建议至少填写 2 条。")
            profile_save_label = "应用到当前会话" if PUBLIC_BYOK else "保存档案"
            save_clicked = st.form_submit_button(profile_save_label, width="stretch")

        st.button(
            "一键清空当前输入",
            width="stretch",
            on_click=clear_profile_inputs,
            help="清空品牌档案、当前分析结果和自动恢复记录；不会删除历史采集记录。",
        )
        if saved:
            if PUBLIC_BYOK:
                st.caption(f"当前会话档案：{saved.brand_name}。刷新页面后自动清除。")
            else:
                st.caption(f"当前档案：{saved.brand_name}。刷新或重启后会恢复此档案。")
        else:
            if PUBLIC_BYOK:
                st.caption("档案只保留在当前浏览器会话，不会写入服务器。")
            else:
                st.caption("保存档案后，下次打开会自动恢复；需要重新填写时可一键清空。")

        if save_clicked:
            if not brand_name.strip():
                flash("品牌名不能为空。", "warning")
            else:
                profile = BrandProfile(
                    brand_name=brand_name.strip(),
                    aliases=[x.strip() for x in aliases_text.replace("，", ",").split(",") if x.strip()],
                    category=category.strip(),
                    region=region.strip(),
                    city=city.strip(),
                    competitors=[x.strip() for x in competitors_text.replace("，", ",").split(",") if x.strip()],
                    facts=[x.strip() for x in facts_text.splitlines() if x.strip()],
                )
                st.session_state["profile"] = profile
                covered_domains = [
                    x.strip() for x in covered_text.replace("，", ",").split(",") if x.strip()
                ]
                st.session_state["covered_domains"] = covered_domains
                save_last_profile(profile, covered_domains)
                st.session_state.pop("analysis", None)
                st.session_state.pop("records", None)
                st.session_state["review_log"] = []
                flash("品牌档案已应用到当前会话，可以开始体检。" if PUBLIC_BYOK else "品牌档案已保存，可以开始体检。")
                st.rerun()

    return mode, offline, engine_name, parse_choice


def render_status(mode: str, offline: bool, engine_name: str) -> None:
    records = st.session_state.get("records", [])
    label = "离线演示" if offline else engine_name
    st.caption(f"当前模式：{mode.upper()}　|　当前引擎：{label}　|　已分析：{len(records)} 条")


def render_input_tab() -> None:
    profile = st.session_state.get("profile")
    covered = st.session_state.get("covered_domains", [])
    if not profile:
        st.info(
            "请先在左侧填写并保存品牌档案，或打开‘离线演示’加载内置案例。"
            "必填项只有品牌名；建议补充品类、地域、竞品和至少 2 条事实。"
        )
        return

    st.subheader("品牌档案")
    st.json(asdict(profile), expanded=False)
    st.write("已覆盖信源：", "、".join(covered) if covered else "尚未填写")
    if len(profile.facts) < 2:
        st.warning("事实库少于 2 条时，补位建议会因缺少可核验事实而无法生成。")

    questions = questions_for(profile)
    st.subheader(f"体检问题：{len(questions)} 条")
    st.caption("问题按品牌词、品类词、场景词、竞品词、地域词、决策长尾词和风险词分组。")
    st.dataframe(
        pd.DataFrame([{"qid": q.qid, "类别": q.category, "问题": q.text} for q in questions]),
        width="stretch",
        hide_index=True,
    )


def execute_offline() -> None:
    try:
        load_demo_into_session()
        flash("离线演示案例已加载，未调用任何 API。")
        st.rerun()
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        st.error(f"离线演示加载失败：{exc}")


def execute_run(mode: str, engine_name: str, rounds: int, parse_choice: str) -> None:
    profile = st.session_state.get("profile")
    if not profile:
        st.warning("请先在左侧保存品牌档案。")
        return

    questions = questions_for(profile)
    covered = st.session_state.get("covered_domains", [])
    api_config = current_api_config() if mode == "live" else None
    try:
        safe_rounds = max(1, min(int(rounds), 3)) if PUBLIC_BYOK else max(1, int(rounds))
        engine = get_engine(engine_name, mode, api_config=api_config)
        total_calls = len(questions) * safe_rounds
        progress_engine = ProgressEngine(engine, total_calls)
        run_path = run_path_for(profile)
        new_records = run_batch(
            questions,
            progress_engine,
            profile,
            rounds=safe_rounds,
            engine_name=engine_name,
            path=str(run_path),
        )
        raw_records = load_records(str(run_path))
        if not raw_records:
            raise RuntimeError("没有可分析的记录")

        use_dual = mode == "live" and parse_choice.startswith("模型")
        parse_mode = "dual" if use_dual else "rule"
        parser_engine = None
        if use_dual:
            parser_engine = get_engine(engine_name, mode, api_config=api_config)
            if hasattr(parser_engine, "system_prompt"):
                parser_engine.system_prompt = PARSER_SYSTEM_PROMPT
        parse_bar = st.progress(0, text=f"正在解析回答：0 / {len(raw_records)}")

        def update_parse(done: int, total: int, _record: AnswerRecord) -> None:
            parse_bar.progress(min(done / max(total, 1), 1.0), text=f"正在解析回答：{done} / {total}")

        st.session_state["review_log"] = []
        result = analyze_records(
            raw_records,
            profile,
            questions=questions,
            parse_mode=parse_mode,
            parser_engine=parser_engine,
            covered_domains=covered,
            is_mock=(mode == "mock"),
            progress_callback=update_parse,
        )
        store_analysis(result, profile, covered, questions, engine_name)
        failed = result["metrics"].get("failed_records", 0)
        if failed:
            flash(f"本次新增 {len(new_records)} 条；累计 {len(raw_records)} 条，其中 {failed} 条 API 调用失败，已进入复核。", "warning")
        else:
            flash(f"体检完成：本次新增 {len(new_records)} 条，累计分析 {len(raw_records)} 条。")
        st.rerun()
    except ValueError as exc:
        st.error(f"配置错误：{exc}")
    except Exception as exc:
        st.error(f"体检运行失败（{type(exc).__name__}）：{exc}")


def render_run_tab(mode: str, offline: bool, engine_name: str, parse_choice: str) -> None:
    st.subheader("体检运行")
    st.write("执行顺序：生成问题 → 调用引擎采集回答 → 解析提及与来源 → 计算指标 → 生成缺口和建议。")
    if offline:
        st.caption("当前会加载 data/demo_results.json，不访问网络。")
        if st.button("加载离线演示", type="primary", width="stretch"):
            execute_offline()
        return

    max_rounds = 3 if PUBLIC_BYOK else 20
    default_rounds = 1 if PUBLIC_BYOK else 3
    rounds = st.number_input(
        "轮数",
        min_value=1,
        max_value=max_rounds,
        value=default_rounds,
        step=1,
        help="公网 BYOK 模式最多 3 轮，以减少误操作产生的 API 费用。" if PUBLIC_BYOK else None,
    )
    if mode == "live" and not offline:
        st.caption("1 轮通常包含 40 次回答调用；模型 + 规则双轨还会增加解析调用。")
    if mode == "live" and parse_choice.startswith("模型"):
        st.warning("双轨解析会对每条回答额外调用一次抽取模型，请先用 1 轮做小规模验证。")
    if st.button("开始体检", type="primary", width="stretch"):
        execute_run(mode, engine_name, int(rounds), parse_choice)

    analysis = st.session_state.get("analysis")
    if analysis:
        metrics = analysis["metrics"]
        st.success(
            f"当前已有 {metrics.get('n_records', 0)} 条有效样本，"
            f"提及率 {percent(metrics.get('mention_rate'))}。"
        )


def render_results_tab() -> None:
    analysis = st.session_state.get("analysis")
    if not analysis:
        st.info("完成一次体检或加载离线演示后，这里会显示指标、信源缺口和补位建议。")
        return

    profile = st.session_state["profile"]
    metrics = analysis["metrics"]
    gaps = analysis["gaps"]
    suggestions = analysis["suggestions"]
    records = analysis["records"]

    if analysis.get("is_mock"):
        st.warning("当前为模拟或离线演示数据，不能作为真实品牌运行证据。报告已标注“模拟数据”。")
    if metrics.get("failed_records"):
        st.error(f"{metrics['failed_records']} 条记录因 API 调用失败被排除在有效指标之外，请到复核页查看。")

    st.subheader("体检指标")
    cols = st.columns(5)
    cols[0].metric("品牌提及率", percent(metrics.get("mention_rate")))
    cols[1].metric("首位提及率", percent(metrics.get("top1_rate")))
    avg_position = metrics.get("avg_position")
    cols[2].metric("平均提及位次", "暂无" if avg_position in (None, 0) else f"第 {float(avg_position):g} 位")
    cols[3].metric("来源引用率", percent(metrics.get("citation_rate")))
    cols[4].metric("情感得分", f"{float(metrics.get('sentiment_score', 0)):+.2f}")

    chart_left, chart_right = st.columns(2)
    with chart_left:
        st.subheader("按引擎的提及率")
        engine_rows = [{"引擎": key, "提及率": value} for key, value in metrics.get("by_engine", {}).items()]
        if engine_rows:
            st.bar_chart(pd.DataFrame(engine_rows), x="引擎", y="提及率", height=260)
        else:
            st.caption("暂无引擎数据。")
    with chart_right:
        st.subheader("按问题类别的提及率")
        category_rows = [{"类别": key, "提及率": value} for key, value in metrics.get("by_category", {}).items()]
        if category_rows:
            st.bar_chart(pd.DataFrame(category_rows), x="类别", y="提及率", height=260)
        else:
            st.caption("暂无类别数据。")

    st.subheader("信源归因")
    sources = source_frame(analysis["source_stats"])
    if sources.empty:
        st.caption("当前回答没有识别到可归因的来源域名。")
    else:
        st.dataframe(sources, width="stretch", hide_index=True)

    st.subheader("占位缺口")
    if gaps:
        st.dataframe(
            pd.DataFrame(
                [
                    {"平台": item["platform"], "域名": item["domain"], "引用次数": item["count"], "缺口原因": item["gap_reason"]}
                    for item in gaps
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("未发现达到阈值且尚未覆盖的信源缺口。")

    st.subheader("补位建议")
    if suggestions:
        st.dataframe(suggestion_frame(suggestions), width="stretch", hide_index=True)
    else:
        st.caption("事实库不足或没有可用缺口，暂时无法生成补位建议。")

    report_html = render_html_report(profile, metrics, gaps, suggestions)
    download_left, download_middle, download_right = st.columns(3)
    download_left.download_button(
        "下载体检指标 CSV",
        data=export_metrics_csv(metrics),
        file_name="geo_check_metrics.csv",
        mime="text/csv",
        width="stretch",
    )
    download_middle.download_button(
        "下载补位建议 CSV",
        data=export_suggestions_csv(suggestions),
        file_name="geo_check_suggestions.csv",
        mime="text/csv",
        width="stretch",
    )
    download_right.download_button(
        "下载 HTML 体检报告",
        data=report_html.encode("utf-8"),
        file_name="geo_check_report.html",
        mime="text/html",
        width="stretch",
    )

    with st.expander("查看全部回答与解析记录"):
        frame = records_frame(records)
        st.dataframe(frame, width="stretch", hide_index=True)
        st.download_button(
            "下载回答记录 CSV",
            data=frame.to_csv(index=False).encode("utf-8-sig"),
            file_name="geo_check_records.csv",
            mime="text/csv",
            width="stretch",
        )


def save_reviews(pending: list[AnswerRecord], edited: pd.DataFrame) -> None:
    """保存人工修改，并把原值、新值和时间写入审计日志。"""
    timestamp = datetime.now().isoformat(timespec="seconds")
    log = st.session_state.setdefault("review_log", [])
    changed = 0
    for record, row in zip(pending, edited.to_dict("records")):
        new_mentioned = bool(row["是否提及"])
        raw_position = row["位次"]
        new_position = None if pd.isna(raw_position) else int(raw_position)
        new_note = "" if pd.isna(row["复核备注"]) else str(row["复核备注"]).strip()
        if not new_mentioned:
            new_position = None
            new_mention_type = "未提及"
        elif isinstance(new_position, int) and new_position > 0:
            new_mention_type = "清单内"
        else:
            new_position = 0
            new_mention_type = "顺带提到"

        for field, old_value, new_value in (
            ("mentioned", record.mentioned, new_mentioned),
            ("position", record.position, new_position),
            ("mention_type", record.mention_type, new_mention_type),
            ("review_note", getattr(record, "review_note", ""), new_note),
        ):
            if old_value != new_value:
                log.append(
                    {
                        "record_id": record.record_id,
                        "qid": record.qid,
                        "engine": record.engine,
                        "round": record.round,
                        "字段": field,
                        "原值": old_value,
                        "新值": new_value,
                        "修改时间": timestamp,
                    }
                )
                changed += 1

        record.mentioned = new_mentioned
        record.position = new_position
        record.mention_type = new_mention_type
        record.review_note = new_note
        record.reviewed = True
        record.reviewed_at = timestamp

    analysis = st.session_state.get("analysis")
    if analysis:
        profile = st.session_state["profile"]
        refreshed = analyze_records(
            analysis["records"],
            profile,
            questions=st.session_state.get("questions"),
            parse_mode="stored",
            covered_domains=st.session_state.get("covered_domains", []),
            is_mock=analysis.get("is_mock", False),
        )
        st.session_state["analysis"] = refreshed
        st.session_state["records"] = refreshed["records"]
    flash(f"复核已保存，共记录 {changed} 处修改。")
    st.rerun()


def render_review_tab() -> None:
    analysis = st.session_state.get("analysis")
    if not analysis:
        st.info("完成体检后，这里会显示需要人工复核的低置信度记录。")
        return

    records = analysis["records"]
    pending = [
        item for item in records
        if item.needs_review and not getattr(item, "reviewed", False)
    ]
    reviewed_count = sum(1 for item in records if getattr(item, "reviewed", False))
    st.write(f"共 {len(pending)} 条待复核，已处理 {reviewed_count} 条。")

    if pending:
        rows = [
            {
                "qid": item.qid,
                "引擎": item.engine,
                "轮次": item.round,
                "问题": item.question_text,
                "回答": item.answer_text,
                "是否提及": item.mentioned,
                "位次": item.position,
                "复核备注": getattr(item, "review_note", ""),
            }
            for item in pending
        ]
        edited = st.data_editor(
            pd.DataFrame(rows),
            hide_index=True,
            num_rows="fixed",
            width="stretch",
            disabled=["qid", "引擎", "轮次", "问题", "回答"],
            column_config={
                "是否提及": st.column_config.CheckboxColumn("是否提及"),
                "位次": st.column_config.NumberColumn("位次", min_value=0, step=1),
                "复核备注": st.column_config.TextColumn("复核备注", width="large"),
            },
            key="review_editor",
        )
        if st.button("保存复核结果", type="primary"):
            save_reviews(pending, edited)
    else:
        st.caption("当前没有待复核记录。")

    log = st.session_state.get("review_log", [])
    if log:
        with st.expander(f"查看复核审计日志（{len(log)} 条）"):
            st.dataframe(pd.DataFrame(log), width="stretch", hide_index=True)


def main() -> None:
    st.set_page_config(page_title="齐鲁品牌 AI 可见度体检", page_icon="🔎", layout="wide")
    inject_ui_styles()
    st.title("齐鲁品牌 AI 可见度体检")
    st.caption("测量 AI 是否看见品牌、在什么位置看见，以及下一步应在哪些信源补位。")
    if PUBLIC_BYOK:
        st.caption("公网 BYOK 版 · Key 仅用于当前会话 · 不写入服务器 .env · 模拟模式不调用 API")
    load_last_profile()

    mode, offline, engine_name, parse_choice = render_sidebar()
    show_flash()
    render_status(mode, offline, engine_name)

    tab_input, tab_run, tab_result, tab_review = st.tabs(["① 输入", "② 运行", "③ 结果", "④ 复核"])
    with tab_input:
        render_input_tab()
    with tab_run:
        render_run_tab(mode, offline, engine_name, parse_choice)
    with tab_result:
        render_results_tab()
    with tab_review:
        render_review_tab()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        st.error(f"页面运行异常（{type(exc).__name__}）：{exc}")