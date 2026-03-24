@echo off
chcp 65001 >nul
title AI Thesis Polisher - 论文润色神器启动器

echo =======================================================
echo              AI Thesis Polisher 启动脚本             
echo =======================================================
echo.

:: 1. 检查 Python 是否安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 并添加到环境变量 (PATH)。
    echo 下载地址：https://www.python.org/downloads/
    pause
    exit /b
)

:: 2. 检查/创建虚拟环境
if not exist "venv\Scripts\activate.bat" (
    echo [初始化] 正在创建独立的 Python 虚拟环境 (首次运行可能需要几十秒)...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [错误] 虚拟环境创建失败。
        pause
        exit /b
    )
)

:: 3. 激活虚拟环境
call venv\Scripts\activate

:: 4. 安装/更新依赖
echo [依赖安装] 正在配置应用环境...
pip install -r requirements.txt

:: 5. 启动 Streamlit
echo [启动服务] 正在启动本地 Web 界面，请稍候不要关闭此窗口...
streamlit run ui/app.py

pause
