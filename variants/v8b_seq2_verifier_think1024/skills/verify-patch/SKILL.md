---
name: verify-patch
description: Deterministic pre-submit verification gate that inspects git diff, checks Python syntax and protected files, auto-discovers matching real test files in tests/, runs targeted pytest, and outputs a clear PASS/FAIL verdict.
---

# Verify Patch Skill

Run `scripts/run_verify.py` via `run_skill_script(skill_name="verify-patch", file_path="scripts/run_verify.py")` after every source edit and right before `submit_patch`.

It deterministically verifies:
1. Non-empty diff on source files (no protected test/config files modified, no stray untracked files).
2. Zero Python syntax errors (`ast.parse`) across modified `.py` files.
3. Auto-discovers the real existing `tests/` files matching your modified source files/symbols (never guesses non-existent paths).
4. Runs targeted `pytest -x -q --tb=short` on those matched test files and reports `VERDICT: PASS` or `VERDICT: FAIL` with the exact traceback.
