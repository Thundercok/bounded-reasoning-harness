"""Deterministic pre-submit patch, test-spec extractor, and baseline-diff verifier (v7)."""
from __future__ import annotations

import argparse
import ast
import hashlib
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

GENERIC_DIRS = {"src", "lib", "pkg", "docs_src", ""}
GENERIC_STEMS = {"init", "main", "base", "types", "typing"}


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
    for vd in (root.parent / "venv", root / ".venv", root / "venv"):
        py_exe = vd / "bin" / "python"
        if py_exe.is_file():
            return str(py_exe)
    return sys.executable


def _resolve_pytest_cmd(root: Path) -> list[str]:
    """Prefer the sandbox's isolated venv (sibling to workspace/) so editable repo imports resolve."""
    for vd in (root.parent / "venv", root / ".venv", root / "venv"):
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


def _sandbox_digest(root: Path) -> str:
    return hashlib.sha1(str(root.resolve()).encode("utf-8", errors="ignore")).hexdigest()[:12]


def _cache_path(root: Path) -> Path:
    return Path(f"/tmp/_swegemma_verify_cache_{_sandbox_digest(root)}.json")


def _incumbent_path(root: Path) -> Path:
    sb_tmp = root.parent / "tmp"
    if sb_tmp.is_dir():
        return sb_tmp / "_incumbent_best.patch"
    return Path(f"/tmp/_swegemma_incumbent_{_sandbox_digest(root)}.patch")


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
    is_cli_edit = any("cli" in rel.lower() for rel in source_files)

    for rel in source_files:
        p = Path(rel)
        if p.suffix != ".py":
            continue
        clean_stem = p.stem.lower().lstrip("_")
        if clean_stem and clean_stem not in GENERIC_STEMS:
            stems.add(clean_stem)
        pkg_dirs = [part.lower().lstrip("_") for part in p.parts[:-1] if part.lower().lstrip("_") not in GENERIC_DIRS]
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

        if rel_str in sym_hits:
            score += 45 * sym_hits[rel_str]

        if score > 0:
            scored.append((score, -len(rel_str), rel_str))

    scored.sort(reverse=True)
    return [item[2] for item in scored[:max_files]]


def _extract_related_test_examples(
    root: Path,
    matched_tests: list[str],
    symbols: list[str],
    max_examples: int = 2,
    max_lines_per_example: int = 22,
) -> list[dict]:
    """Return up to 2 compact snippets of existing repo tests referencing the changed symbols."""
    if not matched_tests:
        return []
    examples: list[dict] = []
    seen_headers: set[str] = set()
    sym_lower = [s.lower() for s in symbols if len(s) >= 4]

    for tf in matched_tests:
        full = root / tf
        if not full.is_file():
            continue
        try:
            src = full.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(src, filename=tf)
        except Exception:
            continue
        lines = src.splitlines()
        candidates: list[tuple[int, int, str, int, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
                if not (name.startswith("test_") or name.startswith("Test")):
                    continue
                start = getattr(node, "lineno", 1)
                end = min(getattr(node, "end_lineno", start + max_lines_per_example), start + max_lines_per_example - 1)
                block = "\n".join(lines[start - 1 : end])
                bl = block.lower()
                hits = sum(1 for s in sym_lower if s in bl) if sym_lower else 1
                if hits > 0:
                    # Prefer function defs over huge test classes
                    kind_bonus = 2 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else 0
                    candidates.append((hits * 10 + kind_bonus, -start, name, start, end))
        candidates.sort(reverse=True)
        for _score, _neg_s, name, start, end in candidates:
            key = f"{tf}::{name}"
            if key in seen_headers:
                continue
            seen_headers.add(key)
            snippet = "\n".join(lines[start - 1 : end])[:750]
            examples.append({"test": key, "lines": f"{start}-{end}", "snippet": snippet})
            if len(examples) >= max_examples:
                return examples
    return examples


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


def _snapshot_for_baseline_swap(root: Path, base_ref: str, source_touched: list[str]) -> tuple[dict[Path, bytes], dict[Path, bytes]]:
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


def _check_baseline_failures(
    root: Path,
    base_ref: str,
    source_touched: list[str],
    failed_nodes: list[str],
    pytest_cmd: list[str],
) -> set[str]:
    if not failed_nodes or not source_touched:
        return set()
    saved_bytes, baseline_bytes = _snapshot_for_baseline_swap(root, base_ref, source_touched)
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


def _check_repro_script(root: Path, base_ref: str, source_touched: list[str]) -> tuple[str, str]:
    """If /tmp/_repro.py (or sandbox tmp/_repro.py) exists, verify FAIL on baseline and PASS on patched."""
    candidates = [root.parent / "tmp" / "_repro.py", Path("/tmp/_repro.py")]
    repro_path = next((p for p in candidates if p.is_file() and p.stat().st_size > 0), None)
    if repro_path is None:
        return "NOT_PROVIDED", "No /tmp/_repro.py found."

    py_exe = _resolve_python_exe(root)
    env = {"PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p_now = _run(root, [py_exe, str(repro_path)], timeout=20, extra_env=env)
    except subprocess.TimeoutExpired:
        return "FAILED_ON_PATCH", f"{repro_path.name} timed out (>20s) on patched code."

    out_now = ((p_now.stdout or "") + "\n" + (p_now.stderr or "")).strip()
    if p_now.returncode != 0:
        return "FAILED_ON_PATCH", out_now[-700:]

    # Passed on patched code! Now verify it actually FAILED on baseline (non-trivial repro).
    saved_bytes, baseline_bytes = _snapshot_for_baseline_swap(root, base_ref, source_touched)
    if not saved_bytes:
        return "PASSED_ON_PATCH", out_now[-300:]
    try:
        for full, bdata in baseline_bytes.items():
            full.write_bytes(bdata)
        p_base = _run(root, [py_exe, str(repro_path)], timeout=20, extra_env=env)
        if p_base.returncode != 0:
            return "PROVEN_FIX", f"{repro_path.name} FAILED on baseline (rc={p_base.returncode}) and PASSED on patched code (rc=0)!"
        return "TRIVIAL_PASSES_ON_BASELINE", (
            f"WARNING: {repro_path.name} already PASSED on baseline (rc=0)! "
            "Your repro does NOT trigger the reported bug — strengthen /tmp/_repro.py to fail on the unfixed bug."
        )
    except subprocess.TimeoutExpired:
        return "PASSED_ON_PATCH", f"{repro_path.name} passed on patched code (baseline timed out)."
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
            has_env_error = (
                "=== ERRORS ===" in combined
                or "ERROR collecting" in combined
                or "ERROR at setup" in combined
            )
            if rc == 0:
                lines = [ln for ln in combined.splitlines() if ln.strip()]
                return "PASS", "\n".join(lines[-3:]), []
            if rc == 5:
                return "NO_TESTS_COLLECTED", combined[-400:], []
            if "No module named pytest" in combined or "disabled" in combined.lower():
                return "UNAVAILABLE", "pytest unavailable in sandbox.", []
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
                return "FAIL", combined[-1500:], new_regressions or failed_nodes
            if has_env_error:
                return "ENV_SKIPPED", f"{test_file}: sandbox collection/fixture setup error (0 assertions failed).", []
            return "FAIL", combined[-1500:], [test_file]
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
                    "context": _syntax_context(full, exc.lineno),
                })

    diff_text = _git_out(root, "diff", "-U0", base_ref, "--", *source_touched) if source_touched else ""
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
        bad_files = " ".join(sorted({e["file"] for e in syntax_errors}))
        warnings.append(
            f"Python syntax error(s) detected. Inspect syntax_errors[].context and fix, "
            f"or reset cleanly with `git checkout -- {bad_files}` and re-apply your edit."
        )
    if same_diff_streak >= 1:
        warnings.append(
            "LOOP GUARD: Workspace diff has NOT changed since your previous verify-patch call. "
            "Do NOT call verify-patch again without editing source code first."
        )

    matched_tests: list[str] = []
    related_test_examples: list[dict] = []
    pytest_status = "SKIPPED"
    pytest_summary = ""
    filtered_preexisting: list[str] = []
    per_file_results: dict[str, str] = {}
    repro_status = "SKIPPED"
    repro_output = ""

    if source_touched and not syntax_errors:
        symbols = _extract_changed_symbols(diff_text)
        matched_tests = _discover_test_files(root, source_touched, diff_text, max_files=2)
        related_test_examples = _extract_related_test_examples(root, matched_tests, symbols, max_examples=2)
        repro_status, repro_output = _check_repro_script(root, base_ref, source_touched)
        if repro_status == "FAILED_ON_PATCH":
            warnings.append("Issue-specific /tmp/_repro.py FAILED on current patch; fix source before submitting.")
        elif repro_status == "TRIVIAL_PASSES_ON_BASELINE":
            warnings.append("TRIVIAL REPRO: /tmp/_repro.py passed on baseline too; strengthen it to fail on the unfixed bug.")

        if run_pytest:
            if not matched_tests:
                pytest_status = "NO_MATCHING_TESTS"
                pytest_summary = "No matching test file found under tests/."
            else:
                pytest_cmd = _resolve_pytest_cmd(root)
                summaries: list[str] = []
                any_fail = any_pass = any_preexisting_only = False
                last_nonpass_status = "ENV_SKIPPED"
                for tf in matched_tests:
                    st, out, regs = _run_single_test_file(
                        root, base_ref, source_touched, pytest_cmd, tf, preexisting_failed, filtered_preexisting
                    )
                    per_file_results[tf] = st
                    summaries.append(f"[{tf}: {st}]\n{out}".strip())
                    if st == "FAIL":
                        any_fail = True
                        warnings.append(
                            f"CONFIRMED NEW REGRESSION(S) in {tf}: {regs}. Inspect traceback and fix source before submitting."
                        )
                        break
                    elif st == "PASS":
                        any_pass = True
                    elif st == "PASS_PREEXISTING_ONLY":
                        any_preexisting_only = True
                    else:
                        last_nonpass_status = st
                if any_fail:
                    pytest_status = "FAIL"
                elif any_pass:
                    pytest_status = "PASS"
                elif any_preexisting_only:
                    pytest_status = "PASS_PREEXISTING_ONLY"
                else:
                    pytest_status = last_nonpass_status
                pytest_summary = "\n\n".join(summaries)[-1400:]

    cache["preexisting_failed"] = preexisting_failed[:20]
    cache["last_diff_hash"] = diff_hash
    cache["same_diff_streak"] = same_diff_streak
    _save_cache(root, cache)

    static_ok = bool(source_touched) and not protected_touched and not syntax_errors
    repro_failed = repro_status == "FAILED_ON_PATCH"
    pytest_verified = pytest_status in ("PASS", "PASS_PREEXISTING_ONLY")
    tests_not_failed = pytest_status != "FAIL"
    overall_ok = static_ok and tests_not_failed and not repro_failed

    incumbent_file = _incumbent_path(root)
    if overall_ok and repro_status == "PROVEN_FIX":
        full_diff = _git_out(root, "diff", base_ref, "--", *source_touched)
        if full_diff:
            try:
                incumbent_file.write_text(full_diff + "\n", encoding="utf-8")
            except Exception:
                pass
    elif not overall_ok and incumbent_file.is_file():
        shown_path = "/tmp/_incumbent_best.patch" if incumbent_file.name == "_incumbent_best.patch" else str(incumbent_file)
        warnings.append(
            f"INCUMBENT AVAILABLE: A previously PROVEN patch is saved at {shown_path}. "
            f"If stuck, restore cleanly with: git checkout -- {' '.join(source_touched)} && git apply {shown_path}"
        )

    if not overall_ok:
        verdict = "FAIL — DO NOT call submit_patch yet. Fix the errors/failures below and re-run verify-patch."
    elif repro_status == "PROVEN_FIX":
        verdict = "PASS_PROVEN — /tmp/_repro.py FAILED on baseline and PASSED on your fix, with 0 new pytest regressions. Ready to call submit_patch!"
    elif pytest_verified:
        verdict = "PASS_TESTS_ONLY — Existing pytest passed with no new regressions. Confirm your issue-specific /tmp/_repro.py fails on baseline & passes now, then submit."
    else:
        verdict = (
            f"UNVERIFIED ({pytest_status}) — Static checks passed, BUT existing pytest did NOT verify your fix. "
            "Write /tmp/_repro.py reproducing the exact issue + edge cases and re-run verify-patch before submitting!"
        )

    return {
        "verdict": verdict,
        "ok": overall_ok,
        "repro_status": repro_status,
        "repro_output": repro_output,
        "pytest_status": pytest_status,
        "per_file_results": per_file_results,
        "matched_tests": matched_tests,
        "related_test_examples": related_test_examples,
        "source_files": source_touched,
        "protected_files": protected_touched,
        "untracked_files": untracked,
        "syntax_errors": syntax_errors,
        "filtered_preexisting": filtered_preexisting,
        "pytest_output": pytest_summary,
        "diff_stat": diff_stat[:600],
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify /workspace patch with repro gate, test specs, and baseline-diff pytest.")
    parser.add_argument("--root", default=None, help="Optional explicit repository root (auto-detects cwd/.git first)")
    parser.add_argument("--no-pytest", action="store_true", help="Skip running pytest")
    args = parser.parse_args()
    root = _resolve_repo_root(args.root)
    report = verify_workspace(root, run_pytest=not args.no_pytest)
    print(json.dumps(report, ensure_ascii=False, indent=2)[:4600])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
