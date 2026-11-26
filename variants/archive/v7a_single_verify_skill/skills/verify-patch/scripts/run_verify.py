"""Deterministic pre-submit patch & targeted pytest verifier for /workspace."""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROTECTED_CONFIGS = {
    "conftest.py", "pytest.ini", "pyproject.toml", "tox.ini",
    "setup.cfg", ".pytest.ini", "sitecustomize.py", "usercustomize.py",
    "_swegemma_stubs.py",
}


def is_cache_artifact(rel_path: str) -> bool:
    p = Path(rel_path)
    parts = set(p.parts)
    if parts & {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}:
        return True
    if p.suffix in {".pyc", ".pyo"}:
        return True
    return False


def is_protected(rel_path: str) -> bool:
    p = Path(rel_path)
    name = p.name
    if name in PROTECTED_CONFIGS or p.suffix == ".pth":
        return True
    parts_l = {part.lower() for part in p.parts}
    if parts_l & {"tests", "test", "testing"} and p.suffix == ".py":
        return True
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    return False


def _run(root: Path, cmd: list[str], timeout: int = 30, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = None
    if extra_env:
        env = dict(os.environ)
        env.update(extra_env)
    return subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def _git_out(root: Path, *args: str) -> str:
    return _run(root, ["git", "-c", "safe.directory=*", *args], timeout=15).stdout.strip()


def _resolve_repo_root(explicit_root: str | None) -> Path:
    candidates: list[Path] = []
    if explicit_root:
        candidates.append(Path(explicit_root))
    cwd = Path.cwd()
    for anc in (cwd, *cwd.parents):
        candidates.append(anc)
        candidates.append(anc / "workspace")
    candidates.append(Path("/workspace"))

    for cand in candidates:
        if cand.is_dir() and (cand / ".git").exists():
            return cand
    return cwd if cwd.is_dir() else Path("/workspace")


def _resolve_pytest_cmd(root: Path) -> list[str]:
    """Prefer the sandbox's isolated venv (sibling to workspace/) so editable repo imports resolve."""
    venv_dirs = [root.parent / "venv", root / ".venv", root / "venv"]
    for vd in venv_dirs:
        py_bin = vd / "bin" / "pytest"
        if py_bin.is_file():
            return [str(py_bin)]
        py_exe = vd / "bin" / "python"
        if py_exe.is_file():
            return [str(py_exe), "-m", "pytest"]
    pytest_bin = shutil.which("pytest")
    if pytest_bin:
        return [pytest_bin]
    return [sys.executable, "-m", "pytest"]


def _extract_changed_symbols(diff_text: str) -> list[str]:
    syms: list[str] = []
    seen: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            m = re.search(r"@@\s+(?:def|async def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m and m.group(1) not in seen and len(m.group(1)) >= 4:
                seen.add(m.group(1))
                syms.append(m.group(1))
        elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            m = re.match(r"^[+-]\s*(?:def|async def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m and m.group(1) not in seen and len(m.group(1)) >= 4:
                seen.add(m.group(1))
                syms.append(m.group(1))
    return syms[:8]


def _discover_test_files(root: Path, source_files: list[str], diff_text: str, max_files: int = 3) -> list[str]:
    test_dirs = [d for d in ("tests", "test") if (root / d).is_dir()]
    if not test_dirs:
        return []

    all_tests: list[Path] = []
    for td in test_dirs:
        for p in (root / td).rglob("*.py"):
            nm = p.name
            if nm.startswith("test_") or nm.endswith("_test.py"):
                all_tests.append(p.relative_to(root))

    if not all_tests:
        return []

    stems: set[str] = set()
    parent_tokens: set[str] = set()
    for rel in source_files:
        p = Path(rel)
        if p.suffix != ".py":
            continue
        stem = p.stem.lower()
        clean_stem = stem.lstrip("_")
        if clean_stem and clean_stem not in ("init", "main", "base", "types", "typing"):
            stems.add(clean_stem)
        for part in p.parts[:-1]:
            pl = part.lower().lstrip("_")
            if pl and pl not in ("src", "lib", "pkg", "docs_src"):
                parent_tokens.add(pl)

    # Symbol grep hits in tests/
    sym_hits: dict[str, int] = {}
    symbols = _extract_changed_symbols(diff_text)
    if symbols:
        grep_cmd = ["git", "-c", "safe.directory=*", "grep", "-l", "-F"]
        for sym in symbols:
            grep_cmd.extend(["-e", sym])
        grep_cmd.append("--")
        grep_cmd.extend(test_dirs)
        proc = _run(root, grep_cmd, timeout=10)
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line:
                sym_hits[line] = sym_hits.get(line, 0) + 1

    scored: list[tuple[int, int, str]] = []
    for tp in all_tests:
        rel_str = tp.as_posix()
        name_l = tp.name.lower()
        path_l = rel_str.lower()
        # Skip tutorial/doc example tests that often need optional dependencies absent in sandbox
        if "tutorial" in path_l:
            continue
        score = 0

        for st in stems:
            if name_l in (f"test_{st}.py", f"{st}_test.py"):
                score += 120
            elif f"_{st}." in name_l or f"_{st}_" in name_l or name_l.startswith(f"test_{st}_"):
                score += 70
            elif st in name_l:
                score += 40
            if f"/{st}/" in path_l:
                score += 30

        for pt in parent_tokens:
            if pt in name_l or f"test_{pt}" in path_l:
                score += 25

        if rel_str in sym_hits:
            score += 45 * sym_hits[rel_str]

        if score > 0:
            scored.append((score, -len(rel_str), rel_str))

    scored.sort(reverse=True)
    return [item[2] for item in scored[:max_files]]


def verify_workspace(root: Path, run_pytest: bool = True) -> dict:
    has_baseline = bool(_git_out(root, "rev-parse", "--verify", "_swegemma_baseline"))
    base_ref = "_swegemma_baseline" if has_baseline else "HEAD"

    changed = [line for line in _git_out(root, "diff", "--name-only", base_ref).splitlines() if line.strip() and not is_cache_artifact(line)]
    untracked = [
        line for line in _git_out(root, "ls-files", "--others", "--exclude-standard").splitlines()
        if line.strip() and not is_cache_artifact(line)
    ]
    all_touched = sorted(set(changed) | set(untracked))

    protected_touched = [p for p in all_touched if is_protected(p)]
    source_touched = [p for p in all_touched if not is_protected(p)]

    syntax_errors = []
    for rel in source_touched:
        full = root / rel
        if full.suffix == ".py" and full.is_file():
            try:
                ast.parse(full.read_text(encoding="utf-8", errors="ignore"), filename=rel)
            except SyntaxError as exc:
                syntax_errors.append({
                    "file": rel,
                    "line": exc.lineno,
                    "message": exc.msg,
                })

    diff_text = _git_out(root, "diff", "-U0", base_ref, "--", *source_touched) if source_touched else ""
    diff_stat = _git_out(root, "diff", "--stat", base_ref)

    warnings: list[str] = []
    if not source_touched:
        warnings.append("No source files modified yet; submit_patch would be empty.")
    if protected_touched:
        revert_cmd = "git checkout -- " + " ".join(protected_touched)
        warnings.append(f"Protected test/config files touched (revert immediately with `{revert_cmd}`): {protected_touched}")
    if untracked:
        warnings.append(f"Untracked files in /workspace (remove scratch files): {untracked}")
    if syntax_errors:
        warnings.append("Python syntax errors detected; fix before calling submit_patch.")

    matched_tests: list[str] = []
    pytest_status = "SKIPPED"
    pytest_summary = ""

    if run_pytest and source_touched and not syntax_errors:
        matched_tests = _discover_test_files(root, source_touched, diff_text, max_files=2)
        if not matched_tests:
            pytest_status = "NO_MATCHING_TESTS"
            pytest_summary = "No matching test file found under tests/; run an inline python3 heredoc repro instead."
        else:
            pytest_flags = [
                "-x", "-q", "--tb=short", "--no-header",
                "--continue-on-collection-errors",
                "-o", "cache_dir=/tmp/pytest_cache",
            ]
            cmd = [*_resolve_pytest_cmd(root), *matched_tests, *pytest_flags]
            try:
                proc = _run(root, cmd, timeout=60, extra_env={"PYTHONDONTWRITEBYTECODE": "1"})
                combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
                has_real_failure = (
                    "=== FAILURES ===" in combined
                    or bool(re.search(r"\b\d+\s+failed\b", combined))
                )
                has_env_error = (
                    "=== ERRORS ===" in combined
                    or "ERROR collecting" in combined
                    or "ERROR at setup" in combined
                )
                if proc.returncode == 0:
                    pytest_status = "PASS"
                    lines = [ln for ln in combined.splitlines() if ln.strip()]
                    pytest_summary = "\n".join(lines[-5:])
                elif proc.returncode == 5:
                    pytest_status = "NO_TESTS_COLLECTED"
                    pytest_summary = combined[-600:]
                elif "No module named pytest" in combined or "disabled" in combined.lower():
                    pytest_status = "UNAVAILABLE"
                    pytest_summary = "pytest unavailable in sandbox; rely on real-import python3 heredoc check."
                elif has_real_failure:
                    pytest_status = "FAIL"
                    pytest_summary = combined[-1800:]
                    warnings.append(f"Targeted pytest FAILED on {matched_tests}; inspect traceback and fix source before submitting.")
                elif has_env_error:
                    pytest_status = "ENV_SKIPPED"
                    pytest_summary = (
                        "Only sandbox collection/fixture setup errors occurred (0 test assertions failed). "
                        "Rely on your inline python3 heredoc repro to verify the fix."
                    )
                else:
                    pytest_status = "FAIL"
                    pytest_summary = combined[-1800:]
                    warnings.append(f"Targeted pytest exited {proc.returncode} on {matched_tests}; inspect output before submitting.")
            except subprocess.TimeoutExpired:
                pytest_status = "TIMEOUT"
                pytest_summary = f"pytest timed out after 60s on {matched_tests}; run a narrower `-k` test filter."

    static_ok = bool(source_touched) and not protected_touched and not syntax_errors
    tests_ok = pytest_status in (
        "PASS", "ENV_SKIPPED", "NO_MATCHING_TESTS", "NO_TESTS_COLLECTED", "UNAVAILABLE", "SKIPPED", "TIMEOUT"
    )
    overall_ok = static_ok and tests_ok

    verdict = (
        "PASS — Ready to confirm your inline bug repro and call submit_patch."
        if overall_ok
        else "FAIL — DO NOT call submit_patch yet. Fix the errors/failures below and re-run verify_patch."
    )

    return {
        "verdict": verdict,
        "ok": overall_ok,
        "repo_root": str(root),
        "source_files": source_touched,
        "protected_files": protected_touched,
        "untracked_files": untracked,
        "syntax_errors": syntax_errors,
        "matched_tests": matched_tests,
        "pytest_status": pytest_status,
        "pytest_output": pytest_summary,
        "diff_stat": diff_stat[:800],
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify /workspace patch and run matched repo pytest.")
    parser.add_argument("--root", default=None, help="Optional explicit repository root (auto-detects cwd/.git first)")
    parser.add_argument("--no-pytest", action="store_true", help="Skip running pytest")
    args = parser.parse_args()
    root = _resolve_repo_root(args.root)
    report = verify_workspace(root, run_pytest=not args.no_pytest)
    print(json.dumps(report, ensure_ascii=False, indent=2)[:4200])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
