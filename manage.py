#!/usr/bin/env python3
"""cc-connect 配置管理器 — 交互式管理 ~/.cc-connect/config.toml"""

import getpass
import os
import shutil
import sys
from pathlib import Path

try:
    import tomlkit
except ImportError:
    print("需要安装 tomlkit: pip install tomlkit")
    sys.exit(1)

# ── Paths ──────────────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".cc-connect"
CONFIG_FILE = CONFIG_DIR / "config.toml"

# ── Colors ─────────────────────────────────────────────────────────────

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def info(msg: str) -> None:
    print(f"  {GREEN}[OK]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}[!]{RESET} {msg}")


def err(msg: str) -> None:
    print(f"  {RED}[ERR]{RESET} {msg}")


def header(title: str) -> None:
    print()
    print(f"  {CYAN}{'━' * 50}{RESET}")
    print(f"  {BOLD}{title}{RESET}")
    print(f"  {CYAN}{'━' * 50}{RESET}")


def ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {BOLD}{label}{suffix}: {RESET}")
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return answer.strip() or default


def ask_secret(label: str) -> str:
    try:
        answer = getpass.getpass(f"  {BOLD}{label}: {RESET}")
    except (EOFError, KeyboardInterrupt):
        print()
        return ""
    return answer.strip()


def ask_confirm(label: str, default_yes: bool = True) -> bool:
    hint = "Y/n" if default_yes else "y/N"
    try:
        answer = input(f"  {BOLD}{label} [{hint}]: {RESET}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default_yes
    if not answer:
        return default_yes
    return answer.startswith("y")


def mask_secret(s: str) -> str:
    if len(s) <= 4:
        return "****"
    return "****" + s[-4:]


# ── Platform definitions ───────────────────────────────────────────────

PLATFORMS = {
    "feishu": {
        "label": "飞书",
        "fields": [
            {
                "key": "app_id",
                "label": "App ID",
                "hint": "格式如 cli_xxxxxxxxxx",
                "secret": False,
                "validate": lambda v: v.startswith("cli_") or "App ID 应以 cli_ 开头",
            },
            {
                "key": "app_secret",
                "label": "App Secret",
                "hint": "在飞书开放平台 → 凭证与基础信息中获取",
                "secret": True,
                "validate": lambda v: True if v else "App Secret 不能为空",
            },
            {
                "key": "domain",
                "label": "域名",
                "hint": "",
                "secret": False,
                "default": "https://open.feishu.cn",
                "validate": lambda v: True if v.startswith("http") else "域名应以 http 开头",
                "skip_if_default": True,
            },
            {
                "key": "allow_from",
                "label": "允许的用户 ID",
                "hint": "逗号分隔, * 表示所有人",
                "secret": False,
                "default": "*",
                "validate": lambda _: True,
                "skip_if_default": True,
            },
        ],
    },
}

# ── Config I/O ─────────────────────────────────────────────────────────


def load_config() -> tomlkit.TOMLDocument:
    """Load config.toml, or create a minimal one if it doesn't exist."""
    if CONFIG_FILE.exists():
        return tomlkit.parse(CONFIG_FILE.read_text())
    doc = tomlkit.document()
    doc.add(tomlkit.comment("cc-connect 配置文件"))
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
    """Validate and write config.toml, with backup."""
    errors = validate_config(doc)
    if errors:
        err("配置校验失败，未写入:")
        for e in errors:
            print(f"    - {e}")
        return

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        shutil.copy2(CONFIG_FILE, CONFIG_FILE.with_suffix(".toml.bak"))

    CONFIG_FILE.write_text(tomlkit.dumps(doc))
    CONFIG_FILE.chmod(0o600)
    info(f"已写入 {CONFIG_FILE}")


def validate_config(doc: tomlkit.TOMLDocument) -> list[str]:
    """Validate the full config, return list of error messages."""
    errors = []
    projects = doc.get("projects", [])
    names = set()

    for i, proj in enumerate(projects):
        name = proj.get("name", "")
        if not name:
            errors.append(f"项目 #{i + 1}: 名称不能为空")
        elif name in names:
            errors.append(f"项目 #{i + 1}: 名称 '{name}' 重复")
        names.add(name)

        work_dir = proj.get("agent", {}).get("options", {}).get("work_dir", "")
        if work_dir and not Path(work_dir).expanduser().exists():
            warn(f"项目 '{name}': 工作目录 '{work_dir}' 不存在（cc-connect 启动时会尝试创建）")

        platforms = proj.get("platforms", [])
        for plat in platforms:
            ptype = plat.get("type", "")
            opts = plat.get("options", {})
            pdef = PLATFORMS.get(ptype)
            if not pdef:
                continue
            for field in pdef["fields"]:
                if field.get("skip_if_default"):
                    continue
                val = opts.get(field["key"], "")
                result = field["validate"](val)
                if result is not True:
                    errors.append(f"项目 '{name}' → {field['label']}: {result}")

    return errors


# ── Project helpers ────────────────────────────────────────────────────


def get_projects(doc: tomlkit.TOMLDocument) -> tomlkit.items.AoT:
    if "projects" not in doc:
        doc["projects"] = tomlkit.aot()
    return doc["projects"]


def show_projects(doc: tomlkit.TOMLDocument) -> None:
    projects = get_projects(doc)
    print()
    if not projects:
        print(f"  {DIM}(暂无项目){RESET}")
        return

    print(
        f"  {BOLD}{'#':<4} {'名称':<20} {'平台':<10} {'工作目录'}{RESET}"
    )
    print(f"  {DIM}{'─' * 70}{RESET}")
    for i, proj in enumerate(projects, 1):
        name = proj.get("name", "?")
        work_dir = proj.get("agent", {}).get("options", {}).get("work_dir", "-")
        platforms = proj.get("platforms", [])
        ptype = platforms[0].get("type", "-") if platforms else "-"
        print(f"  {i:<4} {name:<20} {ptype:<10} {work_dir}")


def select_project(doc: tomlkit.TOMLDocument, action: str) -> int | None:
    """Ask user to select a project. Returns index or None."""
    projects = get_projects(doc)
    if not projects:
        warn("暂无项目。")
        return None
    show_projects(doc)
    print()
    choice = ask(f"输入要{action}的项目编号")
    try:
        idx = int(choice) - 1
    except ValueError:
        err("请输入数字。")
        return None
    if idx < 0 or idx >= len(projects):
        err("无效的编号。")
        return None
    return idx


# ── Collect platform fields ───────────────────────────────────────────


def collect_platform_options(
    ptype: str, existing: dict | None = None
) -> dict | None:
    """Interactively collect platform-specific options. Returns dict or None on cancel."""
    pdef = PLATFORMS.get(ptype)
    if not pdef:
        err(f"不支持的平台: {ptype}")
        return None

    print(f"\n  {CYAN}── {pdef['label']}配置 ──{RESET}")
    opts = {}

    for field in pdef["fields"]:
        key = field["key"]
        default = ""
        if existing and key in existing:
            default = str(existing[key])
        elif "default" in field:
            default = field["default"]

        if field.get("hint"):
            print(f"  {DIM}{field['hint']}{RESET}")

        while True:
            if field["secret"]:
                if default:
                    print(f"  {DIM}当前值: {mask_secret(default)}{RESET}")
                    if existing:
                        skip = ask("按回车保留当前值，输入 new 重新输入", "keep")
                        if skip == "keep":
                            opts[key] = default
                            break
                val = ask_secret(field["label"])
                if not val and default:
                    val = default
            else:
                val = ask(field["label"], default)

            result = field["validate"](val)
            if result is True:
                opts[key] = val
                break
            else:
                err(result)

    return opts


# ── CRUD operations ────────────────────────────────────────────────────


def add_project(doc: tomlkit.TOMLDocument) -> None:
    header("添加项目")

    projects = get_projects(doc)
    existing_names = {p.get("name", "") for p in projects}

    # Name
    print(f"  {DIM}给项目起个名字，用于区分不同的机器人{RESET}")
    while True:
        name = ask("项目名称")
        if not name:
            err("名称不能为空。")
            continue
        if name in existing_names:
            err(f"项目 '{name}' 已存在。")
            continue
        break

    # Work dir
    print(f"\n  {DIM}Claude 会在这个目录里读写文件{RESET}")
    while True:
        work_dir = ask("工作目录", os.getcwd())
        work_dir = os.path.expanduser(work_dir)
        if not os.path.isdir(work_dir):
            warn(f"目录 '{work_dir}' 不存在。")
            if not ask_confirm("仍然使用？", default_yes=False):
                continue
        break

    # Platform
    available = list(PLATFORMS.keys())
    if len(available) == 1:
        ptype = available[0]
        print(f"\n  {DIM}平台: {PLATFORMS[ptype]['label']}{RESET}")
    else:
        ptype = ask("平台", available[0])

    # Platform options
    opts = collect_platform_options(ptype)
    if opts is None:
        return

    # Preview
    header("确认配置")
    print(f"  名称:     {name}")
    print(f"  工作目录: {work_dir}")
    print(f"  平台:     {ptype}")
    for field in PLATFORMS[ptype]["fields"]:
        val = opts.get(field["key"], "")
        display = mask_secret(val) if field["secret"] else val
        print(f"  {field['label']}: {display}")

    print()
    if not ask_confirm("确认添加？"):
        print("  已取消。")
        return

    # Build TOML structure
    proj = tomlkit.table()
    proj["name"] = name

    agent = tomlkit.table()
    agent["type"] = "claudecode"
    agent_opts = tomlkit.table()
    agent_opts["work_dir"] = work_dir
    agent_opts["mode"] = "code"
    agent["options"] = agent_opts
    proj["agent"] = agent

    plat = tomlkit.table()
    plat["type"] = ptype
    plat_opts = tomlkit.table()
    for field in PLATFORMS[ptype]["fields"]:
        val = opts.get(field["key"], "")
        default = field.get("default", "")
        if field.get("skip_if_default") and val == default:
            continue
        plat_opts[field["key"]] = val
    plat["options"] = plat_opts

    plat_aot = tomlkit.aot()
    plat_aot.append(plat)
    proj["platforms"] = plat_aot

    projects.append(proj)
    save_config(doc)

    # Post-setup hints for feishu
    if ptype == "feishu":
        print()
        print(f"  {BOLD}飞书应用还需要在后台完成以下配置:{RESET}")
        print()
        print(f"  {CYAN}启动前:{RESET}")
        print("    [ ] 权限管理 → 批量添加权限")
        print("    [ ] 添加应用能力 → 启用「机器人」")
        print("    [ ] 版本管理 → 创建版本 → 提交审核 → 管理员审批")
        print()
        print(f"  {CYAN}启动后:{RESET}")
        print("    [ ] 事件与回调 → 长连接")
        print("    [ ] 添加事件: im.message.receive_v1")
        print("    [ ] 添加回调: card.action.trigger")
        print("    [ ] 再次发版 → 审核 → 审批")
        print()


def edit_project(doc: tomlkit.TOMLDocument) -> None:
    header("编辑项目")
    idx = select_project(doc, "编辑")
    if idx is None:
        return

    proj = get_projects(doc)[idx]
    old_name = proj.get("name", "")

    print(f"\n  {DIM}直接回车保持当前值{RESET}")

    # Name
    projects = get_projects(doc)
    existing_names = {p.get("name", "") for i, p in enumerate(projects) if i != idx}
    while True:
        name = ask("项目名称", old_name)
        if not name:
            err("名称不能为空。")
            continue
        if name in existing_names:
            err(f"项目 '{name}' 已存在。")
            continue
        break
    proj["name"] = name

    # Work dir
    agent_opts = proj.get("agent", {}).get("options", {})
    old_dir = agent_opts.get("work_dir", os.getcwd())
    work_dir = ask("工作目录", old_dir)
    work_dir = os.path.expanduser(work_dir)
    agent_opts["work_dir"] = work_dir

    # Mode
    old_mode = agent_opts.get("mode", "code")
    mode = ask("模式 (code/plan/ask)", old_mode)
    agent_opts["mode"] = mode

    # Platform credentials
    platforms = proj.get("platforms", [])
    if platforms:
        plat = platforms[0]
        ptype = plat.get("type", "")
        existing_opts = dict(plat.get("options", {}))
        print()
        if ask_confirm(f"重新输入 {PLATFORMS.get(ptype, {}).get('label', ptype)} 凭证？", default_yes=False):
            new_opts = collect_platform_options(ptype, existing_opts)
            if new_opts is not None:
                plat_opts = plat.get("options", tomlkit.table())
                for k, v in new_opts.items():
                    field = next(
                        (f for f in PLATFORMS.get(ptype, {}).get("fields", []) if f["key"] == k),
                        None,
                    )
                    default = field.get("default", "") if field else ""
                    skip = field.get("skip_if_default", False) if field else False
                    if skip and v == default:
                        plat_opts.pop(k, None)
                    else:
                        plat_opts[k] = v

    save_config(doc)


def delete_project(doc: tomlkit.TOMLDocument) -> None:
    header("删除项目")
    idx = select_project(doc, "删除")
    if idx is None:
        return

    proj = get_projects(doc)[idx]
    name = proj.get("name", "?")

    if ask_confirm(f"确认删除项目 '{name}'？", default_yes=False):
        del get_projects(doc)[idx]
        save_config(doc)
    else:
        print("  已取消。")


# ── Main ───────────────────────────────────────────────────────────────


def main() -> None:
    doc = load_config()

    while True:
        header("cc-connect 配置管理")
        show_projects(doc)
        print()
        print(
            f"  {BOLD}[a]{RESET} 添加项目  "
            f"{BOLD}[e]{RESET} 编辑  "
            f"{BOLD}[d]{RESET} 删除  "
            f"{BOLD}[q]{RESET} 退出"
        )
        print()

        try:
            choice = input(f"  {BOLD}>{RESET} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  再见!")
            break

        if choice == "a":
            add_project(doc)
        elif choice == "e":
            edit_project(doc)
        elif choice == "d":
            delete_project(doc)
        elif choice == "q":
            print("  再见!")
            break
        else:
            err("无效的选择。")


if __name__ == "__main__":
    main()
