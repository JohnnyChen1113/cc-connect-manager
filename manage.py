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
