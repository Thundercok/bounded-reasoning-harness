#!/usr/bin/env python3
"""ADK-faithful local Apple Silicon (Ollama Metal) SWE evaluator for Gemma 4 E4B variants.

Reads any validated ADK variant directory (LlmAgent or SequentialAgent), runs it against
tasks from data/tasks.jsonl in isolated git sandboxes with exact tool & skill semantics,
and grades patches by applying the gold test_patch in a clean checkout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_PATH = REPO_ROOT / "data" / "tasks.jsonl"
CACHE_ROOT = Path("/tmp/swegemma_local_cache")
REPOS_CACHE = CACHE_ROOT / "repos"
VENVS_CACHE = CACHE_ROOT / "venvs"

sys.path.insert(0, str(REPO_ROOT / "tools"))
from pack_and_validate import load_yaml_with_includes  # noqa: E402

PROTECTED_CONFIGS = {
    "conftest.py", "pytest.ini", "pyproject.toml", "tox.ini",
    "setup.cfg", ".pytest.ini", "sitecustomize.py", "usercustomize.py",
    "_swegemma_stubs.py",
}


def is_protected(rel_path: str) -> bool:
    p = Path(rel_path)
    if p.name in PROTECTED_CONFIGS or p.suffix == ".pth":
        return True
    parts_l = {part.lower() for part in p.parts}
    if parts_l & {"tests", "test", "testing"} and p.suffix == ".py":
        return True
    if (p.name.startswith("test_") or p.name.endswith("_test.py")) and p.suffix == ".py":
        return True
    return False


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 120, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env, check=False)


def ensure_repo_cache(repo: str) -> Path:
    REPOS_CACHE.mkdir(parents=True, exist_ok=True)
    slug = repo.replace("/", "__")
    repo_dir = REPOS_CACHE / slug
    if not (repo_dir / ".git").exists():
        url = f"https://github.com/{repo}.git"
        print(f"[cache] Cloning {url} -> {repo_dir} ...", flush=True)
        proc = _run(["git", "clone", url, str(repo_dir)], timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"git clone {url} failed: {proc.stderr[-400:]}")
    return repo_dir


def ensure_repo_venv(repo: str, sample_ws: Path) -> Path:
    """Create one reusable base venv per repo with pytest + repo deps; editable install updated per task."""
    VENVS_CACHE.mkdir(parents=True, exist_ok=True)
    slug = repo.replace("/", "__")
    vdir = VENVS_CACHE / slug
    py = vdir / "bin" / "python"
    if not py.is_file():
        print(f"[cache] Creating venv for {repo} at {vdir} ...", flush=True)
        subprocess.run(["uv", "venv", str(vdir), "--python", "3.12"], check=True, capture_output=True)
        base_deps = ["pytest"]
        if repo == "fastapi/fastapi":
            base_deps += ["pydantic>=2.7,<2.12", "starlette<0.50", "httpx", "typing-extensions", "python-multipart", "email-validator", "anyio", "annotated-doc", "dirty-equals", "inline-snapshot", "pydantic-settings"]
        elif repo == "psf/requests":
            base_deps += ["charset-normalizer", "idna", "urllib3", "certifi"]
        elif repo == "Textualize/rich":
            base_deps += ["markdown-it-py", "pygments", "typing-extensions"]
        subprocess.run(["uv", "pip", "install", "--python", str(py), *base_deps], check=True)
    return vdir


def checkout_task_workspace(repo_cache: Path, base_commit: str, ws: Path) -> None:
    proc = _run(["git", "clone", "--no-hardlinks", str(repo_cache), str(ws)], timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"local clone failed: {proc.stderr[-300:]}")
    # Fetch commit if missing
    chk = _run(["git", "cat-file", "-e", base_commit], cwd=ws)
    if chk.returncode != 0:
        _run(["git", "fetch", "origin", base_commit], cwd=repo_cache, timeout=120)
        _run(["git", "fetch", str(repo_cache), base_commit], cwd=ws, timeout=120)
    proc2 = _run(["git", "checkout", "-f", base_commit], cwd=ws, timeout=60)
    if proc2.returncode != 0:
        raise RuntimeError(f"git checkout {base_commit} failed: {proc2.stderr[-300:]}")
    _run(["git", "branch", "-f", "_swegemma_baseline", "HEAD"], cwd=ws)


def extract_clean_patch(ws: Path) -> str:
    _run(["git", "add", "-N", "."], cwd=ws)
    diff = _run(["git", "diff", "_swegemma_baseline"], cwd=ws).stdout
    if not diff.strip():
        return ""
    # Strip protected files hunk by hunk
    kept: list[str] = []
    for section in re.split(r"(?=^diff --git )", diff, flags=re.M):
        if not section.strip():
            continue
        m = re.match(r"^diff --git a/(.+?) b/(.+?)$", section.splitlines()[0])
        if m and (is_protected(m.group(1)) or is_protected(m.group(2))):
            continue
        kept.append(section)
    return "".join(kept)


def parse_test_patch_targets(test_patch: str) -> tuple[list[str], list[str]]:
    """Extract test files and specific test function/method names added or modified in test_patch."""
    files = sorted(set(re.findall(r"^\+\+\+ b/(.+)$", test_patch, re.M)))
    test_names: list[str] = []
    for line in test_patch.splitlines():
        if line.startswith("@@"):
            m = re.search(r"def\s+(test_[A-Za-z0-9_]+)", line)
            if m and m.group(1) not in test_names:
                test_names.append(m.group(1))
        elif line.startswith("+") and not line.startswith("+++"):
            m = re.match(r"^\+\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)", line)
            if m and m.group(1) not in test_names:
                test_names.append(m.group(1))
    return files, test_names


def grade_patch(repo_cache: Path, venv_dir: Path, task: dict, agent_patch: str) -> tuple[bool, int, str]:
    if not agent_patch.strip():
        return False, -1, "empty_patch"
    with tempfile.TemporaryDirectory(prefix="swegemma_grade_") as td:
        ws = Path(td) / "workspace"
        checkout_task_workspace(repo_cache, task["base_commit"], ws)
        # Apply agent patch
        p_file = Path(td) / "agent.patch"
        p_file.write_text(agent_patch.rstrip("\n") + "\n", encoding="utf-8")
        ap = _run(["git", "apply", "--unsafe-paths", str(p_file)], cwd=ws)
        if ap.returncode != 0:
            ap = _run(["git", "apply", "--unsafe-paths", "-3", str(p_file)], cwd=ws)
        if ap.returncode != 0:
            ap = _run(["git", "apply", "--unsafe-paths", "--recount", "--ignore-whitespace", str(p_file)], cwd=ws)
        if ap.returncode != 0:
            return False, -2, f"patch_apply_failed: {ap.stderr[-300:]}"
        # Apply test_patch
        t_file = Path(td) / "test.patch"
        t_file.write_text(task["test_patch"].rstrip("\n") + "\n", encoding="utf-8")
        tp = _run(["git", "apply", "--unsafe-paths", str(t_file)], cwd=ws)
        if tp.returncode != 0:
            tp = _run(["git", "apply", "--unsafe-paths", "--recount", str(t_file)], cwd=ws)
        if tp.returncode != 0:
            return False, -3, f"test_patch_apply_failed: {tp.stderr[-300:]}"

        test_files, test_names = parse_test_patch_targets(task["test_patch"])
        py = str(venv_dir / "bin" / "python")
        env = dict(os.environ)
        py_paths = [str(ws)]
        if (ws / "src").is_dir():
            py_paths.insert(0, str(ws / "src"))
        env["PYTHONPATH"] = os.pathsep.join(py_paths)
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        cmd = [py, "-m", "pytest", *test_files, "-q", "--tb=short", "--no-header", "-W", "default", "-o", f"cache_dir={td}/pcache"]
        if test_names:
            cmd.extend(["-k", " or ".join(test_names)])
        res = _run(cmd, cwd=ws, timeout=120, env=env)
        out = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
        return res.returncode == 0, res.returncode, out[-1200:]


def build_tool_schemas(tool_names: list[str], has_skills: bool) -> list[dict]:
    catalog = {
        "run_command": {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "Execute a bash command inside /workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        },
        "read_file": {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read up to 150 lines from a file inside /workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    "required": ["filepath"],
                },
            },
        },
        "edit_file": {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Replace exact old_string with new_string in a file inside /workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "allow_multiple": {"type": "boolean"},
                    },
                    "required": ["filepath", "old_string", "new_string"],
                },
            },
        },
        "write_file": {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write full content to a file inside /workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"filepath": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["filepath", "content"],
                },
            },
        },
        "get_status": {
            "type": "function",
            "function": {
                "name": "get_status",
                "description": "Get current budget and git diff status.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "submit_patch": {
            "type": "function",
            "function": {
                "name": "submit_patch",
                "description": "Submit the current source git diff as final patch.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    }
    tools = [catalog[t] for t in tool_names if t in catalog]
    if has_skills:
        tools.append({
            "type": "function",
            "function": {
                "name": "run_skill_script",
                "description": "Execute a deterministic script from an installed skill (e.g. skill_name='verify-patch', file_path='scripts/run_verify.py').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {"type": "string"},
                        "file_path": {"type": "string"},
                    },
                    "required": ["skill_name", "file_path"],
                },
            },
        })
    return tools


def _compact_messages(messages: list[dict], max_chars: int = 18000) -> list[dict]:
    """Keep system + initial user prompt + recent messages compact so 8K Metal ctx never OOMs."""
    total = sum(len(json.dumps(m)) for m in messages)
    if total <= max_chars or len(messages) <= 6:
        return messages
    compacted = [messages[0], messages[1]]
    middle = messages[2:-6]
    tail = messages[-6:]
    for m in middle:
        if m.get("role") == "tool" and len(str(m.get("content", ""))) > 350:
            mc = dict(m)
            mc["content"] = str(m["content"])[:320] + "...[truncated]"
            compacted.append(mc)
        else:
            compacted.append(m)
    compacted.extend(tail)
    # If still too large, drop oldest middle turns in pairs
    while sum(len(json.dumps(m)) for m in compacted) > max_chars and len(compacted) > 8:
        compacted.pop(2)
    return compacted


def call_ollama(model: str, messages: list[dict], tools: list[dict], sampling: dict) -> dict:
    tc = sampling.get("thinking_config") or {}
    think_budget = int(tc.get("thinking_budget", 0) or 0)
    max_pred = 1280 if think_budget > 0 else 768
    payload: dict[str, Any] = {
        "model": model,
        "messages": _compact_messages(messages, max_chars=14000),
        "tools": tools,
        "stream": False,
        "think": think_budget > 0,
        "options": {
            "temperature": float(sampling.get("temperature", 0.2)),
            "top_p": float(sampling.get("top_p", 0.95)),
            "num_predict": max_pred,
            "num_ctx": 6144,
        },
    }
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:400]
        raise RuntimeError(f"Ollama HTTP {e.code}: {body}") from e


class LocalSandbox:
    def __init__(self, sb_dir: Path, ws: Path, venv_dir: Path, variant_dir: Path, max_calls: int, max_minutes: float):
        self.sb_dir = sb_dir
        self.ws = ws
        self.sb_tmp = sb_dir / "tmp"
        self.sb_tmp.mkdir(parents=True, exist_ok=True)
        # Symlink sandbox venv to repo venv so run_verify.py finds <sandbox>/venv/bin/pytest!
        sb_venv = sb_dir / "venv"
        if not sb_venv.exists():
            os.symlink(venv_dir, sb_venv)
        self.venv_dir = venv_dir
        self.variant_dir = variant_dir
        self.max_calls = max_calls
        self.max_seconds = max_minutes * 60.0
        self.calls_used = 0
        self.submitted = False
        self.start_t = time.perf_counter()

    def _base_env(self) -> dict[str, str]:
        env = dict(os.environ)
        py_paths = [str(self.ws)]
        if (self.ws / "src").is_dir():
            py_paths.insert(0, str(self.ws / "src"))
        env["PYTHONPATH"] = os.pathsep.join(py_paths)
        env["PATH"] = f"{self.venv_dir / 'bin'}:{env.get('PATH', '')}"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def exec_tool(self, name: str, args: dict) -> str:
        counted = name not in ("get_status", "submit_patch")
        if counted:
            if self.calls_used >= self.max_calls:
                return json.dumps({"status": "error", "error_type": "BudgetExceeded", "error_message": f"Tool call budget exhausted ({self.max_calls})"})
            self.calls_used += 1

        try:
            if name == "run_command":
                cmd = str(args.get("command", ""))
                cmd = cmd.replace("/workspace", str(self.ws)).replace("/tmp/", f"{self.sb_tmp}/")
                proc = subprocess.run(["bash", "-c", cmd], cwd=self.ws, capture_output=True, text=True, timeout=90, env=self._base_env())
                out = (proc.stdout or "")[:5000]
                err = (proc.stderr or "")[:2000]
                if proc.returncode == 0:
                    res: dict[str, Any] = {"status": "ok", "stdout": out, "stderr": err, "exit_code": 0}
                else:
                    res = {"status": "error", "error_type": "CommandError", "error_message": (err or out)[-2500:], "exit_code": proc.returncode}
            elif name == "read_file":
                fp = str(args.get("filepath", "")).lstrip("/")
                if fp.startswith("workspace/"):
                    fp = fp[len("workspace/"):]
                full = (self.ws / fp).resolve()
                if not full.is_file():
                    res = {"status": "error", "error_type": "FileNotFoundError", "error_message": f"Not found: {fp}"}
                else:
                    lines = full.read_text(encoding="utf-8", errors="ignore").splitlines()
                    s = max(1, int(args.get("start_line") or 1))
                    e = min(len(lines), int(args.get("end_line") or (s + 149)))
                    e = min(e, s + 149)
                    content = "\n".join(f"{i}: {lines[i-1]}" for i in range(s, e + 1))[:10000]
                    res = {"status": "ok", "filepath": fp, "start_line": s, "end_line": e, "total_lines": len(lines), "content": content}
            elif name == "edit_file":
                fp = str(args.get("filepath", "")).lstrip("/")
                if fp.startswith("workspace/"):
                    fp = fp[len("workspace/"):]
                full = (self.ws / fp).resolve()
                old_s = args.get("old_string")
                new_s = args.get("new_string")
                if not isinstance(old_s, str) or not isinstance(new_s, str):
                    res = {"status": "error", "error_type": "ValueError", "error_message": "Missing old_string or new_string"}
                elif not full.is_file():
                    res = {"status": "error", "error_type": "FileNotFoundError", "error_message": f"Not found: {fp}"}
                else:
                    txt = full.read_text(encoding="utf-8")
                    cnt = txt.count(old_s)
                    if cnt == 0:
                        res = {"status": "error", "error_type": "FileEditError", "error_message": "Failed to replace: old_string not found."}
                    elif cnt > 1 and not args.get("allow_multiple"):
                        res = {"status": "error", "error_type": "FileEditError", "error_message": f"Target string occurs {cnt} times."}
                    else:
                        updated = txt.replace(old_s, new_s) if args.get("allow_multiple") else txt.replace(old_s, new_s, 1)
                        full.write_text(updated, encoding="utf-8")
                        diff = _run(["git", "diff", "--", fp], cwd=self.ws).stdout[:2000]
                        res = {"status": "ok", "filepath": fp, "occurrences": cnt, "diff": diff}
            elif name == "write_file":
                fp = str(args.get("filepath", "")).lstrip("/")
                if fp.startswith("workspace/"):
                    fp = fp[len("workspace/"):]
                full = (self.ws / fp).resolve()
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(str(args.get("content", "")), encoding="utf-8")
                res = {"status": "ok", "filepath": fp}
            elif name == "get_status":
                patch = extract_clean_patch(self.ws)
                res = {
                    "status": "ok",
                    "tool_calls_used": self.calls_used,
                    "max_tool_calls": self.max_calls,
                    "elapsed_seconds": round(time.perf_counter() - self.start_t, 1),
                    "patch_size": len(patch),
                }
            elif name == "submit_patch":
                self.submitted = True
                patch = extract_clean_patch(self.ws)
                files = sorted(set(re.findall(r"^\+\+\+ b/(.+)$", patch, re.M)))
                res = {"status": "ok", "patch_size": len(patch), "files_changed": len(files)}
            elif name == "run_skill_script":
                sn = args.get("skill_name", "")
                if isinstance(sn, list): sn = sn[0]
                fp = args.get("file_path", "")
                if isinstance(fp, list): fp = fp[0]
                script_path = self.variant_dir / "skills" / str(sn) / str(fp)
                if not script_path.is_file():
                    res = {"status": "error", "error_message": f"Skill script not found: {sn}/{fp}"}
                else:
                    skill_cwd = self.sb_tmp / "skill_exec"
                    skill_cwd.mkdir(parents=True, exist_ok=True)
                    proc = _run([str(self.venv_dir / "bin" / "python"), str(script_path)], cwd=skill_cwd, timeout=90, env=self._base_env())
                    res = {"skill_name": sn, "file_path": fp, "stdout": proc.stdout[:4800], "stderr": proc.stderr[:1000], "exit_code": proc.returncode}
            else:
                res = {"status": "error", "error_message": f"Unknown tool: {name}"}
        except subprocess.TimeoutExpired:
            res = {"status": "error", "error_type": "TimeoutExceeded", "error_message": "Command timed out"}
        except Exception as exc:
            res = {"status": "error", "error_type": type(exc).__name__, "error_message": str(exc)[:400]}

        rem = self.max_calls - self.calls_used
        if counted and self.max_calls >= 20 and rem <= 8 and isinstance(res, dict):
            res["budget_warning"] = f"Only {rem} tool call(s) remaining ({self.calls_used}/{self.max_calls}). Finalize and call submit_patch soon."
        return json.dumps(res, ensure_ascii=False)


def run_agent_stage(
    model: str,
    agent_cfg: dict,
    sb: LocalSandbox,
    user_prompt: str,
    max_turns: int,
    stage_log: list[dict],
) -> str:
    sys_prompt = str(agent_cfg.get("instruction", ""))
    sampling = agent_cfg.get("generate_content_config") or {}
    tool_names = list(agent_cfg.get("tools") or [])
    has_skills = bool(agent_cfg.get("skills"))
    tools = build_tool_schemas(tool_names, has_skills)
    include_thoughts = bool((sampling.get("thinking_config") or {}).get("include_thoughts", False))

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]
    last_text = ""
    nudges = 0
    for turn in range(max_turns):
        if time.perf_counter() - sb.start_t > sb.max_seconds:
            break
        if sb.calls_used >= sb.max_calls and "submit_patch" not in tool_names:
            break
        resp = call_ollama(model, messages, tools, sampling)
        msg = resp.get("message") or {}
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []
        last_text = content

        # Strip thinking if include_thoughts is False (matching ADK!)
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
        if include_thoughts and msg.get("thinking"):
            assistant_msg["thinking"] = msg["thinking"]
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        step_rec: dict[str, Any] = {
            "author": agent_cfg.get("name", "agent"),
            "turn": turn,
            "content": content[:300],
            "tool_calls": [],
        }

        if not tool_calls:
            stage_log.append(step_rec)
            if sb.submitted or "submit_patch" not in tool_names:
                break
            nudges += 1
            if nudges > 2:
                break
            messages.append({"role": "user", "content": "Please continue using your tools, or call submit_patch once verified."})
            continue

        nudges = 0
        for tc in tool_calls:
            fn = tc.get("function") or {}
            tname = fn.get("name", "")
            targs = fn.get("arguments") or {}
            if isinstance(targs, str):
                try:
                    targs = json.loads(targs)
                except Exception:
                    targs = {}
            obs = sb.exec_tool(tname, targs)
            step_rec["tool_calls"].append({"name": tname, "args": str(targs)[:180], "obs": obs[:240]})
            messages.append({"role": "tool", "name": tname, "content": obs})
        stage_log.append(step_rec)
        if sb.submitted:
            break
    return last_text


def run_variant_on_task(variant_dir: Path, model: str, task: dict) -> dict:
    root_cfg = load_yaml_with_includes(variant_dir / "agent.yaml", variant_dir)
    ev_raw = yaml.safe_load((variant_dir / "eval_config.yaml").read_text()) or {}
    ev = ev_raw.get("evaluation", ev_raw)
    max_calls = int(ev.get("max_tool_calls", 28))
    max_minutes = float(ev.get("max_time_minutes", 5.0))
    max_turns = int(ev.get("max_turns", 80))

    repo_cache = ensure_repo_cache(task["repo"])
    with tempfile.TemporaryDirectory(prefix=f"swegemma_m1_{task['instance_id']}_") as td:
        sb_dir = Path(td)
        ws = sb_dir / "workspace"
        checkout_task_workspace(repo_cache, task["base_commit"], ws)
        venv_dir = ensure_repo_venv(task["repo"], ws)
        sb = LocalSandbox(sb_dir, ws, venv_dir, variant_dir, max_calls=max_calls, max_minutes=max_minutes)

        prob = (
            f"Repository: {task['repo']}\n"
            f"Problem Statement:\n{task['problem_statement']}\n\n"
            f"Task Budget: {max_calls} tool calls, {max_minutes:.1f} minutes."
        )
        trace: list[dict] = []
        if root_cfg.get("agent_class") == "SequentialAgent":
            state: dict[str, str] = {"problem_description": task["problem_statement"]}
            for sub in root_cfg["sub_agents"]:
                sub_cfg = load_yaml_with_includes(variant_dir / sub["config_path"], variant_dir)
                inst = str(sub_cfg.get("instruction", ""))
                for k, v in state.items():
                    inst = inst.replace(f"{{{k}}}", v).replace(f"{{{k}?}}", v)
                sub_cfg["instruction"] = inst
                stage_u = prob if sub_cfg.get("include_contents") != "none" else "Begin your verification and finalize with submit_patch."
                out_txt = run_agent_stage(model, sub_cfg, sb, stage_u, max_turns=max_turns, stage_log=trace)
                if sub_cfg.get("output_key"):
                    state[sub_cfg["output_key"]] = out_txt
        else:
            run_agent_stage(model, root_cfg, sb, prob, max_turns=max_turns, stage_log=trace)

        elapsed = round(time.perf_counter() - sb.start_t, 1)
        patch = extract_clean_patch(ws)

    resolved, rc, grade_out = grade_patch(repo_cache, venv_dir, task, patch)
    return {
        "variant": variant_dir.name,
        "id": task["instance_id"],
        "repo": task["repo"],
        "resolved": resolved,
        "test_rc": rc,
        "patch_chars": len(patch),
        "tool_calls": sb.calls_used,
        "seconds": elapsed,
        "submitted": sb.submitted,
        "grade_tail": grade_out[-300:],
        "patch": patch,
        "trace": trace,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("variant_dir", type=Path)
    ap.add_argument("--tasks", nargs="+", required=True)
    ap.add_argument("--model", default="gemma4:e4b")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    all_tasks = {r["instance_id"]: r for r in (json.loads(l) for l in TASKS_PATH.read_text().splitlines() if l.strip())}
    out_dir = args.out or (REPO_ROOT / "results" / "local_m1" / args.variant_dir.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "per_task.jsonl"

    solved = 0
    for tid in args.tasks:
        task = all_tasks[tid]
        res = run_variant_on_task(args.variant_dir.resolve(), args.model, task)
        if res["resolved"]:
            solved += 1
        trace = res.pop("trace")
        patch = res.pop("patch")
        (out_dir / f"trace_{tid}.json").write_text(json.dumps({"meta": res, "patch": patch, "trace": trace}, indent=2))
        with jsonl_path.open("a") as f:
            f.write(json.dumps(res) + "\n")
        print(
            f"[{args.variant_dir.name}] {tid:<15} resolved={str(res['resolved']):<5} "
            f"calls={res['tool_calls']:2d} s={res['seconds']:5.1f}s patch={res['patch_chars']}B sub={res['submitted']}",
            flush=True,
        )
    print(f"\nTOTAL [{args.variant_dir.name}]: {solved}/{len(args.tasks)} ({solved/len(args.tasks)*100:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
