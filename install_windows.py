"""Windows user installation: python install_windows.py --codex --no-setup."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


BEGIN = "# >>> agent-bemfa-notify >>>"
END = "# <<< agent-bemfa-notify <<<"
BLOCK = re.compile(r"\n?# >>> agent-bemfa-notify >>>.*?# <<< agent-bemfa-notify <<<\n?", re.S)


def write_backed_up(path, content):
    if path.exists() and path.read_text(encoding="utf-8-sig") == content:
        return
    backup = path.with_name(path.name + ".bak-agent-notify")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-agent-notify")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    print(f"Updated {path}")


def configure_codex(codex_home, script, remove=False):
    hooks_path = codex_home / "hooks.json"
    data = json.loads(hooks_path.read_text(encoding="utf-8-sig")) if hooks_path.exists() else {}
    hooks = data.setdefault("hooks", {})
    for event in list(hooks):
        groups = []
        for group in hooks[event]:
            kept = [handler for handler in group.get("hooks", [])
                    if "agent_notify.py" not in json.dumps(handler)]
            if kept:
                groups.append(dict(group, hooks=kept))
        hooks[event] = groups
        if not hooks[event]:
            del hooks[event]
    if not remove:
        command = subprocess.list2cmdline([sys.executable, "-X", "utf8", str(script), "hook", "codex"])
        for event in ("UserPromptSubmit", "Stop", "PermissionRequest"):
            handler = {"type": "command", "command": command, "timeout": 10}
            if event != "UserPromptSubmit":
                handler["async"] = True
            hooks.setdefault(event, []).append({"hooks": [handler]})
    if not hooks:
        data.pop("hooks", None)

    config_path = codex_home / "config.toml"
    original = config_path.read_text(encoding="utf-8-sig") if config_path.exists() else ""
    text = BLOCK.sub("\n", original)
    if not remove:
        if re.search(r'^\s*\[mcp_servers\.(?:agent-notify|"agent-notify"|\'agent-notify\')\]', text, re.M):
            raise RuntimeError("An unmarked agent-notify MCP configuration already exists; review it first.")
        text = text.rstrip() + "\n\n" + "\n".join([
            BEGIN,
            "[mcp_servers.agent-notify]",
            "command = " + json.dumps(sys.executable, ensure_ascii=False),
            "args = " + json.dumps(["-X", "utf8", str(script), "mcp"], ensure_ascii=False),
            'env = { AGENT_NOTIFY_SOURCE = "codex" }',
            END,
            "",
        ])
    try:
        import tomllib
    except ImportError:
        pass
    else:
        tomllib.loads(text)
    if hooks_path.exists() or not remove:
        write_backed_up(hooks_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    if config_path.exists() or not remove:
        write_backed_up(config_path, text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", action="store_true", help="Register Codex hooks and MCP")
    parser.add_argument("--no-setup", action="store_true", help="Leave UID setup for later")
    parser.add_argument("--uninstall", action="store_true", help="Remove this installation; keep credentials and backups")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Use install.sh on macOS/Linux")
    if sys.version_info < (3, 9):
        parser.error("Python 3.9 or later is required")
    source = Path(__file__).resolve().parent
    user_home = Path.home()
    destination = Path(os.environ.get("AGENT_NOTIFY_HOME", user_home / ".local" / "share" / "agent-bemfa-notify"))
    bin_dir = user_home / ".local" / "bin"
    script = destination / "agent_notify.py"
    codex_home = Path(os.environ.get("CODEX_HOME", user_home / ".codex"))
    launcher = bin_dir / "agent-notify.cmd"
    if args.uninstall:
        if (codex_home / "hooks.json").exists() or (codex_home / "config.toml").exists():
            configure_codex(codex_home, script, remove=True)
        if launcher.exists() and str(script) in launcher.read_text(encoding="utf-8"):
            launcher.unlink()
        # Remove only the files this installer owns, leaving any other files intact.
        for name in ("agent_notify.py", "install_windows.py", "WINDOWS-SETUP.md", "LICENSE"):
            path = destination / name
            if path.exists():
                path.unlink()
        if destination.exists() and not any(destination.iterdir()):
            destination.rmdir()
        print("Removed. Credentials and config backups were kept.")
        return 0
    destination.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("agent_notify.py", "install_windows.py", "WINDOWS-SETUP.md", "LICENSE"):
        src = source / name
        if src.resolve() != (destination / name).resolve():
            shutil.copy2(src, destination / name)
    # cmd reads batch files in the console code page, even when Python uses UTF-8.
    # Switch before the non-ASCII paths and restore the caller's code page on exit.
    launcher.write_text("\n".join([
        "@echo off",
        "setlocal EnableExtensions DisableDelayedExpansion",
        'for /f "tokens=2 delims=:" %%c in (\'chcp\') do set "AGENT_NOTIFY_SAVED_CP=%%c"',
        "chcp 65001 >nul",
        '"' + sys.executable + '" -X utf8 "' + str(script) + '" %*',
        'set "AGENT_NOTIFY_EXIT=%errorlevel%"',
        "if defined AGENT_NOTIFY_SAVED_CP chcp %AGENT_NOTIFY_SAVED_CP% >nul",
        "exit /b %AGENT_NOTIFY_EXIT%",
        "",
    ]), encoding="utf-8")
    print(f"Installed {script}")
    print(f"Command: {launcher}")
    if args.codex:
        configure_codex(codex_home, script)
    if not args.no_setup:
        return subprocess.call([sys.executable, "-X", "utf8", str(script), "setup"])
    print("Next: agent-notify setup; agent-notify test")
    if args.codex:
        print("Review the three new hooks in Codex Settings > Hooks, or /hooks in the CLI.")
        print("Restart Codex to load the MCP server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
