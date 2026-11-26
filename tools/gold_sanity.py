#!/usr/bin/env python3
"""Gold-patch sanity check for a task panel: every gold patch must pass the local grader.

usage: gold_sanity.py data/heldout24_seed0.json
"""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_m1_eval as L

ids = json.load(open(sys.argv[1]))
tasks = {r["instance_id"]: r for r in map(json.loads, L.TASKS_PATH.read_text().splitlines()) if r}
ok = 0
for tid in ids:
    t = tasks[tid]
    cache = L.ensure_repo_cache(t["repo"])
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "ws"
        L.checkout_task_workspace(cache, t["base_commit"], ws)
        venv = L.ensure_repo_venv(t["repo"], ws)
    passed, rc, msg = L.grade_patch(cache, venv, t, t["patch"])
    ok += passed
    print(f"{tid:<16} gold_pass={passed} rc={rc} {'' if passed else msg[-120:]}", flush=True)
print(f"GOLD {ok}/{len(ids)}")
