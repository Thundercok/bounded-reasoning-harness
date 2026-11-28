"""Deterministic IDF + AST multi-symbol source & test locator for SWE tasks."""
from __future__ import annotations

import ast
import json
import math
import re
import subprocess
import sys
from pathlib import Path

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "when", "should", "would",
    "into", "only", "used", "using", "have", "does", "not", "are", "was", "were",
    "fix", "fixes", "fixed", "bug", "issue", "issues", "error", "add", "added",
    "allow", "support", "update", "make", "use", "can", "will", "all", "any",
    "python", "true", "false", "none", "self", "return", "test", "tests", "code",
}


def _resolve_repo_root() -> Path:
    cwd = Path.cwd()
    candidates: list[Path] = []
    for anc in (cwd, *cwd.parents):
        candidates.append(anc)
        candidates.append(anc / "workspace")
    candidates.append(Path("/workspace"))
    for cand in candidates:
        if cand.is_dir() and (cand / ".git").exists():
            return cand
    return Path("/workspace") if Path("/workspace").is_dir() else cwd


def _parse_query_args(raw_argv: list[str]) -> list[str]:
    tokens: list[str] = []
    for raw in raw_argv:
        if raw.startswith("-"):
            if "=" in raw:
                raw = raw.split("=", 1)[1]
            else:
                continue
        for m in re.findall(r"\b(?:fix|feat|refactor|perf)\(([a-zA-Z0-9_/-]+)\)", raw):
            if m and m not in tokens:
                tokens.append(m)
        parts = re.split(r"[\s,;]+", raw.strip()) if (" " in raw or "," in raw) else [raw.strip()]
        for p in parts:
            p = p.strip("`'\"()[]{}:")
            if not p:
                continue
            if p.endswith(".py") and "/" in p:
                p_stem = Path(p).stem
                if p not in tokens:
                    tokens.append(p)
                if len(p_stem) >= 3 and p_stem not in tokens:
                    tokens.append(p_stem)
                continue
            for sub in ([p, p.split(".")[-1]] if "." in p else [p]):
                if len(sub) >= 2 and sub.lower() not in STOPWORDS and sub not in tokens:
                    tokens.append(sub)
    return tokens[:12]


def _extract_best_spans(txt: str, filename: str, matched_terms: list[str], idf: dict[str, float]) -> list[dict]:
    lines = txt.splitlines()
    n_lines = len(lines)
    if n_lines == 0:
        return []

    spans: list[tuple[float, int, dict]] = []
    try:
        tree = ast.parse(txt, filename=filename)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and hasattr(node, "end_lineno"):
                end_ln = int(node.end_lineno or node.lineno)
                span_len = end_ln - node.lineno + 1
                if isinstance(node, ast.ClassDef) and span_len > 140:
                    continue
                seg_lines = lines[node.lineno - 1 : end_ln]
                seg = "\n".join(seg_lines)
                seg_l = seg.lower()
                hits = [t for t in matched_terms if t in seg or t.lower() in seg_l]
                if not hits:
                    continue
                sp_score = sum(25.0 * idf.get(t, 1.0) for t in hits) + 15.0 * len(hits) * max(0, len(hits) - 1)
                if node.name in matched_terms or node.name.lower() in {t.lower() for t in matched_terms}:
                    sp_score += 60.0

                hit_lns: list[int] = []
                previews: list[str] = []
                for offset, ln_txt in enumerate(seg_lines):
                    abs_ln = node.lineno + offset
                    if any(t in ln_txt or t.lower() in ln_txt.lower() for t in hits):
                        hit_lns.append(abs_ln)
                        if len(previews) < 3:
                            previews.append(f"L{abs_ln}: {ln_txt.strip()[:100]}")

                if span_len <= 115:
                    s_ln, e_ln = node.lineno, end_ln
                else:
                    center = hit_lns[0] if hit_lns else node.lineno + 30
                    s_ln = max(node.lineno, center - 35)
                    e_ln = min(end_ln, s_ln + 110)

                spans.append((sp_score, node.lineno, {
                    "symbol": node.name,
                    "start_line": s_ln,
                    "end_line": e_ln,
                    "matched": hits,
                    "preview": previews,
                }))
        spans.sort(key=lambda x: (-x[0], x[1]))
    except Exception:
        pass

    deduped: list[dict] = []
    used_ranges: list[tuple[int, int]] = []
    for _, _, item in spans:
        r = (item["start_line"], item["end_line"])
        if any(not (r[1] < ur[0] or r[0] > ur[1]) for ur in used_ranges):
            continue
        used_ranges.append(r)
        deduped.append(item)
        if len(deduped) >= 2:
            break

    if not deduped and matched_terms:
        for i, ln_txt in enumerate(lines, 1):
            if any(t in ln_txt or t.lower() in ln_txt.lower() for t in matched_terms):
                s_ln = max(1, i - 20)
                e_ln = min(n_lines, s_ln + 90)
                deduped.append({
                    "symbol": "<module>",
                    "start_line": s_ln,
                    "end_line": e_ln,
                    "matched": matched_terms[:4],
                    "preview": [f"L{i}: {ln_txt.strip()[:100]}"],
                })
                break
    return deduped


def _find_matching_test(root: Path, test_files: list[str], top_src_files: list[str], terms: list[str]) -> dict | None:
    if not test_files:
        return None
    src_stems = {Path(f).stem.lower().lstrip("_") for f in top_src_files[:2] if Path(f).stem != "__init__"}
    best_tf: list[tuple[int, str]] = []
    for tf in test_files:
        name_l = Path(tf).name.lower()
        sc = 0
        for st in src_stems:
            if name_l in (f"test_{st}.py", f"{st}_test.py"):
                sc += 120
            elif st and st in name_l:
                sc += 55
        for t in terms:
            tl = t.lower().lstrip("_")
            if len(tl) >= 4 and tl in name_l:
                sc += 35
        if sc > 0:
            best_tf.append((sc, tf))
    best_tf.sort(key=lambda x: (-x[0], len(x[1])))

    candidates = [tf for _, tf in best_tf[:4]]
    if not candidates and terms:
        grep = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(root), "grep", "-l", "-F", *[x for t in terms[:4] for x in ("-e", t)], "--", "tests", "test"],
            capture_output=True, text=True, timeout=8, check=False,
        )
        candidates = [ln.strip() for ln in grep.stdout.splitlines() if ln.strip() and "tutorial" not in ln.lower()][:3]

    best_fn: tuple[int, dict] | None = None
    for tf in candidates:
        fp = root / tf
        if not fp.is_file():
            continue
        try:
            txt = fp.read_text(encoding="utf-8", errors="ignore")
            lines = txt.splitlines()
            tree = ast.parse(txt, filename=tf)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test") and hasattr(node, "end_lineno"):
                    end_ln = int(node.end_lineno or node.lineno)
                    seg = "\n".join(lines[node.lineno - 1 : end_ln])
                    hits = sum(2 if t in seg else (1 if t.lower() in seg.lower() else 0) for t in terms)
                    sc = hits * 20 + (10 if end_ln - node.lineno <= 45 else 0)
                    if best_fn is None or sc > best_fn[0]:
                        snip_end = min(end_ln, node.lineno + 14)
                        best_fn = (sc, {
                            "filepath": tf,
                            "test_name": node.name,
                            "start_line": node.lineno,
                            "end_line": min(end_ln, node.lineno + 60),
                            "snippet": "\n".join(lines[node.lineno - 1 : snip_end])[:900],
                        })
        except Exception:
            continue
    return best_fn[1] if best_fn else None


def locate_workspace(root: Path, terms: list[str]) -> dict:
    want_docs = any("docs_src" in t or "tutorial" in t for t in terms)
    proc = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(root), "ls-files", "*.py"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    all_py = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    src_files: list[str] = []
    test_files: list[str] = []
    for f in all_py:
        p = Path(f)
        parts = set(p.parts)
        if parts & {"tests", "test", "testing"} or p.name.startswith("test_") or p.name.endswith("_test.py"):
            if want_docs or "tutorial" not in f.lower():
                test_files.append(f)
            continue
        if p.name in ("conftest.py", "setup.py") or (parts & {"benchmarks", "examples", "scripts", "docs"}):
            continue
        if not want_docs and "docs_src" in parts:
            continue
        src_files.append(f)

    if not terms:
        return {
            "error": "Pass 3-6 symbols/keywords in args, e.g. args=['Symbol1', 'Symbol2', 'module_name'].",
            "sample_source_files": src_files[:20],
        }

    contents: dict[str, str] = {}
    for f in src_files:
        fp = root / f
        if fp.is_file():
            try:
                contents[f] = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass

    n_docs = max(1, len(contents))
    idf: dict[str, float] = {}
    for t in terms:
        tl = t.lower()
        df = sum(1 for txt in contents.values() if tl in txt.lower())
        idf[t] = math.log(1.0 + n_docs / (1.0 + df))

    prelim: list[tuple[float, str, str, list[str]]] = []
    for f, txt in contents.items():
        p = Path(f)
        stem = p.stem.lower().lstrip("_")
        txt_l = txt.lower()
        score = 0.0
        matched: list[str] = []
        for t in terms:
            tl = t.lower().lstrip("_")
            w = idf[t]
            if f.endswith(t) or stem == tl:
                score += 95.0 * max(1.0, w / 2.0)
            elif len(tl) >= 4 and (tl in stem or (len(stem) >= 4 and stem in tl)):
                score += 35.0 * max(1.0, w / 2.5)
            if t in txt:
                matched.append(t)
                score += 18.0 * w
                if re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(t)}\b", txt, re.M):
                    score += 50.0 * w
            elif t.lower() in txt_l:
                matched.append(t)
                score += 8.0 * w
        if len(matched) >= 2:
            score += 12.0 * len(matched) * sum(idf[m] for m in matched)
        if score > 0:
            prelim.append((score, f, txt, matched))

    prelim.sort(key=lambda x: (-x[0], len(x[1])))

    final_ranked: list[tuple[float, dict]] = []
    for base_sc, f, txt, mterms in prelim[:8]:
        spans = _extract_best_spans(txt, f, mterms, idf)
        span_bonus = 0.0
        if spans:
            top_sp_hits = spans[0]["matched"]
            span_bonus = 20.0 * len(top_sp_hits) * max(1, len(top_sp_hits) - 1)
        total_lines = txt.count("\n") + 1
        final_ranked.append((base_sc + span_bonus, {
            "filepath": f,
            "total_lines": total_lines,
            "score": round(base_sc + span_bonus, 1),
            "matched_terms": mterms,
            "spans": spans,
        }))

    final_ranked.sort(key=lambda x: (-x[0], len(x[1]["filepath"])))
    top_items = [item for _, item in final_ranked[:4]]
    top_paths = [item["filepath"] for item in top_items]
    matching_test = _find_matching_test(root, test_files, top_paths, terms)

    return {
        "query_terms": terms,
        "top_source_files": top_items,
        "matching_test": matching_test,
        "hint": "Pass explicit start_line and end_line from spans[] to read_file() so large files are never truncated at line 150.",
    }


def main() -> int:
    root = _resolve_repo_root()
    terms = _parse_query_args(sys.argv[1:])
    report = locate_workspace(root, terms)
    print(json.dumps(report, ensure_ascii=False, indent=2)[:4600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
