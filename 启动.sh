#!/usr/bin/env bash
echo "============================================================"
echo " 门店交接班差异登记工具 - 启动"
echo "============================================================"

if ! python3 -c "import flask" 2>/dev/null; then
  echo "[*] 正在安装依赖..."
  python3 -m pip install -r requirements.txt
fi

echo "[*] 启动服务..."
python3 app.py
