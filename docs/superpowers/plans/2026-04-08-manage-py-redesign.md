# manage.py Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite manage.py to directly operate config.toml (removing .env intermediary), display session IDs in the dashboard, and add a project reuse workflow.

**Architecture:** Single-file Python script (~manage.py) using tomlkit to read/write `~/.cc-connect/config.toml` directly. Session info is read from `~/.cc-connect/sessions/{name}_{hash}.json` files. Daemon control via `cc-connect daemon restart` subprocess call.

**Tech Stack:** Python 3.10+, tomlkit, json (stdlib), subprocess (stdlib)

---

### Task 1: Core infrastructure — paths, colors, helpers, config I/O

**Files:**
- Rewrite: `manage.py` (complete replacement)

- [ ] **Step 1: Write the module header, imports, path constants, and terminal helpers**

```python
#!/usr/bin/env python3
"""cc-connect 配置管理器 — 交互式管理 ~/.cc-connect 项目配置

数据流: 用户输入 → config.toml → cc-connect daemon
                      ↑
               sessions/*.json（只读，展示用）
"""

import getpass
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import tomlkit
except ImportError:
    print("需要安装 tomlkit: pip install tomlkit")
    sys.exit(1)

# ── Paths ─────────────────────────────────────────────────────────────

CC_DIR = Path.home() / ".cc-connect"
CONFIG_FILE = CC_DIR / "config.toml"
SESSIONS_DIR = CC_DIR / "sessions"

# ── Colors ────────────────────────────────────────────────────────────

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def info(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


def err(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def header(title: str) -> None:
    print(f"\n  {CYAN}{'━' * 50}{RESET}")
    print(f"  {BOLD}{title}{RESET}")
    print(f"  {CYAN}{'━' * 50}{RESET}")


def ask(label: str, default: str = "") -> str:
    suffix = f" {DIM}[{default}]{RESET}" if default else ""
    try:
        answer = input(f"  {label}{suffix}: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return answer.strip() or default


def ask_secret(label: str) -> str:
    try:
        answer = getpass.getpass(f"  {label}: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return ""
    return answer.strip()


def ask_confirm(label: str, default_yes: bool = True) -> bool:
    hint = "Y/n" if default_yes else "y/N"
    try:
        answer = input(f"  {label} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default_yes
    if not answer:
        return default_yes
    return answer.startswith("y")


def mask(s: str) -> str:
    if len(s) <= 4:
        return "****"
    return "****" + s[-4:]
```

- [ ] **Step 2: Write config.toml read/write functions**

```python
# ── Config I/O ────────────────────────────────────────────────────────


def load_config() -> tomlkit.TOMLDocument:
    """Load config.toml. Returns empty doc if file doesn't exist."""
    if CONFIG_FILE.exists():
        return tomlkit.parse(CONFIG_FILE.read_text())
    doc = tomlkit.document()
    doc.add(tomlkit.comment("cc-connect 配置文件"))
    doc.add(tomlkit.comment("由 manage.py 管理"))
    doc.add(tomlkit.nl())
    doc["language"] = "zh"
    doc.add(tomlkit.nl())
    log = tomlkit.table()
    log["level"] = "info"
    doc["log"] = log
    doc.add(tomlkit.nl())
    doc["projects"] = tomlkit.aot()
    return doc


def save_config(doc: tomlkit.TOMLDocument) -> None:
    """Write config.toml with restrictive permissions."""
    CC_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(tomlkit.dumps(doc))
    CONFIG_FILE.chmod(0o600)


def get_projects(doc: tomlkit.TOMLDocument) -> list:
    """Get the projects array from config doc."""
    return doc.get("projects", [])
```

- [ ] **Step 3: Write session info reader**

```python
# ── Session info ──────────────────────────────────────────────────────


def get_session_id(project_name: str) -> str | None:
    """Read the active agent_session_id for a project from its session file.

    Session files are named {project_name}_{hash}.json.
    Returns the agent_session_id of the first session found, or None.
    """
    if not SESSIONS_DIR.exists():
        return None
    for f in SESSIONS_DIR.glob(f"{project_name}_*.json"):
        try:
            data = json.loads(f.read_text())
            sessions = data.get("sessions", {})
            # Find the most recent session with an agent_session_id
            for sid in sorted(sessions.keys(), reverse=True):
                agent_id = sessions[sid].get("agent_session_id")
                if agent_id:
                    return agent_id
        except (json.JSONDecodeError, KeyError):
            continue
    return None
```

- [ ] **Step 4: Write daemon control functions**

```python
# ── Daemon control ────────────────────────────────────────────────────


def is_cc_running() -> tuple[bool, int | None]:
    """Check if cc-connect daemon is running. Returns (running, pid)."""
    try:
        result = subprocess.run(
            ["cc-connect", "daemon", "status"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and "Running" in result.stdout:
            for line in result.stdout.splitlines():
                if "PID:" in line:
                    pid = int(line.split("PID:")[1].strip())
                    return True, pid
            return True, None
    except FileNotFoundError:
        err("cc-connect 未安装或不在 PATH 中")
    except Exception:
        pass
    return False, None


def restart_cc() -> bool:
    """Restart cc-connect daemon."""
    print(f"\n  {DIM}正在重启 cc-connect...{RESET}")
    try:
        result = subprocess.run(
            ["cc-connect", "daemon", "restart"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            info("cc-connect 已重启")
            return True
        else:
            err("重启失败")
            if result.stderr:
                print(f"  {DIM}{result.stderr.strip()}{RESET}")
            return False
    except FileNotFoundError:
        err("cc-connect 未安装或不在 PATH 中")
        return False


def prompt_restart() -> None:
    """Ask user if they want to restart after config change."""
    running, _ = is_cc_running()
    if running:
        print()
        if ask_confirm("cc-connect 正在运行，需要重启才能生效。现在重启？"):
            restart_cc()
        else:
            warn("配置已更新但尚未生效，稍后请手动重启")
            print(f"  {DIM}cc-connect daemon restart{RESET}")
    else:
        warn("cc-connect 未运行")
        print(f"  {DIM}启动: cc-connect --config ~/.cc-connect/config.toml{RESET}")
```

- [ ] **Step 5: Verify the script loads without errors**

Run: `python manage.py 2>&1 | head -5`

This won't fully work yet (no main), but should import cleanly.

- [ ] **Step 6: Commit**

```bash
git add manage.py
git commit -m "feat: rewrite core infrastructure — config I/O, session reader, daemon control"
```

---

### Task 2: Dashboard with Session ID display

**Files:**
- Modify: `manage.py` (append after Task 1 code)

- [ ] **Step 1: Write the dashboard function**

```python
# ── Dashboard ─────────────────────────────────────────────────────────


def show_dashboard() -> None:
    """Show service status + project list with session IDs."""
    running, pid = is_cc_running()
    status = f"{GREEN}运行中{RESET} (PID {pid})" if running else f"{RED}未运行{RESET}"
    print(f"\n  服务状态: {status}")

    doc = load_config()
    projects = get_projects(doc)
    if not projects:
        print(f"  {DIM}暂无项目{RESET}\n")
        return

    print()
    print(
        f"  {BOLD}{'#':<4}{'名称':<21}{'平台':<9}"
        f"{'工作目录':<40}{'Session ID'}{RESET}"
    )
    print(f"  {DIM}{'─' * 110}{RESET}")
    for i, proj in enumerate(projects, 1):
        name = proj.get("name", "?")
        plats = proj.get("platforms", [])
        platform = plats[0].get("type", "-") if plats else "-"
        work_dir = proj.get("agent", {}).get("options", {}).get("work_dir", "-")
        session_id = get_session_id(name) or "—"
        print(
            f"  {i:<4}{name:<21}{platform:<9}"
            f"{work_dir:<40}{session_id}"
        )
    print()
```

- [ ] **Step 2: Write the pick_project helper**

```python
def pick_project(projects: list, action: str) -> int | None:
    """Let user pick a project by number. Returns index or None."""
    if not projects:
        warn("暂无项目。")
        return None
    choice = ask(f"选择要{action}的项目编号")
    try:
        idx = int(choice) - 1
    except ValueError:
        err("请输入数字。")
        return None
    if idx < 0 or idx >= len(projects):
        err("无效编号。")
        return None
    return idx
```

- [ ] **Step 3: Write a minimal main loop to test the dashboard**

```python
# ── Main ──────────────────────────────────────────────────────────────


def main() -> None:
    while True:
        header("cc-connect 配置管理")
        show_dashboard()

        print(
            f"  {BOLD}[a]{RESET} 添加   "
            f"{BOLD}[e]{RESET} 编辑   "
            f"{BOLD}[d]{RESET} 删除   "
            f"{BOLD}[w]{RESET} 复用项目   "
            f"{BOLD}[r]{RESET} 重启   "
            f"{BOLD}[q]{RESET} 退出"
        )
        print()

        try:
            choice = input(f"  {BOLD}>{RESET} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  再见!")
            break

        match choice:
            case "a":
                warn("添加功能尚未实现")
            case "e":
                warn("编辑功能尚未实现")
            case "d":
                warn("删除功能尚未实现")
            case "w":
                warn("复用功能尚未实现")
            case "r":
                restart_cc()
            case "q":
                print("  再见!")
                break
            case _:
                err("无效选择。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run and verify dashboard displays correctly**

Run: `echo "q" | python manage.py`
Expected: Dashboard shows all projects from config.toml with session IDs, then exits.

- [ ] **Step 5: Commit**

```bash
git add manage.py
git commit -m "feat: dashboard with session ID display and main menu loop"
```

---

### Task 3: Platform credential collectors

**Files:**
- Modify: `manage.py` (insert before the Main section)

- [ ] **Step 1: Write platform definitions and the platform chooser**

```python
# ── Platform credentials ──────────────────────────────────────────────

PLATFORMS = {
    "feishu":   "飞书 / Lark",
    "telegram": "Telegram",
    "discord":  "Discord",
    "dingtalk": "钉钉",
    "slack":    "Slack",
    "wechat":   "个人微信",
    "qq":       "QQ",
    "qqbot":    "QQ Bot（官方机器人）",
    "wecom":    "企业微信",
    "line":     "LINE",
}


def choose_platform() -> str | None:
    """Let user pick a platform from the list."""
    print(f"\n  {CYAN}── 选择平台 ──{RESET}")
    items = list(PLATFORMS.items())
    for i, (key, label) in enumerate(items, 1):
        print(f"  {i:>2}) {label} ({key})")
    choice = ask("选择平台编号")
    try:
        idx = int(choice) - 1
    except ValueError:
        err("请输入数字。")
        return None
    if idx < 0 or idx >= len(items):
        err("无效编号。")
        return None
    return items[idx][0]
```

- [ ] **Step 2: Write the feishu credential collector**

```python
def collect_feishu(existing: dict | None = None) -> dict | None:
    """Collect Feishu credentials interactively."""
    print(f"\n  {CYAN}── 飞书应用凭证 ──{RESET}")
    if existing:
        print(f"  {DIM}回车保留当前值{RESET}")

    default_id = existing.get("app_id", "") if existing else ""
    default_secret = existing.get("app_secret", "") if existing else ""

    print(f"  {DIM}在飞书开放平台 → 凭证与基础信息中获取{RESET}")
    app_id = ask("App ID", default_id)
    if app_id and not app_id.startswith("cli_"):
        warn("App ID 通常以 cli_ 开头，请确认")

    if existing and default_secret:
        print(f"  {DIM}当前 App Secret: {mask(default_secret)}{RESET}")
        if ask_confirm("保留当前 App Secret？"):
            app_secret = default_secret
        else:
            app_secret = ask_secret("App Secret")
    else:
        app_secret = ask_secret("App Secret")

    if not app_secret:
        err("App Secret 不能为空")
        return None

    opts = {"app_id": app_id, "app_secret": app_secret}

    domain = ask("Domain", existing.get("domain", "https://open.feishu.cn") if existing else "https://open.feishu.cn")
    if domain != "https://open.feishu.cn":
        opts["domain"] = domain

    return opts
```

- [ ] **Step 3: Write collectors for telegram, discord, dingtalk, slack**

```python
def collect_telegram(existing: dict | None = None) -> dict | None:
    """Collect Telegram credentials."""
    print(f"\n  {CYAN}── Telegram Bot ──{RESET}")
    if existing:
        print(f"  {DIM}回车保留当前值{RESET}")

    default_token = existing.get("bot_token", "") if existing else ""

    print(f"  {DIM}从 @BotFather 获取 Bot Token{RESET}")
    if existing and default_token:
        print(f"  {DIM}当前 Token: {mask(default_token)}{RESET}")
        if ask_confirm("保留当前 Token？"):
            bot_token = default_token
        else:
            bot_token = ask_secret("Bot Token")
    else:
        bot_token = ask_secret("Bot Token")

    if not bot_token:
        err("Bot Token 不能为空")
        return None

    opts = {"bot_token": bot_token}
    chat_id = ask("Chat ID（可选，限定群组）", existing.get("chat_id", "") if existing else "")
    if chat_id:
        opts["chat_id"] = chat_id
    return opts


def collect_discord(existing: dict | None = None) -> dict | None:
    """Collect Discord credentials."""
    print(f"\n  {CYAN}── Discord Bot ──{RESET}")
    if existing:
        print(f"  {DIM}回车保留当前值{RESET}")

    default_token = existing.get("bot_token", "") if existing else ""

    print(f"  {DIM}从 Discord Developer Portal 获取{RESET}")
    if existing and default_token:
        print(f"  {DIM}当前 Token: {mask(default_token)}{RESET}")
        if ask_confirm("保留当前 Token？"):
            bot_token = default_token
        else:
            bot_token = ask_secret("Bot Token")
    else:
        bot_token = ask_secret("Bot Token")

    if not bot_token:
        err("Bot Token 不能为空")
        return None

    opts = {"bot_token": bot_token}
    guild_id = ask("Guild ID（可选，限定服务器）", existing.get("guild_id", "") if existing else "")
    if guild_id:
        opts["guild_id"] = guild_id
    return opts


def collect_dingtalk(existing: dict | None = None) -> dict | None:
    """Collect DingTalk credentials."""
    print(f"\n  {CYAN}── 钉钉应用凭证 ──{RESET}")
    if existing:
        print(f"  {DIM}回车保留当前值{RESET}")

    default_key = existing.get("app_key", "") if existing else ""
    default_secret = existing.get("app_secret", "") if existing else ""

    print(f"  {DIM}在钉钉开放平台获取{RESET}")
    app_key = ask("App Key", default_key)

    if existing and default_secret:
        print(f"  {DIM}当前 App Secret: {mask(default_secret)}{RESET}")
        if ask_confirm("保留当前 App Secret？"):
            app_secret = default_secret
        else:
            app_secret = ask_secret("App Secret")
    else:
        app_secret = ask_secret("App Secret")

    if not app_secret:
        err("App Secret 不能为空")
        return None

    return {"app_key": app_key, "app_secret": app_secret}


def collect_slack(existing: dict | None = None) -> dict | None:
    """Collect Slack credentials."""
    print(f"\n  {CYAN}── Slack Bot ──{RESET}")
    if existing:
        print(f"  {DIM}回车保留当前值{RESET}")

    default_token = existing.get("bot_token", "") if existing else ""
    default_signing = existing.get("signing_secret", "") if existing else ""

    print(f"  {DIM}从 Slack API Dashboard 获取{RESET}")
    if existing and default_token:
        print(f"  {DIM}当前 Bot Token: {mask(default_token)}{RESET}")
        if ask_confirm("保留当前 Token？"):
            bot_token = default_token
        else:
            bot_token = ask_secret("Bot Token (xoxb-...)")
    else:
        bot_token = ask_secret("Bot Token (xoxb-...)")

    if not bot_token:
        err("Bot Token 不能为空")
        return None

    if existing and default_signing:
        print(f"  {DIM}当前 Signing Secret: {mask(default_signing)}{RESET}")
        if ask_confirm("保留当前 Signing Secret？"):
            signing_secret = default_signing
        else:
            signing_secret = ask_secret("Signing Secret")
    else:
        signing_secret = ask_secret("Signing Secret")

    if not signing_secret:
        err("Signing Secret 不能为空")
        return None

    return {"bot_token": bot_token, "signing_secret": signing_secret}
```

- [ ] **Step 4: Write collectors for wechat, qq, qqbot**

```python
def collect_wechat(existing: dict | None = None) -> dict | None:
    """Collect WeChat credentials."""
    print(f"\n  {CYAN}── 个人微信 ──{RESET}")
    print(f"  {DIM}个人微信接入需配合 wechat-bridge 使用{RESET}")
    print(f"  {DIM}详见: https://github.com/chenhg5/cc-connect{RESET}")
    if existing:
        print(f"  {DIM}回车保留当前值{RESET}")
    return collect_generic(existing)


def collect_qq(existing: dict | None = None) -> dict | None:
    """Collect QQ credentials."""
    print(f"\n  {CYAN}── QQ ──{RESET}")
    if existing:
        print(f"  {DIM}回车保留当前值{RESET}")

    default_app_id = existing.get("app_id", "") if existing else ""

    print(f"  {DIM}从 QQ 开放平台获取{RESET}")
    app_id = ask("App ID", default_app_id)

    default_token = existing.get("token", "") if existing else ""
    if existing and default_token:
        print(f"  {DIM}当前 Token: {mask(default_token)}{RESET}")
        if ask_confirm("保留当前 Token？"):
            token = default_token
        else:
            token = ask_secret("Token")
    else:
        token = ask_secret("Token")

    default_secret = existing.get("app_secret", "") if existing else ""
    if existing and default_secret:
        print(f"  {DIM}当前 App Secret: {mask(default_secret)}{RESET}")
        if ask_confirm("保留当前 App Secret？"):
            app_secret = default_secret
        else:
            app_secret = ask_secret("App Secret")
    else:
        app_secret = ask_secret("App Secret")

    if not token or not app_secret:
        err("Token 和 App Secret 不能为空")
        return None

    return {"app_id": app_id, "token": token, "app_secret": app_secret}


def collect_qqbot(existing: dict | None = None) -> dict | None:
    """Collect QQ Bot credentials."""
    print(f"\n  {CYAN}── QQ Bot（官方机器人）──{RESET}")
    if existing:
        print(f"  {DIM}回车保留当前值{RESET}")

    default_app_id = existing.get("app_id", "") if existing else ""

    print(f"  {DIM}从 QQ 机器人平台获取{RESET}")
    app_id = ask("App ID", default_app_id)

    default_token = existing.get("token", "") if existing else ""
    if existing and default_token:
        print(f"  {DIM}当前 Token: {mask(default_token)}{RESET}")
        if ask_confirm("保留当前 Token？"):
            token = default_token
        else:
            token = ask_secret("Token")
    else:
        token = ask_secret("Token")

    default_secret = existing.get("app_secret", "") if existing else ""
    if existing and default_secret:
        print(f"  {DIM}当前 App Secret: {mask(default_secret)}{RESET}")
        if ask_confirm("保留当前 App Secret？"):
            app_secret = default_secret
        else:
            app_secret = ask_secret("App Secret")
    else:
        app_secret = ask_secret("App Secret")

    if not token or not app_secret:
        err("Token 和 App Secret 不能为空")
        return None

    return {"app_id": app_id, "token": token, "app_secret": app_secret}
```

- [ ] **Step 5: Write generic collector and the dispatcher**

```python
def collect_generic(existing: dict | None = None) -> dict | None:
    """Generic key=value collector for unsupported platforms."""
    print(f"\n  {DIM}请输入该平台的配置项（格式: key=value，空行结束）{RESET}")
    if existing:
        print(f"  {DIM}当前配置:{RESET}")
        for k, v in existing.items():
            print(f"    {k}={v}")
        print(f"  {DIM}重新输入全部配置（回车保留当前配置）:{RESET}")

    opts = {}
    while True:
        line = ask("").strip()
        if not line:
            break
        if "=" not in line:
            warn("格式: key=value")
            continue
        key, _, val = line.partition("=")
        opts[key.strip()] = val.strip()

    if not opts and existing:
        return dict(existing)
    if not opts:
        warn("未输入任何配置项")
        return None
    return opts


PLATFORM_COLLECTORS = {
    "feishu": collect_feishu,
    "telegram": collect_telegram,
    "discord": collect_discord,
    "dingtalk": collect_dingtalk,
    "slack": collect_slack,
    "wechat": collect_wechat,
    "qq": collect_qq,
    "qqbot": collect_qqbot,
}


def collect_platform_creds(platform: str, existing: dict | None = None) -> dict | None:
    """Dispatch to the right credential collector."""
    collector = PLATFORM_COLLECTORS.get(platform, collect_generic)
    return collector(existing)
```

- [ ] **Step 6: Write the feishu setup guide**

```python
def show_feishu_guide(app_id: str) -> None:
    """Post-setup guide for Feishu app configuration."""
    url = f"https://open.feishu.cn/app/{app_id}" if app_id else "https://open.feishu.cn/app"

    header("飞书应用配置清单")
    print(f"  控制台: {CYAN}{url}{RESET}\n")

    print(f"  {BOLD}1. 启用机器人能力{RESET}")
    print(f"     添加应用能力 → 机器人 → 填写名称和描述\n")

    print(f"  {BOLD}2. 添加权限{RESET}")
    print(f"     权限管理 → 批量开通 → 从其他应用导入 → 粘贴 JSON:")
    print()
    print(f'     {CYAN}{{"scopes": {{"tenant": [{RESET}')
    scopes = [
        "im:message:send_as_bot", "im:message:readonly",
        "im:message.p2p_msg:readonly", "im:message.group_at_msg:readonly",
        "im:message:update", "im:message.reactions:read",
        "im:message.reactions:write_only", "im:chat:read",
        "im:resource", "cardkit:card:write", "cardkit:card:read",
    ]
    for i, s in enumerate(scopes):
        comma = "," if i < len(scopes) - 1 else ""
        print(f'       {CYAN}"{s}"{comma}{RESET}')
    print(f'     {CYAN}], "user": []}}}}{RESET}\n')

    print(f"  {BOLD}3. 事件订阅{RESET}")
    print(f"     事件与回调 → 事件配置")
    print(f"     请求方式: 长连接（无需公网 IP）")
    print(f"     添加事件: {GREEN}im.message.receive_v1{RESET}")
    print(f"     添加回调: {GREEN}card.action.trigger{RESET}\n")

    print(f"  {BOLD}4. 发布版本{RESET}")
    print(f"     版本管理 → 创建版本 → 提交审核 → 管理员审批\n")

    print(f"  {BOLD}5. 开始使用{RESET}")
    print(f"     在飞书中搜索机器人名称，发起单聊或拉入群聊\n")
```

- [ ] **Step 7: Commit**

```bash
git add manage.py
git commit -m "feat: platform credential collectors for 8 platforms + generic fallback"
```

---

### Task 4: CRUD operations — add, edit, delete

**Files:**
- Modify: `manage.py` (insert before the Main section)

- [ ] **Step 1: Write a helper to build a project TOML table**

```python
# ── TOML project builder ──────────────────────────────────────────────


def build_project_table(
    name: str,
    work_dir: str,
    platform_type: str,
    platform_opts: dict,
    mode: str = "code",
    model: str = "",
) -> tomlkit.items.Table:
    """Build a [[projects]] TOML table."""
    t = tomlkit.table()
    t["name"] = name

    agent = tomlkit.table()
    agent["type"] = "claudecode"
    agent_opts = tomlkit.table()
    agent_opts["work_dir"] = work_dir
    agent_opts["mode"] = mode
    if model:
        agent_opts["model"] = model
    agent["options"] = agent_opts
    t["agent"] = agent

    plat = tomlkit.table()
    plat["type"] = platform_type
    plat_opts_table = tomlkit.table()
    for k, v in platform_opts.items():
        plat_opts_table[k] = v
    plat["options"] = plat_opts_table

    plat_aot = tomlkit.aot()
    plat_aot.append(plat)
    t["platforms"] = plat_aot

    return t
```

- [ ] **Step 2: Write do_add()**

```python
# ── Interactive flows ─────────────────────────────────────────────────


def do_add() -> None:
    """Add a new project."""
    header("添加项目")

    doc = load_config()
    projects = get_projects(doc)
    existing_names = {p.get("name", "") for p in projects}

    # 1. Name
    print(f"  {DIM}给项目起个名字，用于区分不同机器人{RESET}")
    while True:
        name = ask("项目名称")
        if not name:
            err("名称不能为空。")
            continue
        if name in existing_names:
            err(f"'{name}' 已存在。")
            continue
        break

    # 2. Work dir
    print(f"\n  {DIM}Claude 会在这个目录下读写文件{RESET}")
    work_dir = ask("工作目录", os.getcwd())
    work_dir = os.path.expanduser(work_dir)
    if not os.path.isdir(work_dir):
        warn(f"目录 '{work_dir}' 不存在")

    # 3. Platform
    platform = choose_platform()
    if not platform:
        return

    # 4. Credentials
    creds = collect_platform_creds(platform)
    if not creds:
        return

    # 5. Build & save
    proj_table = build_project_table(name, work_dir, platform, creds)
    if "projects" not in doc:
        doc["projects"] = tomlkit.aot()
    doc["projects"].append(proj_table)
    save_config(doc)
    info(f"项目 '{name}' 已添加")

    # 6. Restart
    prompt_restart()

    # 7. Platform guide
    if platform == "feishu":
        show_feishu_guide(creds.get("app_id", ""))
```

- [ ] **Step 3: Write do_edit()**

```python
def do_edit() -> None:
    """Edit an existing project."""
    header("编辑项目")

    doc = load_config()
    projects = get_projects(doc)
    show_dashboard()

    idx = pick_project(projects, "编辑")
    if idx is None:
        return

    proj = projects[idx]
    old_name = proj.get("name", "")
    print(f"\n  {DIM}回车保留当前值{RESET}")

    # Editable fields
    new_name = ask("项目名称", old_name)
    # Check for name conflict (but allow keeping same name)
    existing_names = {p.get("name", "") for p in projects}
    if new_name != old_name and new_name in existing_names:
        err(f"'{new_name}' 已存在。")
        return

    agent_opts = proj.get("agent", {}).get("options", {})
    new_dir = os.path.expanduser(
        ask("工作目录", agent_opts.get("work_dir", ""))
    )
    new_mode = ask(
        "模式 (code/plan/ask)",
        agent_opts.get("mode", "code"),
    )

    # Update fields
    proj["name"] = new_name
    agent_opts["work_dir"] = new_dir
    agent_opts["mode"] = new_mode

    # Platform credentials
    plats = proj.get("platforms", [])
    if plats:
        plat = plats[0]
        plat_type = plat.get("type", "feishu")
        if ask_confirm(f"更新 {plat_type} 凭证？", default_yes=False):
            existing_opts = dict(plat.get("options", {}))
            new_opts = collect_platform_creds(plat_type, existing_opts)
            if new_opts:
                opts_table = tomlkit.table()
                for k, v in new_opts.items():
                    opts_table[k] = v
                plat["options"] = opts_table

    save_config(doc)
    info(f"项目 '{new_name}' 已更新")
    prompt_restart()
```

- [ ] **Step 4: Write do_delete()**

```python
def do_delete() -> None:
    """Delete a project."""
    header("删除项目")

    doc = load_config()
    projects = get_projects(doc)
    show_dashboard()

    idx = pick_project(projects, "删除")
    if idx is None:
        return

    name = projects[idx].get("name", "?")
    if ask_confirm(f"确认删除 '{name}'？", default_yes=False):
        del doc["projects"][idx]
        save_config(doc)
        info(f"项目 '{name}' 已删除")
        prompt_restart()
    else:
        print("  已取消。")
```

- [ ] **Step 5: Wire up menu items in main()**

Replace the placeholder match arms:

```python
        match choice:
            case "a":
                do_add()
            case "e":
                do_edit()
            case "d":
                do_delete()
            case "w":
                warn("复用功能尚未实现")
            case "r":
                restart_cc()
            case "q":
                print("  再见!")
                break
            case _:
                err("无效选择。")
```

- [ ] **Step 6: Test add/edit/delete flow manually**

Run: `python manage.py`
Verify: `[a]` creates a new entry in config.toml, `[e]` modifies it, `[d]` removes it.

- [ ] **Step 7: Commit**

```bash
git add manage.py
git commit -m "feat: add, edit, delete project operations"
```

---

### Task 5: Project reuse workflow

**Files:**
- Modify: `manage.py` (insert `do_reuse()` before the Main section, wire up `[w]`)

- [ ] **Step 1: Write do_reuse()**

```python
def do_reuse() -> None:
    """Reuse an existing project (Feishu bot) for a different task.

    Changes project name and work_dir, restarts daemon.
    Old session files and Claude Code memory are preserved.
    """
    header("复用项目")

    doc = load_config()
    projects = get_projects(doc)
    show_dashboard()

    idx = pick_project(projects, "复用")
    if idx is None:
        return

    proj = projects[idx]
    old_name = proj.get("name", "")
    old_dir = proj.get("agent", {}).get("options", {}).get("work_dir", "")

    print(f"\n  {DIM}将复用此项目的平台凭证，切换到新的工作内容{RESET}")
    print(f"  {DIM}旧项目的 session 和记忆文件将保留{RESET}\n")

    # 1. New name
    existing_names = {p.get("name", "") for p in projects}
    while True:
        new_name = ask("新项目名称")
        if not new_name:
            err("名称不能为空。")
            continue
        if new_name in existing_names:
            err(f"'{new_name}' 已存在。")
            continue
        break

    # 2. New work dir
    new_dir = ask("新工作目录")
    if not new_dir:
        err("工作目录不能为空。")
        return
    new_dir = os.path.expanduser(new_dir)
    if not os.path.isdir(new_dir):
        warn(f"目录 '{new_dir}' 不存在（可稍后创建）")

    # 3. Confirm
    print(f"\n  {BOLD}变更预览:{RESET}")
    print(f"    名称:     {old_name} → {new_name}")
    print(f"    工作目录: {old_dir} → {new_dir}")
    print(f"    平台凭证: 保持不变")
    print()
    if not ask_confirm("确认复用？"):
        print("  已取消。")
        return

    # 4. Update config
    proj["name"] = new_name
    proj["agent"]["options"]["work_dir"] = new_dir

    save_config(doc)
    info(f"项目已从 '{old_name}' 切换到 '{new_name}'")

    # 5. Restart
    prompt_restart()
```

- [ ] **Step 2: Wire up [w] in main()**

Replace the placeholder:

```python
            case "w":
                do_reuse()
```

- [ ] **Step 3: Test the reuse flow manually**

Run: `python manage.py`
Steps:
1. Press `[w]`
2. Pick a project
3. Enter new name and work dir
4. Verify config.toml is updated with new name and work_dir
5. Verify old session files are untouched

- [ ] **Step 4: Commit**

```bash
git add manage.py
git commit -m "feat: project reuse workflow — switch name and work_dir, preserve old sessions"
```

---

### Task 6: Final cleanup and restart menu

**Files:**
- Modify: `manage.py` (update `[r]` handler)

- [ ] **Step 1: Write do_restart()**

```python
def do_restart() -> None:
    """Restart cc-connect daemon."""
    header("重启服务")
    running, pid = is_cc_running()
    if running:
        print(f"  当前 PID: {pid}")
    else:
        warn("cc-connect 未运行")
    restart_cc()
```

- [ ] **Step 2: Wire up [r] in main()**

Replace:
```python
            case "r":
                do_restart()
```

- [ ] **Step 3: Full end-to-end test**

Run: `python manage.py`
Test each menu item: `[a]`, `[e]`, `[d]`, `[w]`, `[r]`, `[q]`
Verify:
- Dashboard shows all projects with session IDs
- Add creates correct TOML structure
- Edit preserves unmodified fields
- Delete removes project from config
- Reuse changes name + work_dir, keeps credentials
- Restart calls `cc-connect daemon restart`

- [ ] **Step 4: Commit**

```bash
git add manage.py
git commit -m "feat: complete manage.py redesign — direct config.toml, session IDs, project reuse"
```
