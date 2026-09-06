import os
import sys
import time
import json
import shutil
import uuid
import hashlib
from pathlib import Path

import streamlit as st
import pythoncom

try:
    pythoncom.CoInitialize()
except Exception:
    pass

import logging

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler("app_debug.log", encoding="utf-8", mode="a"),
            logging.StreamHandler(sys.stdout),
        ],
    )

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_ROOT / "user_config.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs"
UPLOAD_CACHE_ROOT = PROJECT_ROOT / "cache" / "uploads"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(config: dict):
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def update_config(patch: dict):
    cfg = load_config()
    cfg.update(patch)
    save_config(cfg)


def sanitize_filename(name: str) -> str:
    cleaned = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in name).strip()
    return cleaned or "document"


def ensure_directory(path_value: str) -> str:
    path = Path(path_value).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return str(path.resolve())


def build_run_output_dir(output_root: str, original_name: str) -> str:
    root = Path(ensure_directory(output_root))
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    stem = sanitize_filename(Path(original_name).stem)
    run_dir = root / f"{timestamp}_{stem}_{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return str(run_dir)


def persist_result_to_session(word_path: str, excel_path: str = ""):
    if word_path and os.path.exists(word_path):
        with open(word_path, "rb") as f:
            st.session_state["result_docx_bytes"] = f.read()
        st.session_state["result_docx_name"] = os.path.basename(word_path)
        st.session_state["result_docx_path"] = word_path

    if excel_path and os.path.exists(excel_path):
        with open(excel_path, "rb") as f:
            st.session_state["result_excel_bytes"] = f.read()
        st.session_state["result_excel_name"] = os.path.basename(excel_path)
        st.session_state["result_excel_path"] = excel_path


def hydrate_last_result_from_config(config: dict):
    if st.session_state.get("result_docx_bytes"):
        return

    last_result = config.get("last_result", {})
    word_path = last_result.get("word_path", "")
    excel_path = last_result.get("excel_path", "")

    if word_path and os.path.exists(word_path):
        persist_result_to_session(word_path, excel_path)
        st.session_state["last_output_dir"] = last_result.get("output_dir", os.path.dirname(word_path))


user_cfg = load_config()
hydrate_last_result_from_config(user_cfg)

sys.path.append(str(PROJECT_ROOT))

from engine.stage_models import build_stage_clients, persistable_overrides, saved_http_opt_in, DEFAULTS
from engine.document import DocumentProcessor
from engine.sentence_pipeline import SentencePolishingPipeline as PolishingPipeline
from engine.uploads import upload_identity

st.set_page_config(page_title="AI Thesis Polisher", page_icon="🎓", layout="wide")

default_output_root = ensure_directory(str(user_cfg.get("output_root", DEFAULT_OUTPUT_ROOT)))
prompt_cfg = user_cfg.get("prompt_customization", {}) or {}

with st.sidebar:
    st.title("⚙️ 大模型与输出设置")

    api_key = st.text_input("API Key", type="password", value=user_cfg.get("api_key", ""), help="用于向配置的模型接口鉴权；保存账号会写入本地配置")
    base_url = st.text_input("Base URL", value=user_cfg.get("base_url", "https://api.deepseek.com/v1"), help="支持中转站或任意兼容 OpenAI 官方的端点")
    allow_http = False
    if base_url.lower().startswith('http://'):
        st.warning('HTTP 不加密传输。已保存的主接口保留兼容；新地址需明确授权，建议改用 HTTPS。')
        allow_http = st.checkbox('允许当前主接口使用 HTTP', value=saved_http_opt_in(user_cfg, base_url),
            key='http-main-' + hashlib.sha256(base_url.encode()).hexdigest())
    model_name = st.text_input("Model", value=user_cfg.get("model", "deepseek-chat"), help="输入你要调用的模型名称")
    output_root = st.text_input("输出根目录", value=default_output_root, help="每次运行会在这里自动创建一个时间戳子目录")

    review_modes = ['off', 'same', 'independent']
    saved_mode = user_cfg.get('review_mode', 'same')
    review_mode = st.selectbox('复审模式', review_modes,
        index=review_modes.index(saved_mode) if saved_mode in review_modes else 1,
        format_func=lambda mode: {'off': '不复审（最低调用量）', 'same': '同模型复审', 'independent': '独立模型／接口复审'}[mode])
    stage_enabled = st.checkbox('分阶段模型配置', value=user_cfg.get('stage_models_enabled', False))
    stage_settings = persistable_overrides(user_cfg.get('stage_models', {}))
    if stage_enabled:
        for stage, label in [('understanding', '章节理解'), ('editor', '编辑'), ('reviewer', '复审')]:
            saved = stage_settings.get(stage, {})
            with st.expander(label + '模型设置'):
                st.caption('模型和地址留空则沿用主配置。不同地址必须填写自己的 Key；阶段 Key 仅留在本次会话。')
                if stage == 'reviewer':
                    st.caption('同模型模式沿用编辑阶段模型和 Key；关闭复审时以下设置不生效。')
                stage_model = st.text_input(label + ' Model', value=saved.get('model', ''), placeholder=model_name)
                stage_url = st.text_input(label + ' Base URL', value=saved.get('base_url', ''), placeholder=base_url)
                stage_http = False
                if stage_url.lower().startswith('http://'):
                    stage_http = st.checkbox(label + '：明确允许此地址使用 HTTP（不加密）',
                        value=saved.get('base_url') == stage_url and saved.get('allow_insecure_http', False) is True,
                        key='http-' + stage + hashlib.sha256(stage_url.encode()).hexdigest())
                stage_settings[stage] = {
                    'model': stage_model,
                    'base_url': stage_url,
                    'allow_insecure_http': stage_http,
                    'api_key': st.text_input(label + ' API Key', type='password'),
                    'temperature': st.number_input(label + ' temperature', min_value=0.0, max_value=2.0,
                        value=float(saved.get('temperature', DEFAULTS[stage][0])), step=0.1),
                    'timeout': int(st.number_input(label + ' 超时（秒）', min_value=1, max_value=600,
                        value=int(saved.get('timeout', DEFAULTS[stage][1])))),
                    'max_retries': int(st.number_input(label + ' 尝试次数（含首次）', min_value=1, max_value=5,
                        value=int(saved.get('max_retries', 3)))),
                }
    if review_mode == 'independent' and not stage_enabled:
        st.info('独立复审需开启分阶段配置，并指定不同的复审模型或接口。')

    if st.button("💾 保存账号/模型/输出设置"):
        try:
            normalized_output_root = ensure_directory(output_root)
            update_config({
                "api_key": api_key,
                "base_url": base_url,
                'allow_insecure_http': allow_http,
                "model": model_name,
                "output_root": normalized_output_root,
                'review_mode': review_mode,
                'stage_models_enabled': stage_enabled,
                'stage_models': persistable_overrides(stage_settings),
            })
            st.success(f"配置已保存，本地输出目录：{normalized_output_root}")
        except Exception as e:
            st.error(f"保存配置失败：{e}")

    st.markdown("---")
    st.caption("结果不会只留在页面里。每次处理结束后，Word 和 Excel 都会落到你指定的输出目录。")

st.title("🎓 论文逐句润色神器 (Open Source)")
st.markdown("基于多阶段交叉复审（Cross-Review）防止“AI味”的 Word 原生修订工具。")

uploaded_file = st.file_uploader("上传待润色的 Word 文档 (.docx)", type=["docx"])

if uploaded_file is not None and api_key:
    UPLOAD_CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    upload_hash, upload_changed = upload_identity(uploaded_file.getbuffer(), uploaded_file.name, st.session_state)
    if upload_changed:
        timestamp = int(time.time())
        safe_upload_name = sanitize_filename(uploaded_file.name)
        work_copy_path = UPLOAD_CACHE_ROOT / f"{upload_hash}_{safe_upload_name}"
        with open(work_copy_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        st.session_state["work_copy_path"] = str(work_copy_path)
        st.session_state["last_uploaded_name"] = uploaded_file.name
        st.session_state["last_uploaded_hash"] = upload_hash
        st.session_state.pop("parsed_chapters", None)
    else:
        work_copy_path = Path(st.session_state["work_copy_path"])

    with st.spinner("正在解析文档章节结构（需几秒钟后台调起 Word）..."):
        try:
            if "parsed_chapters" not in st.session_state:
                with DocumentProcessor() as dp:
                    dp.open_document(str(work_copy_path), read_only=True)
                    chapters = dp.parse_chapters()
                    st.session_state["parsed_chapters"] = [ch["name"] for ch in chapters]
        except Exception as e:
            st.error(f"解析 Word 发生错误。请确保关闭该文档且未被其他程序占用: {e}")
            st.stop()

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("📝 语言与润色强度")
        language_mode = st.selectbox(
            "论文主体语言",
            ["chinese", "english", "mixed"],
            format_func=lambda x: {
                "chinese": "纯中文 (只认汉字)",
                "english": "纯英文 (只认单词)",
                "mixed": "中英混合 (自动测算)",
            }[x],
        )
        intensity = st.selectbox(
            "润色强度 (Intensity)",
            ["light", "standard", "heavy"],
            index=1,
            format_func=lambda x: {
                "light": "轻度：仅纠正错别字与基础语病",
                "standard": "标准：优化学术表达，去除AI味",
                "heavy": "重度：全方位重写不通顺长句",
            }[x],
        )
        min_chars = st.number_input("忽略过短的段落 (最小字符数)", value=20, min_value=1, help="低于此字数的段落（如图注、短标题）将被直接跳过")

    with col2:
        st.subheader("🔧 复审与输出")
        use_cross_review = review_mode != 'off'
        st.caption('复审模式在左侧设置；跨模型不代表质量必然更高。')
        all_chapters = st.session_state.get("parsed_chapters", [])
        skipped_chapters = st.multiselect(
            "需要跳过的章节",
            options=all_chapters,
            default=[],
            help="如致谢、参考文献等可跳过",
        )
        st.text_input("本次输出根目录", value=output_root, disabled=True, help="如需修改，请到左侧设置区更改")

    with st.expander("🧠 Prompt 自定义", expanded=False):
        st.caption("现在不是完全黑箱了。内置 Prompt 仍保留，下面三栏会追加到对应 Stage 的 Prompt 末尾。这样更稳，也足够让你定规则。")
        stage0_extra = st.text_area(
            "Stage 0 章节理解附加要求",
            value=prompt_cfg.get("stage0", ""),
            height=100,
            help="例如：额外关注术语统一、材料体系、章节主线。",
        )
        stage1_extra = st.text_area(
            "Stage 1 提名附加要求",
            value=prompt_cfg.get("stage1", ""),
            height=140,
            help="例如：更保守地处理被动句，或重点清理某些套话。",
        )
        stage2_extra = st.text_area(
            "Stage 2 复审附加要求",
            value=prompt_cfg.get("stage2", ""),
            height=140,
            help="例如：严格保留实验过程描述，不接受风格性换写。",
        )

        prompt_col1, prompt_col2 = st.columns(2)
        with prompt_col1:
            if st.button("💾 保存 Prompt 自定义"):
                update_config({
                    "prompt_customization": {
                        "stage0": stage0_extra,
                        "stage1": stage1_extra,
                        "stage2": stage2_extra,
                    }
                })
                st.success("Prompt 自定义已保存到本地。")
        with prompt_col2:
            st.caption("Prompt 结构：Stage 0 章节预读 -> Stage 1 提名 -> Stage 2 复审。")

        with st.expander("查看当前 Prompt 结构说明", expanded=False):
            st.markdown(
                """
`Stage 0`：整章预读，产出关键术语、风险区域、逻辑脉络。

`Stage 1`：逐段逐句审，结合章节笔记、上下文、被动句边界示例，提出候选修改。

`Stage 2`：对候选修改做保守复审，过滤掉术语误判、无实质提升、合理被动句被硬改等情况。
                """
            )

    st.markdown("---")

    if st.button("🚀 开始一键润色 (生成修订版)", type="primary"):
        progress_text = "正在逐段处理并写入 Word，请耐心等待..."
        my_bar = st.progress(0, text=progress_text)
        status_box = st.empty()
        clients = None

        try:
            normalized_output_root = ensure_directory(output_root)
            run_output_dir = build_run_output_dir(normalized_output_root, uploaded_file.name)
            word_output_name = f"Polished_{sanitize_filename(uploaded_file.name)}"
            excel_output_name = f"Report_{sanitize_filename(Path(uploaded_file.name).stem)}.xlsx"

            clients = build_stage_clients({'api_key': api_key, 'base_url': base_url, 'model': model_name, 'allow_insecure_http': allow_http},
                stage_settings if stage_enabled else {}, review_mode)

            with DocumentProcessor() as doc_parser:
                pipeline = PolishingPipeline(
                    llm_client=clients['editor'],
                    stage_clients=clients,
                    doc_parser=doc_parser,
                    config={
                        "language": language_mode,
                        "intensity": intensity,
                        "min_chars": int(min_chars),
                        "use_cross_review": use_cross_review,
                        "skipped_chapters": skipped_chapters,
                        "original_filename": uploaded_file.name,
                        "output_dir": run_output_dir,
                        "excel_output_filename": excel_output_name,
                        "prompt_customization": {
                            "stage0": stage0_extra,
                            "stage1": stage1_extra,
                            "stage2": stage2_extra,
                        },
                    },
                )

                def on_progress(current, total):
                    pct = int(current / total * 100)
                    my_bar.progress(pct, text=f"{progress_text} ({current}/{total})")
                    status_box.info(f"⏳ 正在处理第 {current} 段 (共 {total} 段)")

                word_output_path = os.path.join(run_output_dir, word_output_name)
                shutil.copy2(str(work_copy_path), word_output_path)
                changes, excel_path = pipeline.process_document(word_output_path, progress_callback=on_progress)

            word_output_path = os.path.join(run_output_dir, word_output_name)

            persist_result_to_session(word_output_path, excel_path)
            with st.expander('本次模型路由与调用记录'):
                st.json(pipeline.run_summary)
            st.session_state["last_output_dir"] = run_output_dir

            update_config({
                "output_root": normalized_output_root,
                "prompt_customization": {
                    "stage0": stage0_extra,
                    "stage1": stage1_extra,
                    "stage2": stage2_extra,
                },
                "last_result": {
                    "output_dir": run_output_dir,
                    "word_path": word_output_path,
                    "excel_path": excel_path,
                },
            })

            my_bar.empty()
            failures = sum(r.get("status", "").endswith(("ERROR", "FAILED", "TIMEOUT")) or r.get("status") == 'SKIPPED_UNSUPPORTED' for r in pipeline.last_records)
            if failures:
                status_box.warning(f"处理结束，但有 {failures} 条失败或结构跳过记录；请检查 Excel 状态列。修订 {changes} 句。输出：{run_output_dir}")
            else:
                status_box.success(f"✅ 润色完毕！共计修订了 {changes} 句。输出目录：{run_output_dir}")

        except Exception as e:
            st.error(f"❌ 运行过程中发生错误：{e}")
        finally:
            for client in (clients or {}).values():
                try:
                    client.client.close()
                except Exception as error:
                    logging.getLogger('stage_clients').warning('Client cleanup failed (%s)', type(error).__name__)

if st.session_state.get("result_docx_bytes"):
    st.markdown("### 📥 下载与输出")
    if st.session_state.get("last_output_dir"):
        st.info(f"最近一次输出目录：{st.session_state['last_output_dir']}")

    if st.session_state.get("result_docx_path"):
        st.caption(f"Word：{st.session_state['result_docx_path']}")
    if st.session_state.get("result_excel_path"):
        st.caption(f"Excel：{st.session_state['result_excel_path']}")

    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        st.download_button(
            label="⬇️ 下载润色后的 Word 审阅版",
            data=st.session_state["result_docx_bytes"],
            file_name=st.session_state["result_docx_name"],
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary",
        )
    with dl_col2:
        if st.session_state.get("result_excel_bytes"):
            st.download_button(
                label="📊 下载 AI 润色分析报告 (Excel)",
                data=st.session_state["result_excel_bytes"],
                file_name=st.session_state["result_excel_name"],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
