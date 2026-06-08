# Release & Distribution 问题修复

**日期**：2026-06-01  
**影响范围**：打包发布流程、用户首次安装与升级体验（仅 macOS）  
**涉及文件**：`runtime_main.py`、`app/config.py`、`start.command`、`.env.example`、`runtime_main.spec`、`scripts/build_runtime.sh`、`.github/workflows/release.yml`、`.github/workflows/ci.yml`、`README.md`

---

## 问题一：`config.env` 升级时被新模板覆盖

### 现象
用户解压新版本 zip 覆盖旧目录后，exe 目录里的 `config.env` 模板可能被替换。若用户曾直接编辑 exe 目录里的配置，API Key 会丢失，Runtime 启动后所有 AI 功能失败。

### 根本原因
旧流程把配置模板放在 exe 目录，容易让用户误以为应该直接编辑该目录中的 `config.env`。同时，若构建脚本从开发者本地 `config.env` 复制模板，也存在误把本地密钥打入发布包的风险。

### 修复
将 `config.env` 的存储位置迁移到用户数据目录（与数据库并列），exe 目录只保留空模板用于首次安装引导：

**`runtime_main.py`**：
```python
_config_in_data = os.path.join(_data_home, "config.env")
_config_in_exe  = os.path.join(_base, "config.env")

# 首次安装：从 exe 目录模板 seed 到数据目录
if not os.path.exists(_config_in_data):
    if os.path.exists(_config_in_exe):
        shutil.copy2(_config_in_exe, _config_in_data)
    else:
        open(_config_in_data, "w").close()

# 永远从数据目录加载（升级不影响）
_load_env_file(_config_in_data)
```

升级后用户配置文件路径：
- macOS：`~/Library/Application Support/xhs-growth-agent/config.env`

### 模板升级感知

为避免“保留旧配置”导致用户错过新版模板字段，Runtime 启动时还会比较：

- 用户真实配置：`~/Library/Application Support/xhs-growth-agent/config.env`
- 发布包模板：`./config.env`（由 `.env.example` 生成）

若模板中存在用户配置缺失的 key：

1. 先创建 `config.env.bak-YYYYMMDD-HHMMSS` 备份
2. 将缺失的 `KEY=value` 行追加到真实 `config.env` 末尾
3. 在终端输出新增 key 列表和配置文件路径，提醒用户检查

该逻辑只追加缺失 key，不覆盖用户已有值。

---

## 问题二：`start.command` 数据目录显示路径错误

### 现象
启动脚本打印 `数据目录: $(pwd)/data/`，但实际数据存储在 `~/Library/Application Support/xhs-growth-agent/`。用户去错误路径寻找数据库文件，排查问题时严重误导。

### 修复
**`start.command`**：
- 修正数据目录显示为真实路径
- 同时显示配置文件的准确位置
- 增加 API Key 未填写检测，启动时给出明确提示而不是静默运行后报错

---

## 问题三：版本号三处不同步

### 现象
- `pyproject.toml`：`2.0.0`
- `app/config.py` hardcode：`0.1.0`
- `/health` 端点返回 `0.1.0`

前端依赖 `/health` 做版本兼容检查，拿到的是错误版本号。

### 修复
**`app/config.py`** 改用 `importlib.metadata` 从 `pyproject.toml` 读取版本，不再手写：

```python
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    _RUNTIME_VERSION = _pkg_version("xhs-note-generator")
except PackageNotFoundError:
    _RUNTIME_VERSION = "dev"

class Settings(BaseSettings):
    RUNTIME_VERSION: str = Field(default=_RUNTIME_VERSION, ...)
```

**`runtime_main.spec`** 加入 `copy_metadata("xhs-note-generator")`，确保打包后的 bundle 里包含 dist-info，`importlib.metadata` 在 frozen 环境中可正常读取版本号。

同时，发布包中的 `config.env` 固定由 tracked 的 `.env.example` 生成，不再读取开发者本地被 ignore 的 `config.env`。

**`scripts/build_runtime.sh`** 构建前加 `pip install -e .`，生成 dist-info。

---

## 新增：GitHub Actions 自动化发布流程

### 背景
原发布方式为手动运行 PyInstaller + 手动分发 zip，无版本保护，只支持开发者当前平台（macOS arm64）。

### 新增文件

**`.github/workflows/release.yml`**：
- 触发条件：`git tag v*`
- 构建矩阵：macOS arm64、macOS x86_64
- 版本校验：tag 与 `pyproject.toml` 不一致直接 fail，防止错误发布
- 构建产物自动上传到 GitHub Release 页面

**`.github/workflows/ci.yml`**：
- 触发条件：PR / push to master
- 自动运行 lint（ruff）+ 单元测试

### 发版流程（实现后）
```bash
# 1. 改 pyproject.toml 版本号（唯一入口）
# 2. git tag v2.1.0 && git push origin v2.1.0
# 3. 等约 15 分钟，Release 页面自动出现两个 macOS 平台 zip
```

---

## 其他同步修复

| 文件 | 修改内容 |
|---|---|
| `.env.example` | 作为发布包内 `config.env` 模板来源，避免打包本地密钥 |
| `runtime_main.spec` | launcher 复制逻辑限定为 macOS（`sys.platform == "darwin"`） |
| `README.md` | 顶部新增 "Download & Install" 区块，指向 GitHub Releases 页面 |
