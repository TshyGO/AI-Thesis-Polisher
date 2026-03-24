import os
import sys
import time
import tempfile
import streamlit as st

# 把项目根目录加入 path 方便引入 engine
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.llm_client import LLMClient
from engine.document import DocumentProcessor
from engine.pipelines import PolishingPipeline

st.set_page_config(page_title="AI Thesis Polisher", page_icon="🎓", layout="wide")

# ================================
# 1. 侧边栏：全局账号与模型配置区
# ================================
with st.sidebar:
    st.title("⚙️ 大模型配置")
    
    # 默认填写一个兼容国内的大模型中转地址或原厂地址 (例如 DeepSeek)
    api_key = st.text_input("API Key", type="password", help="本地保存，绝不上传")
    base_url = st.text_input("Base URL", value="https://api.deepseek.com/v1", help="支持中转站或任意兼容 OpenAI 官方的端点")
    
    # 支持自定义填写模型
    model_name = st.text_input("Model", value="deepseek-chat", help="输入你要调用的模型名称 (如 gpt-4o, claude-3-5-sonnet-20240620)")
    
    st.markdown("---")
    st.markdown("""
    💡 **小贴士**：
    1. 首推采用国密级别的数据中转站以防论文泄露。
    2. 如果使用官方 OpenAI 请切记科学上网。
    """)

# ================================
# 主工作区
# ================================
st.title("🎓 论文逐句润色神器 (Open Source)")
st.markdown("基于多阶段交叉复审（Cross-Review）防止“AI味”的 Word 原生修订工具。")

# --- 文档上传区 ---
uploaded_file = st.file_uploader("上传待润色的 Word 文档 (.docx)", type=["docx"])

# --- 参数配置区 ---
if uploaded_file is not None and api_key:
    # 1. 保存临时文件以供解析
    if "work_copy_path" not in st.session_state or st.session_state.get("last_uploaded_name") != uploaded_file.name:
        temp_dir = tempfile.gettempdir()
        timestamp = int(time.time())
        work_copy_path = os.path.join(temp_dir, f"{timestamp}_{uploaded_file.name}")
        
        with open(work_copy_path, "wb") as f:
             f.write(uploaded_file.getbuffer())
             
        st.session_state["work_copy_path"] = work_copy_path
        st.session_state["last_uploaded_name"] = uploaded_file.name
    else:
        work_copy_path = st.session_state["work_copy_path"]

    # 2. 解析章节
    with st.spinner("正在解析文档章节结构（需几秒钟后台调起 Word）..."):
         try:
             # 为了避免 Streamlit 的多次重绘导致反复解析，我们可以缓存解析出的章节名称
             if "parsed_chapters" not in st.session_state or st.session_state.get("last_uploaded_name") != uploaded_file.name:
                 with DocumentProcessor() as dp:
                     dp.open_document(work_copy_path, read_only=True)
                     chapters = dp.parse_chapters()
                     st.session_state["parsed_chapters"] = [ch["name"] for ch in chapters]
                     # 由于共用了 last_uploaded_name，这里无需重复设置
         except Exception as e:
             st.error(f"解析 Word 发生错误。请确保关闭该文档且未被其他程序占用: {e}")
             st.stop()
             
    # --- UI 配置层 ---
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📝 语言与润色强度")
        language_mode = st.selectbox("论文主体语言", ["chinese", "english", "mixed"], format_func=lambda x: {"chinese":"纯中文 (只认汉字)","english":"纯英文 (只认单词)","mixed":"中英混合 (自动测算)"}[x])
        intensity = st.selectbox("润色强度 (Intensity)", ["light", "standard", "heavy"], index=1, format_func=lambda x: {"light":"轻度：仅纠正错别字与基础语病","standard":"标准：优化学术表达，去除AI味","heavy":"重度：全方位重写不通顺长句"}[x])
        min_chars = st.number_input("忽略过短的段落 (最小字符数)", value=20, min_value=1, help="低于此字数的段落（如图注、短标题）将被直接跳过")

    with col2:
        st.subheader("🔧 高级过滤与复审")
        use_cross_review = st.checkbox("开启 AI 交叉检查 (Cross-Review) 🌟核心", value=True, help="第一轮提取出修改建议后，用第二轮极小 temperature 重新复审建议，可减少90%过度修改。建议永久开启！")
        
        # 章节多选框
        all_chapters = st.session_state.get("parsed_chapters", [])
        skipped_chapters = st.multiselect(
            "需要跳过的章节 (不进行润色)", 
            options=all_chapters,
            default=[],
            help="在这里选择你不需要 AI 处理的章节（如致谢、参考文献等）"
        )
        
    st.markdown("---")
    
    # ================================
    # 运行执行区
    # ================================
    if st.button("🚀 开始一键润色 (生成修订版)", type="primary"):
        progress_text = "正在逐段处理并写入 Word，请耐心等待..."
        my_bar = st.progress(0, text=progress_text)
        status_box = st.empty()
        
        try:
            # 初始化组件
            client = LLMClient(api_key=api_key, base_url=base_url, model=model_name)
            
            # 使用上下文管理器接管 doc parser
            with DocumentProcessor() as doc_parser:
                pipeline = PolishingPipeline(
                    llm_client=client,
                    doc_parser=doc_parser,
                    config={
                        "language": language_mode,
                        "intensity": intensity,
                        "min_chars": int(min_chars),
                        "use_cross_review": use_cross_review,
                        "skipped_chapters": skipped_chapters,
                        "original_filename": uploaded_file.name
                    }
                )
                
                # 回调函数更新进度
                def on_progress(current, total):
                    pct = int(current / total * 100)
                    my_bar.progress(pct, text=f"{progress_text} ({current}/{total})")
                    status_box.info(f"⏳ 正在处理第 {current} 段 (共 {total} 段)")

                changes, excel_path = pipeline.process_document(work_copy_path, progress_callback=on_progress)
                
                my_bar.empty()
                status_box.success(f"✅ 润色完毕！共计采纳了 {changes} 处实质性修改（以保留 Word 修订痕迹）。")
                st.balloons()
                
            # 提供下载
            st.markdown("### 📥 下载中心")
            dl_col1, dl_col2 = st.columns(2)
            
            with dl_col1:
                with open(work_copy_path, "rb") as f:
                    st.download_button(
                        label="⬇️ 下载润色后的 Word 审阅版",
                        data=f,
                        file_name=f"Polished_{uploaded_file.name}",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        type="primary"
                    )
            
            with dl_col2:
                if os.path.exists(excel_path):
                    with open(excel_path, "rb") as ef:
                        st.download_button(
                            label="📊 下载 AI 润色分析报告 (Excel)",
                            data=ef,
                            file_name=f"Report_{uploaded_file.name}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                        
                
        except Exception as e:
            st.error(f"❌ 运行过程中发生错误：{e}")

elif uploaded_file is None:
    st.info("👆 请先在上方上传一个需要润色的 Word 文件。")
elif not api_key:
    st.warning("👈 请先在左边侧边栏填写有效的大模型 API Key。")
