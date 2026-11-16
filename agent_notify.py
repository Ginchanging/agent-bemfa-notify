#!/usr/bin/env python3
"""agent-bemfa-notify: push AI coding-agent status (Claude Code, Codex, ...) to
an MQTT topic on Bemfa cloud (bemfa.com), e.g. for an ESP32 desk display.

Standard library only, Python >= 3.9, macOS and Linux.

  agent_notify.py setup                    store the Bemfa UID and options
  agent_notify.py status                   show the active configuration
  agent_notify.py test                     send a test message
  agent_notify.py send --kind done --msg "..." [--src other] [--project X]
  agent_notify.py hook claude              Claude Code hook (JSON on stdin)
  agent_notify.py hook codex               Codex hook (JSON on stdin)
  agent_notify.py mcp                      stdio MCP server with the notify_board tool

Message published to the topic (<= 256 bytes, Bemfa's limit):
  {"v":1,"s":"claude","k":"done","p":"my-project","h":"my-laptop","m":"summary"}
  s = source (claude | codex | other), k = kind (done | attention | error | info),
  p = project (working directory name), h = device, m = message.

Hooks never block or fail the agent: network timeout 4 s, every error is only
logged to ~/.cache/agent-notify/log, and the process always exits 0.
"""
import argparse
import getpass
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "1.0.0"
BEMFA_URL = "https://apis.bemfa.com/va/postJsonMsg"
KEYCHAIN_SERVICE = "agent-bemfa-notify"
KEYCHAIN_ACCOUNT = "bemfa-uid"
MAX_BYTES = 256
KINDS = ("done", "attention", "error", "info")
ATTENTION_EVERY_S = 120   # at most one "needs you" push per session per 2 min
ERROR_EVERY_S = 300

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "agent-notify"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agent-notify"
LOG_FILE = STATE_DIR / "log"


# ---------------------------------------------------------------- utilities

def log(*parts):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 256 * 1024:
            LOG_FILE.replace(LOG_FILE.with_suffix(".old"))
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(p) for p in parts) + "\n")
    except OSError:
        pass


def load_file_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def keychain_uid():
    if platform.system() != "Darwin":
        return ""
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                            "-a", KEYCHAIN_ACCOUNT, "-w"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def setting(name, default=""):
    """env AGENT_NOTIFY_<NAME> > Claude plugin option > config file > default"""
    for key in (f"AGENT_NOTIFY_{name.upper()}", f"CLAUDE_PLUGIN_OPTION_{name.upper()}"):
        v = os.environ.get(key, "")
        if v and not v.startswith("${"):  # unsubstituted placeholder
            return v
    v = load_file_config().get(name)
    return default if v in (None, "") else v


def config():
    uid = setting("uid") or setting("bemfa_uid") or keychain_uid()
    try:
        min_s = float(setting("min_seconds", 60))
    except ValueError:
        min_s = 60.0
    return {
        "uid": uid,
        "topic": setting("topic", "agentalert"),
        "min_seconds": min_s,
        "device": setting("device_name") or socket.gethostname().split(".")[0][:20],
        "privacy": setting("privacy", "full"),
        "endpoint": setting("endpoint", BEMFA_URL),
        "dry_run": setting("dry_run", "") not in ("", "0", "false", "no"),
    }


def clip_utf8(text, limit):
    b = text.encode("utf-8")
    if len(b) <= limit:
        return text
    b = b[:limit]
    while b and (b[-1] & 0xC0) == 0x80:
        b = b[:-1]
    if b and b[-1] >= 0xC0:  # dangling lead byte
        b = b[:-1]
    return b.decode("utf-8", "ignore").rstrip() + "…"


def summarize(text, limit=200):
    """First meaningful line of an assistant message, without markdown noise."""
    if not text:
        return ""
    lines = []
    for line in str(text).splitlines():
        line = re.sub(r"[`*_>#|]+", "", line).strip(" -•\t")
        lines.append(re.sub(r"\s+", " ", line))
    for min_len in (8, 2):  # skip bare headings like "Done" when a real sentence follows
        for line in lines:
            if len(line) >= min_len:
                return clip_utf8(line, limit)
    return ""


def build_payload(cfg, source, kind, message, project):
    data = {"v": 1, "s": source[:12], "k": kind, "p": (project or "")[:40], "h": cfg["device"], "m": ""}
    if cfg["privacy"] == "minimal":
        message = ""
    base = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    data["m"] = clip_utf8(message or "", max(0, MAX_BYTES - base - 4))
    out = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    while len(out.encode("utf-8")) > MAX_BYTES and data["m"]:  # escapes can push it over
        data["m"] = clip_utf8(data["m"][:-2], len(data["m"].encode("utf-8")) - 8)
        out = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return out


def publish(cfg, payload):
    if cfg["dry_run"]:
        log("dry-run", payload)
        print(payload, file=sys.stderr)
        return True
    if not cfg["uid"]:
        log("no Bemfa UID configured; run: agent_notify.py setup")
        return False
    body = json.dumps({"uid": cfg["uid"], "topic": cfg["topic"], "type": 1, "msg": payload}).encode("utf-8")
    req = urllib.request.Request(cfg["endpoint"], data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8",
                                          "User-Agent": f"agent-bemfa-notify/{VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            reply = json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        log("publish failed:", type(e).__name__, e)
        return False
    if reply.get("code") != 0:
        hint = " (wrong UID, or the topic does not exist in the Bemfa console)" if reply.get("code") == 40004 else ""
        log("Bemfa replied", reply.get("code"), reply.get("message", ""), hint)
        return False
    log("sent", payload)
    return True


def send(source, kind, message, project=""):
    cfg = config()
    return publish(cfg, build_payload(cfg, source, kind, message, project))


# ---------------------------------------------------------------- per-session state

def _state_path(name):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:120]
    return STATE_DIR / safe


def state_get(name):
    try:
        return float(_state_path(name).read_text())
    except (OSError, ValueError):
        return 0.0


def state_set(name, value=None):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _state_path(name).write_text(str(time.time() if value is None else value))
    except OSError:
        pass


def gc_state(max_age=7 * 86400):
    try:
        now = time.time()
        for p in STATE_DIR.glob("*-*"):
            if now - p.stat().st_mtime > max_age:
                p.unlink()
    except OSError:
        pass


def rate_limited(key, every):
    last = state_get(key)
    if time.time() - last < every:
        return True
    state_set(key)
    return False


# ---------------------------------------------------------------- hooks

ATTENTION_TYPES = {"permission_prompt", "agent_needs_input", "elicitation_dialog", "elicitation_url_dialog"}


def project_of(event):
    cwd = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.basename(str(cwd).rstrip("/")) or str(cwd)


def on_turn_start(source, sid):
    state_set(f"start-{source}-{sid}")


def on_turn_stop(source, sid, event, message):
    cfg = config()
    start = state_get(f"start-{source}-{sid}")
    if not start:
        log(source, "stop without a recorded start; skipped")
        return
    took = time.time() - start
    if took < cfg["min_seconds"]:
        return
    if state_get("manual-push") >= start:  # the agent already reported via MCP this turn
        log(source, "stop: MCP push already sent this turn")
        return
    text = summarize(message) or f"done in {int(took // 60)} min {int(took % 60)} s"
    publish(cfg, build_payload(cfg, source, "done", text, project_of(event)))


def hook_claude(event):
    name = event.get("hook_event_name", "")
    sid = str(event.get("session_id", "nosession"))
    if name == "UserPromptSubmit":
        on_turn_start("claude", sid)
    elif name == "Stop":
        on_turn_stop("claude", sid, event, event.get("last_assistant_message", ""))
    elif name == "Notification":
        ntype = event.get("notification_type", "")
        if ntype in ATTENTION_TYPES and not rate_limited(f"attn-claude-{sid}", ATTENTION_EVERY_S):
            send("claude", "attention", event.get("message") or ntype, project_of(event))
    elif name == "StopFailure":
        err = event.get("error", "unknown")
        if not rate_limited(f"err-claude-{err}", ERROR_EVERY_S):
            detail = event.get("error_details") or event.get("last_assistant_message") or ""
            send("claude", "error", f"{err}: {detail}".strip(": "), project_of(event))


def codex_approval_reviewer(event):
    """Resolve this turn's reviewer; permission_mode does not identify auto-review."""
    if "approvals_reviewer" in event:
        return event["approvals_reviewer"]
    transcript = event.get("transcript_path")
    turn_id = event.get("turn_id")
    if not transcript or not turn_id:
        return None
    try:
        # Current Codex hooks omit the reviewer, but the rollout records it in
        # turn_context. Read backwards to avoid parsing a whole long session.
        # Bound the lookup so a missing context cannot hold up the agent.
        deadline = time.monotonic() + 1
        with Path(transcript).open("rb") as f:
            pos = f.seek(0, os.SEEK_END)
            pending = b""
            while pos and time.monotonic() < deadline:
                size = min(pos, 64 * 1024)
                pos -= size
                f.seek(pos)
                lines = (f.read(size) + pending).split(b"\n")
                pending = lines.pop(0) if pos else b""
                for line in reversed(lines):
                    if b'"turn_context"' not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if not isinstance(record, dict) or record.get("type") != "turn_context":
                        continue
                    context = record.get("payload")
                    if isinstance(context, dict) and context.get("turn_id") == turn_id:
                        return context.get("approvals_reviewer")
    except (OSError, TypeError, ValueError):
        pass
    return None


def hook_codex(event):
    name = event.get("hook_event_name", "")
    sid = str(event.get("session_id", "nosession"))
    if name == "UserPromptSubmit":
        on_turn_start("codex", sid)
    elif name == "Stop":
        on_turn_stop("codex", sid, event, event.get("last_assistant_message", ""))
    elif name == "PermissionRequest":
        # This event precedes approval routing, including automatic review.
        # Only a confirmed human reviewer warrants an attention notification.
        if codex_approval_reviewer(event) != "user":
            log("codex", "permission request: no confirmed user reviewer; skipped")
            return
        if not rate_limited(f"attn-codex-{sid}", ATTENTION_EVERY_S):
            tool = event.get("tool_name", "")
            send("codex", "attention", f"needs approval: {tool}".strip(), project_of(event))


def run_hook(agent):
    """Read one hook event from stdin; never raise, never block the agent."""
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        (hook_claude if agent == "claude" else hook_codex)(event)
        gc_state()
        if agent == "codex" and event.get("hook_event_name") == "Stop":
            print("{}")  # Codex expects JSON on stdout from Stop hooks
    except Exception as e:  # noqa: BLE001 - a hook must never break the agent
        log("hook error:", type(e).__name__, e)
    return 0


# ---------------------------------------------------------------- MCP (stdio JSON-RPC 2.0)

MCP_TOOL = {
    "name": "notify_board",
    "description": ("Push a short status message to the user's desk display / phone through Bemfa "
                    "MQTT. Use it when a long task finishes (one-sentence summary of the result), "
                    "when you are blocked and need the user, or when something failed. Keep the "
                    "message under ~60 characters, in the user's language. Don't call it for "
                    "routine progress."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "One short sentence, e.g. 'Firmware built and flashed'"},
            "kind": {"type": "string", "enum": list(KINDS),
                     "description": "done = finished, attention = needs the user, error = failed, info = other"},
        },
        "required": ["message"],
    },
}


def mcp_reply(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run_mcp():
    source = os.environ.get("AGENT_NOTIFY_SOURCE", "other")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method, msg_id = req.get("method", ""), req.get("id")
        if msg_id is None:  # notification (e.g. notifications/initialized)
            continue
        if method == "initialize":
            client_version = (req.get("params") or {}).get("protocolVersion", "2025-06-18")
            mcp_reply(msg_id, {"protocolVersion": client_version,
                               "capabilities": {"tools": {}},
                               "serverInfo": {"name": "agent-bemfa-notify", "version": VERSION}})
        elif method == "ping":
            mcp_reply(msg_id, {})
        elif method == "tools/list":
            mcp_reply(msg_id, {"tools": [MCP_TOOL]})
        elif method == "tools/call":
            params = req.get("params") or {}
            args = params.get("arguments") or {}
            if params.get("name") != "notify_board":
                mcp_reply(msg_id, error={"code": -32602, "message": "unknown tool"})
                continue
            kind = args.get("kind", "info")
            kind = kind if kind in KINDS else "info"
            ok = send(source, kind, str(args.get("message", ""))[:400], os.path.basename(os.getcwd()))
            if ok:
                state_set("manual-push")
            text = "sent" if ok else "not sent (see ~/.cache/agent-notify/log)"
            mcp_reply(msg_id, {"content": [{"type": "text", "text": text}], "isError": not ok})
        else:
            mcp_reply(msg_id, error={"code": -32601, "message": f"method not found: {method}"})
    return 0


# ---------------------------------------------------------------- setup / CLI

def cmd_setup(args):
    cfg = load_file_config()
    print("agent-bemfa-notify setup (press Enter to keep the current value)")
    topic = input(f"Bemfa topic [{cfg.get('topic', 'agentalert')}]: ").strip() or cfg.get("topic", "agentalert")
    mins = input(f"Only report turns longer than N seconds [{cfg.get('min_seconds', 60)}]: ").strip()
    device = input(f"Device name [{cfg.get('device_name', socket.gethostname().split('.')[0])}]: ").strip()
    privacy = input(f"Privacy: full (with summary) or minimal [{cfg.get('privacy', 'full')}]: ").strip()
    cfg["topic"] = topic
    if mins:
        cfg["min_seconds"] = float(mins)
    if device:
        cfg["device_name"] = device
    if privacy in ("full", "minimal"):
        cfg["privacy"] = privacy

    if platform.system() == "Darwin":
        print("The Bemfa private key (UID) goes into the macOS Keychain; macOS asks for it twice.")
        r = subprocess.run(["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE,
                            "-a", KEYCHAIN_ACCOUNT, "-w"])
        if r.returncode != 0:
            print("Keychain update failed.", file=sys.stderr)
        cfg.pop("uid", None)
    else:
        uid = getpass.getpass("Bemfa private key (UID, hidden; Enter keeps the current one): ").strip()
        if uid:
            cfg["uid"] = uid
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG_FILE)
    print(f"Saved {CONFIG_FILE}. Try: {sys.argv[0]} test")
    return 0


def cmd_status(args):
    cfg = config()
    shown = dict(cfg, uid=("set (" + cfg["uid"][:4] + "…)") if cfg["uid"] else "NOT SET")
    print(json.dumps(shown, ensure_ascii=False, indent=2))
    print(f"log: {LOG_FILE}")
    return 0


def cmd_test(args):
    ok = send("other", "info", "agent-bemfa-notify test ✓", "test")
    print("sent" if ok else f"failed, see {LOG_FILE}")
    return 0 if ok else 1


def cmd_send(args):
    ok = send(args.src, args.kind, args.msg, args.project or os.path.basename(os.getcwd()))
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    sub.add_parser("status")
    sub.add_parser("test")
    s = sub.add_parser("send")
    s.add_argument("--kind", choices=KINDS, default="info")
    s.add_argument("--msg", required=True)
    s.add_argument("--src", default="other")
    s.add_argument("--project", default="")
    h = sub.add_parser("hook")
    h.add_argument("agent", choices=("claude", "codex"))
    sub.add_parser("mcp")
    sub.add_parser("version")
    args = p.parse_args()
    if args.cmd == "hook":
        return run_hook(args.agent)
    if args.cmd == "mcp":
        return run_mcp()
    if args.cmd == "version":
        print(VERSION)
        return 0
    return {"setup": cmd_setup, "status": cmd_status, "test": cmd_test, "send": cmd_send}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
