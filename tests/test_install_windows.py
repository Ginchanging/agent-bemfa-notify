"""Windows installer regression tests; all config writes use temporary directories."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("install_windows", ROOT / "install_windows.py")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class WindowsConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.codex = self.root / "codex"
        self.codex.mkdir()
        self.config = self.codex / "config.toml"
        self.hooks = self.codex / "hooks.json"
        self.script = self.root / "应用 with spaces" / "agent_notify.py"

    def test_merge_reinstall_and_uninstall_preserve_existing_configuration(self):
        original_config = 'notify = ["existing-notify"]\n[mcp_servers.existing]\ncommand = "existing-server"\n'
        existing_handler = {"type": "command", "command": "existing-hook"}
        original_hooks = {"description": "用户 hooks", "hooks": {"Stop": [
            {"hooks": [existing_handler]},
        ]}}
        self.config.write_text(original_config, encoding="utf-8")
        self.hooks.write_text(json.dumps(original_hooks), encoding="utf-8")

        INSTALLER.configure_codex(self.codex, self.script)
        first_config = self.config.read_bytes()
        first_hooks = self.hooks.read_bytes()
        installed = json.loads(first_hooks)
        self.assertEqual(installed["description"], original_hooks["description"])
        self.assertEqual(installed["hooks"]["Stop"][0]["hooks"], [existing_handler])
        self.assertIn(original_config.strip(), self.config.read_text(encoding="utf-8"))
        for name in ("UserPromptSubmit", "Stop", "PermissionRequest"):
            ours = [handler for group in installed["hooks"][name] for handler in group["hooks"]
                    if "agent_notify.py" in handler["command"]]
            self.assertEqual(len(ours), 1)
            self.assertEqual(ours[0].get("async", False), name != "UserPromptSubmit")

        INSTALLER.configure_codex(self.codex, self.script)
        self.assertEqual(self.config.read_bytes(), first_config)
        self.assertEqual(self.hooks.read_bytes(), first_hooks)
        self.assertEqual(self.config.with_name("config.toml.bak-agent-notify").read_text(encoding="utf-8"), original_config)
        self.assertEqual(json.loads(self.hooks.with_name("hooks.json.bak-agent-notify").read_text(encoding="utf-8")), original_hooks)

        INSTALLER.configure_codex(self.codex, self.script, remove=True)
        self.assertEqual(self.config.read_text(encoding="utf-8").strip(), original_config.strip())
        self.assertEqual(json.loads(self.hooks.read_text(encoding="utf-8")), original_hooks)

    def test_uninstall_keeps_other_handlers_in_the_same_matcher_group(self):
        existing = {"type": "command", "command": "keep-this-handler"}
        self.hooks.write_text(json.dumps({"hooks": {"Stop": [{"matcher": "custom", "hooks": [
            existing, {"type": "command", "command": "python agent_notify.py hook codex"},
        ]}]}}), encoding="utf-8")
        INSTALLER.configure_codex(self.codex, self.script, remove=True)
        self.assertEqual(json.loads(self.hooks.read_text(encoding="utf-8")), {
            "hooks": {"Stop": [{"matcher": "custom", "hooks": [existing]}]},
        })
        self.assertFalse(self.config.exists())

    def test_unmarked_mcp_conflict_leaves_both_files_untouched(self):
        self.config.write_text('[mcp_servers.agent-notify]\ncommand = "custom"\n', encoding="utf-8")
        self.hooks.write_text('{"description": "keep"}\n', encoding="utf-8")
        config_before, hooks_before = self.config.read_bytes(), self.hooks.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            INSTALLER.configure_codex(self.codex, self.script)
        self.assertEqual(self.config.read_bytes(), config_before)
        self.assertEqual(self.hooks.read_bytes(), hooks_before)

    def test_uninstall_before_install_does_not_create_config_files(self):
        INSTALLER.configure_codex(self.codex, self.script, remove=True)
        self.assertEqual(list(self.codex.iterdir()), [])


@unittest.skipUnless(os.name == "nt", "Requires native Windows cmd.exe")
class WindowsInstallTest(unittest.TestCase):
    def test_install_launcher_and_uninstall_with_unicode_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user_home = root / "用户 home"
            destination = root / "安装 directory"
            codex = root / "custom codex"
            user_home.mkdir()
            env = dict(os.environ, USERPROFILE=str(user_home), AGENT_NOTIFY_HOME=str(destination),
                       CODEX_HOME=str(codex), XDG_CONFIG_HOME=str(root / "config"),
                       XDG_CACHE_HOME=str(root / "cache"), PYTHONUTF8="1", AGENT_NOTIFY_DRY_RUN="1")
            command = [sys.executable, "-X", "utf8", str(ROOT / "install_windows.py")]
            installed = subprocess.run(command + ["--codex", "--no-setup"], env=env,
                                       capture_output=True, encoding="utf-8", timeout=20)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual((destination / "agent_notify.py").read_bytes(), (ROOT / "agent_notify.py").read_bytes())
            launcher = user_home / ".local" / "bin" / "agent-notify.cmd"
            result = subprocess.run(["cmd.exe", "/d", "/c", str(launcher), "test"], env=env,
                                    capture_output=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "sent")
            self.assertIn("test ✓", json.loads(result.stderr.strip())["m"])

            # Run uninstall from the installed copy, as documented, and retain credentials.
            credentials = root / "config" / "agent-notify" / "config.json"
            credentials.parent.mkdir(parents=True)
            credentials.write_text('{"uid": "test-placeholder"}', encoding="utf-8")
            result = subprocess.run([sys.executable, "-X", "utf8", str(destination / "install_windows.py"), "--uninstall"],
                                    env=env, capture_output=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(launcher.exists())
            self.assertFalse(destination.exists())
            self.assertEqual(credentials.read_text(encoding="utf-8"), '{"uid": "test-placeholder"}')
            self.assertNotIn(INSTALLER.BEGIN, (codex / "config.toml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
