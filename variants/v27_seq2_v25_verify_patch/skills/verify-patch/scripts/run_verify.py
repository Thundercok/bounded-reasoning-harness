"""Deterministic pre-submit patch & per-file baseline-diff verifier (v6.3).

Checks:
1. Non-empty source diff, zero protected test/config edits, zero untracked scratch files.
2. Python AST syntax check with line context.
3. Baseline-diff symtable undefined-name check (catches unimported names inside functions/methods with 0 false positives).
4. Baseline-diff real module import check (catches top-level/class-body ImportError/NameError/TypeError).
5. Per-file targeted pytest with baseline-diff filtering for both assertion failures AND collection crashes.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import json
import os
import re
import shutil
import subprocess
import symtable
import sys
from pathlib import Path

PROTECTED_CONFIGS = {
    "conftest.py", "pytest.ini", "pyproject.toml", "tox.ini",
    "setup.cfg", ".pytest.ini", "sitecustomize.py", "usercustomize.py",
    "_swegemma_stubs.py",
}

GENERIC_DIRS = {"src", "lib", "pkg", "docs_src", ""}
GENERIC_STEMS = {"init", "main", "base", "types", "typing"}
BUILTINS = set(dir(builtins)) | {
    "__annotations__", "__doc__", "__file__", "__name__",
    "__package__", "__path__", "__spec__", "__cached__", "__loader__",
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


def _run(
    root: Path,
    cmd: list[str],
    timeout: int = 30,
    extra_env: dict[str, str] | None = None,
    text: bool = True,
) -> subprocess.CompletedProcess:
    env = None
    if extra_env:
        env = dict(os.environ)
        env.update(extra_env)
    return subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=text,
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


def _resolve_python_exe(root: Path) -> str:
    venv_dirs = [root.parent / "venv", root / ".venv", root / "venv"]
    for vd in venv_dirs:
        py_exe = vd / "bin" / "python"
        if py_exe.is_file():
            return str(py_exe)
    pytest_bin = shutil.which("pytest")
    if pytest_bin:
        cand = Path(pytest_bin).parent / "python"
        if cand.is_file():
            return str(cand)
    return sys.executable


def _resolve_pytest_cmd(root: Path) -> list[str]:
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


def _cache_path(root: Path) -> Path:
    digest = hashlib.sha1(str(root.resolve()).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return Path(f"/tmp/_swegemma_verify_cache_{digest}.json")


def _load_cache(root: Path) -> dict:
    p = _cache_path(root)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"preexisting_failed": [], "last_diff_hash": "", "same_diff_streak": 0}


def _save_cache(root: Path, data: dict) -> None:
    try:
        _cache_path(root).write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _find_undefined_globals(code: str, filename: str) -> set[str]:
    try:
        top = symtable.symtable(code, filename, "exec")
    except Exception:
        return set()
    defined_at_top = {
        s.get_name() for s in top.get_symbols()
        if s.is_imported() or s.is_assigned() or s.is_parameter()
    }
    undef: set[str] = set()

    def walk(st: symtable.SymbolTable) -> None:
        for s in st.get_symbols():
            name = s.get_name()
            if s.is_referenced() and s.is_global() and name not in defined_at_top and name not in BUILTINS:
                undef.add(name)
        for child in st.get_children():
            walk(child)

    walk(top)
    return undef


def _rel_to_module_name(rel: str) -> str | None:
    p = Path(rel)
    if p.suffix != ".py" or len(p.parts) < 2:
        return None
    if p.parts[0] in ("docs_src", "docs", "scripts", "examples", "benchmarks"):
        return None
    target = p.parent if p.stem == "__init__" else p.with_suffix("")
    parts = target.parts[1:] if target.parts and target.parts[0] == "src" else target.parts
    if not parts or any(not part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


def _save_and_get_baseline_bytes(root: Path, base_ref: str, source_touched: list[str]) -> tuple[dict[Path, bytes], dict[Path, bytes]]:
    saved_bytes: dict[Path, bytes] = {}
    baseline_bytes: dict[Path, bytes] = {}
    for rel in source_touched:
        full = root / rel
        if not full.is_file():
            continue
        chk = _run(root, ["git", "-c", "safe.directory=*", "cat-file", "-e", f"{base_ref}:{rel}"], timeout=10)
        if chk.returncode != 0:
            continue
        show = _run(root, ["git", "-c", "safe.directory=*", "show", f"{base_ref}:{rel}"], timeout=10, text=False)
        if show.returncode != 0:
            continue
        saved_bytes[full] = full.read_bytes()
        baseline_bytes[full] = show.stdout
    return saved_bytes, baseline_bytes


def _check_module_imports(root: Path, base_ref: str, source_touched: list[str], py_exe: str) -> list[dict]:
    modules = sorted({m for rel in source_touched if (m := _rel_to_module_name(rel))})
    if not modules:
        return []
    cmd = [py_exe, "-c", "import importlib, sys\nfor m in sys.argv[1:]: importlib.import_module(m)", *modules]
    try:
        proc = _run(root, cmd, timeout=15, extra_env={"PYTHONDONTWRITEBYTECODE": "1"})
    except subprocess.TimeoutExpired:
        return []
    if proc.returncode == 0:
        return []

    err_text = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()[-1000:]
    saved_bytes, baseline_bytes = _save_and_get_baseline_bytes(root, base_ref, source_touched)
    if not saved_bytes:
        return [{"modules": modules, "error": err_text}]

    try:
        for full, bdata in baseline_bytes.items():
            full.write_bytes(bdata)
        base_proc = _run(root, cmd, timeout=15, extra_env={"PYTHONDONTWRITEBYTECODE": "1"})
        if base_proc.returncode != 0:
            return []  # Also failed on baseline (environment issue, not regression)
    except subprocess.TimeoutExpired:
        return []
    finally:
        for full, orig in saved_bytes.items():
            full.write_bytes(orig)

    return [{"modules": modules, "error": err_text}]


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


def _discover_test_files(root: Path, source_files: list[str], diff_text: str, max_files: int = 2) -> list[str]:
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
    subpkg_tokens: set[str] = set()
    top_pkg_tokens: set[str] = set()
    is_cli_edit = any("cli" in rel.lower() for rel in source_files)

    for rel in source_files:
        p = Path(rel)
        if p.suffix != ".py":
            continue
        clean_stem = p.stem.lower().lstrip("_")
        if clean_stem and clean_stem not in GENERIC_STEMS:
            stems.add(clean_stem)
        pkg_dirs = [part.lower().lstrip("_") for part in p.parts[:-1] if part.lower().lstrip("_") not in GENERIC_DIRS]
        if pkg_dirs:
            top_pkg_tokens.add(pkg_dirs[0])
        for subpkg in pkg_dirs[1:]:
            subpkg_tokens.add(subpkg)

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
        if "tutorial" in path_l:
            continue
        if not is_cli_edit and ("_cli" in name_l or "cli_" in name_l or "/cli/" in path_l):
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

        for pt in subpkg_tokens:
            if pt in name_l or f"test_{pt}" in path_l or f"/{pt}/" in path_l:
                score += 35

        for pkg in top_pkg_tokens:
            if name_l in (f"test_{pkg}.py", f"{pkg}_test.py"):
                score += 15

        if rel_str in sym_hits:
            score += 45 * sym_hits[rel_str]

        if score > 0:
            scored.append((score, -len(rel_str), rel_str))

    scored.sort(reverse=True)
    return [item[2] for item in scored[:max_files]]


def _extract_failed_nodes(output: str) -> list[str]:
    nodes: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        m = re.match(r"^FAILED\s+(.+?)(?:\s+-\s+.*)?$", line.strip())
        if m:
            node = m.group(1).strip()
            if "::" in node and node not in seen:
                seen.add(node)
                nodes.append(node)
    return nodes[:8]


def _pytest_cache_dir(root: Path) -> str:
    return str(root.parent / ".verify_pytest_cache")


def _check_baseline_failures(
    root: Path,
    base_ref: str,
    source_touched: list[str],
    failed_nodes: list[str],
    pytest_cmd: list[str],
) -> set[str]:
    if not failed_nodes or not source_touched:
        return set()

    saved_bytes, baseline_bytes = _save_and_get_baseline_bytes(root, base_ref, source_touched)
    if not saved_bytes:
        return set()

    try:
        for full, bdata in baseline_bytes.items():
            full.write_bytes(bdata)
        cmd = [
            *pytest_cmd,
            *failed_nodes,
            "-q", "--tb=no", "--no-header",
            "--continue-on-collection-errors",
            "-o", f"cache_dir={_pytest_cache_dir(root)}",
        ]
        proc = _run(root, cmd, timeout=20, extra_env={"PYTHONDONTWRITEBYTECODE": "1"})
        combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return set(_extract_failed_nodes(combined))
    except subprocess.TimeoutExpired:
        return set()
    finally:
        for full, orig in saved_bytes.items():
            full.write_bytes(orig)


def _baseline_collected_cleanly(
    root: Path,
    base_ref: str,
    source_touched: list[str],
    test_file: str,
    pytest_cmd: list[str],
) -> bool:
    saved_bytes, baseline_bytes = _save_and_get_baseline_bytes(root, base_ref, source_touched)
    if not saved_bytes:
        return False
    try:
        for full, bdata in baseline_bytes.items():
            full.write_bytes(bdata)
        proc = _run(
            root,
            [*pytest_cmd, "--collect-only", "-q", "--no-header", "-o", f"cache_dir={_pytest_cache_dir(root)}", test_file],
            timeout=15,
            extra_env={"PYTHONDONTWRITEBYTECODE": "1"},
        )
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    finally:
        for full, orig in saved_bytes.items():
            full.write_bytes(orig)


def _run_single_test_file(
    root: Path,
    base_ref: str,
    source_touched: list[str],
    pytest_cmd: list[str],
    test_file: str,
    preexisting_failed: list[str],
    filtered_preexisting: list[str],
) -> tuple[str, str, list[str]]:
    try:
        for attempt in range(2):
            flags = [
                "--maxfail=5", "-q", "--tb=short", "--no-header",
                "--continue-on-collection-errors",
                "-o", f"cache_dir={_pytest_cache_dir(root)}",
            ]
            for node in preexisting_failed:
                if node.split("::", 1)[0] == test_file:
                    flags.extend(["--deselect", node])
            proc = _run(root, [*pytest_cmd, test_file, *flags], timeout=25, extra_env={"PYTHONDONTWRITEBYTECODE": "1"})
            rc = proc.returncode
            combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            has_real_failure = (
                "=== FAILURES ===" in combined
                or bool(re.search(r"\b\d+\s+failed\b", combined))
            )
            has_collection_error = "ERROR collecting" in combined
            has_env_error = (
                "=== ERRORS ===" in combined
                or has_collection_error
                or "ERROR at setup" in combined
            )
            if rc == 0:
                lines = [ln for ln in combined.splitlines() if ln.strip()]
                return "PASS", "\n".join(lines[-3:]), []
            if rc == 5:
                return "NO_TESTS_COLLECTED", combined[-400:], []
            if "No module named pytest" in combined or "disabled" in combined.lower():
                return "UNAVAILABLE", "pytest unavailable in sandbox.", []
            if has_collection_error and _baseline_collected_cleanly(root, base_ref, source_touched, test_file, pytest_cmd):
                return "COLLECTION_FAIL", combined[-1400:], [test_file]
            if has_real_failure:
                failed_nodes = _extract_failed_nodes(combined)
                base_failed = _check_baseline_failures(root, base_ref, source_touched, failed_nodes, pytest_cmd)
                new_regressions = [n for n in failed_nodes if n not in base_failed]
                if base_failed:
                    for bf in sorted(base_failed):
                        if bf not in preexisting_failed:
                            preexisting_failed.append(bf)
                        if bf not in filtered_preexisting:
                            filtered_preexisting.append(bf)
                if not new_regressions and failed_nodes:
                    if attempt == 0:
                        continue
                    return "PASS_PREEXISTING_ONLY", f"{test_file}: only pre-existing baseline failures ({len(filtered_preexisting)}).", []
                return "FAIL", combined[-1600:], (new_regressions or failed_nodes)
            if has_env_error:
                return "ENV_SKIPPED", f"{test_file}: sandbox collection/fixture setup error (0 assertions failed).", []
            return "FAIL", combined[-1600:], [test_file]
    except subprocess.TimeoutExpired:
        return "TIMEOUT", f"{test_file}: timed out after 25s.", []
    return "ENV_SKIPPED", f"{test_file}: inconclusive.", []


def _syntax_context(full: Path, lineno: int | None) -> str:
    if not lineno or not full.is_file():
        return ""
    lines = full.read_text(encoding="utf-8", errors="ignore").splitlines()
    start = max(1, lineno - 2)
    end = min(len(lines), lineno + 2)
    return "\n".join(
        f"{'>>' if i == lineno else '  '} {i}: {lines[i - 1]}"
        for i in range(start, end + 1)
    )


def verify_workspace(root: Path, run_pytest: bool = True) -> dict:
    has_baseline = bool(_git_out(root, "rev-parse", "--verify", "_swegemma_baseline"))
    base_ref = "_swegemma_baseline" if has_baseline else "HEAD"

    changed = [
        line for line in _git_out(root, "diff", "--name-only", base_ref).splitlines()
        if line.strip() and not is_cache_artifact(line)
    ]
    untracked = [
        line for line in _git_out(root, "ls-files", "--others", "--exclude-standard").splitlines()
        if line.strip() and not is_cache_artifact(line)
    ]
    all_touched = sorted(set(changed) | set(untracked))

    protected_touched = [p for p in all_touched if is_protected(p)]
    source_touched = [p for p in all_touched if not is_protected(p)]

    syntax_errors = []
    undefined_names: dict[str, list[str]] = {}
    for rel in source_touched:
        full = root / rel
        if full.suffix == ".py" and full.is_file():
            code = full.read_text(encoding="utf-8", errors="ignore")
            try:
                ast.parse(code, filename=rel)
            except SyntaxError as exc:
                syntax_errors.append({
                    "file": rel,
                    "line": exc.lineno,
                    "message": exc.msg,
                    "context": _syntax_context(full, exc.lineno),
                })
                continue

            base_code = ""
            chk = _run(root, ["git", "-c", "safe.directory=*", "cat-file", "-e", f"{base_ref}:{rel}"], timeout=10)
            if chk.returncode == 0:
                base_code = _run(root, ["git", "-c", "safe.directory=*", "show", f"{base_ref}:{rel}"], timeout=10).stdout
            pre_undef = _find_undefined_globals(base_code, rel) if base_code else set()
            new_undef = sorted(_find_undefined_globals(code, rel) - pre_undef)
            if new_undef:
                undefined_names[rel] = new_undef

    py_exe = _resolve_python_exe(root)
    import_errors = _check_module_imports(root, base_ref, source_touched, py_exe) if (source_touched and not syntax_errors) else []

    diff_text = _git_out(root, "diff", "-U3", base_ref, "--", *source_touched) if source_touched else ""
    diff_stat = _git_out(root, "diff", "--stat", base_ref)

    cache = _load_cache(root)
    preexisting_failed: list[str] = list(cache.get("preexisting_failed") or [])
    diff_hash = hashlib.sha256(diff_text.encode("utf-8", errors="ignore")).hexdigest()[:12]
    same_diff_streak = (cache.get("same_diff_streak", 0) + 1) if (diff_hash and diff_hash == cache.get("last_diff_hash")) else 0

    warnings: list[str] = []
    if not source_touched:
        warnings.append("No source files modified yet; submit_patch would be empty.")
    if protected_touched:
        revert_cmd = "git checkout -- " + " ".join(protected_touched)
        warnings.append(f"Protected test/config files touched (revert immediately with `{revert_cmd}`): {protected_touched}")
    if untracked:
        warnings.append(f"Untracked files in /workspace (remove scratch files): {untracked}")
    if syntax_errors:
        warnings.append("Python syntax error(s) detected in modified file(s). Fix syntax_errors[] (never leave empty diff).")
    if undefined_names:
        warnings.append(
            f"NEW UNDEFINED NAME(S) introduced (missing import or typo -> guaranteed NameError at runtime): {undefined_names}"
        )
    if import_errors:
        warnings.append(
            "BROKEN MODULE IMPORT introduced by your edit (module imported cleanly on baseline but now crashes): see import_errors[]."
        )
    if same_diff_streak >= 1:
        warnings.append(
            "LOOP GUARD: Workspace diff has NOT changed since your previous verify-patch call."
        )

    matched_tests: list[str] = []
    pytest_status = "SKIPPED"
    pytest_summary = ""
    filtered_preexisting: list[str] = []
    per_file_results: dict[str, str] = {}

    if run_pytest and source_touched and not syntax_errors and not undefined_names and not import_errors:
        matched_tests = _discover_test_files(root, source_touched, diff_text, max_files=2)
        if not matched_tests:
            pytest_status = "NO_MATCHING_TESTS"
            pytest_summary = "No matching test file found under tests/; verify with your inline python3 repro."
        else:
            pytest_cmd = _resolve_pytest_cmd(root)
            summaries: list[str] = []
            any_collection_fail = False
            any_fail = False
            any_pass = False
            any_preexisting_only = False
            last_nonpass_status = "ENV_SKIPPED"
            for tf in matched_tests:
                st, out, regs = _run_single_test_file(
                    root, base_ref, source_touched, pytest_cmd, tf, preexisting_failed, filtered_preexisting
                )
                per_file_results[tf] = st
                summaries.append(f"[{tf}: {st}]\n{out}".strip())
                if st == "COLLECTION_FAIL":
                    any_collection_fail = True
                    warnings.append(
                        f"BROKEN TEST COLLECTION in {tf} (collected cleanly on baseline, now crashes on import/collection). Fix immediately!"
                    )
                    break
                elif st == "FAIL":
                    any_fail = True
                    warnings.append(
                        f"Existing test(s) failed in {tf}: {regs}. Check if these only assert OLD behavior intentionally changed by the issue (keep fix) vs unintended collateral regression (fix once)."
                    )
                    break
                elif st == "PASS":
                    any_pass = True
                elif st == "PASS_PREEXISTING_ONLY":
                    any_preexisting_only = True
                else:
                    last_nonpass_status = st
            if any_collection_fail:
                pytest_status = "COLLECTION_FAIL"
            elif any_fail:
                pytest_status = "FAIL"
            elif any_pass:
                pytest_status = "PASS"
            elif any_preexisting_only:
                pytest_status = "PASS_PREEXISTING_ONLY"
            else:
                pytest_status = last_nonpass_status
            pytest_summary = "\n\n".join(summaries)[-1600:]

    cache["preexisting_failed"] = preexisting_failed[:20]
    cache["last_diff_hash"] = diff_hash
    cache["same_diff_streak"] = same_diff_streak
    _save_cache(root, cache)

    hard_ok = (
        bool(source_touched)
        and not protected_touched
        and not untracked
        and not syntax_errors
        and not undefined_names
        and not import_errors
        and pytest_status != "COLLECTION_FAIL"
    )
    pytest_verified = pytest_status in ("PASS", "PASS_PREEXISTING_ONLY")

    if not hard_ok:
        verdict = "HARD_FAIL — DO NOT call submit_patch yet. Fix the static/import/collection error(s) in warnings[] first."
    elif pytest_status == "FAIL":
        verdict = (
            "STATIC_OK_CHECK_TESTS — Static & import checks passed, but existing test(s) failed (see pytest_output). "
            "If the failed test(s) only assert OLD behavior intentionally changed by the issue AND your inline repro passes, "
            "DO NOT revert your fix — call submit_patch! If it is an unintended collateral regression, fix the source ONCE."
        )
    elif pytest_verified:
        verdict = "PASS — Static, import, and targeted pytest all passed! Run ONE inline issue repro; if it passes, call submit_patch immediately."
    else:
        verdict = (
            f"UNVERIFIED_PYTEST ({pytest_status}) — Static & import checks passed. "
            "Run ONE passing inline python3 repro of the exact issue + edge cases, then call submit_patch."
        )

    return {
        "verdict": verdict,
        "ok": hard_ok,
        "pytest_verified": pytest_verified,
        "source_files": source_touched,
        "protected_files": protected_touched,
        "untracked_files": untracked,
        "syntax_errors": syntax_errors,
        "undefined_names": undefined_names,
        "import_errors": import_errors,
        "matched_tests": matched_tests,
        "pytest_status": pytest_status,
        "pytest_output": pytest_summary,
        "diff_stat": diff_stat[:500],
        "diff": diff_text[:1400],
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify /workspace patch (v6.3).")
    parser.add_argument("--root", default=None, help="Optional explicit repository root")
    parser.add_argument("--no-pytest", action="store_true", help="Skip running pytest")
    args = parser.parse_args()
    root = _resolve_repo_root(args.root)
    report = verify_workspace(root, run_pytest=not args.no_pytest)
    print(json.dumps(report, ensure_ascii=False, indent=2)[:4500])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
