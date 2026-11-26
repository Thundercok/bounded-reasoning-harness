#!/usr/bin/env python3
"""Re-grade Colab patches locally (gold test_patch in clean checkout) and compare against v14.

usage: regrade_results.py <results_dir> [<variant> ...] [--out graded.json]
  <results_dir>/<variant>/per_task.jsonl and <results_dir>/<variant>/patches/<id>.diff
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_m1_eval as L  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
HELDOUT24 = set(json.load(open(REPO / "data" / "heldout24_seed0.json")))

# v14 baseline passes (dev16 from colab_v13_v15, heldout24 from colab_heldout24_4arms)
V14_PASS = {
    "fastapi_14301", "fastapi_14458", "fastapi_14463", "fastapi_14786", "requests_7309",
    "requests_7427", "rich_3006", "rich_3894", "rich_3905",
    "fastapi_14459", "fastapi_14605", "requests_7315", "requests_7505", "rich_4077",
}


def grade_variant(vdir: Path, tasks: dict) -> dict:
    rows = [json.loads(x) for x in (vdir / "per_task.jsonl").read_text().splitlines() if x.strip()]
    out = {}
    for r in rows:
        tid = r["id"]
        t = tasks[tid]
        diff = vdir / "patches" / f"{tid}.diff"
        patch = diff.read_text() if diff.is_file() else ""
        passed, rc = False, None
        if patch.strip():
            cache = L.ensure_repo_cache(t["repo"])
            with tempfile.TemporaryDirectory() as td:
                ws = Path(td) / "ws"
                L.checkout_task_workspace(cache, t["base_commit"], ws)
                venv = L.ensure_repo_venv(t["repo"], ws)
            passed, rc, _ = L.grade_patch(cache, venv, t, patch)
        out[tid] = dict(
            split="heldout24" if tid in HELDOUT24 else "dev16", true_pass=bool(passed),
            colab_resolved=r.get("resolved"), calls=r.get("tool_calls"), sec=r.get("agent_seconds"),
            patch_chars=len(patch), err=r.get("error"), rc=rc,
        )
        print(f"  {vdir.name:<28} {tid:<16} pass={passed} colab={r.get('resolved')} "
              f"calls={r.get('tool_calls')} sec={r.get('agent_seconds')}", flush=True)
    return out


def leaderboard_row(name: str, per: dict) -> dict:
    def frac(sel):
        n = len(sel)
        k = sum(per[t]["true_pass"] for t in sel)
        return f"{k}/{n} ({100 * k / n:.1f}%)" if n else "0/0"
    ids = list(per)
    secs = sorted(per[t]["sec"] for t in ids if per[t]["sec"] is not None)
    calls = [per[t]["calls"] for t in ids if per[t]["calls"] is not None]
    passes = {t for t in ids if per[t]["true_pass"]}
    return {
        "variant": name,
        "combined_40": frac(ids),
        "heldout_24": frac([t for t in ids if per[t]["split"] == "heldout24"]),
        "dev_16": frac([t for t in ids if per[t]["split"] == "dev16"]),
        "empty_40": sum(per[t]["patch_chars"] == 0 for t in ids),
        "timeouts_40": sum("timeout" in str(per[t]["err"] or "").lower() for t in ids),
        "mean_calls": round(sum(calls) / len(calls), 1) if calls else None,
        "median_s": secs[len(secs) // 2] if secs else None,
        "gained_vs_v14": sorted(passes - V14_PASS),
        "lost_vs_v14": sorted(V14_PASS & set(ids) - passes),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results_dir", type=Path)
    ap.add_argument("variants", nargs="*")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    tasks = {r["instance_id"]: r for r in map(json.loads, L.TASKS_PATH.read_text().splitlines()) if r}
    names = a.variants or sorted(p.name for p in a.results_dir.iterdir() if (p / "per_task.jsonl").is_file())
    per_task = {n: grade_variant(a.results_dir / n, tasks) for n in names}
    board = [leaderboard_row(n, per_task[n]) for n in names]
    out = a.out or (a.results_dir / "true_graded.json")
    out.write_text(json.dumps({"leaderboard": board, "per_task": per_task}, indent=1))
    print(json.dumps(board, indent=1))
    print(f"v14 reference: 14/40 (35.0%) | heldout 5/24 | dev 9/16  -> wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
