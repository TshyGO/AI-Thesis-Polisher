# 🎓 AI Thesis Polisher (论文润色神器)

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Streamlit](https://img.shields.io/badge/UI-Streamlit-red)
![License](https://img.shields.io/badge/License-MIT-green)

*(English README follows below / 英文介绍在下方)*

AI Thesis Polisher 是一款专为学术辅助设计的本地开源 Word 润色工具。它通过调用大语言模型 (LLM) API 对文档进行智能语言打磨，并结合 `win32com` 接口，将大模型输出的修改建议以原生“修订追踪 (Track Changes)”的形式写入 Microsoft Word 文档中，以保证用户对内容的绝对控制与可追溯性。

## 🎯 项目动因

现有主流模型润色方法多采用“复制粘贴对话框”的形式，面临以下局限：
1. **失去痕迹**：在长篇章学术论文中，作者难以追踪 AI 具体修改了哪些细节。
2. **过度修改**：脱离上下文的大段投喂极易导致模型扭曲原作者本身的行脉与逻辑。

本项目通过段落维度的流式处理、格式屏障保护、多轮交叉审查机制以及原生 Word 对象操纵接口，为研究者提供真正工程级别的高效、精准论文优化手段。

## ✨ 核心特性

- 📝 **原生 Word 修订映射**：系统控制本地 Word 进程读取段落，并使用 Find & Execute 将 AI 生成的新文本注入，从而原生生成 Word 红线“修订（Track Changes）”，方便作者逐句接受或拒绝。
- 🕵️ **多阶段交叉复审 (Cross-Review)**：由 Stage 1 以标准温度发散提出局部短语优化建议，随后进入 Stage 2 以最低温度 (0.1) 扮演终审角色过滤掉过度改写。极大降低了“机器生成味”。
- 🗄️ **状态缓存机制 (Checkpointing)**：由于长篇博士论文接口调用时间可能超过数小时。程序会在本地实时保存每一段落的哈希与进度缓存；处理中途崩溃、超时或退出均可从断点恢复进度，跨进程有效。
- 🛡️ **底层格式隔离保护**：利用 Range 检测技术拦截包含了上标、下标（如元素分子式、数学常量表）与 OMaths 区域的文本编辑指令，保障文档基础排版稳定。
- 🌐 **多语言与结构解耦系统**：针对不同论文情况，内置自适应段落切分模式，并在初始化阶段通过枚举 Word `Heading1` 一级大纲样式获取论文目录树结构，以便细粒度跳过指定段落（如：致谢、参考文献）。

## 🚀 部署指南

### 环境要求
- 一台已合法安装 **Microsoft Word (Office Desktop)** 的 Windows 计算机。
- **Python (3.8+)** 环境配置完成（且已加入环境变量）。

### 部署步骤
1. 下载/克隆本项目代码压缩包。
2. 双击项目根目录下的 **`start.bat`**。该脚本将执行自动化初始化流程：
   - 检查 Python 可执行环境。
   - 使用 venv 隔离创建 Python 虚拟环境。
   - 通过国内镜像源下载对应依赖包 (`streamlit`, `openai`, `pywin32`, `openpyxl`)。
   - 启动基于 Streamlit 的本地 Web UI 服务。
3. 待命令行提示服务开启后，可在被弹出的浏览器应用页进行 API 密钥设定与参数调节。最后上传目标 `.docx` 文件完成运行。

## 🔧 扩展与二次开发

该项目使用严格解耦的 MVC 型架构设计：
- `engine/llm_client.py`: 通用大模型连入中转器（全面兼容符合 OpenAI 格式的调用方接口及 DeepSeek 系列）。
- `engine/document.py`: 负责底座 Office 进程派放与清洗过滤。
- `engine/pipelines.py`: 调度工作流核心主线，进行顺序节点控制（阶段注入等）。

## 💡 开源协议
MIT License.  
期待更多的科研人员、开发者提交 PR 为这一提效工具贡献力量。

---

# 🎓 AI Thesis Polisher (English Version)

AI Thesis Polisher is an open-source, locally hosted Word document editing tool designed for academic assistance. By leveraging Large Language Model (LLM) APIs such as DeepSeek or OpenAI, it intelligently refines academic texts. Crucially, it integrates with the `win32com` interface to inject the LLM-generated editing suggestions directly into Microsoft Word as native "Track Changes". This guarantees authors absolute control and traceability over their content.

## 🎯 Motivation

Most mainstream AI-assisted writing methods rely on "copy-and-paste dialog boxes", inherently suffering from two major limitations:
1. **Loss of Traceability**: In lengthy academic papers, authors struggle to track exactly what the AI altered.
2. **Over-Editing**: Feeding massive chunks of text completely isolated from the document's structure leads to the AI severely distorting the author's original narrative and logic.

This project offers researchers a truly robust, effective, and precise manuscript optimization tool by orchestrating paragraph-level streaming, formatting barriers, an intensive multi-round cross-review mechanism, and native Word object manipulation.

## ✨ Core Features

- 📝 **Native Word Track Changes Integration**: The system handles a local Word process to parse paragraphs and uses Find & Execute to inject AI revisions into the original document, keeping all track changes visible.
- 🕵️ **Multi-Stage Cross-Review**: "Stage 1" proposes local phrasing enhancements with higher temperature divergence. Then, "Stage 2" acts as a strict jury (with Temperature 0.1) validating Stage 1's suggestions, dropping the ones that simply rewrite without academic merit.
- 🗄️ **Persistent Checkpointing**: Processing a lengthy doctoral thesis can take hours. To prevent API timeouts or network errors from ruining the whole process, the script maps progress locally via MD5 Hashes. If restarted, all previously processed paragraphs are instantly bypassed.
- 🛡️ **Format Isolation Shield**: Uses internal Range Detection to block edit commands on paragraphs containing superscripts, subscripts (e.g., chemical formulas like H₂O), and OMaths equations.
- 🌐 **Adaptive Decoupled System**: Autonomously partitions sentences relying on Chinese/English punctuation and pulls a map of the document's abstract tree structure using the `Heading 1` Word style, letting users actively ignore structural subsets like 'Acknowledgements' or 'References'.

## 🚀 Quick Start

### Pre-requisites
- A Windows PC with **Microsoft Word (Office Desktop)** properly installed.
- **Python (3.8+)** installed and added to the PATH.

### Deployment Steps
1. Download or clone this repository folder.
2. Double-click **`start.bat`** in the root directory. This script performs the standard initialization pipeline:
   - Validates Python accessibility.
   - Installs and isolates an automated venv environment.
   - Installs crucial dependencies.
   - Starts the local Streamlit Web UI.
3. Once the local URL pops up in your browser, configure your chosen `API Key` and model parameters. Finally, upload the `.docx` file and start the polishing pipeline!

## 💡 License
MIT License.  
PRs, technical discussions, and contributions are widely welcomed and encouraged!
