"""Check installation contracts with a fake Python toolchain, never pip or accounts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class MacScriptsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="matrix mac test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "源码 with spaces"
        self.root.mkdir()
        shutil.copytree(REPO / "scripts", self.root / "scripts")
        for name in ("安装.command", "启动.command", "启动手机工具.command"):
            shutil.copy2(REPO / name, self.root / name)
        for name in ("xhs-matrix-desk-v2.7.7", "android-device-desk"):
            app = self.root / name
            app.mkdir()
            for filename in ("run.py", "启动器.py", "requirements.txt", "requirements-mac-install.txt", "requirements-live.txt"):
                (app / filename).write_text("# fixture\n", encoding="utf-8")
        self.tools = self.root / "fake tools"
        self.tools.mkdir()
        self.log = self.root / "tool-log.jsonl"
        self.python = self.tools / "python3.12"
        self.python.write_text(
            f"#!{sys.executable}\n" + """import json, os, pathlib, shutil, sys
args = sys.argv[1:]
with open(os.environ['MATRIX_TEST_LOG'], 'a', encoding='utf-8') as log:
    log.write(json.dumps({'executable': sys.argv[0], 'args': args}) + '\\n')
if args[:1] == ['-c']:
    version = os.environ.get('FAKE_VENV_VERSION' if '/venv/' in sys.argv[0] else 'FAKE_PYTHON_VERSION', '3.12')
    raise SystemExit(0 if version == '3.12' else 1)
if args[:2] == ['-m', 'venv']:
    target = pathlib.Path(args[2]) / 'bin' / 'python'
    target.parent.mkdir(parents=True)
    shutil.copy2(sys.argv[0], target)
    target.chmod(0o755)
if args[:3] == ['-m', 'pip', 'install'] and os.environ.get('FAKE_PIP_FAIL'):
    raise SystemExit(3)
""", encoding="utf-8")
        self.python.chmod(0o755)
        (self.tools / "uname").write_text('#!/bin/bash\necho "${FAKE_OS:-Darwin}"\n', encoding="utf-8")
        (self.tools / "uname").chmod(0o755)
        self.env = {**os.environ, "PATH": str(self.tools) + ":" + os.environ.get("PATH", ""),
                    "MATRIX_PYTHON": str(self.python), "MATRIX_TEST_LOG": str(self.log)}

    def run_script(self, *args, filename="安装.command", environment=None):
        return subprocess.run(["/bin/bash", str(self.root / filename), *args],
                              env={**self.env, **(environment or {})},
                              cwd=self.temporary.name, capture_output=True, text=True, timeout=10)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_shell_syntax(self):
        for path in [*REPO.rglob("*.command"), *REPO.rglob("*.sh")]:
            with self.subTest(path=path.relative_to(REPO)):
                result = subprocess.run(["/bin/bash", "-n", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_check_only_has_no_install_or_browser_download(self):
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.calls())
        self.assertTrue(all(call["args"][0] == "-c" for call in self.calls()))
        self.assertFalse(any(self.root.rglob("venv")))

    def test_default_installs_separate_environments_and_pinned_desktop_requirements(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        environments = [call["args"][2] for call in calls if call["args"][:2] == ["-m", "venv"]]
        self.assertEqual(set(environments), {str(self.root / name / "venv") for name in ("xhs-matrix-desk-v2.7.7", "android-device-desk")})
        files = [Path(call["args"][-1]).name for call in calls if "-r" in call["args"]]
        self.assertEqual(files, ["requirements-mac-install.txt", "requirements.txt"])
        downloads = [call for call in calls if call["args"][:2] == ["-m", "playwright"]]
        self.assertEqual(len(downloads), 1)
        self.assertIn("xhs-matrix-desk-v2.7.7/venv/bin/python", downloads[0]["executable"])

    def test_phone_live_install_does_not_install_desktop_or_chromium(self):
        result = self.run_script("--phone", "--with-live")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "xhs-matrix-desk-v2.7.7" / "venv").exists())
        files = [Path(call["args"][-1]).name for call in self.calls() if "-r" in call["args"]]
        self.assertEqual(files, ["requirements.txt", "requirements-live.txt"])
        self.assertFalse(any("playwright" in call["args"] for call in self.calls()))

    def test_unsupported_system_stops_before_install(self):
        result = self.run_script(environment={"FAKE_OS": "Linux"})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls())

    def test_wrong_python_and_existing_invalid_environment_do_not_mutate(self):
        result = self.run_script(environment={"FAKE_PYTHON_VERSION": "3.11"})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(self.root.rglob("venv")))
        invalid = self.root / "android-device-desk" / "venv"
        invalid.mkdir()
        marker = invalid / "keep-this-file"
        marker.write_text("preserved")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(), "preserved")
        self.assertFalse((self.root / "xhs-matrix-desk-v2.7.7" / "venv").exists())

    def test_failed_pip_does_not_claim_success_or_continue_to_phone(self):
        result = self.run_script(environment={"FAKE_PIP_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("安装完成", result.stdout)
        self.assertFalse((self.root / "android-device-desk" / "venv").exists())

    def test_launch_requires_environment_and_preserves_arguments(self):
        missing = self.run_script(filename="启动.command")
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(self.run_script().returncode, 0)
        result = self.run_script("--tk", filename="启动.command")
        self.assertEqual(result.returncode, 0, result.stderr)
        final = self.calls()[-1]
        self.assertEqual(final["args"], [str(self.root / "xhs-matrix-desk-v2.7.7" / "启动器.py"), "--tk"])
        result = self.run_script(filename="启动手机工具.command")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1]["args"], [str(self.root / "android-device-desk" / "run.py")])


if __name__ == "__main__":
    unittest.main()
