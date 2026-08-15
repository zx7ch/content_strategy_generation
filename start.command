#!/bin/bash

# XHS Growth Agent — 本地 Runtime 启动脚本
# 双击此文件即可启动，请保持此窗口开启

cd "$(dirname "$0")" || exit 1

# ── 网络代理 ──────────────────────────────────────────────────
#
# 优先保留启动环境显式提供的 HTTP/HTTPS 代理。
# 如果没有，再读取 macOS 当前启用的静态系统代理。
# 没有代理属于正常情况，不警告、不阻止 Runtime 启动。
# TUN/VPN 全局路由不需要设置环境变量。

get_system_proxy_value() {
    printf '%s\n' "$SYSTEM_PROXY_CONFIG" |
        awk -v key="$1" '$1 == key { print $3; exit }'
}

PROXY_CONFIGURED=0

EXISTING_HTTP_PROXY="${HTTP_PROXY:-${http_proxy:-}}"
EXISTING_HTTPS_PROXY="${HTTPS_PROXY:-${https_proxy:-}}"

# 1. 优先使用启动环境已有的代理配置
if [ -n "$EXISTING_HTTP_PROXY" ] || [ -n "$EXISTING_HTTPS_PROXY" ]; then
    if [ -n "$EXISTING_HTTP_PROXY" ]; then
        export HTTP_PROXY="$EXISTING_HTTP_PROXY"
        export http_proxy="$EXISTING_HTTP_PROXY"
    fi

    if [ -n "$EXISTING_HTTPS_PROXY" ]; then
        export HTTPS_PROXY="$EXISTING_HTTPS_PROXY"
        export https_proxy="$EXISTING_HTTPS_PROXY"
    fi

    PROXY_CONFIGURED=1
    PROXY_SOURCE="启动环境"

# 2. 否则读取 macOS 当前系统代理
else
    SCUTIL_BIN="${SCUTIL_BIN:-/usr/sbin/scutil}"

    if [ -x "$SCUTIL_BIN" ]; then
        SYSTEM_PROXY_CONFIG="$("$SCUTIL_BIN" --proxy 2>/dev/null || true)"
    else
        SYSTEM_PROXY_CONFIG=""
    fi

    HTTP_ENABLED="$(get_system_proxy_value HTTPEnable)"
    HTTP_HOST="$(get_system_proxy_value HTTPProxy)"
    HTTP_PORT="$(get_system_proxy_value HTTPPort)"

    HTTPS_ENABLED="$(get_system_proxy_value HTTPSEnable)"
    HTTPS_HOST="$(get_system_proxy_value HTTPSProxy)"
    HTTPS_PORT="$(get_system_proxy_value HTTPSPort)"

    if [ "$HTTP_ENABLED" = "1" ] &&
       [ -n "$HTTP_HOST" ] &&
       [ -n "$HTTP_PORT" ]; then
        export HTTP_PROXY="http://${HTTP_HOST}:${HTTP_PORT}"
        export http_proxy="$HTTP_PROXY"
        PROXY_CONFIGURED=1
    fi

    if [ "$HTTPS_ENABLED" = "1" ] &&
       [ -n "$HTTPS_HOST" ] &&
       [ -n "$HTTPS_PORT" ]; then
        export HTTPS_PROXY="http://${HTTPS_HOST}:${HTTPS_PORT}"
        export https_proxy="$HTTPS_PROXY"
        PROXY_CONFIGURED=1
    fi

    if [ "$PROXY_CONFIGURED" = "1" ]; then
        PROXY_SOURCE="macOS 系统设置"
    fi
fi

# 本地 Runtime 通信不经过代理
if [ "$PROXY_CONFIGURED" = "1" ]; then
    EXISTING_NO_PROXY="${NO_PROXY:-${no_proxy:-}}"

    if [ -n "$EXISTING_NO_PROXY" ]; then
        export NO_PROXY="${EXISTING_NO_PROXY},localhost,127.0.0.1,::1"
    else
        export NO_PROXY="localhost,127.0.0.1,::1"
    fi

    export no_proxy="$NO_PROXY"

    echo "✅ 已应用 ${PROXY_SOURCE} 中的网络代理配置"
fi

# ── 数据目录（数据库与本机登录信息均存放于此，更新版本不影响）──

DATA_HOME="$HOME/Library/Application Support/xhs-growth-agent"

# ── 检查可执行文件 ────────────────────────────────────────────

if [ ! -f "xhs-runtime" ]; then
    echo "❌ 找不到 xhs-runtime，请确认启动脚本和 xhs-runtime 在同一文件夹"
    read -p "按回车键关闭..."
    exit 1
fi

# ── 启动 ─────────────────────────────────────────────────────

echo ""
echo "✅ 正在启动 XHS Growth Agent Runtime..."
echo ""
echo "   数据目录 : $DATA_HOME"
echo ""
echo "   启动后请在浏览器打开:"
echo "   https://content-strategy-generation.vercel.app"
echo ""
echo "   在 Creator 中点击“内容调研”，然后在右侧栏配置模型服务和小红书登录。"
echo "   关闭此窗口即可停止 Runtime"
echo ""

./xhs-runtime

echo ""
echo "Runtime 已停止。按回车键关闭窗口..."
read