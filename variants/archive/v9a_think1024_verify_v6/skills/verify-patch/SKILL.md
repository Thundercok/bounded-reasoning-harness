---
name: verify-patch
description: Deterministic pre-submit verification gate that inspects git diff, checks Python syntax and protected files, auto-discovers matching real test files in tests/, runs baseline-diff targeted pytest (filtering pre-existing sandbox failures), and outputs a clear PASS/FAIL verdict.
---

# Verify Patch Skill

Run `scripts/run_verify.py` via `run_skill_script(skill_name="verify-patch", file_path="scripts/run_verify.py")` after a source edit and right before `submit_patch`.

It deterministically verifies:
1. Non-empty diff on source files (no protected test/config files modified, no stray untracked files).
2. Zero Python syntax errors (`ast.parse`) across modified `.py` files (with line context & reset command if broken).
3. Auto-discovers real `tests/` files matching your modified source files/symbols (skipping unrelated tutorial/CLI tests).
4. Runs baseline-diff targeted `pytest`: filters out pre-existing sandbox failures so `FAIL` only triggers on real regressions introduced by your patch.
