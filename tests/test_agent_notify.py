"""End-to-end tests: run agent_notify.py as a subprocess in an isolated HOME.

    python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent / "agent_notify.py")


class Bemfa(BaseHTTPRequestHandler):
    received = []
    reply = {"code": 0, "message": "OK"}

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Bemfa.received.append(json.loads(body))
        out = json.dumps(Bemfa.reply).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class AgentNotifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Bemfa)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        Bemfa.received.clear()
        Bemfa.reply = {"code": 0, "message": "OK"}
        self.env = {
            "PATH": os.environ["PATH"], "HOME": self.tmp.name,
            "USERPROFILE": self.tmp.name, "PYTHONUTF8": "1",
            **({"SYSTEMROOT": os.environ["SYSTEMROOT"]} if "SYSTEMROOT" in os.environ else {}),
            "XDG_CONFIG_HOME": self.tmp.name + "/cfg", "XDG_CACHE_HOME": self.tmp.name + "/cache",
            "AGENT_NOTIFY_UID": "test-uid", "AGENT_NOTIFY_TOPIC": "agentalert",
            "AGENT_NOTIFY_DEVICE_NAME": "testbox", "AGENT_NOTIFY_MIN_SECONDS": "60",
            "AGENT_NOTIFY_ENDPOINT": f"http://127.0.0.1:{self.server.server_port}/va/postJsonMsg",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, stdin="", env=None):
        return subprocess.run([sys.executable, SCRIPT, *args], input=stdin, capture_output=True,
                              text=True, encoding="utf-8", env=dict(self.env, **(env or {})), timeout=20)

    def hook(self, agent, event, env=None):
        return self.run_cli("hook", agent, stdin=json.dumps(event), env=env)

    def start_file(self, src, sid):
        return Path(self.tmp.name) / "cache" / "agent-notify" / f"start-{src}-{sid}"

    def payloads(self):
        return [json.loads(r["msg"]) for r in Bemfa.received]

    # ------------------------------------------------------------ claude
    def test_long_claude_turn_is_reported_with_summary(self):
        ev = {"session_id": "s1", "cwd": "/work/ESPdev", "hook_event_name": "UserPromptSubmit", "prompt": "x"}
        r = self.hook("claude", ev)
        self.assertEqual(r.stdout, "")  # UserPromptSubmit stdout would become context
        f = self.start_file("claude", "s1")
        f.write_text(str(time.time() - 120))  # pretend the turn started 2 min ago
        self.hook("claude", {"session_id": "s1", "cwd": "/work/ESPdev", "hook_event_name": "Stop",
                             "last_assistant_message": "## Done\n**Firmware** built and `flashed`."})
        (p,) = self.payloads()
        self.assertEqual((p["s"], p["k"], p["p"], p["h"]), ("claude", "done", "ESPdev", "testbox"))
        self.assertEqual(p["m"], "Firmware built and flashed.")
        self.assertEqual(Bemfa.received[0]["uid"], "test-uid")
        self.assertEqual(Bemfa.received[0]["type"], 1)

    def test_short_turn_is_not_reported(self):
        self.hook("claude", {"session_id": "s2", "hook_event_name": "UserPromptSubmit"})
        self.hook("claude", {"session_id": "s2", "hook_event_name": "Stop", "last_assistant_message": "hi"})
        self.assertEqual(Bemfa.received, [])

    def test_permission_prompt_is_attention_and_rate_limited(self):
        ev = {"session_id": "s3", "cwd": "/w/p", "hook_event_name": "Notification",
              "notification_type": "permission_prompt", "message": "Claude needs your permission to use Bash"}
        self.hook("claude", ev)
        self.hook("claude", ev)
        (p,) = self.payloads()
        self.assertEqual(p["k"], "attention")
        self.assertIn("permission", p["m"])

    def test_idle_prompt_is_ignored(self):
        self.hook("claude", {"session_id": "s4", "hook_event_name": "Notification", "notification_type": "idle_prompt"})
        self.assertEqual(Bemfa.received, [])

    def test_stop_failure_is_error(self):
        self.hook("claude", {"session_id": "s5", "hook_event_name": "StopFailure", "error": "rate_limit",
                             "error_details": "429 Too Many Requests"})
        (p,) = self.payloads()
        self.assertEqual(p["k"], "error")
        self.assertTrue(p["m"].startswith("rate_limit: 429"))

    # ------------------------------------------------------------ codex
    def test_codex_stop_prints_json_and_reports(self):
        self.hook("codex", {"session_id": "c1", "turn_id": "t", "hook_event_name": "UserPromptSubmit", "prompt": "x"})
        self.start_file("codex", "c1").write_text(str(time.time() - 300))
        r = self.hook("codex", {"session_id": "c1", "cwd": "/w/ISBFweb", "hook_event_name": "Stop",
                                "last_assistant_message": "所有测试通过，已提交 PR"})
        self.assertEqual(r.stdout.strip(), "{}")
        (p,) = self.payloads()
        self.assertEqual((p["s"], p["k"], p["p"], p["m"]), ("codex", "done", "ISBFweb", "所有测试通过，已提交 PR"))

    def codex_permission(self, **fields):
        return self.hook("codex", dict(session_id="c2", turn_id="t2", cwd="/w/ESPdev",
                                       hook_event_name="PermissionRequest", tool_name="Bash",
                                       permission_mode="default", **fields))

    def transcript(self, *contexts, suffix=""):
        p = Path(self.tmp.name) / "rollout.jsonl"
        p.write_text("\n".join(json.dumps({"type": "turn_context", "payload": c}) for c in contexts)
                     + "\n" + suffix, encoding="utf-8")
        return str(p)

    def test_codex_user_permission_request_is_rate_limited(self):
        r = self.codex_permission(approvals_reviewer="user")
        self.codex_permission(approvals_reviewer="user")
        self.assertEqual(r.stdout, "")  # don't decide: let the normal approval prompt run
        self.assertEqual(r.returncode, 0)
        (p,) = self.payloads()
        self.assertEqual((p["s"], p["k"], p["p"]), ("codex", "attention", "ESPdev"))

    def test_codex_auto_review_and_unknown_requests_are_ignored(self):
        for reviewer in ("auto_review", "guardian_subagent", "unknown", "", None):
            with self.subTest(reviewer=reviewer):
                r = self.codex_permission(approvals_reviewer=reviewer)
                self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.codex_permission()  # old hook payload: no reviewer or transcript
        self.assertEqual(Bemfa.received, [])
        self.assertFalse((Path(self.tmp.name) / "cache/agent-notify/attn-codex-c2").exists())

    def test_codex_transcript_auto_review_does_not_mute_later_user_request(self):
        path = self.transcript({"turn_id": "t1", "approvals_reviewer": "user"},
                               {"turn_id": "t2", "approvals_reviewer": "auto_review"})
        self.codex_permission(transcript_path=path)
        self.assertEqual(Bemfa.received, [])
        path = self.transcript({"turn_id": "t2", "approvals_reviewer": "user"})
        self.codex_permission(transcript_path=path)
        (p,) = self.payloads()
        self.assertEqual((p["s"], p["k"]), ("codex", "attention"))

    def test_codex_transcript_matches_current_turn_and_ignores_other_content(self):
        secret = "private conversation must stay on this computer"
        suffix = json.dumps({"type": "response_item", "payload": {
            "content": secret, "turn_id": "t2", "approvals_reviewer": "auto_review"}}) + "\n"
        # Force the lookup to cross read-block boundaries and a malformed tail.
        suffix += json.dumps({"type": "response_item", "payload": "x" * (128 * 1024)}) + "\n"
        suffix += 'not JSON "turn_context"\n{"type":"turn_context","payload":'
        path = self.transcript({"turn_id": "t2", "approvals_reviewer": "user"},
                               {"turn_id": "other-turn", "approvals_reviewer": "auto_review"},
                               suffix=suffix)
        self.codex_permission(transcript_path=path)
        (p,) = self.payloads()
        self.assertEqual(p["k"], "attention")
        self.assertNotIn(secret, json.dumps(Bemfa.received))
        self.assertNotIn(secret, (Path(self.tmp.name) / "cache/agent-notify/log").read_text())

    def test_codex_transcript_missing_context_or_unreadable_is_ignored(self):
        cases = [
            {"transcript_path": str(Path(self.tmp.name) / "missing.jsonl")},
            {"transcript_path": self.tmp.name},  # a directory is not a transcript
            {"transcript_path": None},
            {"transcript_path": self.transcript({"turn_id": "old-turn", "approvals_reviewer": "user"})},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                r = self.codex_permission(**fields)
                self.assertEqual((r.returncode, r.stdout), (0, ""))
        path = self.transcript({"turn_id": "t2", "approvals_reviewer": "user"}, {"turn_id": "t2"})
        self.codex_permission(transcript_path=path)  # don't reuse an older reviewer
        self.assertEqual(Bemfa.received, [])

    def test_codex_event_reviewer_takes_precedence_over_transcript(self):
        path = self.transcript({"turn_id": "t2", "approvals_reviewer": "user"})
        self.codex_permission(approvals_reviewer="auto_review", transcript_path=path)
        self.assertEqual(Bemfa.received, [])

    # ------------------------------------------------------------ payload rules
    def test_payload_fits_256_bytes(self):
        self.run_cli("send", "--kind", "done", "--msg", "汉字" * 300, "--project", "p" * 80)
        msg = Bemfa.received[0]["msg"]
        self.assertLessEqual(len(msg.encode("utf-8")), 256)
        json.loads(msg)

    def test_minimal_privacy_drops_summary(self):
        self.run_cli("send", "--kind", "done", "--msg", "secret detail", env={"AGENT_NOTIFY_PRIVACY": "minimal"})
        self.assertEqual(self.payloads()[0]["m"], "")

    def test_bemfa_error_and_bad_input_never_break_hooks(self):
        Bemfa.reply = {"code": 40004, "message": "topic not found"}
        r = self.hook("claude", {"session_id": "s6", "hook_event_name": "StopFailure", "error": "unknown"})
        self.assertEqual(r.returncode, 0)
        r = self.run_cli("hook", "claude", stdin="not json")
        self.assertEqual(r.returncode, 0)
        r = self.hook("claude", {"hook_event_name": "Notification", "notification_type": "permission_prompt"},
                      env={"AGENT_NOTIFY_ENDPOINT": "http://127.0.0.1:9/nothing"})
        self.assertEqual(r.returncode, 0)
        log = (Path(self.tmp.name) / "cache" / "agent-notify" / "log").read_text(encoding="utf-8")
        self.assertIn("40004", log)

    # ------------------------------------------------------------ MCP
    def test_mcp_handshake_and_call_suppresses_stop_push(self):
        self.hook("claude", {"session_id": "m1", "hook_event_name": "UserPromptSubmit"})
        self.start_file("claude", "m1").write_text(str(time.time() - 200))
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "notify_board", "arguments": {"message": "固件已烧录", "kind": "done"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "nope"},
        ]
        r = self.run_cli("mcp", stdin="\n".join(json.dumps(x) for x in reqs) + "\n",
                         env={"AGENT_NOTIFY_SOURCE": "claude"})
        out = [json.loads(l) for l in r.stdout.splitlines()]
        self.assertEqual([o["id"] for o in out], [1, 2, 3, 4])
        self.assertEqual(out[1]["result"]["tools"][0]["name"], "notify_board")
        self.assertEqual(out[2]["result"]["content"][0]["text"], "sent")
        self.assertEqual(out[3]["error"]["code"], -32601)
        self.hook("claude", {"session_id": "m1", "hook_event_name": "Stop", "last_assistant_message": "x"})
        self.assertEqual([p["m"] for p in self.payloads()], ["固件已烧录"])  # no second, generic push


if __name__ == "__main__":
    unittest.main()
