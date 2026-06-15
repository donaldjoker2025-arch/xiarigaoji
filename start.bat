@echo off
chcp 65001 >nul
echo ============================
echo   夏日告急 - 空调电量监控
echo ============================
echo.



REM 创建虚拟环境（如果不存在）
if not exist ".venv" (
    echo [1/3] 创建虚拟环境...
    python -m venv .venv
)

echo [2/3] 安装依赖...
call .venv\Scripts\activate.bat
pip install -r requirements.txt -q

echo [3/3] 启动服务...
echo.
python app.py

pause
