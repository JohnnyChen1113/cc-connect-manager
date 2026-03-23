# cc-connect-manager Design Spec

## Problem

The existing `~/.cc-connect/manage.sh` (590-line bash script) has several issues:
- Confusing menu (unclear difference between "add" and "quick add", manual "generate config" step)
- Fragile hand-rolled TOML parser
- `source`-based .env loading is a security risk
- Intermediate `projects.d/*.env` layer adds complexity without value
- No input validation
- Hard to extend for new platforms

## Solution

Single-file Python script (`manage.py`) using `tomlkit` to directly read/write `~/.cc-connect/config.toml` as the sole data source. No intermediate formats.

## Data Source

`~/.cc-connect/config.toml` is the single source of truth. `tomlkit` preserves comments and formatting. The `projects.d/` directory is not used.

## Menu

Main screen shows project list + action keys in one view:

```
cc-connect 配置管理

  当前项目:
  #  名称              平台    工作目录
  1  my-project        feishu  /Users/johnny
  2  sci-humanizer     feishu  /Users/johnny/sci_humanizer

  [a] 添加项目  [e] 编辑  [d] 删除  [q] 退出
  >
```

No separate "view list" or "generate config" steps.

## Flows

### Add Project
1. Project name (validate: non-empty, unique)
2. Work directory (validate: exists, default cwd)
3. Platform selection (currently only feishu, auto-selected)
4. Feishu App ID (validate: starts with `cli_`)
5. Feishu App Secret (hidden input, validate: non-empty)
6. Preview summary -> confirm -> validate -> write config.toml

### Edit Project
1. Select project by number
2. Show current values, ask per field (Enter to keep)
3. Option to skip credentials
4. Preview changes -> confirm -> validate -> write

### Delete Project
1. Select project by number
2. Confirm -> write

## Validation (pre-write)

- Project names unique
- Work directory is valid path
- Feishu App ID starts with `cli_`
- App Secret non-empty
- On failure: show specific errors, do not write

## Auto-save

Every mutation (add/edit/delete) automatically validates and writes config.toml. Backup to `config.toml.bak` before each write.

## Platform Extensibility

Platform-specific logic (fields, validation, display) is defined in a dictionary mapping. Adding a new platform means adding one dict entry — no structural changes needed. Only feishu is implemented initially.

## File Structure

```
cc-connect-manager/
├── manage.py          # Single-file script
└── requirements.txt   # tomlkit
```

## Dependencies

- Python 3.11+
- tomlkit (pip install)

## Non-goals

- Telegram/Discord support (deferred)
- Daemon management (out of scope)
- GUI (terminal only)
