# Backlog

未做的事，按"什么时候值得回来捡"分组。每项都是在某次讨论/review 里明确讨论过并 skip 掉的，不是临时想起来的。

---

## 🟠 有真实价值，等需求出现再做

### 1. install.sh 真机测试 + 修补
**状态：** 从未在干净机器上跑过。现有代码基于作者自己的环境（已有 miniforge nodejs env）推理出来。  
**风险：** 小白用户真跑一行命令时可能踩坑（conda 环境权限、shell rc 检测、PATH 注入失败等）。  
**触发条件：** 第一次向听众/朋友推这个工具前必须做。  
**建议做法：** 新开一个 macOS VM，纯净状态（无 conda、无 brew），curl | bash 跑一遍。把踩坑处补进 install.sh。

### 2. 错误提示去技术黑话
**状态：** 大量技术术语暴露给了小白用户（launchd、tomlkit、session hash、daemon、TOML parse error…）。  
**例子：**
- "cc-connect daemon 运行中 (PID 63479)" → "服务正在运行"
- "tomlkit 未安装" → "缺少一个 Python 依赖，需要运行 pip install tomlkit"
- "写入 config.toml 失败" → 可以保留但加一行"通常是权限问题，尝试..."  

**触发条件：** 有非技术听众真在用的反馈，或者做 audience-facing demo 前。  
**建议做法：** 走一遍完整 happy path + 常见 failure path 的所有输出，把每条消息按"小白能看懂吗"打分，改掉 F 级。

### 3. Linux systemd 支持
**状态：** 目前只支持 macOS launchd。Linux 安装会卡在 `_setup_launchd`。  
**触发条件：** 有 Linux 用户问到。  
**工作量：** 写一个 `_setup_systemd()` 并行分支 + 在 `install.sh` 里按 `uname` 分流。~80 LOC。

### 4. collect_* 平台凭证收集函数的 schema 化重构
**状态：** `collect_feishu / telegram / discord / dingtalk / slack / qq / qqbot` 7 个函数重复度很高（"existing-token-handling" 那段贴了 11 次）。  
**潜在收益：** ~120-150 LOC 可删。  
**风险：** 中等——改动动到凭证路径，一旦出 bug 用户可能填错凭证。没有测试覆盖时不做。  
**触发条件：** 添加新平台（第 8 个）时一起做，或者要加凭证字段校验时顺手做。

### 5. CLI 子命令模式（非交互式）
**状态：** 记忆里标记为"未来"。当前只有交互式 TUI。  
**需求：** 熟悉后想脚本化，例如 `cc-manage switch-model LLM_wiki sonnet` 或 `cc-manage add-project --from-file spec.toml`。  
**触发条件：** 自己开始有脚本化需求；或者被要求集成到更大的自动化流程。  
**工作量：** 不小——需要把每个 `do_*` 函数的参数提取出来做 argparse 或 click，同时保留 TUI 入口。

### 6. install.sh 的 uninstall 模式
**状态：** 没做。  
**需求：** `install.sh --uninstall` 清掉 launchd plist、`~/.cc-connect-manager/`、shell rc 别名。  
**触发条件：** 第一次需要给别人卸载/重新安装时。

### 7. LICENSE 文件
**状态：** README 里 "TBD"。  
**决策：** 你还没决定用 MIT / Apache 2.0 / 别的。  
**触发条件：** 公开分享之前。

---

## 🟡 代码质量类，等真遇到问题再捞

### 8. do_import_session 拆函数
**来自：** 2026-04-17 simplify review。  
**状态：** ~195 LOC 的线性 TUI 流程。拆成 `_wait_for_first_chat` / `_pick_slot` / `_apply_import` 可以降到 ~60 LOC 编排。  
**为什么 skip：** 单次使用的流程，拆了反而增加跳转。只有在加类似功能（例如反向：把 Feishu session 导回桌面）时值得抽共用部分。

### 9. 槽位 `s1/s10/s2` 自然排序
**来自：** 2026-04-17 simplify review。  
**状态：** 用 `sorted(slots.keys())` 会按字典序排，出现 s1/s10/s2 误排。  
**为什么 skip：** 需要用户有 >9 个 session 槽位才触发，实际几乎不可能。  
**修法：** `sorted(slots.keys(), key=lambda k: (len(k), k))` 或者正则提取数字部分。

### 10. ask_secret SIGTSTP (Ctrl+Z) 处理
**来自：** 2026-04-17 simplify review。  
**状态：** 用户输 Secret 时按 Ctrl+Z 挂起，恢复后终端可能停留在 raw mode。  
**为什么 skip：** 用户一般不会在输密码时按 Ctrl+Z。即使遇到也 `reset` 命令能修。  
**修法：** `signal.signal(signal.SIGTSTP, ...)` 在 finally 之外恢复 termios。

### 11. cc-connect session 文件 schema 版本检查
**来自：** 2026-04-17 simplify review。  
**状态：** `do_import_session` 直接写 `{"id", "name", "agent_session_id", "history"}` 这些字段，假设了当前的格式。cc-connect 改格式的话会默默出 bug。  
**为什么 skip：** 过度工程——cc-connect 还在 1.x 且格式稳定多个版本了。  
**修法：** 读 `cc_data.get("version")`，未知版本报 bail，而不是瞎写。

### 12. 引导等待 daemon 死而复生的检测
**来自：** 2026-04-17 simplify review（bootstrap polling loop）。  
**状态：** `[s]` 引导用户"请在飞书发消息"后循环等文件出现。如果 daemon 在这期间挂了，用户会无止尽按回车。  
**为什么 skip：** 罕见路径。既然 daemon 是 launchd KeepAlive 管着的，挂了会自己拉起。  
**修法：** 循环里每 N 次 glob 失败后 `is_cc_running()` 检查一下，daemon 挂了就告警。

### 13. install.sh 的 `set -euo pipefail`
**来自：** 2026-04-16 simplify review。  
**状态：** 当前只 `set -u`。很多管道失败被静默吃掉（`conda env list | awk | grep`）。  
**为什么 skip：** 需要逐 pipe 审核哪些失败是预期的，风险大于收益。  
**修法：** 开 `set -euo pipefail`，然后对每个允许失败的命令加 `|| true`。

---

## 🟢 锦上添花，有空再看

### 14. `[s]` 入口加一句提示
**来自：** 2026-04-17 讨论。  
**状态：** 用户发现大多数场景 Claude Code 从项目文件就能恢复上下文，不用绑 session。  
**建议加法：** `[s]` 进去先给一句"大多数情况下新开 session 就够了，这个功能适合 X Y Z 场景"。  
**为什么 skip：** 用户说"可加可不加"。

### 15. install.sh 自动更新 check
**状态：** 没做。工具本身有新版本，用户怎么知道？  
**想法：** `cc-manage` 启动时偶尔（每 7 天）静默检查 github release。  
**为什么 skip：** 没明确需求，也不想 phone-home 默认开。

### 16. 语音转文字 / 流式预览 / 速率限制的健康检查
**状态：** `[i] → 2` 体检只看 core 项目。speech 如果配了但 ffmpeg 缺失、whisper key 过期等情况没检测。  
**为什么 skip：** 冷门配置，大多数用户用不到。

---

## 记录格式说明

每次做完一次 /simplify review 或者 session 结束，把"明确讨论但 skip"的项目追加到这里。**不要把随手想到的 idea 加进来**——那些进 MEMORY 的 plant-seed 或者你自己的 notes。这个文档的价值是"我已经审慎评估过，决定不做"，读的时候能立刻判断要不要做。

每次回来捡的时候，把做完的删掉，把决定永久放弃的移到底部"放弃区"（目前没有）。
