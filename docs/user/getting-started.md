# 使用入门

> 本文档面向种子用户，介绍从安装到生成第一批内容选题的完整流程。


---

## 一、你需要准备什么

| 准备项 | 说明 |
|---|---|
| Anthropic API Key | 用于驱动 AI 模型，在 [console.anthropic.com](https://console.anthropic.com) 申请 |
| 小红书账号 | 用于采集竞品内容，需要登录后获取 Cookie |
| Mac 电脑 | 当前版本仅支持 macOS |

---

## 二、首次安装

### 1. 解压安装包

我们会通过微信直接把 `xhs-runtime.zip` 发给你。收到后解压，你会看到以下文件：

```
xhs-runtime/
├── xhs-runtime        ← 核心程序（不要移动它）
├── config.env         ← 配置模板（首次启动时会复制到用户数据目录）
└── start.command      ← 双击启动
```

> 数据和真实配置存储在 `~/Library/Application Support/xhs-growth-agent/`，升级 exe 不会影响这里的数据。

### 2. 填写配置文件

第一次启动 Runtime 后，用任意文本编辑器打开真实配置文件：

```
~/Library/Application Support/xhs-growth-agent/config.env
```

填入以下内容：

```
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxx   ← 填你的 Key
XHS_SPIDER_COOKIES=                          ← 填小红书 Cookie（见下方获取步骤）
```

**如何获取小红书 Cookie：**

1. 用 Chrome 打开 [小红书网页版](https://www.xiaohongshu.com) 并登录
2. 按 `F12` 打开开发者工具 → 切换到 **Network** 标签页
3. 刷新页面，点击任意一个请求
4. 在右侧找到 **Request Headers** → 复制 **Cookie** 字段的完整内容
5. 粘贴到 `config.env` 的 `XHS_SPIDER_COOKIES=` 后面

### 3. 允许程序运行（首次需要）

macOS 默认会阻止未认证的程序运行，需要手动放行一次：

1. 右键点击 `start.command` → 选择**打开**
2. 弹窗提示"无法验证开发者"→ 点击**打开**
3. 之后双击即可直接启动，无需再次确认

---

## 三、启动 Runtime

**每次使用前需要先启动 Runtime**（保持黑色窗口开启）：

1. 双击 `start.command`
2. 屏幕上会弹出一个**终端窗口**（即 macOS 自带的 Terminal 应用，在 Launchpad → 其他 里可以找到，双击 start.command 会自动打开它）
3. 终端里出现 `✅ 正在启动...` 并显示访问地址，说明启动成功
4. 在浏览器中打开：**https://content-strategy-generation.vercel.app**

> ⚠️ 整个使用过程中请**不要关闭终端窗口**，关闭即停止服务。用完后直接关闭即可。

---

## 四、创建第一个内容策略

### 第一步：选择品牌

进入网页后，左上角选择你的品牌（首次使用默认为「轻量户外」示例品牌）。

如需创建自己的品牌：点击顶部导航 **品牌设置** → 填写品牌信息 → 保存。

### 第二步：进入创作台

点击顶部导航 **创作台** → 点击右上角 **新建任务**。

### 第三步：描述你的需求

在对话框中用自然语言描述你的内容需求，例如：

> 我想做防晒产品的内容，目标人群是 25-35 岁爱户外运动的女性，希望找到差异化的选题方向。

Agent 会依次执行以下步骤（每步完成后会告知进度）：

| 步骤 | 内容 | 大约时长 |
|---|---|---|
| 采集数据 | 爬取小红书相关内容 | 2-5 分钟 |
| 生成策略 | 分析内容机会和差异化方向 | 1-2 分钟 |
| 生成选题 | 产出可发布的笔记草稿 | 3-5 分钟 |

### 第四步：查看并完成

全部步骤完成后，点击 **已完成** 按钮，系统会将生成的内容存入选题库。

点击弹出提示中的 **查看选题库** 链接，即可看到所有生成的选题和笔记草稿。

---

## 五、选题库说明

选题库展示所有完成任务后积累的内容，每条记录包含：

- **选题标题** — 可直接用于笔记标题
- **核心假设** — 这个选题的底层逻辑
- **预测潜力分** — AI 预测的内容潜力（0-1，越高越好）
- **笔记正文** — 展开查看完整内容草稿

---

## 六、注意事项

- **保持 Runtime 窗口开启**：关闭终端窗口会停止所有服务
- **小红书 Cookie 有效期约 7 天**：过期后需重新获取并更新 `~/Library/Application Support/xhs-growth-agent/config.env`
- **数据本地存储**：所有生成内容和真实配置存储在 `~/Library/Application Support/xhs-growth-agent/`，不会上传到服务器；升级 Runtime 不影响历史数据或已填写配置
- **配置模板更新**：升级后如果新版模板增加了配置项，Runtime 会自动追加缺失项到真实 `config.env`，并生成备份文件；请按终端提示检查新增配置
- **API 费用**：每次完整任务约消耗 $0.5-2 的 Anthropic API 费用（取决于内容量）

---

遇到问题？查看 [故障排查](./troubleshooting.md)
