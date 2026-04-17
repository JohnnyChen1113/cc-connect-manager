#!/usr/bin/env bash
# cc-connect-manager bootstrap
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/JohnnyChen1113/cc-connect-manager/main/install.sh | bash
#
# Flow:
#   1. Scan for existing Node/npm (conda envs / brew / nvm / system)
#   2. Let user pick one, or if none, fall back to conda/brew
#   3. Ensure tomlkit is installed in matching Python env
#   4. npm install -g cc-connect
#   5. Download manage.py to ~/.cc-connect-manager/
#   6. Add `cc-manage` alias to shell rc
#   7. Run manage.py (triggers in-app do_install for launchd)

set -u

# ── Colors ────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    RED=$'\033[0;31m'
    GREEN=$'\033[0;32m'
    YELLOW=$'\033[1;33m'
    CYAN=$'\033[0;36m'
    BOLD=$'\033[1m'
    DIM=$'\033[2m'
    RESET=$'\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; BOLD=''; DIM=''; RESET=''
fi

info()   { echo "  ${GREEN}✓${RESET} $*"; }
warn()   { echo "  ${YELLOW}!${RESET} $*"; }
err()    { echo "  ${RED}✗${RESET} $*" >&2; }
header() {
    echo
    echo "  ${CYAN}──────────────────────────────────────────────────${RESET}"
    echo "  ${BOLD}$*${RESET}"
    echo "  ${CYAN}──────────────────────────────────────────────────${RESET}"
}

MANAGER_DIR="$HOME/.cc-connect-manager"
MANAGER_SCRIPT="$MANAGER_DIR/manage.py"
MANAGE_URL="https://raw.githubusercontent.com/JohnnyChen1113/cc-connect-manager/main/manage.py"

# Globals populated by detection
SELECTED_NODE_BIN=""
SELECTED_NODE_LABEL=""
SELECTED_PYTHON_BIN=""
SELECTED_PIP_ARGS=""    # extra args passed to pip (e.g. --user)

# ── Node detection ────────────────────────────────────────────────────

# Fill FOUND_PATHS / FOUND_LABELS with candidate node binaries.
FOUND_PATHS=()
FOUND_LABELS=()

label_for_path() {
    local p="$1"
    case "$p" in
        *"/miniforge3/envs/"*|*"/miniconda3/envs/"*|*"/anaconda3/envs/"*)
            local envname base
            envname="$(echo "$p" | sed -E 's#.*/envs/([^/]+)/.*#\1#')"
            base="$(echo "$p" | sed -E 's#.*/(miniforge3|miniconda3|anaconda3)/.*#\1#')"
            echo "conda env ${envname} (${base})"
            ;;
        *"/miniforge3/bin/"*|*"/miniconda3/bin/"*|*"/anaconda3/bin/"*)
            local base
            base="$(echo "$p" | sed -E 's#.*/(miniforge3|miniconda3|anaconda3)/.*#\1#')"
            echo "conda base (${base})"
            ;;
        *"/.nvm/versions/node/"*)
            local ver
            ver="$(echo "$p" | sed -E 's#.*/node/([^/]+)/.*#\1#')"
            echo "nvm ${ver}"
            ;;
        /opt/homebrew/*|/usr/local/Cellar/*|/usr/local/bin/*)
            if [[ "$p" == */Cellar/* ]] || brew --prefix >/dev/null 2>&1; then
                echo "homebrew"
            else
                echo "system ($p)"
            fi
            ;;
        *)
            echo "system ($p)"
            ;;
    esac
}

canonical() {
    if command -v realpath >/dev/null 2>&1; then
        realpath "$1" 2>/dev/null || echo "$1"
    else
        # Fallback: use readlink + cd trick
        local d="$(cd "$(dirname "$1")" 2>/dev/null && pwd -P)"
        [[ -n "$d" ]] && echo "$d/$(basename "$1")" || echo "$1"
    fi
}

add_candidate() {
    local bin="$1"
    [[ ! -x "$bin" ]] && return
    # Require adjacent npm
    local dir="$(dirname "$bin")"
    [[ ! -x "$dir/npm" ]] && return

    local canon
    canon="$(canonical "$bin")"
    # dedupe
    local existing
    for existing in "${FOUND_PATHS[@]:-}"; do
        [[ "$existing" == "$canon" ]] && return
    done

    FOUND_PATHS+=("$canon")
    FOUND_LABELS+=("$(label_for_path "$canon")")
}

scan_nodes() {
    local bin
    # PATH-based
    if command -v node >/dev/null 2>&1; then
        while IFS= read -r bin; do
            add_candidate "$bin"
        done < <(which -a node 2>/dev/null)
    fi

    # Conda envs
    for prefix in "$HOME/miniforge3" "$HOME/miniconda3" "$HOME/anaconda3" \
                  "/opt/miniforge3" "/opt/miniconda3" "/opt/anaconda3"; do
        [[ -x "$prefix/bin/node" ]] && add_candidate "$prefix/bin/node"
        if [[ -d "$prefix/envs" ]]; then
            local env_bin
            for env_bin in "$prefix"/envs/*/bin/node; do
                [[ -x "$env_bin" ]] && add_candidate "$env_bin"
            done
        fi
    done

    # nvm
    if [[ -d "$HOME/.nvm/versions/node" ]]; then
        local nvm_bin
        for nvm_bin in "$HOME"/.nvm/versions/node/*/bin/node; do
            [[ -x "$nvm_bin" ]] && add_candidate "$nvm_bin"
        done
    fi

    # brew
    if command -v brew >/dev/null 2>&1; then
        local brew_prefix
        brew_prefix="$(brew --prefix 2>/dev/null)"
        [[ -n "$brew_prefix" && -x "$brew_prefix/bin/node" ]] && add_candidate "$brew_prefix/bin/node"
    fi

    # Common macOS paths
    for p in /usr/local/bin/node /opt/homebrew/bin/node /usr/bin/node; do
        [[ -x "$p" ]] && add_candidate "$p"
    done
}

pick_node() {
    local count=${#FOUND_PATHS[@]}
    if [[ $count -eq 0 ]]; then
        return 1
    fi

    if [[ $count -eq 1 ]]; then
        SELECTED_NODE_BIN="${FOUND_PATHS[0]}"
        SELECTED_NODE_LABEL="${FOUND_LABELS[0]}"
        local ver
        ver="$("$SELECTED_NODE_BIN" --version 2>/dev/null)"
        info "使用 Node.js ${ver}（${SELECTED_NODE_LABEL}）"
        info "路径: ${DIM}${SELECTED_NODE_BIN}${RESET}"
        return 0
    fi

    echo
    echo "  ${BOLD}检测到多个 Node.js 安装，请选择一个：${RESET}"
    local i
    for ((i=0; i<count; i++)); do
        local ver
        ver="$("${FOUND_PATHS[$i]}" --version 2>/dev/null || echo 'unknown')"
        printf "  %2d) %s  ${DIM}%s${RESET}\n" \
            "$((i+1))" \
            "${FOUND_LABELS[$i]} (${ver})" \
            "${FOUND_PATHS[$i]}"
    done
    echo
    local choice
    read -rp "  选择编号 [1]: " choice < /dev/tty
    choice="${choice:-1}"
    if ! [[ "$choice" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > count )); then
        err "无效编号。"
        return 1
    fi
    SELECTED_NODE_BIN="${FOUND_PATHS[$((choice-1))]}"
    SELECTED_NODE_LABEL="${FOUND_LABELS[$((choice-1))]}"
    info "已选: ${SELECTED_NODE_LABEL}"
    return 0
}

# ── Install Node if missing ───────────────────────────────────────────

install_node_via_conda() {
    local conda_bin
    conda_bin="$(command -v conda || true)"
    [[ -z "$conda_bin" ]] && return 1

    echo
    info "检测到 conda: $conda_bin"
    local env_name="cc-connect"
    local use_existing="n"
    read -rp "  新建 conda 环境 '${env_name}' 并安装 nodejs？(Y/n) " use_existing < /dev/tty
    use_existing="${use_existing:-y}"
    if [[ ! "$use_existing" =~ ^[Yy] ]]; then
        warn "跳过 conda 安装。"
        return 1
    fi

    # Check if env exists
    if conda env list 2>/dev/null | awk '{print $1}' | grep -qx "$env_name"; then
        info "环境 '${env_name}' 已存在，将在其中安装 nodejs"
    else
        info "创建环境 '${env_name}'..."
        if ! conda create -n "$env_name" -c conda-forge python=3.11 -y; then
            err "conda create 失败"
            return 1
        fi
    fi

    info "在 '${env_name}' 安装 nodejs..."
    if ! conda install -n "$env_name" -c conda-forge nodejs -y; then
        err "conda install nodejs 失败"
        return 1
    fi

    # Locate the newly installed node
    local conda_base
    conda_base="$(conda info --base 2>/dev/null)"
    local env_node="$conda_base/envs/$env_name/bin/node"
    if [[ ! -x "$env_node" ]]; then
        err "未能在 '${env_name}' 找到 node"
        return 1
    fi
    add_candidate "$env_node"
    SELECTED_NODE_BIN="$(canonical "$env_node")"
    SELECTED_NODE_LABEL="conda env ${env_name}"
    return 0
}

install_node_via_brew() {
    command -v brew >/dev/null 2>&1 || return 1

    echo
    info "通过 Homebrew 安装 Node.js"
    local confirm
    read -rp "  确认执行 'brew install node'？(Y/n) " confirm < /dev/tty
    confirm="${confirm:-y}"
    if [[ ! "$confirm" =~ ^[Yy] ]]; then
        warn "跳过 brew 安装。"
        return 1
    fi

    if ! brew install node; then
        err "brew install node 失败"
        return 1
    fi

    local brew_node
    brew_node="$(brew --prefix)/bin/node"
    [[ -x "$brew_node" ]] || { err "brew 装完后仍找不到 node"; return 1; }
    add_candidate "$brew_node"
    SELECTED_NODE_BIN="$(canonical "$brew_node")"
    SELECTED_NODE_LABEL="homebrew"
    return 0
}

# ── Python + tomlkit ──────────────────────────────────────────────────

py_version_code() {
    # Prints "310" for Python 3.10 etc. Empty on failure.
    "$1" -c 'import sys; print(f"{sys.version_info.major}{sys.version_info.minor:02d}")' 2>/dev/null
}

resolve_python_for_node() {
    # Find a Python 3.10+ — prefer the node's env, then scan PATH for
    # python3.13..python3.10, then fall back to plain python3.
    local node_dir
    node_dir="$(dirname "$SELECTED_NODE_BIN")"

    local candidate ver
    for candidate in "$node_dir/python3" "$node_dir/python"; do
        [[ -x "$candidate" ]] || continue
        ver="$(py_version_code "$candidate")"
        if [[ -n "$ver" ]] && (( ver >= 310 )); then
            SELECTED_PYTHON_BIN="$candidate"
            SELECTED_PIP_ARGS=""
            return 0
        fi
    done

    # Scan PATH — newest first. python3 last as fallback.
    local name
    for name in python3.13 python3.12 python3.11 python3.10 python3; do
        candidate="$(command -v "$name" 2>/dev/null)" || continue
        ver="$(py_version_code "$candidate")"
        if [[ -n "$ver" ]] && (( ver >= 310 )); then
            SELECTED_PYTHON_BIN="$candidate"
            # If the found python is inside a conda env, no --user needed
            case "$candidate" in
                *"/envs/"*/bin/python*|*"/miniforge3/bin/"*|*"/miniconda3/bin/"*|*"/anaconda3/bin/"*)
                    SELECTED_PIP_ARGS="" ;;
                *)
                    SELECTED_PIP_ARGS="--user" ;;
            esac
            return 0
        fi
    done

    err "未找到 Python 3.10+"
    echo "  ${DIM}建议用 conda 安装: conda install -n <env> -c conda-forge python=3.11${RESET}"
    return 1
}

ensure_tomlkit() {
    if "$SELECTED_PYTHON_BIN" -c 'import tomlkit' 2>/dev/null; then
        info "tomlkit 已就绪"
        return 0
    fi

    info "安装 tomlkit..."
    local pip_cmd=("$SELECTED_PYTHON_BIN" -m pip install)
    [[ -n "$SELECTED_PIP_ARGS" ]] && pip_cmd+=("$SELECTED_PIP_ARGS")
    pip_cmd+=(tomlkit)
    if ! "${pip_cmd[@]}"; then
        err "tomlkit 安装失败，请手动运行: ${SELECTED_PYTHON_BIN} -m pip install tomlkit"
        return 1
    fi
    return 0
}

# ── cc-connect install ────────────────────────────────────────────────

ensure_cc_connect() {
    local node_dir
    node_dir="$(dirname "$SELECTED_NODE_BIN")"
    local npm_bin="$node_dir/npm"
    local cc_bin="$node_dir/cc-connect"

    if [[ -x "$cc_bin" ]]; then
        local ver
        ver="$("$cc_bin" --version 2>/dev/null | head -1)"
        info "cc-connect 已安装: ${ver}"
        local reinstall
        read -rp "  检查并更新到最新版？(y/N) " reinstall < /dev/tty
        reinstall="${reinstall:-n}"
        if [[ ! "$reinstall" =~ ^[Yy] ]]; then
            return 0
        fi
    fi

    info "通过 npm 安装 cc-connect..."
    if ! PATH="$node_dir:$PATH" "$npm_bin" install -g cc-connect; then
        err "npm install -g cc-connect 失败"
        echo "  ${DIM}若权限不足，尝试: sudo ${npm_bin} install -g cc-connect${RESET}"
        return 1
    fi

    if [[ ! -x "$cc_bin" ]]; then
        err "安装后仍找不到 cc-connect: $cc_bin"
        return 1
    fi
    info "cc-connect 已安装"
    return 0
}

# ── manage.py download ────────────────────────────────────────────────

download_manage_py() {
    mkdir -p "$MANAGER_DIR"

    if [[ -f "$MANAGER_SCRIPT" ]]; then
        info "检测到已有 manage.py，将更新到最新版"
    fi

    # Prefer local copy if script sits next to install.sh (dev workflow)
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
    if [[ -f "$here/manage.py" ]]; then
        info "使用本地 manage.py: $here/manage.py"
        cp "$here/manage.py" "$MANAGER_SCRIPT"
    else
        info "下载 manage.py..."
        if ! curl -fsSL "$MANAGE_URL" -o "$MANAGER_SCRIPT.tmp"; then
            err "下载失败: $MANAGE_URL"
            rm -f "$MANAGER_SCRIPT.tmp"
            return 1
        fi
        mv "$MANAGER_SCRIPT.tmp" "$MANAGER_SCRIPT"
    fi
    chmod +x "$MANAGER_SCRIPT"
    info "安装到: $MANAGER_SCRIPT"
    return 0
}

# ── Shell alias ───────────────────────────────────────────────────────

ensure_shell_alias() {
    local rc
    case "${SHELL:-}" in
        */zsh) rc="$HOME/.zshrc" ;;
        */bash)
            if [[ -f "$HOME/.bashrc" ]]; then rc="$HOME/.bashrc";
            else rc="$HOME/.bash_profile"; fi
            ;;
        *) rc="$HOME/.profile" ;;
    esac

    local alias_line="alias cc-manage='${SELECTED_PYTHON_BIN} ${MANAGER_SCRIPT}'"
    local marker="# cc-connect-manager alias"

    touch "$rc"
    if grep -q "$marker" "$rc" 2>/dev/null; then
        # Replace existing block (two lines)
        local tmp
        tmp="$(mktemp)"
        awk -v marker="$marker" '
            $0 ~ marker { skip=1; next }
            skip && /^alias cc-manage=/ { skip=0; next }
            { print }
        ' "$rc" > "$tmp"
        mv "$tmp" "$rc"
    fi
    {
        echo ""
        echo "$marker"
        echo "$alias_line"
    } >> "$rc"
    info "已添加 cc-manage 别名到 $rc"
    warn "当前终端生效: 执行 ${BOLD}source $rc${RESET}  或重开终端"
    return 0
}

# ── Main ──────────────────────────────────────────────────────────────

main() {
    header "cc-connect-manager 安装向导"

    # Step 1: find or install node
    header "[1/5] 检查 Node.js 环境"
    scan_nodes
    if ! pick_node; then
        warn "未找到可用的 Node.js 安装"
        echo
        echo "  可选方案："
        if command -v conda >/dev/null 2>&1; then
            echo "    1) conda 创建 cc-connect 环境"
        fi
        if command -v brew >/dev/null 2>&1; then
            echo "    2) Homebrew 安装"
        fi
        if ! command -v conda >/dev/null 2>&1 && ! command -v brew >/dev/null 2>&1; then
            err "请先安装 Homebrew 或 Miniforge，然后重新运行此脚本"
            echo
            echo "  Homebrew: ${CYAN}/bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"${RESET}"
            echo "  Miniforge: ${CYAN}curl -L -O https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-\$(uname)-\$(uname -m).sh && bash Miniforge3-\$(uname)-\$(uname -m).sh${RESET}"
            exit 1
        fi

        # Try conda first if present, else brew
        local installed=0
        if command -v conda >/dev/null 2>&1; then
            install_node_via_conda && installed=1
        fi
        if [[ $installed -eq 0 ]] && command -v brew >/dev/null 2>&1; then
            install_node_via_brew && installed=1
        fi
        if [[ $installed -eq 0 ]]; then
            err "Node.js 安装失败"
            exit 1
        fi
    fi

    # Step 2: Python + tomlkit
    header "[2/5] 检查 Python / tomlkit"
    resolve_python_for_node || exit 1
    local py_ver
    py_ver="$("$SELECTED_PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)"
    info "Python ${py_ver}: $SELECTED_PYTHON_BIN"
    ensure_tomlkit || exit 1

    # Step 3: cc-connect
    header "[3/5] 安装 cc-connect"
    ensure_cc_connect || exit 1

    # Step 4: manage.py
    header "[4/5] 部署 manage.py"
    download_manage_py || exit 1
    ensure_shell_alias

    # Step 5: launch manage.py do_install for launchd
    header "[5/5] 注册后台服务 / 进入主菜单"
    echo
    info "启动 manage.py..."
    echo
    # Export cc-connect's directory to PATH so manage.py can find it
    export PATH="$(dirname "$SELECTED_NODE_BIN"):$PATH"
    exec "$SELECTED_PYTHON_BIN" "$MANAGER_SCRIPT"
}

main "$@"
