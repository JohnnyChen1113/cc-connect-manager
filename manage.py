#!/usr/bin/env python3
"""cc-connect 配置管理器 — 交互式管理 ~/.cc-connect 项目配置

数据流: 用户输入 → projects.d/*.env + config.toml → 重启 cc-connect
"""

import getpass
import os
import shutil
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
PROJECTS_DIR = CC_DIR / "projects.d"
RESTART_SCRIPT = CC_DIR / "restart.sh"

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


# ── Service status ────────────────────────────────────────────────────


def is_cc_running() -> tuple[bool, int | None]:
    """Check if cc-connect daemon is running. Returns (running, pid)."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "cc-connect/bin/cc-connect"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            pid = int(result.stdout.strip().split("\n")[0])
            return True, pid
    except Exception:
        pass
    return False, None


def restart_cc() -> bool:
    """Restart cc-connect using the restart script."""
    if not RESTART_SCRIPT.exists():
        err(f"重启脚本不存在: {RESTART_SCRIPT}")
        print(f"  {DIM}请手动重启: cc-connect daemon restart{RESET}")
        return False

    print(f"\n  {DIM}正在重启 cc-connect...{RESET}")
    result = subprocess.run(
        ["bash", str(RESTART_SCRIPT)],
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


# ── Project data model ────────────────────────────────────────────────

# Default values for env file fields
ENV_DEFAULTS = {
    "P_RUNTIME": "claude",
    "P_MODE": "code",
    "P_MODEL": "",
    "P_PLATFORM": "feishu",
    "P_DOMAIN": "https://open.feishu.cn",
    "P_ALLOW_FROM": "*",
    "P_BOT_TOKEN": "",
    "P_CHAT_ID": "",
    "P_ALLOWED_USERS": "",
    "P_ALLOWED_CHANNELS": "",
    "P_ALLOWED_GUILDS": "",
}

ENV_KEYS = [
    "P_NAME", "P_DIR", "P_RUNTIME", "P_MODE", "P_MODEL", "P_PLATFORM",
    "P_APP_ID", "P_APP_SECRET", "P_DOMAIN", "P_ALLOW_FROM",
    "P_BOT_TOKEN", "P_CHAT_ID", "P_ALLOWED_USERS", "P_ALLOWED_CHANNELS",
    "P_ALLOWED_GUILDS",
]


def load_env(path: Path) -> dict:
    """Load a projects.d/*.env file into a dict."""
    data = dict(ENV_DEFAULTS)
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"')
        data[key] = val
    return data


def save_env(data: dict) -> None:
    """Write a single project's env file."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    path = PROJECTS_DIR / f"{data['P_NAME']}.env"
    lines = []
    for key in ENV_KEYS:
        val = data.get(key, "")
        lines.append(f'{key}="{val}"')
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)


def delete_env(name: str) -> None:
    """Remove a project's env file."""
    path = PROJECTS_DIR / f"{name}.env"
    if path.exists():
        path.unlink()


def list_projects() -> list[dict]:
    """Load all projects from projects.d/."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    projects = []
    for f in sorted(PROJECTS_DIR.glob("*.env")):
        projects.append(load_env(f))
    return projects


# ── Config generation ─────────────────────────────────────────────────


def generate_config(projects: list[dict]) -> None:
    """Generate config.toml from all project env data."""
    CC_DIR.mkdir(parents=True, exist_ok=True)

    # Backup existing
    if CONFIG_FILE.exists():
        shutil.copy2(CONFIG_FILE, CONFIG_FILE.with_suffix(".toml.bak"))

    doc = tomlkit.document()
    doc.add(tomlkit.comment("cc-connect 配置文件"))
    doc.add(tomlkit.comment("由 manage.py 自动生成 — 请勿手动编辑"))
    doc.add(tomlkit.nl())
    doc["language"] = "zh"
    doc.add(tomlkit.nl())

    log = tomlkit.table()
    log["level"] = "info"
    doc["log"] = log
    doc.add(tomlkit.nl())

    aot = tomlkit.aot()
    for proj in projects:
        t = tomlkit.table()
        t["name"] = proj["P_NAME"]

        agent_type = proj.get("P_RUNTIME", "claude")
        if agent_type == "claude":
            agent_type = "claudecode"

        agent = tomlkit.table()
        agent["type"] = agent_type
        agent_opts = tomlkit.table()
        agent_opts["work_dir"] = proj["P_DIR"]
        agent_opts["mode"] = proj.get("P_MODE", "code")
        if proj.get("P_MODEL"):
            agent_opts["model"] = proj["P_MODEL"]
        agent["options"] = agent_opts
        t["agent"] = agent

        plat = tomlkit.table()
        plat["type"] = proj.get("P_PLATFORM", "feishu")
        plat_opts = tomlkit.table()

        platform = proj.get("P_PLATFORM", "feishu")
        if platform == "feishu":
            plat_opts["app_id"] = proj.get("P_APP_ID", "")
            plat_opts["app_secret"] = proj.get("P_APP_SECRET", "")
            domain = proj.get("P_DOMAIN", "https://open.feishu.cn")
            if domain != "https://open.feishu.cn":
                plat_opts["domain"] = domain
            allow = proj.get("P_ALLOW_FROM", "*")
            if allow != "*":
                plat_opts["allow_from"] = allow
        elif platform == "telegram":
            plat_opts["bot_token"] = proj.get("P_BOT_TOKEN", "")
            if proj.get("P_CHAT_ID"):
                plat_opts["chat_id"] = proj["P_CHAT_ID"]
            if proj.get("P_ALLOWED_USERS"):
                plat_opts["allowed_users"] = proj["P_ALLOWED_USERS"]
        elif platform == "discord":
            plat_opts["bot_token"] = proj.get("P_BOT_TOKEN", "")
            if proj.get("P_ALLOWED_USERS"):
                plat_opts["allowed_users"] = proj["P_ALLOWED_USERS"]
            if proj.get("P_ALLOWED_CHANNELS"):
                plat_opts["allowed_channels"] = proj["P_ALLOWED_CHANNELS"]
            if proj.get("P_ALLOWED_GUILDS"):
                plat_opts["allowed_guilds"] = proj["P_ALLOWED_GUILDS"]

        plat["options"] = plat_opts
        plat_aot = tomlkit.aot()
        plat_aot.append(plat)
        t["platforms"] = plat_aot

        aot.append(t)

    doc["projects"] = aot
    CONFIG_FILE.write_text(tomlkit.dumps(doc))
    CONFIG_FILE.chmod(0o600)


def sync_all() -> None:
    """Regenerate config.toml from all env files, prompt restart if needed."""
    projects = list_projects()
    generate_config(projects)
    info(f"config.toml 已同步（{len(projects)} 个项目）")

    running, pid = is_cc_running()
    if running:
        print()
        if ask_confirm("cc-connect 正在运行，需要重启才能生效。现在重启？"):
            restart_cc()
        else:
            warn("配置已更新但尚未生效，稍后请手动重启")
            print(f"  {DIM}bash ~/.cc-connect/restart.sh{RESET}")
    else:
        warn("cc-connect 未运行")
        print(f"  {DIM}启动: cc-connect --config ~/.cc-connect/config.toml{RESET}")


# ── Interactive flows ─────────────────────────────────────────────────


def show_dashboard() -> None:
    """Show status + project list."""
    running, pid = is_cc_running()
    status = f"{GREEN}运行中{RESET} (PID {pid})" if running else f"{RED}未运行{RESET}"
    print(f"\n  服务状态: {status}")

    projects = list_projects()
    if not projects:
        print(f"  {DIM}暂无项目{RESET}\n")
        return

    print()
    print(f"  {BOLD}{'#':<4}{'名称':<20}{'平台':<10}{'工作目录'}{RESET}")
    print(f"  {DIM}{'─' * 60}{RESET}")
    for i, proj in enumerate(projects, 1):
        name = proj.get("P_NAME", "?")
        platform = proj.get("P_PLATFORM", "-")
        work_dir = proj.get("P_DIR", "-")
        print(f"  {i:<4}{name:<20}{platform:<10}{work_dir}")
    print()


def pick_project(projects: list[dict], action: str) -> int | None:
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


def collect_feishu(existing: dict | None = None) -> dict:
    """Collect Feishu credentials interactively."""
    print(f"\n  {CYAN}── 飞书应用凭证 ──{RESET}")

    if existing:
        print(f"  {DIM}回车保留当前值{RESET}")

    default_id = existing.get("P_APP_ID", "") if existing else ""
    default_secret = existing.get("P_APP_SECRET", "") if existing else ""

    print(f"  {DIM}在飞书开放平台 → 凭证与基础信息中获取{RESET}")
    app_id = ask("App ID", default_id)
    if not app_id.startswith("cli_"):
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
        return {}

    return {"P_APP_ID": app_id, "P_APP_SECRET": app_secret}


def do_add() -> None:
    """Add a new project."""
    header("添加项目")

    existing_names = {p["P_NAME"] for p in list_projects()}

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

    # 3. Platform credentials
    creds = collect_feishu()
    if not creds:
        return

    # 4. Build & save
    data = dict(ENV_DEFAULTS)
    data["P_NAME"] = name
    data["P_DIR"] = work_dir
    data["P_APP_ID"] = creds["P_APP_ID"]
    data["P_APP_SECRET"] = creds["P_APP_SECRET"]

    save_env(data)
    info(f"项目 '{name}' 已添加")

    # 5. Sync & restart
    sync_all()

    # 6. Setup guide
    show_feishu_guide(creds["P_APP_ID"])


def do_edit() -> None:
    """Edit an existing project."""
    header("编辑项目")
    projects = list_projects()
    show_dashboard()

    idx = pick_project(projects, "编辑")
    if idx is None:
        return

    proj = projects[idx]
    old_name = proj["P_NAME"]
    print(f"\n  {DIM}回车保留当前值{RESET}")

    # Editable fields
    proj["P_NAME"] = ask("项目名称", old_name)
    proj["P_DIR"] = os.path.expanduser(ask("工作目录", proj["P_DIR"]))
    proj["P_MODE"] = ask("模式 (code/plan/ask)", proj.get("P_MODE", "code"))

    if ask_confirm("更新飞书凭证？", default_yes=False):
        creds = collect_feishu(proj)
        if creds:
            proj.update(creds)

    # Handle rename
    if proj["P_NAME"] != old_name:
        delete_env(old_name)

    save_env(proj)
    info(f"项目 '{proj['P_NAME']}' 已更新")
    sync_all()


def do_delete() -> None:
    """Delete a project."""
    header("删除项目")
    projects = list_projects()
    show_dashboard()

    idx = pick_project(projects, "删除")
    if idx is None:
        return

    name = projects[idx]["P_NAME"]
    if ask_confirm(f"确认删除 '{name}'？", default_yes=False):
        delete_env(name)
        info(f"项目 '{name}' 已删除")
        sync_all()
    else:
        print("  已取消。")


def do_status() -> None:
    """Show detailed status."""
    header("服务状态")
    running, pid = is_cc_running()

    if running:
        print(f"  状态: {GREEN}运行中{RESET} (PID {pid})")
    else:
        print(f"  状态: {RED}未运行{RESET}")

    projects = list_projects()
    print(f"  项目数: {len(projects)}")
    print(f"  配置文件: {CONFIG_FILE}")

    if CONFIG_FILE.exists():
        import time
        mtime = CONFIG_FILE.stat().st_mtime
        mstr = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
        print(f"  最后修改: {mstr}")

    # Check env vs config consistency
    env_names = {p["P_NAME"] for p in projects}
    if CONFIG_FILE.exists():
        try:
            doc = tomlkit.parse(CONFIG_FILE.read_text())
            toml_names = {p.get("name", "") for p in doc.get("projects", [])}
            only_env = env_names - toml_names
            only_toml = toml_names - env_names
            if only_env:
                warn(f"env 中有但 config.toml 中缺失: {', '.join(only_env)}")
                if ask_confirm("同步 config.toml？"):
                    sync_all()
            if only_toml:
                warn(f"config.toml 中有但 env 缺失: {', '.join(only_toml)}")
        except Exception:
            pass

    print()


def do_restart() -> None:
    """Restart cc-connect."""
    header("重启服务")
    running, pid = is_cc_running()
    if running:
        print(f"  当前 PID: {pid}")
    restart_cc()


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


def do_import() -> None:
    """Import projects from config.toml that are missing in projects.d/."""
    if not CONFIG_FILE.exists():
        return

    env_names = {p["P_NAME"] for p in list_projects()}

    try:
        doc = tomlkit.parse(CONFIG_FILE.read_text())
    except Exception:
        return

    imported = 0
    for proj in doc.get("projects", []):
        name = proj.get("name", "")
        if not name or name in env_names:
            continue

        agent_opts = proj.get("agent", {}).get("options", {})
        agent_type = proj.get("agent", {}).get("type", "claudecode")
        runtime = "claude" if agent_type == "claudecode" else agent_type

        platforms = proj.get("platforms", [])
        plat = platforms[0] if platforms else {}
        plat_opts = plat.get("options", {})

        data = dict(ENV_DEFAULTS)
        data["P_NAME"] = name
        data["P_DIR"] = agent_opts.get("work_dir", "")
        data["P_RUNTIME"] = runtime
        data["P_MODE"] = agent_opts.get("mode", "code")
        data["P_MODEL"] = agent_opts.get("model", "")
        data["P_PLATFORM"] = plat.get("type", "feishu")
        data["P_APP_ID"] = plat_opts.get("app_id", "")
        data["P_APP_SECRET"] = plat_opts.get("app_secret", "")
        data["P_DOMAIN"] = plat_opts.get("domain", "https://open.feishu.cn")
        data["P_ALLOW_FROM"] = plat_opts.get("allow_from", "*")
        data["P_BOT_TOKEN"] = plat_opts.get("bot_token", "")
        data["P_CHAT_ID"] = plat_opts.get("chat_id", "")
        data["P_ALLOWED_USERS"] = plat_opts.get("allowed_users", "")
        data["P_ALLOWED_CHANNELS"] = plat_opts.get("allowed_channels", "")
        data["P_ALLOWED_GUILDS"] = plat_opts.get("allowed_guilds", "")

        save_env(data)
        imported += 1
        info(f"从 config.toml 导入: {name}")

    if imported > 0:
        sync_all()


# ── Main ──────────────────────────────────────────────────────────────


def main() -> None:
    # Auto-import on first run
    if not any(PROJECTS_DIR.glob("*.env")) if PROJECTS_DIR.exists() else True:
        do_import()
    else:
        # Check for projects in config.toml missing from projects.d
        do_import()

    while True:
        header("cc-connect 配置管理")
        show_dashboard()

        print(
            f"  {BOLD}[a]{RESET} 添加   "
            f"{BOLD}[e]{RESET} 编辑   "
            f"{BOLD}[d]{RESET} 删除   "
            f"{BOLD}[s]{RESET} 状态   "
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
                do_add()
            case "e":
                do_edit()
            case "d":
                do_delete()
            case "s":
                do_status()
            case "r":
                do_restart()
            case "q":
                print("  再见!")
                break
            case _:
                err("无效选择。")


if __name__ == "__main__":
    main()
