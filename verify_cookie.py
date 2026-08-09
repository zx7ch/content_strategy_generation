#!/usr/bin/env python3
"""
验证小红书 Cookie 有效性
========================
直接测试 Cookie 是否能成功获取搜索结果
"""

import os
import sys
import asyncio
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def test_cookie_direct():
    """直接测试 Cookie（绕过 spider 封装，测试底层 API）"""
    import requests
    import time

    cookie = os.environ.get("XHS_SPIDER_COOKIES", "")
    if not cookie:
        print("❌ 错误: 未设置 XHS_SPIDER_COOKIES 环境变量")
        return False

    print("="*70)
    print("🔍 验证小红书 Cookie 有效性")
    print("="*70)
    print(f"\n📋 Cookie 长度: {len(cookie)} 字符")
    print(f"📋 Cookie 前 100 字符: {cookie[:100]}...")

    # 提取关键字段
    key_fields = ["web_session", "a1", "xsecappid", "acw_tc", "webId"]
    found_fields = []
    for field in key_fields:
        if field in cookie:
            found_fields.append(field)
    print(f"📋 关键字段检测: {', '.join(found_fields)}")

    # 构建小红书搜索 API 请求
    url = "https://edith.xiaohongshu.com/api/sns/web/v1/search/notes"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.xiaohongshu.com",
        "Referer": "https://www.xiaohongshu.com/",
        "Cookie": cookie,
    }

    # 构建请求体（简化版搜索请求）
    payload = {
        "keyword": "测试",
        "page": 1,
        "page_size": 10,
        "search_id": f"verify_{int(time.time())}",
        "sort": "general",
    }

    print(f"\n🌐 发送测试请求...")
    print(f"   URL: {url}")
    print(f"   关键词: 测试")

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30
        )

        print(f"\n📊 响应状态:")
        print(f"   HTTP Status: {response.status_code}")
        print(f"   Content-Type: {response.headers.get('Content-Type', 'N/A')}")
        print(f"   Content-Length: {len(response.text)} bytes")

        if response.status_code == 200:
            try:
                data = response.json()
                if data.get("success") or data.get("data"):
                    notes = data.get("data", {}).get("notes", [])
                    print(f"\n✅ Cookie 验证成功!")
                    print(f"   返回笔记数: {len(notes)}")
                    if notes:
                        print(f"   第一条笔记: {notes[0].get('title', 'N/A')[:50]}...")
                    return True
                else:
                    print(f"\n⚠️  API 返回失败")
                    print(f"   错误信息: {data.get('msg', 'Unknown')}")
                    return False
            except json.JSONDecodeError:
                print(f"\n⚠️  响应不是有效 JSON")
                print(f"   原始响应: {response.text[:200]}...")
                return False
        else:
            print(f"\n❌ HTTP 请求失败")
            print(f"   状态码: {response.status_code}")
            print(f"   响应: {response.text[:500]}...")

            # 检查特定错误
            if response.status_code == 401:
                print(f"\n💡 提示: Cookie 可能已过期 (401 Unauthorized)")
            elif response.status_code == 403:
                print(f"\n💡 提示: 可能触发反爬机制 (403 Forbidden)")
            elif response.status_code == 429:
                print(f"\n💡 提示: 请求过于频繁 (429 Too Many Requests)")
            return False

    except requests.exceptions.Timeout:
        print(f"\n❌ 请求超时 (30s)")
        print(f"💡 可能原因: 网络问题或小红书服务器无响应")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"\n❌ 连接错误")
        print(f"   错误: {e}")
        return False
    except Exception as e:
        print(f"\n❌ 未知错误")
        print(f"   错误类型: {type(e).__name__}")
        print(f"   错误信息: {e}")
        return False


def test_spider_module():
    """测试 Spider 模块是否能正常工作"""
    print("\n" + "="*70)
    print("🔍 测试 Spider 模块集成")
    print("="*70)

    import os
    import sys
    from pathlib import Path

    submodule_path = Path(__file__).parent / "app" / "ingest" / "xhs_spider"
    if not submodule_path.exists():
        print(f"\n❌ Submodule 目录不存在: {submodule_path}")
        return False

    # 添加 submodule 路径
    if str(submodule_path) not in sys.path:
        sys.path.insert(0, str(submodule_path))

    # 切换到 submodule 目录（因为代码里有相对路径引用）
    original_dir = os.getcwd()

    try:
        os.chdir(str(submodule_path))

        # 尝试导入
        print("\n📦 检查模块导入...")
        from apis.xhs_pc_apis import XHS_Apis
        print("   ✅ XHS_Apis 导入成功")

        # 尝试初始化
        print("\n🔧 初始化 API 客户端...")
        api = XHS_Apis()
        print("   ✅ API 客户端初始化成功")

        return True

    except Exception as e:
        print(f"\n❌ Spider 模块测试失败")
        print(f"   错误: {type(e).__name__}: {e}")
        return False
    finally:
        os.chdir(original_dir)


def suggest_fix():
    """提供修复建议"""
    print("\n" + "="*70)
    print("💡 修复建议")
    print("="*70)
    print("""
如果 Cookie 验证失败，请按以下步骤获取新 Cookie:

1. 打开浏览器，访问 https://www.xiaohongshu.com
2. 登录你的小红书账号
3. 按 F12 打开开发者工具
4. 切换到 Network (网络) 标签
5. 在页面中搜索任意内容（如"穿搭"）
6. 找到名为 "notes" 或包含 "search" 的请求
7. 右键点击请求 -> Copy -> Copy as cURL (bash)
8. 从 cURL 命令中提取 Cookie 部分

或者使用浏览器控制台:
1. 按 F12 打开开发者工具
2. 切换到 Console (控制台) 标签
3. 输入: document.cookie
4. 复制输出的完整 Cookie 字符串

⚠️ 注意:
- Cookie 通常有时效性，可能需要定期更新
- 小红书可能会检测异常登录，建议在常用设备上获取
- 复制 Cookie 后不要分享给他人，保护账号安全
""")


def main():
    # 测试 Spider 模块
    spider_ok = test_spider_module()

    # 测试 Cookie
    cookie_ok = test_cookie_direct()

    # 总结
    print("\n" + "="*70)
    print("📋 验证总结")
    print("="*70)
    print(f"   Spider 模块: {'✅ 正常' if spider_ok else '❌ 异常'}")
    print(f"   Cookie 有效性: {'✅ 有效' if cookie_ok else '❌ 无效/过期'}")

    if not cookie_ok:
        suggest_fix()
        return 1

    print("\n🎉 所有验证通过！现在可以运行端到端 Strategy Agent 测试")
    return 0


if __name__ == "__main__":
    exit(main())
