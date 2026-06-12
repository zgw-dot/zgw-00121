@echo off
chcp 65001 >nul
echo ============================================================
echo   门店交接班差异登记工具 - 启动
echo ============================================================

REM 检查 Flask 是否已安装
python -c "import flask" 2>nul
if errorlevel 1 (
  echo [*] 正在安装依赖...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [!] 依赖安装失败，请手动执行: pip install -r requirements.txt
    pause
    exit /b 1
  )
)

echo [*] 启动服务...
python app.py
pause
