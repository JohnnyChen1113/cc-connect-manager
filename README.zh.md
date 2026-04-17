# cc-connect-manager

[cc-connect](https://github.com/chenhg5/cc-connect) 的零门槛安装器和 TUI —— cc-connect 是把本机 Claude Code（以及其他 CLI agent）接到飞书、Telegram、Slack 等消息平台的桥梁。

*语言：**中文** · [English](README.md)*

---

## 为什么做这个

cc-connect 功能强，但装起来对小白不友好：Node.js、全局 `npm install`、launchd 后台服务、TOML 配置、按项目配 API 供应商。这个项目把这些事打包成：

- **一行 bootstrap** —— 自动找你现有的 Node 环境（conda、nvm、Homebrew、系统），没有就帮你装
- **TUI (`manage.py`)** —— 日常操作：加项目、切模型、看日志、备份配置、健康体检
- **不重复造轮子** —— 所有操作都在调官方 `cc-connect` CLI，这是一层薄薄的运维外壳，不是 fork

如果你已经熟 cc-connect、手改 `~/.cc-connect/config.toml` 完全没压力，这个工具你不需要。如果你想让身边朋友一行命令把 Claude 接到飞书，这个工具就是给他们准备的。

## 安装

```bash
curl -fsSL https://raw.githubusercontent.com/JohnnyChen1113/cc-connect-manager/main/install.sh | bash
```

脚本会做的事：

1. 扫描现有 Node.js（conda 环境、nvm、Homebrew、系统）。找到多个会让你选。
2. 一个都没找到 → 有 conda 就用 conda（推荐），没有就用 Homebrew 装。
3. 在选定的 Node 环境里 `npm install -g cc-connect`。
4. 下载 `manage.py` 到 `~/.cc-connect-manager/`。
5. 往你的 shell rc（zsh / bash 自动识别）里加 `cc-manage` 别名。
6. 把 `cc-connect` 注册为 launchd 后台服务，保证重启后自动起来。

目前只支持 macOS。Linux (systemd) 在路线图上。

## 日常使用

开一个新终端（让别名生效），然后：

```bash
cc-manage
```

主菜单：

| 按键 | 作用 |
|-----|-----|
| `a` | 添加项目（飞书 / Telegram / Slack / Discord / 钉钉 / QQ / 微信 / …） |
| `e` | 编辑现有项目 |
| `d` | 删除项目 |
| `w` | 复用项目（把机器人指向新工作目录） |
| `m` | 按项目切换模型/服务商 |
| `c` | 管理定时任务 (cron) |
| `l` | 查看 daemon 日志（最近 N 条 / 实时跟随 / 只看错误） |
| `h` | 聊天里能用的 slash 命令速查 |
| `g` | 高级设置（语音转文字 / 速率限制 / 流式预览 / 静默模式） |
| `b` | 备份/恢复配置和 cron 状态 |
| `r` | 重启服务 |
| `i` | 安装 / 更新 / 体检 |
| `q` | 退出 |

任何"选择项目"的提示，回车 / `0` / `q` 都能静默返回 —— 你可以进来看看，不会被迫提交选择。

## 模型和服务商切换

`[m]` 按"认证方式"组织：

**1. 订阅登录** —— 沿用 daemon 的 Claude Code 默认账号，只换模型（sonnet / opus / haiku / 任意完整模型 ID）。

**2. 按项目独立 API Key** —— 把项目绑到一把特定 API Key。内置预设：

- Anthropic 官方 (`api.anthropic.com`)
- 智谱 GLM
- 月之暗面 Kimi
- DeepSeek
- 通义千问 Qwen
- 硅基流动 SiliconFlow
- 自定义（任何兼容 Claude API 协议的接入点）

**3. 恢复默认** —— 两个都清，回到 daemon 默认。

典型场景（#2）：有个项目别人赞助了一把 Anthropic 官方 key，你想让这个项目的飞书机器人用赞助 key，其他项目继续用你自己的订阅，不互相串账。

## Slash 命令（聊天里直接用）

在飞书/Telegram/Slack 和机器人对话时，这些随时可用：

- `/stop` —— 打断当前这一轮
- `/new` —— 开新 session
- `/compress` —— 压缩上下文
- `/model` —— 切模型
- `/mode` —— 切换模式（code / plan / ask / auto-edit / full-auto）
- `/provider` —— 管理 API 服务商
- `/history [n]` —— 查看最近 N 条消息
- `/help` —— 完整命令列表

`cc-manage` 的 `[h]` 会按场景分组显示速查表。

## 健康体检

`[i] → 2` 跑 10 项检查：

- cc-connect 二进制、Node.js、Python、tomlkit
- `config.toml` 存在且能解析
- daemon 正在运行
- launchd plist 已注册
- `~/.cc-connect/` 子目录齐全
- session 文件、cron 健康、provider 库

绿色 = 正常，黄色 = 提示（比如某个 cron 任务有临时错误），红色 = 阻塞性问题。

## 备份

`[b]` 把 `config.toml` + `crons/jobs.json`（可选：所有 session 历史）打包成带时间戳的 ZIP，放在 `~/.cc-connect-manager/backups/`。恢复前自动先做快照，回滚随时一键。

备份体量超过 50 MB 会提前警告（session 历史可能很大）。

## 系统要求

- macOS（Linux 支持进行中）
- Python 3.10+（安装器可以通过 conda 建一个）
- Node.js（安装器会检测或安装）
- 消息平台应用（飞书 App ID + Secret、Telegram bot token 等）

## 相关项目

- [cc-connect](https://github.com/chenhg5/cc-connect) —— 底层桥接工具本身
- 飞书开放平台：<https://open.feishu.cn>
- Lark Developer Console：<https://open.larksuite.com>

## License

TBD.
