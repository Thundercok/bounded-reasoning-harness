#!/usr/bin/env python3
"""Explain why each patch in a Colab wave failed, by regrading it locally with the gold tests.

usage: analyze_failures.py <results_dir> <variant> [<variant> ...]
  reads  <results_dir>/<variant>/per_task.jsonl and patches/<id>.diff
  writes <results_dir>/failure_analysis.json and prints a per-variant summary

For every task it records:
  outcome   PASS | empty | apply_fail | import_broken | runtime_exc | wrong_logic
  location  same_region | same_file_other_region | wrong_file   (agent hunks vs gold hunks, source files only)
  tests     existing_test_fails | only_new_tests   (were the failing tests already in the repo at base_commit?)
  issue     short (<400 chars and no code) | informative
Only "existing_test_fails" failures can be caught by the agent before it submits.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_m1_eval as L  # noqa: E402

CRASH = r"^E\s+(NameError|AttributeError|TypeError|IndexError|KeyError|UnboundLocalError|ValueError)"


def source_hunks(patch: str) -> dict[str, list[tuple[int, int]]]:
    out: dict[str, list[tuple[int, int]]] = {}
    f = None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            f = line[4:].removeprefix("b/")
            out.setdefault(f, [])
        m = re.match(r"@@ -(\d+)(?:,(\d+))? ", line)
        if m and f:
            start = int(m[1])
            out[f].append((start, start + int(m[2] or 1)))
    return {k: v for k, v in out.items() if not re.search(r"(^|/)tests?/|test_", k)}


def location(gold: str, agent: str) -> str:
    g, a = source_hunks(gold), source_hunks(agent)
    shared = set(g) & set(a)
    if not shared:
        return "wrong_file"
    near = any(abs(x[0] - y[0]) < 40 or (x[0] <= y[1] and y[0] <= x[1]) for f in shared for x in g[f] for y in a[f])
    return "same_region" if near else "same_file_other_region"


def test_kind(cache: Path, base: str, out: str) -> str:
    kinds = []
    for nid in re.findall(r"^(?:FAILED|ERROR) (\S+?)(?: - |$)", out, re.M):
        path, _, name = nid.partition("::")
        name = name.split("::")[-1].split("[")[0]
        if not name:
            kinds.append("existing")  # collection error: the whole existing file fails
            continue
        rc = subprocess.run(["git", "-C", str(cache), "grep", "-q", "-E", rf"def {re.escape(name)}\b", base, "--", path]).returncode
        kinds.append("existing" if rc == 0 else "new")
    if not kinds:
        return "unknown"
    return "existing_test_fails" if "existing" in kinds else "only_new_tests"


def analyze(results: Path, variant: str, tasks: dict) -> dict:
    rows = {}
    for line in (results / variant / "per_task.jsonl").read_text().splitlines():
        tid = json.loads(line)["id"]
        t = tasks[tid]
        ps = t["problem_statement"]
        issue = "short" if len(ps) < 400 and not re.search(r"```|>>>|\bimport ", ps) else "informative"
        diff = results / variant / "patches" / f"{tid}.diff"
        patch = diff.read_text() if diff.is_file() else ""
        row = dict(outcome="empty", location=None, tests=None, issue=issue)
        if patch.strip():
            cache = L.ensure_repo_cache(t["repo"])
            with tempfile.TemporaryDirectory() as td:
                ws = Path(td) / "ws"
                L.checkout_task_workspace(cache, t["base_commit"], ws)
                venv = L.ensure_repo_venv(t["repo"], ws)
            ok, rc, out = L.grade_patch(cache, venv, t, patch)
            if ok:
                row["outcome"] = "PASS"
            elif rc in (-2, -3):
                row["outcome"] = "apply_fail"
            else:
                if "during collection" in out:
                    row["outcome"] = "import_broken"
                elif re.search(CRASH, out, re.M):
                    row["outcome"] = "runtime_exc"
                else:
                    row["outcome"] = "wrong_logic"
                row["tests"] = test_kind(cache, t["base_commit"], out)
            row["location"] = location(t["patch"], patch)
        rows[tid] = row
        print(f"  {variant:<26} {tid:<16} {row['outcome']:<13} {row['location'] or '-':<24} {row['tests'] or '-':<20} {issue}", flush=True)
    return rows


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    results = Path(sys.argv[1])
    tasks = {json.loads(x)["instance_id"]: json.loads(x) for x in L.TASKS_PATH.read_text().splitlines() if x.strip()}
    report = {v: analyze(results, v, tasks) for v in sys.argv[2:]}
    (results / "failure_analysis.json").write_text(json.dumps(report, indent=1))
    for v, rows in report.items():
        print(f"\n== {v}")
        for field in ("outcome", "location", "tests", "issue"):
            print(f"  {field:<9}", dict(Counter(r[field] for r in rows.values() if r[field])))
        short = [r for r in rows.values() if r["issue"] == "short"]
        print(f"  pass on short issues: {sum(r['outcome'] == 'PASS' for r in short)}/{len(short)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
