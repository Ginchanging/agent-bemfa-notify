# Windows 安装说明

Windows 安装器支持 Codex 的 hooks 和 MCP，无需 WSL、Bash 或额外 Python 包。
需要 Python ≥ 3.9；在 PowerShell 中运行：

```powershell
git clone https://github.com/Ginchanging/agent-bemfa-notify
cd agent-bemfa-notify
python -X utf8 install_windows.py --codex
& "$env:USERPROFILE\.local\bin\agent-notify.cmd" test
& "$env:USERPROFILE\.local\bin\agent-notify.cmd" status
```

安装器自动运行 `setup`，在本机终端隐藏输入巴法云 UID。请先在巴法云 MQTT 设备云创建主题
`agentalert`。默认任务阈值为 60 秒，privacy 为 full（包含最后回复的摘要）；
可在 setup 中改为 minimal。

安装位置：`%USERPROFILE%\.local\share\agent-bemfa-notify`。
命令入口：`%USERPROFILE%\.local\bin\agent-notify.cmd`。
配置位置：`%USERPROFILE%\.config\agent-notify\config.json`。
日志位置：`%USERPROFILE%\.cache\agent-notify\log`。

安装后重启 Codex，在“设置 > Hooks”中审核并信任 UserPromptSubmit、Stop 和
PermissionRequest 三个新增 hook；CLI 中可使用 `/hooks`。
首次修改现有 Codex 配置前自动创建 `.bak-agent-notify` 备份。
启动器和 MCP 使用明确的 Python 路径及 UTF-8 模式，支持中文路径和消息。
发送通知的 Stop、PermissionRequest
在后台运行，UserPromptSubmit 同步记录开始时间。

如果 `%USERPROFILE%\.local\bin` 已在 PATH 中，可直接使用 `agent-notify`。
安装器不修改 PATH；否则使用上面的完整命令路径。

安装时加 `--no-setup` 可稍后配置 UID；重新配置时运行：

```powershell
& "$env:USERPROFILE\.local\bin\agent-notify.cmd" setup
```

卸载（保留 UID、日志和备份）：

```powershell
python -X utf8 "$env:USERPROFILE\.local\share\agent-bemfa-notify\install_windows.py" --uninstall
```

与 Unix 安装器一致，`AGENT_NOTIFY_HOME` 可自定义安装目录；`CODEX_HOME`、
`XDG_CONFIG_HOME` 和 `XDG_CACHE_HOME` 分别指定 Codex、通知配置及日志目录。
自定义安装目录时，从该目录运行 `install_windows.py --uninstall`，并保留相同的环境变量。
