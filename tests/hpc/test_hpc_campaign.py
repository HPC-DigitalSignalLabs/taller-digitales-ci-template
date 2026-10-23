# SPDX-License-Identifier: Apache-2.0
"""Pruebas stdlib del controlador; no envían jobs ni requieren Apptainer."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[2] / "tools" / "hpc_campaign.py"
spec = importlib.util.spec_from_file_location("hpc_campaign", TOOL)
hpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hpc)

XML = '<testsuite tests="1" failures="0"><testcase name="pass"/></testsuite>'


def config() -> dict:
    return hpc.validate_config({
        "partition": "cpu-other-cluster", "account": "", "qos": "", "module": "",
        "module_init": "", "runtime": "apptainer", "image": "/images/verify.sif",
        "results_root": "/results", "cpus": 1, "memory_mb": 512, "time_minutes": 2,
        "max_tasks": 4, "max_concurrency": 2, "max_cpus": 2,
        "max_memory_mb": 1024, "max_time_minutes": 5,
    })


def manifest(n: int = 1) -> dict:
    return {"schema": 1, "config": config(), "batch_size": n, "repetitions": 1,
            "concurrency": 1, "mode": "regression", "pytest_target": "tests/counter",
            "cycles": 200, "tasks": [{"index": i, "seed": 1234} for i in range(n)],
            "source": {"digest": "source-digest", "dirty": False}}


def row(jid: str = "123_0", status: str = "COMPLETED", **values: str) -> dict:
    result = dict(zip(hpc.FIELDS, [jid, status, "0:0", "2026-09-25T00:00:00",
             "2026-09-25T00:00:10", "2026-09-25T00:00:30", "20", "2",
             "00:10.000", "", "512Mn"]))
    result.update(values)
    return result


class CampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def evidence(self, index: int = 0) -> Path:
        attempt = hpc.new_attempt(self.root, "123", index, "0")
        xml = attempt / "results" / "junit.xml"
        xml.parent.mkdir()
        xml.write_text(XML, encoding="utf-8")
        hpc.write_json(attempt / "task.json", {
            "job_id": "123", "task": index, "seed": 1234, "status": "COMPLETED",
            "exit_code": 0, "manifest_sha256": "manifest-hash",
            "source_digest": "source-digest", "image_sha256": "a" * 64,
            "evidence": [{"path": "results/junit.xml", "sha256": hpc.sha256(xml)}],
        })
        return attempt

    def summary(self, rows: list[dict], n: int = 1) -> dict:
        return hpc.summarize(manifest(n), rows, "123", "manifest-hash", self.root)

    def test_success_requires_accounting_and_valid_evidence(self) -> None:
        self.assertEqual(self.summary([row()])["verdict"], "NOT_PASS")
        self.evidence()
        result = self.summary([row()])
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["tasks"][0]["queue_s"], 10)
        self.assertEqual(result["tasks"][0]["execution_s"], 20)
        self.assertEqual(result["tasks"][0]["total_s"], 30)
        self.assertEqual(result["throughput_success_per_minute"], 2)

    def test_all_failure_states_never_pass(self) -> None:
        self.evidence()
        for status in ("FAILED", "TIMEOUT", "CANCELLED by 123", "PENDING", "RUNNING",
                       "NODE_FAIL", "OUT_OF_MEMORY", "PREEMPTED", "UNKNOWN"):
            with self.subTest(status=status):
                self.assertEqual(self.summary([row(status=status)])["verdict"], "NOT_PASS")

    def test_exit_signal_fails_even_if_completed(self) -> None:
        self.evidence()
        self.assertEqual(self.summary([row(ExitCode="0:9")])["verdict"], "NOT_PASS")

    def test_failed_step_cannot_hide_behind_completed_parent(self) -> None:
        self.evidence()
        self.assertEqual(self.summary([row(), row("123_0.batch", status="FAILED",
                                                 ExitCode="1:0")])["verdict"], "NOT_PASS")

    def test_steps_and_parent_not_double_counted(self) -> None:
        self.evidence()
        result = self.summary([row(), row("123"), row("123_0.batch", MaxRSS="2M"),
                               row("123_0.extern", TotalCPU="00:02", MaxRSS="1M")])
        self.assertEqual(result["successful_tasks"], 1)
        self.assertEqual(result["tasks"][0]["cpu_s"], 10)
        self.assertEqual(result["tasks"][0]["cpu_efficiency"], .25)
        self.assertEqual(result["tasks"][0]["max_rss_bytes"], 2 * 1024**2)

    def test_missing_cpu_uses_batch_without_extern(self) -> None:
        result = self.summary([row(TotalCPU=""), row("123_0.batch", TotalCPU="00:03.5"),
                               row("123_0.extern", TotalCPU="00:09")])
        self.assertEqual(result["tasks"][0]["cpu_s"], 3.5)

    def test_pending_and_missing_not_zero_latency(self) -> None:
        result = self.summary([row(status="PENDING", Start="Unknown", End="Unknown")], n=2)
        self.assertFalse(result["complete"])
        self.assertIsNone(result["throughput_success_per_minute"])
        self.assertIsNone(result["failure_fraction"])
        self.assertIsNone(result["tasks"][0]["queue_s"])
        self.assertEqual(result["tasks"][1]["state"], "MISSING")

    def test_compressed_pending_only_matches_its_indices(self) -> None:
        result = self.summary([row("123_[0-2:2%2]", status="PENDING")], n=4)
        self.assertEqual([t["state"] for t in result["tasks"]],
                         ["PENDING_UNRESOLVED", "MISSING", "PENDING_UNRESOLVED", "MISSING"])

    def test_duplicate_allocation_fails_closed(self) -> None:
        self.evidence()
        result = self.summary([row(), row()])
        self.assertEqual(result["tasks"][0]["state"], "AMBIGUOUS")
        self.assertEqual(result["verdict"], "NOT_PASS")

    def test_invalid_time_cannot_be_campaign_pass(self) -> None:
        self.evidence()
        for fields in ({"Start": "Unknown"}, {"End": "2026-09-24T00:00:00"},
                       {"ElapsedRaw": "Unknown"}, {"Start": "2026-09-26T00:00:00"}):
            with self.subTest(fields=fields):
                result = self.summary([row(**fields)])
                self.assertFalse(result["timing_complete"])
                self.assertEqual(result["verdict"], "NOT_PASS")

    def test_percentiles_and_small_sample_warning(self) -> None:
        self.assertIsNone(hpc.percentile([], .95))
        self.assertEqual(hpc.describe([17])["p95"], 17)
        self.assertTrue(hpc.describe([17])["p95_exploratory"])
        self.assertAlmostEqual(hpc.describe([0, 100])["p95"], 95)
        self.assertFalse(hpc.describe(list(range(20)))["p95_exploratory"])

    def test_parse_real_sacct_shape_and_optional_trailing_pipe(self) -> None:
        text = "|".join(row().values())
        self.assertEqual(hpc.parse_sacct(text), [row()])
        self.assertEqual(hpc.parse_sacct(text + "|\n"), [row()])
        self.assertEqual(hpc.parse_sacct(""), [])
        with self.assertRaises(ValueError):
            hpc.parse_sacct("123|COMPLETED")

    def test_duration_and_memory_units(self) -> None:
        self.assertEqual(hpc.duration("1-01:02:03.5"), 90123.5)
        self.assertEqual(hpc.duration("00:11.304"), 11.304)
        self.assertIsNone(hpc.duration(""))
        self.assertIsNone(hpc.duration("Unknown"))
        self.assertEqual(hpc.memory_bytes("1.5G"), 1.5 * 1024**3)
        self.assertIsNone(hpc.memory_bytes(""))
        self.assertIsNone(hpc.memory_bytes("512Mn"))  # ReqMem no es MaxRSS.

    def test_junit_empty_missing_malformed_fail_skip(self) -> None:
        file = self.root / "test.xml"
        self.assertFalse(hpc.junit_valid(file))
        for text in ("", "<broken", "<testsuite tests='0'/>",
                     '<testsuites errors="1"><testsuite><testcase/></testsuite></testsuites>',
                     '<testsuites skipped="1"><testsuite><testcase/></testsuite></testsuites>',
                     '<testsuite failures="1"><testcase/></testsuite>',
                     '<testsuite><testcase><error/></testcase></testsuite>',
                     '<testsuite><testcase><skipped/></testcase></testsuite>'):
            file.write_text(text, encoding="utf-8")
            self.assertFalse(hpc.junit_valid(file), text)
        file.write_text(XML, encoding="utf-8")
        self.assertTrue(hpc.junit_valid(file))

    def test_evidence_tamper_missing_and_retry_never_green(self) -> None:
        attempt = self.evidence()
        xml = attempt / "results" / "junit.xml"
        xml.write_text(XML + "\n", encoding="utf-8")
        self.assertFalse(self.summary([row()])["tasks"][0]["success"])
        xml.unlink()
        self.assertFalse(self.summary([row()])["tasks"][0]["success"])
        self.evidence()
        self.assertEqual(self.summary([row()])["tasks"][0]["evidence"], "multiple-attempts")

    def test_killed_attempt_without_record_does_not_hide_on_retry(self) -> None:
        hpc.new_attempt(self.root, "123", 0, "0")  # Timeout antes de task.json.
        self.evidence()
        result = self.summary([row()])
        self.assertEqual(result["verdict"], "NOT_PASS")
        self.assertEqual(result["tasks"][0]["evidence"], "multiple-attempts")

    def test_wrong_manifest_seed_and_image_rejected(self) -> None:
        attempt = self.evidence()
        path = attempt / "task.json"
        original = hpc.load(path)
        for key, value in (("manifest_sha256", "other"), ("seed", 999), ("image_sha256", "")):
            hpc.write_json(path, {**original, key: value})
            self.assertFalse(self.summary([row()])["tasks"][0]["success"])

    def test_inventory_excludes_outputs_and_detects_changes(self) -> None:
        for name in ("Makefile", "sim.mk", "src/counter.sv", "tests/counter/test_counter.py",
                     "tests/counter/__pycache__/cached.pyc", "results/old.xml",
                     "sim_build/old.cpp", "README.md", "hpc/logs/old.log"):
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("source", encoding="utf-8")
        inventory = hpc.inventory(self.root)
        self.assertEqual(set(inventory), {"Makefile", "sim.mk", "src/counter.sv",
                                         "tests/counter/test_counter.py"})
        (self.root / "src/counter.sv").write_text("new", encoding="utf-8")
        self.assertNotEqual(hpc.tree_digest(inventory), hpc.tree_digest(hpc.inventory(self.root)))

    def test_root_normalization(self) -> None:
        template = self.root / "template"
        template.mkdir()
        (template / "Makefile").touch()
        (template / "sim.mk").touch()
        self.assertEqual(hpc.template_root(self.root), template)
        self.assertEqual(hpc.template_root(template), template)

    def test_unique_attempts_for_concurrent_tasks_and_restarts(self) -> None:
        with ThreadPoolExecutor(max_workers=4) as executor:
            paths = list(executor.map(lambda i: hpc.new_attempt(self.root, "123", i % 2, "0"), range(8)))
        self.assertEqual(len(set(paths)), 8)
        for i, path in enumerate(paths):
            (path / "result").write_text(str(i), encoding="utf-8")
        self.assertEqual([(p / "result").read_text() for p in paths], list(map(str, range(8))))

    def test_limits_and_portable_sbatch_arguments(self) -> None:
        m = manifest(4)
        m["concurrency"] = 2
        command = hpc.submission_command(self.root / "manifest.json", m)
        for arg in ("--array=0-3%2", "--cpus-per-task=1", "--mem=512M", "--time=2",
                    "--export=NONE", "--no-requeue", "--partition=cpu-other-cluster"):
            self.assertIn(arg, command)
        self.assertFalse(any(a.startswith("--account=") for a in command))
        m["concurrency"] = 3
        with self.assertRaises(ValueError):
            hpc.validate_manifest(m)
        m["concurrency"] = 1
        m["repetitions"] = 2
        with self.assertRaises(ValueError):
            hpc.validate_manifest(m)

    def test_invalid_config_fails_closed(self) -> None:
        for key, value in (("cpus", 99), ("memory_mb", 0), ("time_minutes", -1),
                           ("max_tasks", 257), ("image", "/a,b"), ("runtime", "bash"),
                           ("partition", "nukwa"), ("cpus", True)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                hpc.validate_config({**config(), key: value})

    def test_single_task_demo_pilot_bounds(self) -> None:
        m = manifest()
        m["mode"] = "demo"
        m["config"].update(partition="kura-wide", cpus=1, memory_mb=2048,
                           time_minutes=5, max_tasks=1, max_concurrency=1,
                           max_cpus=1, max_memory_mb=2048, max_time_minutes=5)
        command = hpc.submission_command(self.root / "manifest.json", m)
        for arg in ("--nodes=1", "--ntasks=1", "--array=0-0%1",
                    "--cpus-per-task=1", "--mem=2048M", "--time=5"):
            self.assertIn(arg, command)
        self.assertTrue(command[-3].endswith("demo-showcase.slurm"))
        self.assertEqual(m["cycles"], 200)
        m["repetitions"] = 2
        with self.assertRaises(ValueError):
            hpc.validate_manifest(m)

    def test_host_environment_overrides_removed(self) -> None:
        with patch.dict(os.environ, {"APPTAINER_BIND": "/:/work", "SINGULARITYENV_TB_SEED": "2",
                                     "MAKEFLAGS": "-j100", "PYTEST_ADDOPTS": "-x",
                                     "LD_LIBRARY_PATH": "/module/lib"}):
            clean = hpc.clean_host_env()
        self.assertNotIn("APPTAINER_BIND", clean)
        self.assertNotIn("SINGULARITYENV_TB_SEED", clean)
        self.assertNotIn("MAKEFLAGS", clean)
        self.assertEqual(clean["LD_LIBRARY_PATH"], "/module/lib")

    def test_submit_is_dry_run_by_default(self) -> None:
        path = self.root / "manifest.json"
        hpc.write_json(path, manifest())
        args = argparse.Namespace(manifest=path, execute=False, allow_dirty=False)
        with patch.object(hpc, "verify_snapshot"), patch.object(hpc.subprocess, "run") as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(hpc.submit(args), 0)
            run.assert_not_called()
        self.assertFalse((self.root / "submission-claim.json").exists())

    def test_no_run_on_login_node(self) -> None:
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            hpc.run_task(argparse.Namespace(manifest=self.root / "missing", mode="demo"))

    def test_plan_snapshot_lf_seeds_and_repetitions(self) -> None:
        source = self.root / "repo"
        source.mkdir()
        for name in ("Makefile", "sim.mk", "tests/counter/test_counter.py",
                     "hpc/regression.slurm", "tools/hpc_campaign.py"):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# fixture\r\n")
        cfg = self.root / "config.json"
        hpc.write_json(cfg, config())
        args = argparse.Namespace(config=cfg, source=source, output=self.root / "plan",
             mode="regression", batch_size=2, repetitions=2, concurrency=2, seed=1234,
             seed_mode="indexed", cycles=200, pytest_target="tests/counter")
        results = [subprocess.CompletedProcess([], 0, stdout="a" * 40),
                   subprocess.CompletedProcess([], 0, stdout=" M tools/hpc_campaign.py")]
        with patch.object(hpc.subprocess, "run", side_effect=results):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(hpc.plan(args), 0)
        m = hpc.load(args.output / "manifest.json")
        self.assertEqual([t["seed"] for t in m["tasks"]], [1234, 1235, 1234, 1235])
        self.assertTrue(m["source"]["dirty"])
        self.assertNotEqual(m["source"]["original_digest"], m["source"]["digest"])
        self.assertNotIn(b"\r", (args.output / "source/hpc/regression.slurm").read_bytes())
        hpc.verify_snapshot(args.output / "manifest.json", m)
        (args.output / "source/Makefile").write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            hpc.verify_snapshot(args.output / "manifest.json", m)

    def submission_fixture(self) -> tuple[Path, dict]:
        m = manifest()
        image = self.root / "verify.sif"
        image.touch()
        m["config"]["image"] = image.as_posix()
        m["config"]["results_root"] = (self.root / "results").as_posix()
        path = self.root / "manifest.json"
        hpc.write_json(path, m)
        return path, m

    @unittest.skipUnless(os.name == "posix", "Envío soportado solo en Linux; se prueba también con Python Linux")
    def test_explicit_submission_receipt_and_no_duplicate_send(self) -> None:
        path, _ = self.submission_fixture()
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            if command[0] == "squeue":
                return subprocess.CompletedProcess(command, 0, stdout="")
            self.assertTrue((self.root / "logs").is_dir())  # Antes de sbatch.
            return subprocess.CompletedProcess(command, 0, stdout="123;cluster-test\n", stderr="")

        args = argparse.Namespace(manifest=path, execute=True, allow_dirty=False)
        with patch.object(hpc, "verify_snapshot"), patch.object(hpc.subprocess, "run", side_effect=fake_run):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(hpc.submit(args), 0)
                with self.assertRaises(FileExistsError):
                    hpc.submit(args)
        self.assertEqual(len(commands), 2)
        receipt = hpc.load(self.root / "submission.json")
        self.assertEqual(receipt["job_id"], "123")
        self.assertEqual(receipt["cluster"], "cluster-test")
        self.assertEqual(receipt["manifest_sha256"], hpc.sha256(path))
        self.assertFalse((self.root / "results/.submission.lock").exists())

    @unittest.skipUnless(os.name == "posix", "Guarda de envío Linux")
    def test_live_task_budget_prevents_sbatch(self) -> None:
        path, _ = self.submission_fixture()
        queue = subprocess.CompletedProcess([], 0, stdout="10_0\n10_1\n10_2\n10_3\n")
        with patch.object(hpc, "verify_snapshot"), patch.object(hpc.subprocess, "run", return_value=queue) as run:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                hpc.submit(argparse.Namespace(manifest=path, execute=True, allow_dirty=False))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][0], "squeue")

    @unittest.skipUnless(os.name == "posix", "Guarda de envío Linux")
    def test_require_idle_checks_all_user_jobs_and_never_submits(self) -> None:
        path, _ = self.submission_fixture()
        queue = subprocess.CompletedProcess([], 0, stdout="999\n")
        with patch.object(hpc, "verify_snapshot"), patch.object(hpc.subprocess, "run", return_value=queue) as run:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "cola"):
                hpc.submit(argparse.Namespace(manifest=path, execute=True,
                                             allow_dirty=False, require_idle=True))
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("--name", run.call_args.args[0])
        self.assertTrue((self.root / "submission-claim.json").exists())

    @unittest.skipUnless(os.name == "posix", "Timeout de envío Linux")
    def test_submission_timeout_retains_claim(self) -> None:
        path, _ = self.submission_fixture()
        responses = [subprocess.CompletedProcess([], 0, stdout=""),
                     subprocess.TimeoutExpired("sbatch", 30)]
        with patch.object(hpc, "verify_snapshot"), patch.object(hpc.subprocess, "run", side_effect=responses):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                hpc.submit(argparse.Namespace(manifest=path, execute=True, allow_dirty=False))
        self.assertTrue((self.root / "submission-claim.json").exists())
        self.assertNotIn("job_id", hpc.load(self.root / "submission.json"))
        self.assertFalse((self.root / "results/.submission.lock").exists())

    @unittest.skipUnless(os.name == "posix", "Guarda dirty de envío Linux")
    def test_dirty_requires_explicit_authorization(self) -> None:
        path, m = self.submission_fixture()
        m["source"]["dirty"] = True
        hpc.write_json(path, m)
        with patch.object(hpc, "verify_snapshot"), patch.object(hpc.subprocess, "run") as run:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                hpc.submit(argparse.Namespace(manifest=path, execute=True, allow_dirty=False))
            run.assert_not_called()
        self.assertFalse((self.root / "submission-claim.json").exists())

    def test_collect_offline_keeps_history_and_rejects_changed_manifest(self) -> None:
        path = self.root / "manifest.json"
        hpc.write_json(path, manifest())
        hpc.write_json(self.root / "submission.json",
                       {"job_id": "123", "manifest_sha256": hpc.sha256(path)})
        sacct = self.root / "sacct.txt"
        sacct.write_text("", encoding="utf-8")  # Accounting aún no disponible.
        args = argparse.Namespace(manifest=path, sacct_file=sacct, results_root=self.root)
        with patch.object(hpc.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(hpc.collect(args), 1)
            self.assertEqual(hpc.collect(args), 1)
            run.assert_not_called()
        self.assertEqual(len(list((self.root / "accounting").glob("*.json"))), 2)
        self.assertEqual(hpc.load(self.root / "summary.json")["states"], {"MISSING": 1})
        m = manifest()
        m["cycles"] += 1
        hpc.write_json(path, m)
        with self.assertRaises(ValueError):
            hpc.collect(args)
        self.assertEqual(hpc.load(self.root / "summary.json")["verdict"], "NOT_PASS")

    def test_bad_accounting_preserved_and_old_green_invalidated(self) -> None:
        path = self.root / "manifest.json"
        hpc.write_json(path, manifest())
        hpc.write_json(self.root / "submission.json",
                       {"job_id": "123", "manifest_sha256": hpc.sha256(path)})
        hpc.write_json(self.root / "summary.json", {"verdict": "PASS"})
        sacct = self.root / "bad.txt"
        sacct.write_text("bad|columns", encoding="utf-8")
        with self.assertRaises(ValueError):
            hpc.collect(argparse.Namespace(manifest=path, sacct_file=sacct, results_root=self.root))
        self.assertEqual(hpc.load(self.root / "summary.json")["verdict"], "NOT_PASS")
        raw = hpc.load(next((self.root / "accounting").glob("*.json")))
        self.assertEqual(raw["raw"], "bad|columns")
        self.assertIsNone(raw["summary"])

    def wait_fixture(self) -> argparse.Namespace:
        path = self.root / "manifest.json"
        hpc.write_json(path, manifest())
        hpc.write_json(self.root / "submission.json",
                       {"job_id": "123", "manifest_sha256": hpc.sha256(path)})
        return argparse.Namespace(manifest=path, results_root=self.root,
                                  timeout_seconds=30, poll_seconds=5)

    def test_wait_pending_then_pass_emits_one_full_json(self) -> None:
        args = self.wait_fixture()
        attempt = self.evidence()
        record = hpc.load(attempt / "task.json")
        record["manifest_sha256"] = hpc.sha256(args.manifest)
        hpc.write_json(attempt / "task.json", record)
        responses = [subprocess.CompletedProcess([], 0, stdout=""),
                     subprocess.CompletedProcess([], 0, stdout="|".join(row().values()))]
        output = io.StringIO()
        with patch.object(hpc.subprocess, "run", side_effect=responses) as run:
            with patch.object(hpc.time, "sleep"), contextlib.redirect_stdout(output):
                self.assertEqual(hpc.wait(args), 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(summary["wait_status"], "terminal")
        self.assertEqual(summary["manifest_sha256"], hpc.sha256(args.manifest))
        self.assertEqual(len(summary["tasks"]), 1)
        self.assertEqual(len(list((self.root / "accounting").glob("*.json"))), 2)
        self.assertTrue(all(c.args[0][0] == "sacct" for c in run.call_args_list))

    def test_wait_terminal_failure_is_nonzero(self) -> None:
        args = self.wait_fixture()
        failed = row(status="FAILED", ExitCode="7:0")
        with patch.object(hpc.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout="|".join(failed.values()))):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(hpc.wait(args), 1)
        self.assertEqual(json.loads(output.getvalue())["tasks"][0]["exit_code"], "7:0")

    def test_wait_completed_job_rechecks_delayed_task_record(self) -> None:
        args = self.wait_fixture()
        attempt = self.evidence()
        record = hpc.load(attempt / "task.json")
        record["manifest_sha256"] = hpc.sha256(args.manifest)
        (attempt / "task.json").unlink()

        def publish_record(_seconds):
            self.assertEqual(hpc.load(self.root / "summary.json")["verdict"], "NOT_PASS")
            hpc.write_json(attempt / "task.json", record)

        with patch.object(hpc.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout="|".join(row().values()))) as run:
            with patch.object(hpc.time, "sleep", side_effect=publish_record) as sleep:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(hpc.wait(args), 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(summary["wait_status"], "terminal")
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(c.args[0][0] == "sacct" for c in run.call_args_list))
        sleep.assert_called_once_with(5)
        observations = [hpc.load(p)["summary"] for p in sorted((self.root / "accounting").glob("*.json"))]
        self.assertEqual([s["tasks"][0]["evidence"] for s in observations],
                         ["invalid-task-record", "valid"])

    def test_wait_completed_job_with_invalid_evidence_times_out(self) -> None:
        args = self.wait_fixture()
        self.evidence()  # Manifest hash deliberately does not match.
        with patch.object(hpc.time, "monotonic", side_effect=[0, 0, 1, 30]):
            with patch.object(hpc.time, "sleep") as sleep:
                with patch.object(hpc.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, stdout="|".join(row().values()))) as run:
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(hpc.wait(args), 1)
        summary = json.loads(output.getvalue())
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["verdict"], "NOT_PASS")
        self.assertEqual(summary["wait_status"], "timeout")
        self.assertEqual(summary["tasks"][0]["evidence"], "invalid-task-record")
        self.assertEqual(run.call_count, 1)
        sleep.assert_called_once_with(5)

    def test_wait_timeout_keeps_full_summary_and_does_not_cancel(self) -> None:
        args = self.wait_fixture()
        # Una consulta pendiente; el plazo se agota durante la espera.
        with patch.object(hpc.time, "monotonic", side_effect=[0, 0, 1, 30]):
            with patch.object(hpc.time, "sleep") as sleep:
                with patch.object(hpc.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, stdout="")) as run, contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(hpc.wait(args), 1)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["wait_status"], "timeout")
        self.assertEqual(summary["states"], {"MISSING": 1})
        self.assertEqual(summary["expected_tasks"], 1)
        self.assertEqual(run.call_count, 1)
        sleep.assert_called_once_with(5)

    def test_wait_query_failure_invalidates_green_and_emits_json(self) -> None:
        args = self.wait_fixture()
        hpc.write_json(self.root / "summary.json", {"verdict": "PASS"})
        with patch.object(hpc.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["sacct"])):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(hpc.wait(args), 2)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["wait_status"], "collection-error")
        self.assertEqual(summary["verdict"], "NOT_PASS")
        self.assertEqual(hpc.load(self.root / "summary.json"), summary)

    def test_wait_rejects_unbounded_or_aggressive_polling(self) -> None:
        args = self.wait_fixture()
        for key, value in (("timeout_seconds", 0), ("timeout_seconds", 7201),
                           ("poll_seconds", 1), ("poll_seconds", 301)):
            original = getattr(args, key)
            setattr(args, key, value)
            with self.assertRaises(ValueError):
                hpc.wait(args)
            setattr(args, key, original)

    def test_worker_preserves_process_exit_and_signal(self) -> None:
        m = manifest()
        m["config"]["results_root"] = (self.root / "external-results").as_posix()
        path = self.root / "manifest.json"
        hpc.write_json(path, m)
        for code, expected in ((7, 7), (-15, 143)):
            with patch.dict(os.environ, {"SLURM_JOB_ID": str(123 + expected),
                                        "SLURM_ARRAY_TASK_ID": "0"}):
                with patch.object(hpc, "validate_manifest", return_value=m["config"]):
                    with patch.object(hpc, "verify_snapshot",
                                      side_effect=subprocess.CalledProcessError(code, ["make"])):
                        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                            self.assertEqual(hpc.run_task(argparse.Namespace(
                                manifest=path, mode="regression")), expected)
            record_path = next((self.root / "external-results").glob(
                f"{123 + expected}/0/attempt-*/task.json"))
            record = hpc.load(record_path)
            self.assertEqual(record["process_returncode"], code)
            self.assertEqual(record["exit_code"], expected)
            self.assertEqual(record["status"], "FAILED")

    def test_worker_bind_seed_and_missing_evidence_failure(self) -> None:
        """Ejecuta el controlador con runtime simulado y examina los binds reales."""
        m = manifest()
        source = self.root / "source"
        source.mkdir()
        (source / "Makefile").write_text("# fixture", encoding="utf-8")
        m["source"]["files"] = hpc.inventory(source)
        m["source"]["digest"] = hpc.tree_digest(m["source"]["files"])
        image = self.root / "image.sif"
        image.write_bytes(b"test-image")
        m["config"]["image"] = image.as_posix()
        m["config"]["results_root"] = (self.root / "external-results").as_posix()
        # validate_config requiere rutas Linux: mock solo validación de rutas en Windows.
        path = self.root / "manifest.json"
        hpc.write_json(path, m)
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="fake-runtime\n")

        with patch.dict(os.environ, {"SLURM_JOB_ID": "123", "SLURM_ARRAY_TASK_ID": "0"}):
            with patch.object(hpc, "validate_manifest", return_value=m["config"]):
                with patch.object(hpc.shutil, "which", return_value="/bin/apptainer"):
                    with patch.object(hpc.subprocess, "run", side_effect=fake_run):
                        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                            self.assertEqual(hpc.run_task(argparse.Namespace(manifest=path, mode="regression")), 2)
        run_command = next(c for c in calls if "exec" in c)
        self.assertIn("--containall", run_command)
        self.assertIn("--cleanenv", run_command)
        self.assertIn("TB_SEED=1234", run_command)
        self.assertIn("CCACHE_DISABLE=1", run_command)
        self.assertTrue(any("external-results" in a and a.endswith(":/work/results") for a in run_command))
        records = list((self.root / "external-results").glob("123/0/attempt-*/task.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(hpc.load(records[0])["status"], "FAILED")

    def test_worker_success_preserves_separate_task_outputs(self) -> None:
        for mode in ("regression", "demo"):
            with self.subTest(mode=mode):
                root = self.root / mode
                root.mkdir()
                source = root / "source"
                source.mkdir()
                (source / "Makefile").write_text("# fixture", encoding="utf-8")
                m = manifest(2)
                m["mode"] = mode
                m["source"]["files"] = hpc.inventory(source)
                m["source"]["digest"] = hpc.tree_digest(m["source"]["files"])
                image = root / "image.sif"
                image.write_bytes(b"image")
                m["config"]["image"] = image.as_posix()
                m["config"]["image_sha256"] = hpc.sha256(image)
                m["config"]["results_root"] = (root / "results").as_posix()
                path = root / "manifest.json"
                hpc.write_json(path, m)
                builds = []

                def fake_run(command, **kwargs):
                    if "exec" in command:
                        results = Path(next(a[:-len(":/work/results")] for a in command
                                            if a.endswith(":/work/results")))
                        work = Path(next(a[:-len(":/work")] for a in command if a.endswith(":/work")))
                        if "make" in command or "--junitxml=/work/results/junit.xml" in command:
                            build = work / "sim_build"
                            build.mkdir(exist_ok=True)
                            builds.append(build)
                            names = ["junit.xml", "cocotb-verilator-counter-w8.xml"] if "pytest" in command else [
                                "cocotb-verilator-counter-w8.xml" if "SIM=verilator" in command else
                                "cocotb-icarus-counter-w8.xml"]
                            for name in names:
                                (results / name).write_text(XML, encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, stdout="fake-runtime")

                for task_id in range(2):
                    with patch.dict(os.environ, {"SLURM_JOB_ID": "123", "SLURM_ARRAY_TASK_ID": str(task_id),
                                                 "SLURM_CPUS_PER_TASK": "1", "SLURM_CPUS_ON_NODE": "2"}):
                        with patch.object(hpc, "validate_manifest", return_value=m["config"]):
                            with patch.object(hpc.shutil, "which", return_value="/bin/apptainer"):
                                with patch.object(hpc.subprocess, "run", side_effect=fake_run):
                                    with contextlib.redirect_stdout(io.StringIO()):
                                        self.assertEqual(hpc.run_task(argparse.Namespace(
                                            manifest=path, mode=mode)), 0)
                records = list((root / "results").glob("123/*/attempt-*/task.json"))
                self.assertEqual(len(records), 2)
                self.assertEqual(len(set(builds)), 2)
                self.assertTrue(all(hpc.load(p)["status"] == "COMPLETED" for p in records))
                self.assertTrue(all(hpc.load(p)["cpus_per_task"] == "1" for p in records))
                self.assertTrue(all(hpc.load(p)["allocated_cpus_on_node"] == "2" for p in records))
                result = hpc.summarize(m, [row(), row("123_1")], "123", hpc.sha256(path), root / "results")
                self.assertEqual(result["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()
