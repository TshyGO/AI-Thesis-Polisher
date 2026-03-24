# AI Thesis Polisher

本项目是一个在 Windows 本地运行的论文润色工具。

它会读取 `.docx` 文档，调用兼容 OpenAI 接口的大模型 API 生成修改建议，再通过 Microsoft Word 的原生修订功能把修改写回文档。结果不是纯文本覆盖，而是保留 Track Changes，方便作者逐条审阅。

项目当前使用 Streamlit 作为界面层，Word 操作依赖 `pywin32` / `win32com`。

## 适用场景

- 需要保留 Word 修订痕迹，而不是拿一份“改完但看不出改了什么”的新文档
- 论文篇幅较长，希望按段落处理，降低一次性大段投喂带来的误改
- 需要生成 Excel 报表，回看每条建议对应的句子、原因、状态和优先级

## 当前处理流程

### Stage 0

先按章节读取全文，生成章节工作笔记。笔记会概括本章研究内容、关键术语、容易误判的专业表达和文风风险区域。

### Stage 1

对每个段落按句标注后逐句审阅，提名可能需要修改的短语。Prompt 会带上：

- 章节笔记
- 前后文
- 不应修改的内容约束
- 被动句处理边界

### Stage 2

对 Stage 1 的提名结果做保守复审，过滤掉：

- 没有实质提升的改写
- 误伤专业术语、缩写、数值、引用的改写
- 不该动的合理被动句
- 破坏逻辑衔接的改写

### 输出

- Word：原文档写入修订痕迹
- Excel：按章节分 Sheet 输出审核明细

Excel 中会包含：

- 段落号
- 句子编号
- 原句
- 润色理由
- 修改前片段
- 修改后片段
- 润色后完整句
- 状态
- 优先级

## 这版新增内容

- 章节级 Stage 0 预读，不再让每段都在无全局上下文的情况下单独判断
- 更严格的 Stage 2 复审规则
- 中文 Prompt 增加被动句“该改 / 不该改”示例
- Excel 按章节分 Sheet
- 支持删除型修改写入 Word 修订
- 支持在界面中设置输出目录
- 每次运行自动把 Word 和 Excel 落盘到指定目录
- 支持对 Stage 0 / Stage 1 / Stage 2 追加自定义 Prompt 说明

## 环境要求

- Windows
- Microsoft Word（桌面版）
- Python 3.8+

## 安装与启动

### 方式一：直接运行批处理

双击项目根目录下的 `start.bat`。

它会尝试：

1. 检查 Python
2. 创建虚拟环境
3. 安装依赖
4. 启动 Streamlit

### 方式二：手动启动

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
streamlit run ui/app.py
```

## 使用说明

### 1. 配置模型

在左侧栏填写：

- `API Key`
- `Base URL`
- `Model`

支持兼容 OpenAI Chat Completions 的接口。

### 2. 设置输出目录

左侧栏可以设置“输出根目录”。

每次运行时，程序会在该目录下自动创建一个新的时间戳子目录，并把结果写进去，例如：

```text
outputs/
  20260324_201530_demo_paper/
    Polished_demo_paper.docx
    Report_demo_paper.xlsx
```

页面刷新后，如果上一次输出文件仍然存在，界面会尝试恢复最近一次结果的下载入口。

### 3. 上传文档并运行

上传 `.docx` 后，可以设置：

- 语言模式
- 润色强度
- 最小段落长度
- 是否开启交叉复审
- 需要跳过的章节

### 4. Prompt 自定义

界面里提供三个可选输入框：

- Stage 0 章节理解附加要求
- Stage 1 提名附加要求
- Stage 2 复审附加要求

这些内容会追加到内置 Prompt 末尾，而不是直接替换整个 Prompt。

这样做的目的很简单：

- 保留当前流程里已经验证过的基础约束
- 允许用户增加自己的规则
- 降低因为完全自定义 Prompt 导致格式输出失控的概率

## 目录结构

```text
engine/
  document.py        Word 读取、章节解析、修订写入
  excel_exporter.py  Excel 报表导出
  llm_client.py      大模型 API 调用
  pipelines.py       主流程：Stage 0 / 1 / 2

ui/
  app.py             Streamlit 界面
```

## 已知限制

- 依赖本机可用的 Microsoft Word
- 当前界面是 Streamlit，适合单机、本地、单用户使用，不适合做复杂任务管理
- Prompt 自定义目前是“追加说明”，不是完整模板编辑
- 某些复杂格式区域仍然会被主动跳过，以避免破坏文档结构

## 开发说明

### 运行测试

```powershell
python -m unittest test_pipeline_regressions.py
```

### 主要回归点

- 章节预读是否进入后续 Prompt
- 删除型修改是否真的写入 Word 修订
- Excel 是否按章节输出并保留关键字段

## License

MIT
