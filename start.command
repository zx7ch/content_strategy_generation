#!/bin/bash
# XHS Growth Agent — 本地 Runtime 启动脚本
# 双击此文件即可启动，请保持此窗口开启

cd "$(dirname "$0")"

# ── 数据目录（config.env 和数据库均存放于此，更新版本不影响）──
DATA_HOME="$HOME/Library/Application Support/xhs-growth-agent"
CONFIG_FILE="$DATA_HOME/config.env"

# ── 检查可执行文件 ────────────────────────────────────────────
if [ ! -f "xhs-runtime" ]; then
  echo "❌ 找不到 xhs-runtime，请确认启动脚本和 xhs-runtime 在同一文件夹"
  read -p "按回车键关闭..."
  exit 1
fi

# ── 首次运行：如果数据目录还没有 config.env，先启动一次让程序自动复制模板 ──
# （实际由 runtime_main.py 完成 seed，此处仅给出提前提示）
if [ ! -f "$CONFIG_FILE" ]; then
  echo "⚡ 首次启动，正在初始化配置文件..."
  echo "   配置文件将生成于："
  echo "   $CONFIG_FILE"
  echo ""
fi

# ── 检查 API Key 是否已填写 ──────────────────────────────────
_api_filled=false
if [ -f "$CONFIG_FILE" ]; then
  # 任意一个 *_API_KEY 有非空值即视为已配置
  if grep -E "^(ANTHROPIC|OPENAI|DEEPSEEK|MINIMAX|KIMI)_API_KEY=.+" "$CONFIG_FILE" > /dev/null 2>&1; then
    _api_filled=true
  fi
fi

if [ "$_api_filled" = false ]; then
  echo "⚠️  尚未填写 API Key，Runtime 启动后 AI 功能将无法使用。"
  echo ""
  echo "   请用文本编辑器打开并填写："
  echo "   $CONFIG_FILE"
  echo ""
  echo "   填写完毕后重启此脚本即可。"
  echo ""
  echo "   （继续启动中，您也可以启动后再填写 config.env 并重启）"
  echo ""
fi

# ── 启动 ─────────────────────────────────────────────────────
echo "✅ 正在启动 XHS Growth Agent Runtime..."
echo ""
echo "   配置文件 : $CONFIG_FILE"
echo "   数据目录 : $DATA_HOME"
echo ""
echo "   启动后请在浏览器打开: https://content-strategy-generation.vercel.app"
echo "   关闭此窗口即可停止 Runtime"
echo ""

./xhs-runtime

echo ""
echo "Runtime 已停止。按回车键关闭窗口..."
read
