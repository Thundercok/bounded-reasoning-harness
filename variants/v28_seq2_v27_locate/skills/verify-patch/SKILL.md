---
name: verify-patch
description: Deterministic pre-submit verification gate (v6.3) that inspects git diff, checks AST syntax, symtable baseline-diff undefined names, real module import regressions, and per-file baseline-diff targeted pytest.
---

# Verify Patch Skill (v6.3)

Run `scripts/run_verify.py` via `run_skill_script(skill_name="verify-patch", file_path="scripts/run_verify.py")` before calling `submit_patch`.

Verdicts:
- `HARD_FAIL` (`ok: false`): Empty diff, protected/untracked files, `SyntaxError`, newly introduced undefined name (`NameError`), or broken module/test import (`ImportError`/`NameError` during collection). Fix before submitting.
- `STATIC_OK_CHECK_TESTS` (`ok: true`): Static & import checks passed, but an existing test failed. Keep your fix if the failed test asserted old behavior intentionally changed by the issue; fix once if it is an unintended collateral regression.
- `PASS` / `UNVERIFIED_PYTEST` (`ok: true`): Static & import checks passed (and existing pytest passed or was skipped). Confirm one live inline repro of the issue passes, then call `submit_patch`.
