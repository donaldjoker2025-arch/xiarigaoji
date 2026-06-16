@echo off
chcp 65001 >nul
echo ============================================
echo   夏日告急 - 打包为 Windows exe (PyInstaller)
echo ============================================
echo.

REM 使用项目虚拟环境
if not exist ".venv\Scripts\python.exe" (
    echo [X] 未找到 .venv，请先运行 start.bat 创建虚拟环境并安装依赖
    pause
    exit /b 1
)

echo [1/3] 确保 PyInstaller 已安装...
call .venv\Scripts\python.exe -m pip install pyinstaller -q

echo [2/3] 清理旧产物...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

echo [3/3] 开始打包（单文件，首次较慢）...
call .venv\Scripts\python.exe -m PyInstaller xrtj.spec --noconfirm

echo.
if exist "dist\夏日告急.exe" (
    echo [OK] 打包完成：dist\夏日告急.exe
    echo     把这个 exe 发给用户即可，双击运行（需连校园网）。
    echo     注意：data\ 与 secrets\ 不要随 exe 一起发出去。
) else (
    echo [X] 打包失败，请查看上方日志。
)
echo.
pause
