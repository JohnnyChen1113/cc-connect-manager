# cc-connect-manager

A zero-friction installer and TUI for [cc-connect](https://github.com/chenhg5/cc-connect) — the bridge that hooks your local Claude Code (and other CLI agents) up to Feishu, Telegram, Slack, and other messaging platforms.

*Language: **English** · [中文](README.zh.md)*

---

## Why this exists

cc-connect is powerful but has sharp setup edges: Node.js, global `npm install`, launchd registration, per-project TOML, API provider rotation. This project rolls all of that into:

- **A one-line bootstrap** that finds your existing Node runtime (conda envs, nvm, Homebrew, system) or helps you install one.
- **A TUI (`manage.py`)** for daily operation: add projects, switch models, view logs, back up config, run health checks.
- **No reimplementation.** Every operation wraps the official `cc-connect` CLI — this is a thin operator layer, not a fork.

If you already run cc-connect and editing `~/.cc-connect/config.toml` by hand is fine, you don't need this. If you want a friend to get Claude-on-Feishu working with one command, you do.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/JohnnyChen1113/cc-connect-manager/main/install.sh | bash
```

What the script does:

1. Scans for existing Node.js (conda envs, nvm, Homebrew, system). If multiple, prompts which one.
2. If none found, uses conda (preferred) or Homebrew to install one.
3. Installs `cc-connect` into the chosen Node environment via `npm install -g`.
4. Downloads `manage.py` to `~/.cc-connect-manager/`.
5. Adds a `cc-manage` alias to your shell rc (zsh / bash detected automatically).
6. Registers `cc-connect` as a launchd LaunchAgent so it survives reboots.

macOS only right now. Linux `systemd` support is planned.

## Daily use

Open a fresh terminal (so the alias takes effect), then:

```bash
cc-manage
```

Main menu:

| Key | Action |
|-----|--------|
| `a` | Add a project (Feishu / Telegram / Slack / Discord / DingTalk / QQ / WeChat / …) |
| `e` | Edit an existing project |
| `d` | Delete a project |
| `w` | Reuse a project (repoint a bot at a new work directory) |
| `m` | Switch model or API provider per project |
| `c` | Manage scheduled tasks (cron) |
| `l` | View daemon logs (tail / follow / errors only) |
| `h` | Reference card for slash commands you can use in chat |
| `g` | Advanced settings (speech-to-text, rate limit, stream preview, quiet mode) |
| `b` | Backup / restore config and cron state |
| `r` | Restart the daemon |
| `i` | Install / update / health check |
| `q` | Quit |

At any "pick a project" prompt, `Enter` / `0` / `q` silently returns — you can poke around without being forced to commit.

## Model and provider switching

`[m]` is organized around the actual decision users face, *authentication method*:

**1. Subscription login** — keep the daemon's existing Claude Code auth, only change the model (sonnet / opus / haiku / any full model ID).

**2. API Key (per-project)** — bind the project to a specific API key. Presets:

- Anthropic official (`api.anthropic.com`)
- 智谱 GLM
- 月之暗面 Kimi
- DeepSeek
- 通义千问 Qwen
- 硅基流动 SiliconFlow
- Custom (any endpoint that speaks the Claude API protocol)

**3. Reset** — clear both, go back to daemon defaults.

Typical use case for #2: you got a sponsored Anthropic key for one specific project. Bind that project's Feishu bot to the sponsored key — every other project keeps using your subscription, no cross-billing.

## Slash commands (use in chat)

While chatting with a bot on Feishu/Telegram/Slack, these are always available:

- `/stop` — cancel the current agent turn
- `/new` — start a fresh session
- `/compress` — compact the conversation context
- `/model` — switch model
- `/mode` — switch agent mode (code / plan / ask / auto-edit / full-auto)
- `/provider` — manage API providers
- `/history [n]` — last N messages
- `/help` — full list

`[h]` in `cc-manage` shows a curated cheat-sheet grouped by intent.

## Health check

`[i] → 2` runs a 10-item sanity sweep:

- cc-connect binary, Node.js, Python, tomlkit
- `config.toml` exists and parses
- daemon is running
- launchd plist registered
- `~/.cc-connect/` subdirectories
- session files, cron health, provider DB

Green means healthy, yellow is informational (e.g. a cron job had a transient error), red blocks operation.

## Backup

`[b]` writes a timestamped ZIP of `config.toml` + `crons/jobs.json` (+ optionally all session histories) to `~/.cc-connect-manager/backups/`. Restore pre-snapshots automatically so rollback is always one step away.

Warns before creating backups >50 MB (session histories can get large).

## Requirements

- macOS (Linux support in progress)
- Python 3.10+ — the installer can provision one via conda if needed
- Node.js — the installer detects or installs
- A messaging platform app (Feishu app id + secret, Telegram bot token, etc.)

## Related

- [cc-connect](https://github.com/chenhg5/cc-connect) — the underlying bridge this project manages
- Feishu Open Platform: <https://open.feishu.cn>
- Lark Developer Console: <https://open.larksuite.com>

## License

TBD.
