---
name: verify-patch
description: Deterministic pre-submit verification gate that inspects git diff, checks Python syntax and protected files, auto-discovers matching real test files in tests/, runs per-file baseline-diff targeted pytest (filtering pre-existing sandbox failures), and outputs a clear PASS / UNVERIFIED / FAIL verdict.
---

# Verify Patch Skill

Run `scripts/run_verify.py` via `run_skill_script(skill_name="verify-patch", file_path="scripts/run_verify.py")` after a source edit and before `submit_patch`.

Verdicts:
- `PASS`: Static checks + targeted pytest passed with zero new regressions.
- `UNVERIFIED`: Static checks passed, but existing pytest could not verify the change (`ENV_SKIPPED` / `TIMEOUT` / `NO_MATCHING_TESTS`). You MUST pass a real inline `python3` repro before submitting.
- `FAIL`: Syntax error, protected/untracked file, empty diff, or a CONFIRMED NEW REGRESSION in targeted pytest. Never submit on `FAIL`.
