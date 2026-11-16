# agent-bemfa-notify

<img src=".claude-plugin/icon.png" width="96" align="right" alt="Kiki">

AI 编程助手跑完长任务、需要你确认、或者出错时，自动推送一条消息到[巴法云](https://bemfa.com) MQTT 主题。任何订阅了这个主题的设备都能收到，例如桌上的 ESP32 小屏幕、手机，或者另一台电脑。

- **支持的工具**：**Claude Code**（插件形式，内含 hooks、MCP 工具和 skill）和 **Codex CLI**（hooks + MCP）。
- **不需要自己的服务器**：电脑直接通过 HTTPS 调用巴法云接口，设备通过 MQTT 订阅。
- **依赖少**：单个 Python 文件，只用标准库，Python ≥ 3.9，支持 macOS、Linux 和 Windows（Codex）。
- **不会影响 AI 干活**：所有 hook 都在后台运行，网络超时 4 秒，出错只写日志。

```
Claude Code hooks ─┐
Codex hooks ───────┼─► agent_notify.py ──HTTPS──► 巴法云 (topic agentalert) ──MQTT──► 你的设备
MCP: notify_board ─┘
```

English summary is [below](#english).

## 推送什么

| 情况 | Claude Code | Codex | 消息里的 `k` |
|---|---|---|---|
| 一轮任务完成，且耗时超过阈值（默认 60 秒） | `Stop` | `Stop` | `done` |
| 需要你批准或输入 | `Notification`（权限请求、等待输入、MCP 表单） | `PermissionRequest`（确认由用户审批时） | `attention` |
| 因 API 错误中断（限流、过载、认证等） | `StopFailure` | — | `error` |
| AI 主动用 MCP 工具 `notify_board` 发的总结 | ✓ | ✓ | 由 AI 指定 |

几点说明：

- **短问答不推送**，只有耗时超过阈值的轮次才推。
- **需要确认**：同一会话 2 分钟内最多推一次。
- **Codex 自动审批不推送**：`PermissionRequest` 在 auto-review 前也会触发。hook 优先读取事件的 `approvals_reviewer`；当前 Codex 未提供该字段时，读取 `transcript_path` 中与当前 `turn_id` 匹配的 `turn_context.approvals_reviewer`。只有明确为 `user` 才推送；无法确认时跳过，不占用提醒限流额度。读取会话记录最多耗时 1 秒，且不会发送其中的对话内容。
- **摘要**：取 AI 最后一条回复里第一句有内容的话。
- **不重复**：如果 AI 在这一轮已经用 `notify_board` 发过总结，通用的“完成”消息就不再发。

## 1. 准备巴法云

1. 在 [bemfa.com](https://bemfa.com) 注册，进入控制台 → **MQTT 设备云**，新建主题 `agentalert`（名字可以改，但两边要一致）。
2. 控制台首页的**私钥**就是 UID。它只存在你的电脑上：系统钥匙串或 `~/.config/agent-notify/config.json`（权限 600）。

## 2. 安装

### Claude Code（推荐用插件）

```text
/plugin marketplace add Ginchanging/agent-bemfa-notify
/plugin install agent-bemfa-notify@agent-bemfa-notify
```

启用插件时，Claude Code 会让你填以下几项：

- 巴法云 UID：作为敏感项保存在系统钥匙串里。
- 主题名
- 时长阈值
- 设备名
- 是否带摘要

之后可以在 `/config` 里修改。

也可以不用插件，改用安装脚本：`./install.sh --claude-settings`。它会把 hooks 写进 `~/.claude/settings.json`，并用 `claude mcp add` 注册 MCP 服务。

### Codex CLI（macOS / Linux）

```sh
git clone https://github.com/Ginchanging/agent-bemfa-notify
cd agent-bemfa-notify
./install.sh --codex        # 安装命令 agent-notify、写入 ~/.codex/hooks.json 和 MCP 配置，并引导你填 UID
```

装完后，在 codex 里运行一次 `/hooks`，审核并信任这三个新 hook。Codex 不会运行未经信任的 hook。

脚本做的事情：

- 只**合并**自己的条目，不会覆盖你已有的 hooks，也不会改动你原有的 `notify` 设置。
- 第一次修改前，会把原文件备份为 `*.bak-agent-notify`。
- 卸载命令：`./install.sh --uninstall`。

### Codex（Windows）

在 PowerShell 中运行（需要 Python ≥ 3.9）：

```powershell
git clone https://github.com/Ginchanging/agent-bemfa-notify
cd agent-bemfa-notify
python -X utf8 install_windows.py --codex
& "$env:USERPROFILE\.local\bin\agent-notify.cmd" test
```

安装器会在本机隐藏输入 UID，合并 Codex hooks 和 MCP 配置，并备份现有配置。
安装后重启 Codex，在“设置 > Hooks”或 CLI 的 `/hooks` 中审核并信任三个新增 hook。
默认任务阈值为 60 秒；支持后台推送、中文消息、带空格及中文的安装路径。

可加 `--no-setup` 暂时跳过 UID 配置；卸载和路径说明见 [Windows 安装说明](WINDOWS-SETUP.md)。

### 其他电脑、Linux 服务器

步骤相同：`git clone` 之后运行 `./install.sh`，加上 `--codex` 或 `--claude-settings`。Linux 上 UID 保存在 `~/.config/agent-notify/config.json`（权限 600）。

## 3. 测试

```sh
agent-notify test       # 发送一条测试消息
agent-notify status     # 查看当前配置（UID 只显示前 4 位）
agent-notify send --kind done --msg "手动发一条"
```

日志在 `~/.cache/agent-notify/log`。如果巴法云返回 `40004`，说明 UID 填错了，或者控制台里还没建这个主题。

## 消息格式（自己做接收端时用）

发布到主题的消息是一行 JSON，长度不超过 256 字节（巴法云单条消息的上限）：

```json
{"v":1,"s":"claude","k":"done","p":"ESPdev","h":"liu-mbp","m":"Firmware built and flashed."}
```

| 字段 | 含义 |
|---|---|
| `v` | 格式版本，目前是 `1` |
| `s` | 来源：`claude`、`codex`、`other` |
| `k` | 类型：`done` 完成、`attention` 需要你、`error` 出错、`info` 其他 |
| `p` | 项目名，即工作目录名 |
| `h` | 设备名 |
| `m` | 摘要，按 UTF-8 截断；隐私模式 `minimal` 时为空 |

订阅方式：连接 `bemfa.com`，客户端 ID 填你的 UID，订阅主题 `agentalert`。端口 9503 走 TLS（推荐），9501 是明文。

```sh
mosquitto_sub -h bemfa.com -p 9503 --capath /etc/ssl/certs -i "$BEMFA_UID" -t agentalert
```

注意：巴法云在你重新订阅时，可能会重发该主题的最后一条消息，接收端最好按内容去重。完整的 ESP32-S3 接收端示例见作者的 [ESPdev 项目](https://github.com/Ginchanging)。屏幕上的像素猫 Kiki 会针对不同类型做出反应：跳起来、竖耳朵、炸毛。

## 配置

| 插件选项 / 环境变量 | 配置文件键 | 默认值 |
|---|---|---|
| `bemfa_uid` / `AGENT_NOTIFY_BEMFA_UID` | `uid`（macOS 上存钥匙串） | — |
| `topic` / `AGENT_NOTIFY_TOPIC` | `topic` | `agentalert` |
| `min_seconds` / `AGENT_NOTIFY_MIN_SECONDS` | `min_seconds` | `60` |
| `device_name` / `AGENT_NOTIFY_DEVICE_NAME` | `device_name` | 主机名 |
| `privacy` / `AGENT_NOTIFY_PRIVACY` | `privacy` | `full`（`minimal` 表示不带摘要） |
| `AGENT_NOTIFY_DRY_RUN=1` | `dry_run` | 只打印消息，不发送 |

优先级：环境变量 > 插件选项 > `~/.config/agent-notify/config.json` > macOS 钥匙串。

## 隐私

- **UID**：只存在本机，不会写进仓库，也不会出现在日志里。
- **摘要**：开启时，AI 回复的第一句会经过巴法云的服务器。介意的话，把 `privacy` 设为 `minimal`。
- **MCP 工具说明**：要求 AI 不在消息里放任何密钥或个人信息。

## 开发

```sh
python3 -m unittest discover -s tests -v   # 用本地模拟的巴法云接口做端到端测试
claude plugin validate .
```

## License

MIT

---

## English

**agent-bemfa-notify** pushes AI coding-agent status to a [Bemfa](https://bemfa.com) MQTT topic, so a desk display (e.g. an ESP32), a phone or anything else subscribed can show it. No server of your own is involved.

**What it pushes**

- **Long turn finished** (`done`): only turns longer than `min_seconds`, 60 by default.
- **Needs you** (`attention`): Claude Code permission or input requests; Codex `PermissionRequest` only when the event or its matching transcript turn confirms `approvals_reviewer: user`. Auto-reviewed and unknown requests are skipped without consuming the notification cooldown. Transcript lookup is limited to one second and never publishes conversation content.
- **API error** (`error`): Claude Code `StopFailure`.
- **Agent summary**: the agent can send its own one-line summary with the MCP tool `notify_board`. A turn that used it doesn't also get the generic "done" push.

**Install**

1. Create the topic `agentalert` in the Bemfa console and copy your private key (UID).
2. Install for your tool:
   - **Claude Code:**
     ```text
     /plugin marketplace add Ginchanging/agent-bemfa-notify
     /plugin install agent-bemfa-notify@agent-bemfa-notify
     ```
     The UID is requested as a sensitive option and kept in the system keychain.
   - **Codex (macOS/Linux):** run `./install.sh --codex`, then run `/hooks` once in codex to trust the new hooks.
   - **Codex (Windows):** run `python -X utf8 install_windows.py --codex` in PowerShell. Restart Codex and review the three hooks in Settings > Hooks or `/hooks`. See the [Windows setup guide](WINDOWS-SETUP.md) for paths, deferred setup, and uninstall.
   - **Claude Code without plugins:** run `./install.sh --claude-settings`.
3. Test with `agent-notify test`. Logs go to `~/.cache/agent-notify/log`.

**Message format**

One line of JSON, at most 256 bytes:

```json
{"v":1,"s":"claude|codex|other","k":"done|attention|error|info","p":"project","h":"device","m":"summary"}
```

**Safety**: hooks run in the background, time out after 4 s and always exit 0, so they never block or break the agent. The installer only adds or removes its own entries and backs up every file it edits.

MIT licensed.
