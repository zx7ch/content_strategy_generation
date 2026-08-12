#!/bin/bash
# XHS Growth Agent — 本地 Runtime 启动脚本
# 双击此文件即可启动，请保持此窗口开启

cd "$(dirname "$0")"

# ── 数据目录（数据库与本机登录信息均存放于此，更新版本不影响）──
DATA_HOME="$HOME/Library/Application Support/xhs-growth-agent"

# ── 检查可执行文件 ────────────────────────────────────────────
if [ ! -f "xhs-runtime" ]; then
  echo "❌ 找不到 xhs-runtime，请确认启动脚本和 xhs-runtime 在同一文件夹"
  read -p "按回车键关闭..."
  exit 1
fi

# ── 启动 ─────────────────────────────────────────────────────
echo "✅ 正在启动 XHS Growth Agent Runtime..."
echo ""
echo "   数据目录 : $DATA_HOME"
echo ""
echo "   启动后请在浏览器打开: https://content-strategy-generation.vercel.app"
echo "   在 Creator 中点击“内容调研”，然后在右侧栏配置模型服务和小红书登录。"
echo "   关闭此窗口即可停止 Runtime"
echo ""

./xhs-runtime

echo ""
echo "Runtime 已停止。按回车键关闭窗口..."
read
