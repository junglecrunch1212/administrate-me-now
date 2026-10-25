"""Hosted-only lifecycle checks. Seven sequential stubs; no Finance imports."""

import argparse
from dataclasses import replace
import importlib.util
import json
import os
import signal
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

HARNESS = Path(__file__).resolve().parents[1] / "fixtures/finance_buffer_profile.py"
spec = importlib.util.spec_from_file_location("finance_buffer_profile", HARNESS)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

RECORDS = []
SCRATCH = None


class LifecycleChecks(unittest.TestCase):
    def stub(self, name, code, *, limits=None, failure=None):
        with tempfile.TemporaryDirectory(prefix=name + "-", dir=SCRATCH) as folder:
            path = Path(folder)
            supervisor = module.Supervisor(
                path,
                limits
                or replace(module.Limits(), child_wall=3, child_cpu=2, total_wall=5),
            )
            command = [sys.executable, "-I", "-B", "-c", code]
            try:
                if failure:
                    with self.assertRaises(module.ProbeFailure):
                        supervisor.run(name, command, path)
                    self.assertTrue(supervisor.failed)
                    with self.assertRaisesRegex(
                        module.ProbeFailure, "Previous child failed"
                    ):
                        supervisor.run("must not launch", command, path)
                else:
                    supervisor.run(name, command, path)
            finally:
                RECORDS.extend(supervisor.children)
                for saved in supervisor.children:
                    saved["logs"] = {
                        file.name: file.read_text(errors="replace")[:8192]
                        for file in path.glob("*.log")
                    }
                    saved["scratch_removed"] = False
            self.assertEqual(len(supervisor.children), 1)
            record = supervisor.children[0]
            self.assertTrue(record["terminal"]["reaped"])
            self.assertTrue(record["cleanup_confirmed"])
            self.assertEqual(record["remaining_group_members"], [])
        record["scratch_removed"] = not path.exists()
        self.assertTrue(record["scratch_removed"])

    def test_normal_exit(self):
        self.stub("normal", "print('Normal stub completed')")

    def test_nonzero_exit(self):
        self.stub("nonzero", "raise SystemExit(3)", failure=True)

    def test_wall_deadline_and_reap(self):
        self.stub(
            "deadline",
            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(10)",
            limits=replace(module.Limits(), child_wall=0.5, total_wall=4),
            failure=True,
        )
        self.assertTrue(RECORDS[-1]["forced"])
        self.assertLess(RECORDS[-1]["wall_seconds"], 3)

    def test_cpu_stop_and_reap(self):
        self.stub(
            "cpu",
            "while True: pass",
            limits=replace(module.Limits(), child_wall=3, child_cpu=0.2, total_wall=5),
            failure=True,
        )

    def test_rss_stop_and_reap(self):
        self.stub(
            "rss",
            "import time; time.sleep(10)",
            limits=replace(module.Limits(), child_wall=3, child_rss=1, total_wall=5),
            failure=True,
        )

    def test_output_stop_and_reap(self):
        self.stub(
            "output",
            "import os,time; os.write(1,b'x'*100000); time.sleep(10)",
            limits=replace(
                module.Limits(), child_wall=3, artifact=96 * 1024, total_wall=5
            ),
            failure=True,
        )

    def test_supervisor_sigterm_and_reap(self):
        previous = {
            sig: signal.getsignal(sig)
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)
        }
        try:
            module.install_signal_handlers()
            signal.signal(
                signal.SIGALRM,
                lambda signum, frame: os.kill(os.getpid(), signal.SIGTERM),
            )
            signal.setitimer(signal.ITIMER_REAL, 0.5)
            self.stub("supervisor signal", "import time; time.sleep(10)", failure=True)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        self.assertIn("Supervisor received signal", RECORDS[-1]["failure"])

    def fake_run(
        self, folder, *, wait, usage=None, members=None, validate=None, clear=None
    ):
        supervisor = module.Supervisor(Path(folder))
        child = Mock(pid=987654321, returncode=None)
        with (
            patch.object(module.subprocess, "Popen", return_value=child) as launch,
            patch.object(module, "wait_child", side_effect=wait),
            patch.object(
                module,
                "process_usage",
                side_effect=usage
                or (lambda pid: {"pid": pid, "cpu_seconds": 0, "rss_bytes": 0}),
            ),
            patch.object(
                module, "group_members", return_value=[] if members is None else members
            ),
            patch.object(
                module, "clear_group", side_effect=clear or (lambda group: [])
            ),
        ):
            try:
                supervisor.run(
                    "fake", ["not executed"], Path(folder), validate=validate
                )
            except module.ProbeFailure:
                pass
            if supervisor.failed:
                with self.assertRaisesRegex(
                    module.ProbeFailure, "Previous child failed"
                ):
                    supervisor.run("must not launch", ["not executed"], Path(folder))
            self.assertEqual(launch.call_count, 1)
        return supervisor

    def test_missing_proc_row_reconciles_exact_terminal_status(self):
        terminal = {
            "exit_code": 0,
            "cpu_seconds": 0,
            "high_water_rss_bytes": 0,
            "reaped": True,
        }

        def usage(pid):
            if pid == 987654321:
                raise FileNotFoundError("Exited child")
            return {"pid": pid, "cpu_seconds": 0, "rss_bytes": 0}

        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
            result = self.fake_run(folder, wait=[None, terminal], usage=usage)
            self.assertFalse(result.failed)
            self.assertTrue(result.children[0]["cleanup_confirmed"])

    def test_reap_failure_preserves_original_error_and_pid(self):
        def usage(pid):
            if pid == 987654321:
                raise PermissionError("Collector failed")
            return {"pid": pid, "cpu_seconds": 0, "rss_bytes": 0}

        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
            result = self.fake_run(
                folder, wait=[None, module.ProbeFailure("Reap deadline")], usage=usage
            )
            record = json.loads((Path(folder) / "processes.json").read_text())[0]
            self.assertTrue(result.failed)
            self.assertEqual(record["pid"], 987654321)
            self.assertIn("Collector failed", record["failure"])
            self.assertIn("Reap deadline", record["cleanup_errors"][0])
            self.assertFalse(record["cleanup_confirmed"])
            self.assertIsNone(record["terminal"])

    def test_malformed_marks_block_next_launch(self):
        terminal = {
            "exit_code": 0,
            "cpu_seconds": 0,
            "high_water_rss_bytes": 0,
            "reaped": True,
        }
        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
            result = self.fake_run(
                folder,
                wait=[terminal],
                validate=lambda: module.read_child(Path(folder), "fake"),
            )
            self.assertTrue(result.failed)
            self.assertIn("completion", result.children[0]["failure"])

    def test_unexpected_group_uses_only_test_doubles(self):
        terminal = {
            "exit_code": -9,
            "cpu_seconds": 0,
            "high_water_rss_bytes": 0,
            "reaped": True,
        }
        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
            result = self.fake_run(
                folder, wait=[None, terminal], members=[987654321, 987654322]
            )
            self.assertTrue(result.failed)
            self.assertIn("unexpected child process", result.children[0]["failure"])

    def test_post_validation_limit_blocks_next_launch(self):
        terminal = {
            "exit_code": 0,
            "cpu_seconds": 0,
            "high_water_rss_bytes": 0,
            "reaped": True,
        }
        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:

            def invalid_output():
                (Path(folder) / "oversized").write_bytes(b"x" * (2 * module.MIB + 1))

            result = self.fake_run(folder, wait=[terminal], validate=invalid_output)
            self.assertTrue(result.failed)
            self.assertIn("artifact limit", result.children[0]["failure"])

    def test_all_budget_predicates(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
            supervisor = module.Supervisor(Path(folder))
            row = {
                "child_wall_seconds": 0,
                "total_wall_seconds": 0,
                "child": {"pid": 100, "cpu_seconds": 0, "rss_bytes": 0},
                "total_cpu_seconds": 0,
                "combined_rss_bytes": 0,
                "scratch_bytes": 0,
                "group_members": [100],
            }
            self.assertIsNone(supervisor.budget_failure(row))
            for key, value, expected in (
                ("total_wall_seconds", 270, "total wall deadline"),
                ("total_cpu_seconds", 180, "total CPU limit"),
                ("combined_rss_bytes", 1024 * module.MIB + 1, "combined RSS limit"),
                ("scratch_bytes", 32 * module.MIB + 1, "evidence scratch limit"),
                ("scratch_bytes", 2 * module.MIB, "artifact reserve limit"),
                ("group_members", [100, 101], "unexpected child process"),
            ):
                self.assertEqual(
                    supervisor.budget_failure(row | {key: value}), expected
                )
            supervisor.samples = 1201
            self.assertEqual(supervisor.budget_failure(row), "sample count limit")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    SCRATCH = args.output / "scratch"
    SCRATCH.mkdir()
    result = unittest.TextTestRunner(verbosity=2, failfast=True).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(LifecycleChecks)
    )
    SCRATCH.rmdir()
    report = {
        "tests": result.testsRun,
        "success": result.wasSuccessful()
        and result.testsRun == 13
        and len(RECORDS) == 7,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "stubs_created": len(RECORDS),
        "maximum_active_owned_processes": 2,
        "records": RECORDS,
        "scratch_removed": not SCRATCH.exists(),
        "scope": "One test supervisor plus seven sequential short-lived stubs; separate from the two revision children",
    }
    (args.output / "lifecycle.json").write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(
        0
        if result.wasSuccessful() and result.testsRun == 13 and len(RECORDS) == 7
        else 1
    )
