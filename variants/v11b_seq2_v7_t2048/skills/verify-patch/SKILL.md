---
name: verify-patch
description: Deterministic pre-submit verification gate that extracts related existing repo tests, checks /tmp/_repro.py against both baseline (must fail) and patched code (must pass), saves a proven incumbent patch, runs per-file baseline-diff pytest, and outputs PASS_PROVEN / PASS_TESTS_ONLY / UNVERIFIED / FAIL.
---

# Verify Patch Skill

Run `scripts/run_verify.py` via `run_skill_script(skill_name="verify-patch", file_path="scripts/run_verify.py")` after editing source code and before `submit_patch`.

It deterministically verifies:
1. Non-empty diff on source files (no protected test/config files, no syntax errors).
2. Extracts `related_test_examples` from `tests/` showing how the repo tests & mocks the modified symbols.
3. If `/tmp/_repro.py` exists, runs it on BOTH patched code (must PASS) AND `_swegemma_baseline` (checks it FAILED on baseline -> `PROVEN_FIX`), and saves `/tmp/_incumbent_best.patch`.
4. Runs per-file baseline-diff `pytest` (filtering pre-existing sandbox failures) so `FAIL` only triggers on real new regressions or failed `/tmp/_repro.py`.
