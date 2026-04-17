#!/usr/bin/env python3
"""cc-connect 配置管理器 — 交互式管理 ~/.cc-connect 项目配置

数据流: 用户输入 → config.toml → cc-connect daemon
                      ↑
               sessions/*.json（只读，展示用）
"""

import getpass
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from datetime import datetime
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
LAUNCHD_PLIST = Path.home() / "Library/LaunchAgents/com.cc-connect.service.plist"

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
            for sid in sorted(sessions.keys(), reverse=True):
                agent_id = sessions[sid].get("agent_session_id")
                if agent_id:
                    return agent_id
        except (json.JSONDecodeError, KeyError):
            continue
    return None


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


def _daemon_cmd(subcmd: str) -> tuple[int, str, str]:
    """Run `cc-connect daemon <subcmd>`. Returns (rc, stdout, stderr).
    rc = -1 if cc-connect is not found."""
    try:
        result = subprocess.run(
            ["cc-connect", "daemon", subcmd],
            capture_output=True, text=True,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", "cc-connect not found"


def _find_cc_pid() -> int | None:
    """Return PID of running cc-connect, via status or pgrep fallback."""
    running, pid = is_cc_running()
    if running and pid is not None:
        return pid
    try:
        pgrep = subprocess.run(
            ["pgrep", "-f", "cc-connect"],
            capture_output=True, text=True,
        )
        if pgrep.returncode == 0:
            for line in pgrep.stdout.strip().splitlines():
                try:
                    return int(line)
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return None


def restart_cc() -> bool:
    """Restart cc-connect daemon with cascading fallbacks.

    Attempts in order:
      1. `cc-connect daemon restart`
      2. kill PID + wait 2s for launchd KeepAlive to respawn
      3. explicit `cc-connect daemon restart` (now that nothing is running)
      4. `cc-connect daemon start` as last resort

    Only returns False if all four fail.
    """
    print(f"\n  {DIM}正在重启 cc-connect...{RESET}")

    # Attempt 1: daemon restart
    rc, _, _ = _daemon_cmd("restart")
    if rc == -1:
        err("cc-connect 未安装或不在 PATH 中")
        return False
    if rc == 0:
        time.sleep(1)
        running, pid = is_cc_running()
        if running:
            info(f"cc-connect 已重启 (PID {pid})")
            return True

    # Attempt 2: kill the process and wait for launchd KeepAlive
    warn("daemon restart 失败，尝试 kill 进程...")
    pid = _find_cc_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        time.sleep(2)

    running, new_pid = is_cc_running()
    if running and new_pid != pid:
        info(f"cc-connect 已重启 (PID {new_pid})")
        return True

    # Attempt 3: explicit restart (no process should be running now)
    print(f"  {DIM}launchd 未触发，显式启动...{RESET}")
    rc, _, _ = _daemon_cmd("restart")
    if rc == 0:
        time.sleep(1)
        running, new_pid = is_cc_running()
        if running:
            info(f"cc-connect 已重启 (PID {new_pid})")
            return True

    # Attempt 4: daemon start
    rc, _, stderr = _daemon_cmd("start")
    if rc == 0:
        time.sleep(1)
        running, new_pid = is_cc_running()
        if running:
            info(f"cc-connect 已启动 (PID {new_pid})")
            return True

    err("多次尝试均失败，请手动排查")
    if stderr:
        print(f"  {DIM}最后错误: {stderr.strip()}{RESET}")
    print(f"  {DIM}手动启动: cc-connect daemon start{RESET}")
    print(f"  {DIM}查看日志: cc-connect daemon logs -n 50{RESET}")
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


# ── Platform credentials ──────────────────────────────────────────────

# Each entry: (display_key, label, config_type, default_domain_override)
# config_type is what goes into config.toml [[projects.platforms]] type
# default_domain_override is set automatically (None = use platform default)
PLATFORM_CHOICES = [
    ("feishu",   "飞书",                    "feishu", None),
    ("lark",     "Lark (International)",   "feishu", "https://open.larksuite.com"),
    ("telegram", "Telegram",               "telegram", None),
    ("discord",  "Discord",                "discord", None),
    ("dingtalk", "钉钉",                    "dingtalk", None),
    ("slack",    "Slack",                   "slack", None),
    ("wechat",   "个人微信",                "wechat", None),
    ("qq",       "QQ",                      "qq", None),
    ("qqbot",    "QQ Bot（官方机器人）",     "qqbot", None),
    ("wecom",    "企业微信",                "wecom", None),
    ("line",     "LINE",                    "line", None),
]

# Legacy lookup for display (used by dashboard)
PLATFORMS = {key: label for key, label, _, _ in PLATFORM_CHOICES}


# ── Provider / Model presets ──────────────────────────────────────────

# (value, label) — value is what goes into agent.options.model
OFFICIAL_MODEL_CHOICES = [
    ("sonnet", "Sonnet (最新)"),
    ("opus",   "Opus (最新)"),
    ("haiku",  "Haiku (最新)"),
]

# (display_label, preset_name, base_url, suggested_model)
# suggested_model 仅作提示默认值，用户可覆盖为最新版本号
PROVIDER_PRESETS = [
    ("智谱 GLM",            "glm",         "https://open.bigmodel.cn/api/anthropic",                          "glm-4.6"),
    ("月之暗面 Kimi",        "kimi",        "https://api.moonshot.cn/anthropic",                               "kimi-k2-0905-preview"),
    ("DeepSeek",            "deepseek",    "https://api.deepseek.com/anthropic",                              "deepseek-chat"),
    ("通义千问 Qwen",        "qwen",        "https://dashscope.aliyuncs.com/api/v2/apps/claude-code-proxy/v1", "qwen3-coder-plus"),
    ("硅基流动 SiliconFlow",  "siliconflow", "https://api.siliconflow.cn/anthropic",                            ""),
    ("自定义",               "custom",      "",                                                                ""),
]


def choose_platform() -> tuple[str, str | None] | None:
    """Let user pick a platform. Returns (config_type, domain_override) or None."""
    print(f"\n  {CYAN}── 选择平台 ──{RESET}")
    for i, (key, label, _, _) in enumerate(PLATFORM_CHOICES, 1):
        print(f"  {i:>2}) {label} ({key})")
    choice = ask("选择平台编号")
    try:
        idx = int(choice) - 1
    except ValueError:
        err("请输入数字。")
        return None
    if idx < 0 or idx >= len(PLATFORM_CHOICES):
        err("无效编号。")
        return None
    _, _, config_type, domain_override = PLATFORM_CHOICES[idx]
    return config_type, domain_override


def collect_feishu(existing: dict | None = None, domain_override: str | None = None) -> dict | None:
    """Collect Feishu/Lark credentials interactively.

    domain_override: if set (e.g. for Lark), used as the default domain.
    """
    is_lark = domain_override == "https://open.larksuite.com"
    platform_label = "Lark" if is_lark else "飞书"
    console_hint = "Lark Developer Console" if is_lark else "飞书开放平台 → 凭证与基础信息"

    print(f"\n  {CYAN}── {platform_label} 应用凭证 ──{RESET}")
    if existing:
        print(f"  {DIM}回车保留当前值{RESET}")

    default_id = existing.get("app_id", "") if existing else ""
    default_secret = existing.get("app_secret", "") if existing else ""

    print(f"  {DIM}在{console_hint}中获取{RESET}")
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

    # Determine default domain
    default_domain = "https://open.feishu.cn"
    if domain_override:
        default_domain = domain_override
    elif existing and existing.get("domain"):
        default_domain = existing["domain"]

    domain = ask("Domain", default_domain)
    if domain != "https://open.feishu.cn":
        opts["domain"] = domain

    return opts


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


def collect_platform_creds(
    platform: str,
    existing: dict | None = None,
    domain_override: str | None = None,
) -> dict | None:
    """Dispatch to the right credential collector."""
    collector = PLATFORM_COLLECTORS.get(platform, collect_generic)
    if platform == "feishu":
        return collector(existing, domain_override=domain_override)
    return collector(existing)


def show_feishu_guide(app_id: str, is_lark: bool = False) -> None:
    """Post-setup guide for Feishu/Lark app configuration."""
    if is_lark:
        base = "https://open.larksuite.com"
        title = "Lark App Configuration Checklist"
        search_hint = "Search for the bot name in Lark, start a chat or add to a group"
    else:
        base = "https://open.feishu.cn"
        title = "飞书应用配置清单"
        search_hint = "在飞书中搜索机器人名称，发起单聊或拉入群聊"

    url = f"{base}/app/{app_id}" if app_id else f"{base}/app"

    header(title)
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
    print(f"     {search_hint}\n")


# ── cc-connect provider helpers ───────────────────────────────────────


def get_all_providers() -> dict[str, list[dict]]:
    """Parse `cc-connect provider list` output into {project: [{name, raw}, ...]}.

    Returns an empty dict if cc-connect is unavailable or errors.
    """
    try:
        result = subprocess.run(
            ["cc-connect", "provider", "list"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}

    out: dict[str, list[dict]] = {}
    current: str | None = None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("── ") and stripped.endswith(" ──"):
            current = stripped[3:-3].strip()
            out[current] = []
            continue
        if current is None or not stripped:
            continue
        if "no providers" in stripped.lower():
            continue
        # First token is the provider name (may have details after)
        token = stripped.split(maxsplit=1)[0]
        out[current].append({"name": token, "raw": stripped})
    return out


def list_providers(project: str) -> list[dict]:
    return get_all_providers().get(project, [])


def add_provider_cc(
    project: str,
    name: str,
    api_key: str,
    base_url: str = "",
    model: str = "",
) -> bool:
    """Call `cc-connect provider add`. Returns True on success."""
    cmd = [
        "cc-connect", "provider", "add",
        "-project", project,
        "-name", name,
        "-api-key", api_key,
    ]
    if base_url:
        cmd += ["-base-url", base_url]
    if model:
        cmd += ["-model", model]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        err("cc-connect 未安装或不在 PATH 中")
        return False
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or "未知错误"
        err(f"添加 provider 失败: {msg}")
        return False
    return True


def remove_provider_cc(project: str, name: str) -> bool:
    """Call `cc-connect provider remove`. Returns True on success."""
    try:
        result = subprocess.run(
            ["cc-connect", "provider", "remove",
             "-project", project, "-name", name],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        err("cc-connect 未安装或不在 PATH 中")
        return False
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or "未知错误"
        err(f"删除 provider '{name}' 失败: {msg}")
        return False
    return True


def format_model_display(project: dict, providers_cache: dict[str, list[dict]]) -> str:
    """Return short string for the dashboard 模型 column.

    Priority: provider (runtime override) > agent.options.model > fallback.
    """
    name = project.get("name", "")
    provs = providers_cache.get(name, [])
    if provs:
        return provs[0]["name"]
    model = project.get("agent", {}).get("options", {}).get("model", "")
    if model:
        return model
    return "—"


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

    providers_cache = get_all_providers()

    print()
    print(
        f"  {BOLD}{'#':<4}{'名称':<21}{'平台':<9}"
        f"{'工作目录':<36}{'模型/服务商':<22}{'Session ID'}{RESET}"
    )
    print(f"  {DIM}{'─' * 130}{RESET}")
    for i, proj in enumerate(projects, 1):
        name = proj.get("name", "?")
        plats = proj.get("platforms", [])
        if plats:
            plat_type = plats[0].get("type", "-")
            plat_domain = plats[0].get("options", {}).get("domain", "")
            # Show "lark" for feishu projects using larksuite domain
            if plat_type == "feishu" and "larksuite" in plat_domain:
                plat_display = "lark"
            else:
                plat_display = plat_type
        else:
            plat_display = "-"
        work_dir = proj.get("agent", {}).get("options", {}).get("work_dir", "-")
        if len(work_dir) > 34:
            work_dir = "…" + work_dir[-33:]
        model_display = format_model_display(proj, providers_cache)
        if len(model_display) > 20:
            model_display = model_display[:19] + "…"
        session_id = get_session_id(name) or "—"
        print(
            f"  {i:<4}{name:<21}{plat_display:<9}"
            f"{work_dir:<36}{model_display:<22}{session_id}"
        )
    print()


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
    result = choose_platform()
    if not result:
        return
    platform_type, domain_override = result

    # 4. Credentials
    creds = collect_platform_creds(platform_type, domain_override=domain_override)
    if not creds:
        return

    # 5. Build & save
    proj_table = build_project_table(name, work_dir, platform_type, creds)
    if "projects" not in doc:
        doc["projects"] = tomlkit.aot()
    doc["projects"].append(proj_table)
    save_config(doc)
    info(f"项目 '{name}' 已添加")

    # 6. Restart
    prompt_restart()

    # 7. Platform guide
    if platform_type == "feishu":
        is_lark = domain_override == "https://open.larksuite.com"
        show_feishu_guide(creds.get("app_id", ""), is_lark=is_lark)


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


# ── Model / Provider switching ────────────────────────────────────────


def _show_current_model_state(proj: dict, providers: list[dict]) -> None:
    """Print the current model/provider state for a project."""
    current_model = proj.get("agent", {}).get("options", {}).get("model", "")
    print(f"\n  {BOLD}当前状态{RESET}")
    if providers:
        p = providers[0]
        print(f"  服务商:   {GREEN}{p['name']}{RESET} (第三方)")
        print(f"  {DIM}详情: {p['raw']}{RESET}")
        if len(providers) > 1:
            extras = ", ".join(x["name"] for x in providers[1:])
            print(f"  {DIM}其他已注册: {extras}{RESET}")
    else:
        print(f"  服务商:   Claude 官方")
    if current_model:
        print(f"  模型:     {current_model}")
    else:
        print(f"  模型:     {DIM}(未指定 — 使用 cc-connect 默认){RESET}")


def _clear_providers(proj_name: str, providers: list[dict]) -> bool:
    """Remove all providers for a project. Returns True if all succeeded."""
    ok = True
    for p in providers:
        if not remove_provider_cc(proj_name, p["name"]):
            ok = False
    return ok


def _switch_official_model(doc: tomlkit.TOMLDocument, proj: dict, providers: list[dict]) -> None:
    print(f"\n  {CYAN}── 选择 Claude 官方模型 ──{RESET}")
    for i, (_, label) in enumerate(OFFICIAL_MODEL_CHOICES, 1):
        val = OFFICIAL_MODEL_CHOICES[i-1][0]
        print(f"  {i}) {label}  {DIM}[{val}]{RESET}")
    custom_idx = len(OFFICIAL_MODEL_CHOICES) + 1
    print(f"  {custom_idx}) 自定义版本 ID (如 claude-sonnet-4-5)")

    choice = ask("选择编号")
    try:
        idx = int(choice) - 1
    except ValueError:
        err("请输入数字。")
        return

    if 0 <= idx < len(OFFICIAL_MODEL_CHOICES):
        model = OFFICIAL_MODEL_CHOICES[idx][0]
    elif idx == len(OFFICIAL_MODEL_CHOICES):
        model = ask("模型 ID")
        if not model:
            err("未输入模型 ID。")
            return
    else:
        err("无效编号。")
        return

    # Confirm
    proj_name = proj.get("name", "")
    print()
    if providers:
        print(f"  {DIM}切换到官方模型会清除已注册的第三方 provider:{RESET}")
        for p in providers:
            print(f"    - {p['name']}")
    if not ask_confirm(f"切换 '{proj_name}' 模型为 {model}？"):
        print("  已取消。")
        return

    # Apply
    if providers:
        _clear_providers(proj_name, providers)

    agent = proj.setdefault("agent", tomlkit.table())
    agent_opts = agent.setdefault("options", tomlkit.table())
    agent_opts["model"] = model
    save_config(doc)
    info(f"已切换 '{proj_name}' 到 Claude 官方模型: {model}")
    prompt_restart()


def _switch_third_party(doc: tomlkit.TOMLDocument, proj: dict, providers: list[dict]) -> None:
    print(f"\n  {CYAN}── 选择第三方 API 服务商 ──{RESET}")
    for i, (label, _, base, _) in enumerate(PROVIDER_PRESETS, 1):
        suffix = f"  {DIM}{base}{RESET}" if base else ""
        print(f"  {i}) {label}{suffix}")

    choice = ask("选择编号")
    try:
        idx = int(choice) - 1
    except ValueError:
        err("请输入数字。")
        return
    if idx < 0 or idx >= len(PROVIDER_PRESETS):
        err("无效编号。")
        return

    label, preset_name, base_url, suggest_model = PROVIDER_PRESETS[idx]

    # Custom path — collect everything
    if preset_name == "custom":
        print(f"\n  {DIM}填写兼容 Claude API 协议的任意第三方接入点{RESET}")
        name = ask("Provider 名称 (用于标识)", "custom")
        base_url = ask("Base URL (必填)")
        if not base_url:
            err("Base URL 不能为空。")
            return
    else:
        print(f"\n  {CYAN}── {label} ──{RESET}")
        name = ask("Provider 名称 (cc-connect 内部标识)", preset_name)

    api_key = ask_secret(f"{label} API Key")
    if not api_key:
        err("API Key 不能为空。")
        return

    if suggest_model:
        print(f"  {DIM}建议默认: {suggest_model}{RESET}")
        print(f"  {DIM}如有更新版本（如 glm-5.1），直接输入新版本号即可{RESET}")
    model = ask("Model", suggest_model) if suggest_model else ask("Model")
    if not model:
        err("Model 不能为空。")
        return

    # Confirm
    proj_name = proj.get("name", "")
    print()
    print(f"  {BOLD}变更预览:{RESET}")
    print(f"    项目:     {proj_name}")
    print(f"    服务商:   {label} ({name})")
    print(f"    Base URL: {base_url}")
    print(f"    Model:    {model}")
    if providers:
        print(f"    {DIM}将替换已有 provider: {', '.join(p['name'] for p in providers)}{RESET}")
    agent_opts = proj.get("agent", {}).get("options", {})
    if agent_opts.get("model"):
        print(f"    {DIM}将清除 agent.options.model = '{agent_opts['model']}'{RESET}")
    print()
    if not ask_confirm("确认切换？"):
        print("  已取消。")
        return

    # Apply: drop old providers first
    if providers:
        _clear_providers(proj_name, providers)

    # Clear agent.options.model (provider takes precedence at runtime anyway)
    if "model" in agent_opts:
        del proj["agent"]["options"]["model"]
        save_config(doc)

    if add_provider_cc(proj_name, name, api_key, base_url=base_url, model=model):
        info(f"已切换 '{proj_name}' 到 {label} / {model}")
        prompt_restart()


def _reset_to_default(doc: tomlkit.TOMLDocument, proj: dict, providers: list[dict]) -> None:
    proj_name = proj.get("name", "")
    agent_opts = proj.get("agent", {}).get("options", {})
    current_model = agent_opts.get("model", "")

    if not current_model and not providers:
        info("当前已是 Claude 官方默认 (无自定义 model 或 provider)")
        return

    print()
    print(f"  {BOLD}将清除:{RESET}")
    if current_model:
        print(f"    - agent.options.model = '{current_model}'")
    for p in providers:
        print(f"    - provider '{p['name']}'")
    print()
    if not ask_confirm("确认恢复 Claude 官方默认？", default_yes=False):
        print("  已取消。")
        return

    changed = False
    if current_model:
        del proj["agent"]["options"]["model"]
        save_config(doc)
        changed = True
    if providers:
        _clear_providers(proj_name, providers)
        changed = True

    if changed:
        info(f"'{proj_name}' 已恢复官方默认")
        prompt_restart()


def do_model() -> None:
    """Switch model or API provider for a project."""
    header("模型 / 服务商")

    doc = load_config()
    projects = get_projects(doc)
    show_dashboard()

    idx = pick_project(projects, "切换模型/服务商")
    if idx is None:
        return

    proj = projects[idx]
    proj_name = proj.get("name", "")
    providers = list_providers(proj_name)

    _show_current_model_state(proj, providers)

    print(f"\n  {CYAN}── 操作 ──{RESET}")
    print(f"  1) 切换 Claude 官方模型 (sonnet/opus/haiku/自定义)")
    print(f"  2) 切换到第三方 API (GLM/Kimi/DeepSeek/Qwen/硅基流动/自定义)")
    print(f"  3) 恢复 Claude 官方默认 (清除 model 和 provider)")
    print(f"  4) 返回")

    choice = ask("选择")
    match choice:
        case "1":
            _switch_official_model(doc, proj, providers)
        case "2":
            _switch_third_party(doc, proj, providers)
        case "3":
            _reset_to_default(doc, proj, providers)
        case "4" | "":
            return
        case _:
            err("无效选择。")


# ── Slash command reference ───────────────────────────────────────────


# Grouped so users can find by intent, not alphabet
SLASH_COMMAND_GROUPS = [
    ("常用操作", [
        ("/stop",     "打断正在执行的 agent（相当于 Ctrl+C）"),
        ("/new",      "开一个新 session（等于 reset 对话）"),
        ("/compress", "压缩当前上下文，保留关键信息继续对话"),
        ("/history",  "查看最近消息，可加数字如 /history 20"),
        ("/help",     "在飞书里输入这个看完整说明"),
    ]),
    ("切换模型 / 模式 / 服务商", [
        ("/model",    "切换模型（不用进 manage.py）"),
        ("/mode",     "切换模式：code / plan / ask / auto-edit / full-auto"),
        ("/provider", "管理第三方 API 服务商"),
        ("/quiet",    "开关静默模式（隐藏思考和工具调用过程）"),
    ]),
    ("权限 / 技能 / 语言", [
        ("/allow",    "允许某个工具使用（如 /allow Bash）"),
        ("/skills",   "列出当前 agent 可用的 skills"),
        ("/lang",     "切换界面语言: en / zh / zh-TW / ja / es / auto"),
        ("/config",   "查看或修改配置: /config get|set|reload [key] [value]"),
    ]),
    ("扩展命令", [
        ("/commands", "管理自定义命令: /commands add|del"),
        ("/alias",    "命令别名: /alias add 帮助 /help"),
        ("/cron",     "定时任务: /cron add|list|del|enable|disable"),
        ("/search",   "搜索历史消息"),
        ("/memory",   "管理长期记忆: /memory add|global"),
    ]),
    ("系统", [
        ("/status",   "查看当前 session 状态"),
        ("/restart",  "重启 cc-connect（等同 manage.py 的 [r]）"),
        ("/doctor",   "诊断当前环境"),
        ("/version",  "查看 cc-connect 版本"),
        ("/upgrade",  "升级 cc-connect 二进制"),
    ]),
]


CRONS_FILE = CC_DIR / "crons" / "jobs.json"


def _load_crons() -> list[dict]:
    """Load cron jobs from ~/.cc-connect/crons/jobs.json."""
    if not CRONS_FILE.exists():
        return []
    try:
        data = json.loads(CRONS_FILE.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_crons(jobs: list[dict]) -> bool:
    """Persist cron jobs back to jobs.json."""
    try:
        CRONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CRONS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
        return True
    except OSError as e:
        err(f"写入失败: {e}")
        return False


def _show_cron_list(jobs: list[dict]) -> None:
    """Pretty-print the cron jobs table."""
    if not jobs:
        print(f"  {DIM}暂无定时任务{RESET}")
        return
    print()
    print(f"  {BOLD}{'#':<4}{'状态':<6}{'ID':<12}{'Cron':<16}{'项目':<20}{'描述'}{RESET}")
    print(f"  {DIM}{'─' * 110}{RESET}")
    for i, job in enumerate(jobs, 1):
        enabled = job.get("enabled", True)
        icon = f"{GREEN}✓{RESET}" if enabled else f"{DIM}✗{RESET}"
        job_id = job.get("id", "?")[:10]
        expr = job.get("cron_expr", "-")
        project = job.get("project", "-")
        desc = job.get("description", "") or job.get("prompt", "")[:40]
        if len(desc) > 40:
            desc = desc[:39] + "…"
        last_error = job.get("last_error", "")
        print(f"  {i:<4}{icon:<13}{job_id:<12}{expr:<16}{project:<20}{desc}")
        if last_error:
            print(f"      {DIM}└ 上次错误: {last_error[:80]}{RESET}")
    print()


def do_cron() -> None:
    """Manage cron jobs."""
    header("定时任务")

    jobs = _load_crons()
    _show_cron_list(jobs)

    print(f"  {BOLD}操作:{RESET}")
    print(f"  1) 删除任务")
    print(f"  2) 启用/禁用任务")
    print(f"  3) 查看任务详情")
    print(f"  4) 添加新任务（说明）")
    print(f"  5) 刷新列表")
    print(f"  6) 返回")

    choice = ask("选择")

    if choice == "1":
        if not jobs:
            return
        idx = ask("要删除的任务编号")
        try:
            i = int(idx) - 1
        except ValueError:
            err("请输入数字。")
            return
        if i < 0 or i >= len(jobs):
            err("无效编号。")
            return
        job_id = jobs[i].get("id", "")
        desc = jobs[i].get("description", "") or jobs[i].get("prompt", "")[:40]
        if not ask_confirm(f"确认删除 '{desc}' (ID: {job_id[:10]})？", default_yes=False):
            return
        try:
            result = subprocess.run(
                ["cc-connect", "cron", "del", job_id],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                info(f"已删除任务 {job_id[:10]}")
                prompt_restart()
            else:
                err(f"删除失败: {result.stderr.strip() or result.stdout.strip()}")
        except FileNotFoundError:
            err("cc-connect 未安装")

    elif choice == "2":
        if not jobs:
            return
        idx = ask("要切换状态的任务编号")
        try:
            i = int(idx) - 1
        except ValueError:
            err("请输入数字。")
            return
        if i < 0 or i >= len(jobs):
            err("无效编号。")
            return
        job = jobs[i]
        job["enabled"] = not job.get("enabled", True)
        new_state = "启用" if job["enabled"] else "禁用"
        if _save_crons(jobs):
            info(f"已{new_state}任务 {job.get('id', '')[:10]}")
            prompt_restart()

    elif choice == "3":
        if not jobs:
            return
        idx = ask("要查看详情的任务编号")
        try:
            i = int(idx) - 1
        except ValueError:
            err("请输入数字。")
            return
        if i < 0 or i >= len(jobs):
            err("无效编号。")
            return
        job = jobs[i]
        print(f"\n  {BOLD}任务详情{RESET}")
        print(f"  ID:         {job.get('id', '-')}")
        print(f"  项目:       {job.get('project', '-')}")
        print(f"  Cron:       {job.get('cron_expr', '-')}")
        print(f"  描述:       {job.get('description', '-')}")
        print(f"  状态:       {'启用' if job.get('enabled', True) else '禁用'}")
        print(f"  创建时间:    {job.get('created_at', '-')}")
        print(f"  上次执行:    {job.get('last_run', '-') or '—'}")
        if job.get("last_error"):
            print(f"  {YELLOW}上次错误:    {job['last_error']}{RESET}")
        print(f"\n  {BOLD}Prompt:{RESET}")
        prompt = job.get("prompt", "")
        for line in prompt.splitlines() or [prompt]:
            print(f"    {line}")
        input(f"\n  {DIM}按回车返回...{RESET}")

    elif choice == "4":
        print(f"\n  {BOLD}添加新的定时任务{RESET}\n")
        print(f"  添加 cron 需要 session-key（绑定到具体的聊天窗口），")
        print(f"  从 manage.py 外部很难拿到。{GREEN}推荐做法{RESET}：\n")
        print(f"  1. 在飞书里打开要添加任务的对话")
        print(f"  2. 输入: {GREEN}/cron add <min> <hour> <day> <month> <weekday> <你的 prompt>{RESET}")
        print(f"  {DIM}   例: /cron add 0 9 * * * 每天早上给我一份今日日程汇总{RESET}")
        print(f"  3. 创建后回来这里 [c] 就能看到\n")
        input(f"  {DIM}按回车返回...{RESET}")

    elif choice == "5":
        return do_cron()

    elif choice in ("6", ""):
        return
    else:
        err("无效选择。")


def do_logs() -> None:
    """View cc-connect daemon logs."""
    header("查看日志")

    print(f"\n  {BOLD}选择查看方式:{RESET}")
    print(f"  1) 最近 50 条（快速回顾）")
    print(f"  2) 最近 200 条")
    print(f"  3) 实时跟随 (Ctrl+C 退出)")
    print(f"  4) 只看错误和警告（最近 100 条里过滤）")
    print(f"  5) 返回")

    choice = ask("选择")
    try:
        if choice == "1":
            cmd = ["cc-connect", "daemon", "logs", "-n", "50"]
            subprocess.run(cmd)
        elif choice == "2":
            cmd = ["cc-connect", "daemon", "logs", "-n", "200"]
            subprocess.run(cmd)
        elif choice == "3":
            print(f"\n  {DIM}实时日志（按 Ctrl+C 返回菜单）{RESET}\n")
            try:
                subprocess.run(["cc-connect", "daemon", "logs", "-f"])
            except KeyboardInterrupt:
                print(f"\n  {DIM}已停止跟随{RESET}")
        elif choice == "4":
            result = subprocess.run(
                ["cc-connect", "daemon", "logs", "-n", "100"],
                capture_output=True, text=True,
            )
            lines = result.stdout.splitlines()
            matched = [ln for ln in lines if "level=ERROR" in ln or "level=WARN" in ln]
            if not matched:
                info("最近 100 条里没有错误或警告")
            else:
                print()
                for line in matched:
                    color = RED if "level=ERROR" in line else YELLOW
                    print(f"  {color}{line}{RESET}")
                print(f"\n  {DIM}共 {len(matched)} 条{RESET}")
        elif choice in ("5", ""):
            return
        else:
            err("无效选择。")
            return
    except FileNotFoundError:
        err("cc-connect 未安装或不在 PATH 中")
        return

    input(f"\n  {DIM}按回车返回主菜单...{RESET}")


def do_help() -> None:
    """Show cc-connect slash command reference."""
    header("命令参考 — 在飞书对话里直接输入")

    print(f"\n  {DIM}这些命令在飞书/Lark/其他平台的对话框里输入即可使用{RESET}")
    print(f"  {DIM}不需要回到 manage.py，也不需要重启服务{RESET}\n")

    for group_name, commands in SLASH_COMMAND_GROUPS:
        print(f"  {BOLD}── {group_name} ──{RESET}")
        for cmd, desc in commands:
            print(f"    {GREEN}{cmd:<12}{RESET} {desc}")
        print()

    print(f"  {BOLD}常见场景速查:{RESET}")
    print(f"    {DIM}机器人答非所问 → {RESET}{GREEN}/stop{RESET} 然后 {GREEN}/new{RESET}")
    print(f"    {DIM}想换个模型试试 → {RESET}{GREEN}/model{RESET}")
    print(f"    {DIM}上下文太长有点慢 → {RESET}{GREEN}/compress{RESET}")
    print(f"    {DIM}想让机器人安静点 → {RESET}{GREEN}/quiet{RESET}")
    print(f"    {DIM}要禁用某个工具 → {RESET}{GREEN}/config set ...{RESET}  或编辑 config.toml")
    print()

    print(f"  {DIM}在飞书里输入 {GREEN}/help{RESET}{DIM} 可以看到 cc-connect 当前实际支持的完整命令（可能随版本更新）{RESET}")
    input(f"\n  {DIM}按回车返回主菜单...{RESET}")


# ── Install wizard ────────────────────────────────────────────────────


def _find_cc_connect() -> str | None:
    """Return the path to cc-connect binary, or None."""
    return shutil.which("cc-connect")


def _find_npm() -> str | None:
    """Return the path to npm, or None."""
    return shutil.which("npm")


def _get_cc_version() -> str | None:
    """Get installed cc-connect version string."""
    try:
        result = subprocess.run(
            ["cc-connect", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _generate_plist(cc_path: str) -> str:
    """Generate launchd plist content for cc-connect."""
    # Build PATH from current environment
    env_path = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>Label</key>
\t<string>com.cc-connect.service</string>
\t<key>ProgramArguments</key>
\t<array>
\t\t<string>{cc_path}</string>
\t</array>
\t<key>WorkingDirectory</key>
\t<string>{CC_DIR}</string>
\t<key>RunAtLoad</key>
\t<true/>
\t<key>KeepAlive</key>
\t<true/>
\t<key>EnvironmentVariables</key>
\t<dict>
\t\t<key>CC_LOG_FILE</key>
\t\t<string>{CC_DIR / 'logs' / 'cc-connect.log'}</string>
\t\t<key>CC_LOG_MAX_SIZE</key>
\t\t<string>10485760</string>
\t\t<key>PATH</key>
\t\t<string>{env_path}</string>
\t</dict>
\t<key>StandardOutPath</key>
\t<string>/dev/null</string>
\t<key>StandardErrorPath</key>
\t<string>/dev/null</string>
</dict>
</plist>"""


def _setup_launchd(cc_path: str) -> bool:
    """Set up launchd plist to auto-start cc-connect on macOS."""
    if platform.system() != "Darwin":
        warn("自动启动仅支持 macOS launchd")
        print(f"  {DIM}请手动配置 systemd 或其他服务管理器{RESET}")
        return False

    plist_dir = LAUNCHD_PLIST.parent
    plist_dir.mkdir(parents=True, exist_ok=True)

    if LAUNCHD_PLIST.exists():
        if not ask_confirm("launchd 配置已存在，覆盖？", default_yes=False):
            info("保留现有 launchd 配置")
            return True

    plist_content = _generate_plist(cc_path)
    LAUNCHD_PLIST.write_text(plist_content)
    info(f"launchd 配置已写入: {LAUNCHD_PLIST}")

    # Bootstrap the service
    uid = os.getuid()
    try:
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(LAUNCHD_PLIST)],
            capture_output=True, text=True, check=True,
        )
        info("cc-connect 已注册为开机自启动服务")
    except subprocess.CalledProcessError:
        # May already be bootstrapped
        warn("launchctl bootstrap 失败（可能已注册），尝试启动...")

    # Start/restart the service
    time.sleep(1)
    running, pid = is_cc_running()
    if running:
        info(f"cc-connect 已启动 (PID {pid})")
        return True
    else:
        warn("服务已注册但未检测到运行中的进程")
        print(f"  {DIM}手动启动: launchctl kickstart gui/{uid}/com.cc-connect.service{RESET}")
        return False


def _do_install_or_update() -> None:
    """Install or update cc-connect."""
    header("安装 / 更新 cc-connect")

    # Step 1: Check current state
    cc_path = _find_cc_connect()
    if cc_path:
        version = _get_cc_version() or "未知版本"
        info(f"cc-connect 已安装: {cc_path}")
        print(f"  {DIM}版本: {version}{RESET}")
        print()
        if not ask_confirm("检查更新？"):
            return
    else:
        print(f"  {DIM}cc-connect 尚未安装，将引导你完成安装{RESET}\n")

    # Step 2: Check npm
    npm_path = _find_npm()
    if not npm_path:
        err("未找到 npm")
        print(f"  {DIM}请先安装 Node.js: https://nodejs.org/{RESET}")
        print(f"  {DIM}或使用 Homebrew: brew install node{RESET}")
        return

    node_result = subprocess.run(
        ["node", "--version"], capture_output=True, text=True,
    )
    npm_result = subprocess.run(
        [npm_path, "--version"], capture_output=True, text=True,
    )
    info(f"Node.js {node_result.stdout.strip()}, npm {npm_result.stdout.strip()}")

    # Step 3: Install/update via npm
    action = "更新" if cc_path else "安装"
    print(f"\n  {DIM}将执行: npm install -g cc-connect{RESET}")
    if not ask_confirm(f"确认{action}？"):
        print("  已取消。")
        return

    print(f"\n  {DIM}正在{action} cc-connect...{RESET}\n")
    install_result = subprocess.run(
        [npm_path, "install", "-g", "cc-connect"],
        text=True,
    )

    if install_result.returncode != 0:
        err(f"{action}失败")
        print(f"  {DIM}如果权限不足，尝试: sudo npm install -g cc-connect{RESET}")
        return

    # Verify installation
    cc_path = _find_cc_connect()
    if not cc_path:
        err("安装后仍找不到 cc-connect，请检查 PATH")
        return

    version = _get_cc_version() or "未知版本"
    info(f"cc-connect {action}成功: {version}")

    # Step 4: Create config directory
    CC_DIR.mkdir(parents=True, exist_ok=True)
    (CC_DIR / "logs").mkdir(exist_ok=True)
    (CC_DIR / "sessions").mkdir(exist_ok=True)

    # Initialize config.toml if needed
    if not CONFIG_FILE.exists():
        doc = load_config()
        save_config(doc)
        info("已创建默认配置: ~/.cc-connect/config.toml")
    else:
        info("配置文件已存在: ~/.cc-connect/config.toml")

    # Step 5: Set up auto-start
    print()
    if ask_confirm("设置开机自动启动？"):
        _setup_launchd(cc_path)
    else:
        warn("跳过自动启动配置")
        print(f"  {DIM}手动启动: cc-connect --config ~/.cc-connect/config.toml{RESET}")

    # Step 6: Done
    print()
    info("安装完成！接下来可以用 [a] 添加项目")


def _check_item(label: str, ok: bool, detail: str = "", severity: str = "error") -> bool:
    """severity: 'error' (red ✗) or 'warn' (yellow !)."""
    if ok:
        icon = f"{GREEN}✓{RESET}"
    elif severity == "warn":
        icon = f"{YELLOW}!{RESET}"
    else:
        icon = f"{RED}✗{RESET}"
    suffix = f"  {DIM}{detail}{RESET}" if detail else ""
    print(f"  {icon} {label}{suffix}")
    return ok


def _do_health_check() -> None:
    """Run environment health checks."""
    header("环境体检")

    print()
    all_ok = True

    # 1. cc-connect binary
    cc_path = _find_cc_connect()
    if cc_path:
        ver = _get_cc_version() or "unknown"
        _check_item(f"cc-connect 可执行文件", True, f"{cc_path} ({ver})")
    else:
        _check_item("cc-connect 可执行文件", False, "PATH 中未找到 — 请运行 [i] → 1 安装")
        all_ok = False

    # 2. Node.js
    try:
        r = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            _check_item("Node.js", True, r.stdout.strip())
        else:
            _check_item("Node.js", False, "命令失败")
            all_ok = False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _check_item("Node.js", False, "未安装或无法访问")
        all_ok = False

    # 3. Python + tomlkit
    _check_item(
        f"Python {sys.version_info.major}.{sys.version_info.minor}",
        sys.version_info >= (3, 10),
        "需要 3.10+" if sys.version_info < (3, 10) else "",
    )
    _check_item("tomlkit 模块", True, tomlkit.__version__ if hasattr(tomlkit, "__version__") else "已加载")

    # 4. config.toml
    if CONFIG_FILE.exists():
        try:
            doc = tomlkit.parse(CONFIG_FILE.read_text())
            projects = doc.get("projects", [])
            _check_item("config.toml 存在并可解析", True, f"{len(projects)} 个项目")
        except Exception as e:
            _check_item("config.toml 存在并可解析", False, f"解析失败: {e}")
            all_ok = False
    else:
        _check_item("config.toml", False, f"不存在: {CONFIG_FILE}")
        all_ok = False

    # 5. daemon running
    running, pid = is_cc_running()
    if running:
        _check_item("cc-connect daemon 运行中", True, f"PID {pid}")
    else:
        _check_item("cc-connect daemon 运行中", False, "未运行 — 按 [r] 启动")
        all_ok = False

    # 6. launchd plist
    if LAUNCHD_PLIST.exists():
        _check_item("launchd 开机自启配置", True, str(LAUNCHD_PLIST))
    else:
        _check_item("launchd 开机自启配置", False, "未注册 — 运行 [i] → 1")

    # 7. Directories
    for sub in ("sessions", "logs", "crons", "run"):
        path = CC_DIR / sub
        _check_item(f"目录 ~/.cc-connect/{sub}", path.is_dir(), "" if path.is_dir() else "缺失")

    # 8. Session files — basic sanity
    if (CC_DIR / "sessions").is_dir():
        session_files = list((CC_DIR / "sessions").glob("*.json"))
        _check_item(
            f"Session 文件",
            len(session_files) > 0,
            f"{len(session_files)} 个" if session_files else "暂无（第一次收到消息后会生成）",
        )

    # 9. Cron jobs (non-blocking — errors here are often transient)
    jobs = _load_crons()
    if jobs:
        failing = [j for j in jobs if j.get("last_error")]
        ok = len(failing) == 0
        detail = f"{len(jobs)} 个任务"
        if failing:
            detail += f"，其中 {len(failing)} 个上次执行有错（可能是临时问题）"
        _check_item("定时任务健康", ok, detail, severity="warn")
        if failing:
            print(f"  {DIM}   在 [c] → 3 可查看任务详情和错误原因{RESET}")

    # 10. provider DB (at cc-connect's default location — not inspecting contents, just existence)
    providers_data = get_all_providers()
    total_providers = sum(len(v) for v in providers_data.values())
    if total_providers > 0:
        _check_item("第三方 provider 注册", True, f"{total_providers} 个")

    print()
    if all_ok:
        info("✨ 所有核心项健康")
    else:
        warn("有问题项目，参考上面的提示处理")
    print()
    input(f"  {DIM}按回车返回...{RESET}")


BACKUP_DIR = Path.home() / ".cc-connect-manager" / "backups"


def _backup_paths(include_sessions: bool) -> list[tuple[Path, str]]:
    """Return (source, arcname) pairs for backup contents."""
    pairs: list[tuple[Path, str]] = []
    if CONFIG_FILE.exists():
        pairs.append((CONFIG_FILE, "config.toml"))
    crons_file = CC_DIR / "crons" / "jobs.json"
    if crons_file.exists():
        pairs.append((crons_file, "crons/jobs.json"))
    # cc-switch provider DB (location may vary — try common spots)
    for p in (CC_DIR / "cc-switch.db", Path.home() / ".cc-switch.db"):
        if p.exists():
            pairs.append((p, p.name))
    if include_sessions and (CC_DIR / "sessions").is_dir():
        for f in (CC_DIR / "sessions").glob("*.json"):
            pairs.append((f, f"sessions/{f.name}"))
    return pairs


def _first_run_hint() -> None:
    """Show a one-time onboarding banner when the user hasn't set anything up."""
    cc_path = _find_cc_connect()
    has_config = CONFIG_FILE.exists()
    has_projects = False
    if has_config:
        try:
            doc = tomlkit.parse(CONFIG_FILE.read_text())
            has_projects = bool(doc.get("projects"))
        except Exception:
            pass
    has_plist = LAUNCHD_PLIST.exists()

    # Everything set up — no banner
    if cc_path and has_config and has_projects and has_plist:
        return

    print()
    print(f"  {CYAN}╭─ 建议的 3 步开始 ─────────────────────────────────╮{RESET}")
    steps = []
    if not cc_path:
        steps.append(("[i] → 1", "安装 cc-connect"))
    elif not has_plist:
        steps.append(("[i] → 1", "重新注册 launchd 开机自启"))
    if not has_projects:
        steps.append(("[a]", "添加第一个飞书项目（准备好 App ID + Secret）"))
    else:
        steps.append(("[r]", "确保 daemon 在运行"))
    steps.append(("[h]", "查看聊天里能用的 slash 命令"))

    for i, (key, desc) in enumerate(steps[:3], 1):
        print(f"  {CYAN}│{RESET}  {i}. {BOLD}{key:<10}{RESET} {desc}")
    print(f"  {CYAN}│{RESET}")
    print(f"  {CYAN}│{RESET}  {DIM}体检环境: [i] → 2    命令参考: [h]{RESET}")
    print(f"  {CYAN}╰──────────────────────────────────────────────────╯{RESET}")


# ── Advanced settings wizard ──────────────────────────────────────────


def _ensure_table(doc: tomlkit.TOMLDocument, key: str) -> tomlkit.items.Table:
    """Get-or-create a top-level table."""
    if key not in doc:
        doc[key] = tomlkit.table()
    return doc[key]


def do_advanced() -> None:
    """Wizard for speech / stream_preview / rate_limit / quiet settings."""
    header("高级设置")

    doc = load_config()

    # Current state summary
    speech = doc.get("speech", {})
    stream = doc.get("stream_preview", {})
    rate = doc.get("rate_limit", {})
    quiet = doc.get("quiet", None)

    print()
    print(f"  {BOLD}当前设置:{RESET}")
    print(f"  语音转文字:      {'启用' if speech.get('enabled') else '未启用'}")
    print(f"  流式预览:        {'关闭' if stream.get('enabled') is False else '启用（默认）'}")
    rate_max = rate.get("max_messages", 20)
    rate_win = rate.get("window_secs", 60)
    print(f"  速率限制:        {rate_max} 条 / {rate_win} 秒" if rate_max else "  速率限制:        已禁用")
    print(f"  默认静默模式:     {'启用' if quiet else '未启用'}")
    print()

    print(f"  {BOLD}操作:{RESET}")
    print(f"  1) 配置语音转文字 (speech)")
    print(f"  2) 配置流式预览 (stream_preview)")
    print(f"  3) 配置速率限制 (rate_limit)")
    print(f"  4) 切换默认静默模式 (quiet)")
    print(f"  5) 查看完整官方配置参考")
    print(f"  6) 返回")

    choice = ask("选择")

    if choice == "1":
        _configure_speech(doc, speech)
    elif choice == "2":
        _configure_stream_preview(doc, stream)
    elif choice == "3":
        _configure_rate_limit(doc, rate)
    elif choice == "4":
        new_quiet = not bool(quiet)
        doc["quiet"] = new_quiet
        save_config(doc)
        info(f"默认静默模式已{'启用' if new_quiet else '关闭'}")
        prompt_restart()
    elif choice == "5":
        try:
            result = subprocess.run(
                ["cc-connect", "config-example"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                print()
                # Use pager if available
                pager = os.environ.get("PAGER", "less")
                if shutil.which(pager):
                    p = subprocess.Popen([pager], stdin=subprocess.PIPE)
                    p.communicate(result.stdout.encode())
                else:
                    print(result.stdout)
            else:
                err("获取配置参考失败")
        except FileNotFoundError:
            err("cc-connect 未安装")
    elif choice in ("6", ""):
        return
    else:
        err("无效选择。")


def _configure_speech(doc: tomlkit.TOMLDocument, current: dict) -> None:
    print(f"\n  {CYAN}── 语音转文字 ──{RESET}")
    print(f"  {DIM}启用后，飞书/Telegram 收到的语音会先转文字再发给 agent{RESET}")
    print(f"  {DIM}需要 ffmpeg（macOS: brew install ffmpeg）{RESET}\n")

    enabled = current.get("enabled", False)
    if ask_confirm(f"启用语音转文字？", default_yes=not enabled):
        print(f"\n  {BOLD}选择提供商:{RESET}")
        print(f"  1) OpenAI Whisper")
        print(f"  2) Groq (Whisper-large-v3-turbo，通常更快)")
        print(f"  3) 通义千问 ASR")
        prov_choice = ask("选择", "1")
        provider_map = {"1": "openai", "2": "groq", "3": "qwen"}
        provider = provider_map.get(prov_choice, "openai")

        existing_section = current.get(provider, {})
        api_key = ask_secret(f"{provider.upper()} API Key")
        if not api_key:
            err("API Key 不能为空。")
            return

        default_model = {
            "openai": "whisper-1",
            "groq": "whisper-large-v3-turbo",
            "qwen": "qwen3-asr-flash",
        }[provider]
        model = ask("模型", existing_section.get("model", default_model))

        base_url = ask("Base URL (回车用默认)", existing_section.get("base_url", ""))

        speech_table = _ensure_table(doc, "speech")
        speech_table["enabled"] = True
        speech_table["provider"] = provider
        lang = ask("识别语言 (zh/en/留空自动)", current.get("language", ""))
        if lang:
            speech_table["language"] = lang

        prov_table = tomlkit.table()
        prov_table["api_key"] = api_key
        prov_table["model"] = model
        if base_url:
            prov_table["base_url"] = base_url
        speech_table[provider] = prov_table

        save_config(doc)
        info("语音转文字已配置")
        prompt_restart()
    else:
        if "speech" in doc:
            doc["speech"]["enabled"] = False
            save_config(doc)
            info("已关闭语音转文字")
            prompt_restart()


def _configure_stream_preview(doc: tomlkit.TOMLDocument, current: dict) -> None:
    print(f"\n  {CYAN}── 流式预览 ──{RESET}")
    print(f"  {DIM}Agent 输出时实时更新消息（像\"正在输入\"效果）{RESET}")
    print(f"  {DIM}支持飞书 / Telegram / Discord，默认启用{RESET}\n")

    currently_enabled = current.get("enabled", True)
    print(f"  当前: {'启用' if currently_enabled else '关闭'}")
    new_state = ask_confirm("启用流式预览？", default_yes=currently_enabled)

    sp_table = _ensure_table(doc, "stream_preview")
    sp_table["enabled"] = new_state

    if new_state:
        interval = ask("更新间隔 (毫秒)", str(current.get("interval_ms", 1500)))
        try:
            sp_table["interval_ms"] = int(interval)
        except ValueError:
            warn("非数字，保留默认 1500")
            sp_table["interval_ms"] = 1500

    save_config(doc)
    info("流式预览已更新")
    prompt_restart()


def _configure_rate_limit(doc: tomlkit.TOMLDocument, current: dict) -> None:
    print(f"\n  {CYAN}── 速率限制 ──{RESET}")
    print(f"  {DIM}每个会话的滑动窗口限流，防止刷消息{RESET}\n")

    cur_max = current.get("max_messages", 20)
    cur_win = current.get("window_secs", 60)
    print(f"  当前: {cur_max} 条 / {cur_win} 秒（0 = 禁用）\n")

    max_str = ask("每窗口最大消息数（0 禁用）", str(cur_max))
    try:
        max_msg = int(max_str)
    except ValueError:
        err("请输入数字。")
        return

    rl_table = _ensure_table(doc, "rate_limit")
    rl_table["max_messages"] = max_msg

    if max_msg > 0:
        win_str = ask("窗口秒数", str(cur_win))
        try:
            rl_table["window_secs"] = int(win_str)
        except ValueError:
            warn("非数字，保留默认 60")
            rl_table["window_secs"] = 60

    save_config(doc)
    info("速率限制已更新")
    prompt_restart()


def do_backup() -> None:
    """Backup / restore config and related state."""
    header("备份 / 恢复")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(BACKUP_DIR.glob("cc-connect-backup-*.zip"), reverse=True)

    print()
    print(f"  {BOLD}操作:{RESET}")
    print(f"  1) 创建备份（config.toml + crons + providers）")
    print(f"  2) 创建备份（包含会话历史，文件较大）")
    print(f"  3) 从备份恢复")
    print(f"  4) 查看备份列表")
    print(f"  5) 返回")
    print()

    choice = ask("选择")

    if choice in ("1", "2"):
        include_sessions = (choice == "2")
        pairs = _backup_paths(include_sessions)
        if not pairs:
            err("没有可备份的内容。")
            return

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = "-full" if include_sessions else ""
        backup_path = BACKUP_DIR / f"cc-connect-backup-{stamp}{suffix}.zip"

        try:
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for src, arc in pairs:
                    zf.write(src, arcname=arc)
            size_kb = backup_path.stat().st_size / 1024
            info(f"备份已创建: {backup_path}")
            print(f"  {DIM}包含 {len(pairs)} 个文件，{size_kb:.1f} KB{RESET}")
        except OSError as e:
            err(f"备份失败: {e}")

    elif choice == "3":
        if not existing:
            warn("没有可用备份。")
            print(f"  {DIM}备份目录: {BACKUP_DIR}{RESET}")
            return
        print(f"\n  {BOLD}选择要恢复的备份:{RESET}")
        for i, p in enumerate(existing[:20], 1):
            size = p.stat().st_size / 1024
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"  {i:>2}) {p.name}  {DIM}({size:.1f} KB, {mtime}){RESET}")

        idx_str = ask("备份编号")
        try:
            idx = int(idx_str) - 1
        except ValueError:
            err("请输入数字。")
            return
        if idx < 0 or idx >= len(existing[:20]):
            err("无效编号。")
            return

        backup = existing[idx]
        print(f"\n  {YELLOW}警告{RESET}：恢复会覆盖当前的 config.toml 和 crons/jobs.json")
        print(f"  恢复前系统会先自动创建一份快照\n")
        if not ask_confirm(f"确认从 {backup.name} 恢复？", default_yes=False):
            return

        # Pre-restore snapshot
        pre_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        pre_snapshot = BACKUP_DIR / f"cc-connect-backup-{pre_stamp}-pre-restore.zip"
        try:
            snap_pairs = _backup_paths(include_sessions=False)
            if snap_pairs:
                with zipfile.ZipFile(pre_snapshot, "w", zipfile.ZIP_DEFLATED) as zf:
                    for src, arc in snap_pairs:
                        zf.write(src, arcname=arc)
                info(f"恢复前快照: {pre_snapshot.name}")
        except OSError as e:
            warn(f"快照失败（继续恢复）: {e}")

        # Extract
        try:
            with zipfile.ZipFile(backup, "r") as zf:
                zf.extractall(CC_DIR)
            info(f"已从 {backup.name} 恢复")
            prompt_restart()
        except (OSError, zipfile.BadZipFile) as e:
            err(f"恢复失败: {e}")

    elif choice == "4":
        if not existing:
            print(f"  {DIM}暂无备份{RESET}")
            print(f"  {DIM}目录: {BACKUP_DIR}{RESET}")
        else:
            print(f"\n  {BOLD}备份列表（{len(existing)} 个）{RESET}")
            total_kb = 0
            for p in existing[:20]:
                size = p.stat().st_size / 1024
                total_kb += size
                mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                print(f"  {p.name}  {DIM}({size:.1f} KB, {mtime}){RESET}")
            print(f"\n  {DIM}共 {total_kb:.1f} KB{RESET}")
            print(f"  {DIM}目录: {BACKUP_DIR}{RESET}")
        input(f"\n  {DIM}按回车返回...{RESET}")

    elif choice in ("5", ""):
        return
    else:
        err("无效选择。")


def do_install() -> None:
    """Entry point for [i] — install/update and health check."""
    header("安装 / 更新 / 诊断")

    print()
    print(f"  1) 安装 / 更新 cc-connect")
    print(f"  2) 体检（检查当前环境是否健康）")
    print(f"  3) 返回")
    print()

    choice = ask("选择")
    if choice == "1":
        _do_install_or_update()
    elif choice == "2":
        _do_health_check()
    elif choice in ("3", ""):
        return
    else:
        err("无效选择。")


def do_restart() -> None:
    """Restart cc-connect daemon."""
    header("重启服务")
    running, pid = is_cc_running()
    if running:
        print(f"  当前 PID: {pid}")
    else:
        warn("cc-connect 未运行")
    restart_cc()


# ── Main ──────────────────────────────────────────────────────────────


def main() -> None:
    while True:
        header("cc-connect 配置管理")
        show_dashboard()
        _first_run_hint()

        print(
            f"  {BOLD}[a]{RESET} 添加  "
            f"{BOLD}[e]{RESET} 编辑  "
            f"{BOLD}[d]{RESET} 删除  "
            f"{BOLD}[w]{RESET} 复用  "
            f"{BOLD}[m]{RESET} 模型/服务商  "
            f"{BOLD}[c]{RESET} 定时任务  "
            f"{BOLD}[l]{RESET} 日志"
        )
        print(
            f"  {BOLD}[h]{RESET} 命令参考  "
            f"{BOLD}[g]{RESET} 高级设置  "
            f"{BOLD}[b]{RESET} 备份/恢复  "
            f"{BOLD}[r]{RESET} 重启  "
            f"{BOLD}[i]{RESET} 安装/诊断  "
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
                do_add()
            case "e":
                do_edit()
            case "d":
                do_delete()
            case "w":
                do_reuse()
            case "m":
                do_model()
            case "c":
                do_cron()
            case "l":
                do_logs()
            case "h":
                do_help()
            case "g":
                do_advanced()
            case "b":
                do_backup()
            case "r":
                do_restart()
            case "i":
                do_install()
            case "q":
                print("  再见!")
                break
            case _:
                err("无效选择。")


if __name__ == "__main__":
    main()
