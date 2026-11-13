#!/usr/bin/env bash
# agent-bemfa-notify installer (macOS / Linux). Needs python3 >= 3.9.
#
#   ./install.sh                  install the CLI to ~/.local/share/agent-bemfa-notify and run setup
#   ./install.sh --codex          + Codex: hooks in ~/.codex/hooks.json, MCP server in ~/.codex/config.toml
#   ./install.sh --claude-settings + Claude Code without the plugin system: hooks in ~/.claude/settings.json
#                                   and `claude mcp add` (prefer the plugin, see README)
#   ./install.sh --uninstall      remove everything this script added (backups are kept)
#
# Existing files are backed up as <file>.bak-agent-notify before the first change.
# Only entries that mention agent_notify.py are added or removed.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${AGENT_NOTIFY_HOME:-$HOME/.local/share/agent-bemfa-notify}"
BIN="$HOME/.local/bin"
SCRIPT="$DEST/agent_notify.py"

want_codex=0; want_claude=0; uninstall=0; skip_setup=0
for a in "$@"; do
  case "$a" in
    --codex) want_codex=1 ;;
    --claude-settings) want_claude=1 ;;
    --uninstall) uninstall=1 ;;
    --no-setup) skip_setup=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || { echo "python3 >= 3.9 is required" >&2; exit 1; }

edit_configs() { # mode: add|remove, codex 0/1, claude 0/1
  python3 - "$1" "$2" "$3" "$SCRIPT" <<'PY'
import json, os, re, shutil, sys
from pathlib import Path
mode, codex, claude, script = sys.argv[1], sys.argv[2] == "1", sys.argv[3] == "1", sys.argv[4]
home = Path.home()

def backup(p):
    b = p.with_name(p.name + ".bak-agent-notify")
    if p.exists() and not b.exists():
        shutil.copy2(p, b)

def ours(h):
    return "agent_notify.py" in json.dumps(h)

def merge_hooks(path, events, wrap):
    data = {}
    if path.exists():
        data = json.loads(path.read_text() or "{}")
    hooks = data.setdefault("hooks", {})
    for ev in list(hooks):
        hooks[ev] = [g for g in hooks[ev] if not ours(g)]
        if not hooks[ev]:
            del hooks[ev]
    if mode == "add":
        for ev, matcher in events:
            group = {"hooks": [wrap()]}
            if matcher:
                group["matcher"] = matcher
            hooks.setdefault(ev, []).append(group)
    if not hooks:
        data.pop("hooks", None)
    backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(("updated " if mode == "add" else "cleaned ") + str(path))

if codex:
    cmd = f'python3 "{script}" hook codex'
    merge_hooks(home / ".codex" / "hooks.json",
                [("UserPromptSubmit", None), ("Stop", None), ("PermissionRequest", None)],
                lambda: {"type": "command", "command": cmd, "timeout": 10})
    cfg = home / ".codex" / "config.toml"
    text = cfg.read_text() if cfg.exists() else ""
    block = re.compile(r"\n?# >>> agent-bemfa-notify >>>.*?# <<< agent-bemfa-notify <<<\n?", re.S)
    text = block.sub("\n", text).rstrip() + "\n"
    if mode == "add":
        text += ("\n# >>> agent-bemfa-notify >>>\n"
                 "[mcp_servers.agent-notify]\n"
                 'command = "python3"\n'
                 f'args = ["{script}", "mcp"]\n'
                 'env = { AGENT_NOTIFY_SOURCE = "codex" }\n'
                 "# <<< agent-bemfa-notify <<<\n")
    backup(cfg)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(text)
    print(("updated " if mode == "add" else "cleaned ") + str(cfg))

if claude:
    merge_hooks(home / ".claude" / "settings.json",
                [("UserPromptSubmit", None), ("Stop", None),
                 ("Notification", "permission_prompt|agent_needs_input|elicitation_dialog|elicitation_url_dialog"),
                 ("StopFailure", None)],
                lambda: {"type": "command", "command": "python3", "args": [script, "hook", "claude"], "timeout": 10})
PY
}

if [ "$uninstall" = 1 ]; then
  edit_configs remove 1 1
  command -v claude >/dev/null && claude mcp remove --scope user agent-notify >/dev/null 2>&1 || true
  rm -f "$BIN/agent-notify"
  rm -rf "$DEST"
  echo "Removed. Your Bemfa UID stays in the Keychain / ~/.config/agent-notify until you delete it:"
  echo "  security delete-generic-password -s agent-bemfa-notify -a bemfa-uid   (macOS)"
  echo "  rm -rf ~/.config/agent-notify ~/.cache/agent-notify"
  exit 0
fi

mkdir -p "$DEST" "$BIN"
install -m 755 "$SRC/agent_notify.py" "$SCRIPT"
ln -sf "$SCRIPT" "$BIN/agent-notify"
echo "installed $SCRIPT (command: agent-notify)"

if [ "$want_codex" = 1 ] || [ "$want_claude" = 1 ]; then
  edit_configs add "$want_codex" "$want_claude"
fi
if [ "$want_claude" = 1 ] && command -v claude >/dev/null; then
  claude mcp remove --scope user agent-notify >/dev/null 2>&1 || true
  claude mcp add --scope user -e AGENT_NOTIFY_SOURCE=claude agent-notify -- python3 "$SCRIPT" mcp >/dev/null \
    && echo "registered MCP server agent-notify for Claude Code (user scope)"
fi

if [ "$skip_setup" = 0 ] && ! python3 "$SCRIPT" status 2>/dev/null | grep -q '"uid": "set'; then
  python3 "$SCRIPT" setup
fi
echo
echo "Done. Test with: agent-notify test"
[ "$want_codex" = 1 ] && echo "Codex: open codex and run /hooks once to review and trust the new hooks."
exit 0
