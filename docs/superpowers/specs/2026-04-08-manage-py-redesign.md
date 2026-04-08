# manage.py 重新设计

## 背景

现有 manage.py 通过 `.env` 中间文件管理 cc-connect 项目配置，存在以下问题：

1. **Session 不可见** — 无法看到各项目当前运行的 Claude Code session ID，需要手动接入时无从得知
2. **无法复用项目** — 用户希望一个飞书机器人干完一个活后，干净地切换到另一个项目，但旧的 Claude Code 记忆会残留
3. **`.env` 中间层冗余** — cc-connect 本身读取 config.toml，`.env` 文件是不必要的间接层

## 设计目标

- 去掉 `.env` 中间层，直接读写 config.toml
- Dashboard 展示完整 Session ID，方便用 `claude --session-id <uuid>` 手动接入
- 新增「复用项目」功能：一键完成改名 + 换工作目录 + 创建新 session
- 保持交互式 TUI 菜单风格

## 架构

### 数据流

```
用户输入 → config.toml → cc-connect daemon
                ↑
         sessions/*.json（只读，展示用）
```

config.toml 是唯一数据源。manage.py 直接用 tomlkit 读写它。

### Session 信息读取

cc-connect 将每个项目的 session 持久化在 `~/.cc-connect/sessions/{项目名}_{hash}.json`。

文件结构：
```json
{
  "sessions": {
    "s1": {
      "id": "s1",
      "name": "default",
      "agent_session_id": "68555c0f-75ac-468f-acc9-9f21314bae43",
      "history": [...]
    }
  }
}
```

manage.py 通过项目名前缀匹配 session 文件，读取 `agent_session_id` 用于 Dashboard 展示。这是只读操作，不修改 session 文件。

### 重启方式

直接调用 `cc-connect daemon restart`，不再依赖 `restart.sh` 脚本。

## Dashboard

```
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  cc-connect 配置管理
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  服务状态: 运行中 (PID 15453)

  #   名称                 平台       工作目录                            Session ID
  ─────────────────────────────────────────────────────────────────────────────────────────────────
  1   LLM_wiki             feishu     /Users/johnny/LLM_wiki              2b2a987b-f98e-4fa3-bdb3-cbfb772de45a
  2   TSSHubBot            feishu     /Users/johnny/TSSHub                68555c0f-75ac-468f-acc9-9f21314bae43
  3   beimei_boshi_laolao  feishu     /Users/johnny/beimeiboshilaolao     —
  4   sci-humanizer        feishu     /Users/johnny/sci_humanizer         c8d1f3a2-44bc-4e91-a8f7-9f02bb3c7d1e

  [a] 添加   [e] 编辑   [d] 删除
  [w] 复用项目   [r] 重启服务   [q] 退出
```

## 菜单功能

### [a] 添加项目

交互式收集：

1. 项目名称（检查重名）
2. 工作目录
3. 平台类型（选择列表）
4. 平台凭证（按平台类型引导）

写入 config.toml，提示重启 daemon。添加 feishu 项目时显示配置清单（权限、事件订阅等）。

### [e] 编辑项目

选择项目后可修改：

- 项目名称
- 工作目录
- 模式（code/plan/ask 等）
- 平台凭证

更新 config.toml，提示重启 daemon。

### [d] 删除项目

从 config.toml 移除项目，确认后重启 daemon。session 文件不删除。

### [w] 复用项目

核心功能，解决「一个飞书机器人复用干不同活」的需求。

流程：

1. 选择要复用的项目
2. 输入新的项目名称
3. 输入新的工作目录
4. 更新 config.toml（改名 + 换 work_dir）
5. 重启 cc-connect daemon

重启后 cc-connect 找不到新项目名对应的 session 文件，自动创建新 session。旧的 session 文件和 Claude Code 记忆目录都保留不动，以后想切回来随时可以。

Claude Code 的记忆按 work_dir 路径隔离（`~/.claude/projects/-Users-johnny-{目录名}/memory/`），换了目录自然就是全新上下文。

### [r] 重启服务

调用 `cc-connect daemon restart`。显示当前 PID，重启后确认状态。

## 平台凭证引导

以下 8 个平台提供完整的交互式凭证收集引导：

### feishu

```
App ID:      (以 cli_ 开头)
App Secret:  (隐藏输入)
Domain:      [https://open.feishu.cn]  (Lark 用 https://open.larksuite.com)
```

添加后显示飞书配置清单：启用机器人能力、权限配置（含批量导入 JSON）、事件订阅、发布版本。

### telegram

```
Bot Token:   (从 @BotFather 获取)
Chat ID:     (可选，限定群组)
```

### discord

```
Bot Token:   (从 Discord Developer Portal 获取)
Guild ID:    (可选，限定服务器)
```

### dingtalk

```
App Key:      (钉钉开放平台获取)
App Secret:   (隐藏输入)
```

### slack

```
Bot Token:      (xoxb- 开头)
Signing Secret: (隐藏输入)
```

### wechat

```
说明: 个人微信接入，需配合 wechat-bridge 使用
```

具体凭证字段按 cc-connect 文档引导。

### qq

```
App ID:      (QQ 开放平台获取)
Token:       (隐藏输入)
App Secret:  (隐藏输入)
```

### qqbot

```
App ID:      (QQ 机器人平台获取)
Token:       (隐藏输入)
App Secret:  (隐藏输入)
```

### 其他平台（wecom、line 等）

提供通用 key-value 输入：

```
请输入该平台的配置项（格式: key=value，空行结束）:
  corp_id=xxx
  agent_secret=xxx
```

## config.toml 生成格式

直接使用 tomlkit 操作，保持 cc-connect 官方格式：

```toml
language = "zh"

[log]
level = "info"

[[projects]]
name = "TSSHubBot"

[projects.agent]
type = "claudecode"

[projects.agent.options]
work_dir = "/Users/johnny/TSSHub"
mode = "code"

[[projects.platforms]]
type = "feishu"

[projects.platforms.options]
app_id = "cli_a946..."
app_secret = "rF2t..."
```

manage.py 生成的 config.toml 顶部保留注释 `# 由 manage.py 管理 — 手动编辑后运行 manage.py 可自动识别`。

## 去掉的内容

- `projects.d/*.env` 文件的读写逻辑
- `ENV_DEFAULTS`、`ENV_KEYS` 常量
- `load_env()`、`save_env()`、`delete_env()` 函数
- `do_import()` 从 config.toml 导入到 .env 的逻辑
- `do_status()` 独立状态页（Dashboard 已包含所有信息）
- 依赖 `restart.sh` 脚本的重启逻辑

## 依赖

- Python 3.10+（match/case 语法）
- tomlkit（TOML 读写，保持格式和注释）
- cc-connect CLI（daemon 管理，需在 PATH 中）
