# 傻瓜安装 & 模型/服务商切换 设计

## 背景

cc-connect-manager 当前存在两个体验问题：

1. **安装门槛高** — 用户必须先 clone 仓库、装 Python 3.10+、`pip install tomlkit`、才能跑 `./manage.py`。`[i]` 菜单的 install wizard 只覆盖了 `npm install -g cc-connect` 和 launchd 注册这段，不是端到端。
2. **不能切模型** — `build_project_table()` 虽然支持写 `agent.options.model` 字段，但添加/编辑流程里没有任何地方问过 model，第三方 API（GLM/Kimi/DeepSeek 等）更是完全没入口。

## 目标

- 一行 `curl | bash` 命令完成全链路安装，优先复用现有 Node/npm，按需回退到 conda/brew。
- 主菜单新增 `[m] 模型/服务商`，两级切换：Claude 官方 / 第三方 API。

## 设计

### 一、install.sh（bootstrap）

职责：从"什么都没有"到"主菜单"。

```
┌─ 1. 扫描现有 Node/npm ─┐
│   - `which -a node`     │
│   - brew prefix bin     │
│   - miniforge envs/*    │
│   - nvm versions/*      │
└────────────┬────────────┘
             │
     ┌───────┴───────┐
     │               │
   找到 ≥1           没找到
     │               │
  ≥2 让选          检测 conda
     │               │
     │       ┌───────┴───────┐
     │    有 conda       没 conda
     │       │               │
     │   conda create     有 brew → brew install node
     │   -n cc-connect    没 brew → 提示安装 → 退出
     │   nodejs
     └───────┬───────┘
             │
   ┌─────────┴─────────┐
   │  选定 env 后统一走  │
   └─────────┬─────────┘
             │
       pip install tomlkit
             │
       npm install -g cc-connect
             │
       curl manage.py → ~/.cc-connect-manager/
             │
       写 shell rc 别名 cc-manage
             │
       exec python manage.py → do_install()
             │
           主菜单
```

**关键规则：**
- 先看 node，再谈 conda。不假设用户喜不喜欢 conda。
- 多 Node 时列菜单（显示来源 `conda env nodejs` / `brew` / `nvm v20` / `system`），让用户选。
- 选定 Node 所在 env 后，`pip install tomlkit` 也装在同一 env 里，保证 manage.py 能用 tomlkit。
- 别名优先写 `~/.zshrc` 或 `~/.bashrc`（按 `$SHELL` 判断），别名指向 env 里的 python + manage.py 绝对路径。
- 若检测到已安装（cc-connect 和 manage.py 都在），脚本变成"更新"模式。

### 二、manage.py 模型/服务商菜单

#### Dashboard 新增列

```
  #   名称         平台    工作目录            模型           Session ID
  1   LLM_wiki    feishu  /.../LLM_wiki      sonnet         2b2a987b-...
  2   TSSHubBot   feishu  /.../TSSHub        glm (智谱)      68555c0f-...
  3   my-project  feishu  /Users/johnny      —(默认)         —
```

列内容逻辑：
- `agent.options.model` 存在且有值 → 显示该值
- `cc-connect provider list` 里该项目有 provider → 显示 provider 名（如 `glm (智谱)`）
- 两者都没有 → 显示 `—(默认)`

#### `[m]` 菜单流程

```
1. 选项目
2. 展示当前状态：
   当前模型:   sonnet (Claude 官方)
   当前服务商: Claude 官方
   API Key:   (内置)

3. 操作：
   1) 切换 Claude 官方模型
   2) 切换到第三方 API
   3) 恢复官方默认 (清除 model 和 provider)
   4) 返回
```

**分支 1 · Claude 官方模型：**
```
1) sonnet
2) opus
3) haiku
4) 自定义版本 ID (如 claude-sonnet-4-5)
```
→ 写 `agent.options.model` 到 config.toml，若有 provider 则先删除 → 重启 daemon

**分支 2 · 第三方 API：**
```
1) 智谱 GLM
2) 月之暗面 Kimi
3) DeepSeek
4) 通义千问 Qwen
5) 硅基流动 SiliconFlow
6) 自定义
```

选中后引导：
```
Provider 名称 [glm]:              ← 自动生成，可改
API Key:                          ← 隐藏输入
Base URL [<预设>]:                ← 回车用预设
Model [<建议默认>]:                ← 回车用建议，或输入最新版本号
```

提示词"建议默认"仅作为参考，不硬编码为权威版本（模型版本会随时间变化）。

→ `cc-connect provider add --project X --name <n> --api-key ... --base-url ... --model ...`
→ 同时清除 `agent.options.model`（避免冲突）
→ 重启 daemon

**分支 3 · 恢复默认：**
- 删除 `agent.options.model`
- `cc-connect provider remove --project X --name <所有 provider>`
- 重启 daemon

#### 第三方预设表

```python
PROVIDER_PRESETS = [
    # (display_label, preset_name, base_url, suggested_model)
    ("智谱 GLM",          "glm",         "https://open.bigmodel.cn/api/anthropic",                         "glm-4.6"),
    ("月之暗面 Kimi",      "kimi",        "https://api.moonshot.cn/anthropic",                              "kimi-k2-0905-preview"),
    ("DeepSeek",          "deepseek",    "https://api.deepseek.com/anthropic",                             "deepseek-chat"),
    ("通义千问 Qwen",      "qwen",        "https://dashscope.aliyuncs.com/api/v2/apps/claude-code-proxy/v1","qwen3-coder-plus"),
    ("硅基流动 SiliconFlow","siliconflow", "https://api.siliconflow.cn/anthropic",                          ""),  # 让用户选具体模型
    ("自定义",             "custom",      "",                                                               ""),
]
```

`suggested_model` 只是提示默认值，用户可在 prompt 里覆盖。空字符串表示强制用户输入（SiliconFlow 因为支持多家模型）。

### 三、改动范围

#### 新增文件
- `install.sh` — bootstrap 脚本（repo 根）

#### 修改文件
- `manage.py`：
  - `PROVIDER_PRESETS` 常量
  - `do_model()` — 入口
  - `list_providers(project)` — 调用 `cc-connect provider list` 解析输出
  - `add_provider(project, preset_or_custom)` — 调用 `cc-connect provider add`
  - `remove_provider(project, name)` — 调用 `cc-connect provider remove`
  - `show_dashboard()` — 加模型/服务商列
  - `main()` — 菜单加 `[m]`

#### 删除
- 无。

### 四、边界与注意事项

1. **API Key 存储位置**：由 `cc-connect provider add` 自行持久化到 cc-connect 的 provider 存储（SQLite `cc-switch.db` 或类似）。manage.py 不另存 key。
2. **重启冲突**：切换 provider 和切换 model 都要重启 daemon。连续操作时应累积一次重启，而不是每次都重启。
3. **config.toml vs provider 优先级**：cc-connect 运行时 provider 优先级高于 `agent.options.model`。切换到第三方时同时清 model 字段，避免混淆。
4. **网络检查**：install.sh 不做 ping。依赖 `curl`/`npm`/`conda` 本身报错。
5. **权限**：install.sh 不用 sudo；全局 npm 失败时提示用户用 sudo 或切 conda 环境。
6. **幂等**：install.sh 可重复运行，已安装则走"更新"路径。

## 依赖

- Python 3.10+（`match/case`）
- tomlkit（TOML 读写）
- cc-connect ≥ 1.2.1（`provider` 子命令）
- curl / bash / shell rc 文件可写

## 不做的事

- 不做 GUI — 保持 TUI 风格
- 不做 Linux 版 install.sh 路径（`systemd` 分支另做，现阶段只 macOS）
- 不改现有平台凭证收集流程
- 不自动升级 cc-connect（`[i]` 菜单已覆盖）
