# Auto-generated from colab_v13_v14_v15_eval.ipynb cells 2-5; panel = data/heldout24_seed0.json
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# ── Kaggle Authentication ────────────────────────────────────────────────────
# Option A (recommended in IDE Colab): paste your KGAT_... token or set Colab Secrets
#   (KAGGLE_API_TOKEN) or (KAGGLE_USERNAME + KAGGLE_KEY).
# Option B: place ~/.kaggle/kaggle.json on the Colab VM.
# Option C: leave blank and kagglehub.login() will prompt interactively.
KAGGLE_API_TOKEN = os.environ.get('KAGGLE_API_TOKEN', '').strip()
KAGGLE_USERNAME = os.environ.get('KAGGLE_USERNAME', '').strip()
KAGGLE_KEY = os.environ.get('KAGGLE_KEY', '').strip()

if not (KAGGLE_API_TOKEN or (KAGGLE_USERNAME and KAGGLE_KEY)):
    try:
        from google.colab import userdata
        KAGGLE_API_TOKEN = (userdata.get('KAGGLE_API_TOKEN') or '').strip()
    except Exception:
        pass
if not (KAGGLE_API_TOKEN or (KAGGLE_USERNAME and KAGGLE_KEY)):
    try:
        from google.colab import userdata
        KAGGLE_USERNAME = (userdata.get('KAGGLE_USERNAME') or '').strip()
        KAGGLE_KEY = (userdata.get('KAGGLE_KEY') or '').strip()
    except Exception:
        pass

if KAGGLE_API_TOKEN:
    os.environ['KAGGLE_API_TOKEN'] = KAGGLE_API_TOKEN
if KAGGLE_USERNAME and KAGGLE_KEY:
    os.environ['KAGGLE_USERNAME'] = KAGGLE_USERNAME
    os.environ['KAGGLE_KEY'] = KAGGLE_KEY
    kdir = Path.home() / '.kaggle'
    kdir.mkdir(parents=True, exist_ok=True)
    kj = kdir / 'kaggle.json'
    kj.write_text(json.dumps({'username': KAGGLE_USERNAME, 'key': KAGGLE_KEY}), encoding='utf-8')
    kj.chmod(0o600)

# Ensure modern kagglehub (supports KAGGLE_API_TOKEN / KGAT_... tokens)
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-U', 'kagglehub'], check=True)
import kagglehub

has_auth = bool(
    os.environ.get('KAGGLE_API_TOKEN')
    or (os.environ.get('KAGGLE_USERNAME') and os.environ.get('KAGGLE_KEY'))
    or (Path.home() / '.kaggle' / 'kaggle.json').is_file()
)
if not has_auth:
    print('No Kaggle credentials found in env/secrets/~/.kaggle/kaggle.json — calling kagglehub.login()...')
    kagglehub.login()

print('[1/4] Downloading Kaggle wheelhouse...')
WHEELHOUSE_DIR = Path(kagglehub.dataset_download('metric/gemma-4-developer-agent-wheelhouse'))
print('  WHEELHOUSE_DIR =', WHEELHOUSE_DIR)

print('[2/4] Downloading Gemma 4 E4B W4A16-CT weights...')
DECLARED_MODEL = 'gemma-4-e4b-it-qat-w4a16-ct'
MODEL_PATH = Path(kagglehub.model_download(f'google/gemma-4/other/{DECLARED_MODEL}/2'))
print('  MODEL_PATH =', MODEL_PATH)

print('[3/4] Downloading competition data (tasks, snapshots, wheels)...')
DATA_DIR = Path(kagglehub.competition_download('gemma-4-developer-agent'))
print('  DATA_DIR =', DATA_DIR)
assert (DATA_DIR / 'tasks.jsonl').is_file(), f'tasks.jsonl not found under {DATA_DIR}: {list(DATA_DIR.iterdir())[:10]}'
assert (DATA_DIR / 'snapshots').is_dir(), f'snapshots/ not found under {DATA_DIR}'

print('[4/4] Downloading Apache-2.0 on-policy E4B LoRA kit...')
LORA_KIT_DIR = Path(kagglehub.dataset_download('dmitriigluzdov/gemma-4-serving-and-lora-verification-kit'))
print('  LORA_KIT_DIR =', LORA_KIT_DIR)

ADAPTER_EXTRACT_DIR = Path('/content/e4b_onpolicy_lr2e5')
if ADAPTER_EXTRACT_DIR.exists():
    shutil.rmtree(ADAPTER_EXTRACT_DIR)
ADAPTER_EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

zip_cand = LORA_KIT_DIR / 'e4b_adapter_learning_rate_0_00002.zip'
dir_cand = LORA_KIT_DIR / 'e4b_adapter_learning_rate_0_00002'
if zip_cand.is_file():
    with zipfile.ZipFile(zip_cand, 'r') as zf:
        zf.extractall(ADAPTER_EXTRACT_DIR)
elif dir_cand.is_dir():
    shutil.copytree(dir_cand, ADAPTER_EXTRACT_DIR, dirs_exist_ok=True)
else:
    matches = list(LORA_KIT_DIR.rglob('*0_00002*'))
    raise FileNotFoundError(f'Could not locate e4b lr=2e-5 adapter in {LORA_KIT_DIR}; matches={matches[:10]}')

# Flatten nested directory if zip contained a single top-level folder
cfg_hits = list(ADAPTER_EXTRACT_DIR.rglob('adapter_config.json'))
assert cfg_hits, f'adapter_config.json not found in {ADAPTER_EXTRACT_DIR}'
ADAPTER_SRC = cfg_hits[0].parent
assert (ADAPTER_SRC / 'adapter_model.safetensors').is_file(), f'adapter_model.safetensors missing in {ADAPTER_SRC}'
print('  ADAPTER_SRC =', ADAPTER_SRC, '| files =', sorted(p.name for p in ADAPTER_SRC.iterdir()))
print('All Kaggle assets ready!')


import glob
import importlib
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Configure environment variables for offline vLLM serving and LiteLLM routing
os.environ['TRANSFORMERS_NO_TF'] = '1'
os.environ['TRANSFORMERS_NO_FLAX'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['VLLM_NO_USAGE_STATS'] = '1'
os.environ['VLLM_WORKER_MULTIPROC_METHOD'] = 'spawn'
os.environ['VLLM_ENGINE_READY_TIMEOUT_S'] = '1200'
os.environ['VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS'] = '1'
os.environ['NCCL_DEBUG'] = 'WARN'
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
os.environ['LITELLM_TELEMETRY'] = 'False'
os.environ['LITELLM_LOG'] = 'WARNING'
os.environ['OPENAI_API_KEY'] = os.environ.get('OPENAI_API_KEY', 'EMPTY')
os.environ['LOCAL_API_KEY'] = os.environ.get('LOCAL_API_KEY', 'EMPTY')
os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'expandable_segments:True'
os.environ['OTEL_SDK_DISABLED'] = 'true'
os.environ['OTEL_PYTHON_DISABLED'] = 'true'


def configure_logging() -> None:
    for name in (
        'LiteLLM', 'LiteLLM Router', 'LiteLLM Proxy', 'litellm',
        'httpx', 'httpcore', 'openai', 'urllib3',
        'opentelemetry', 'opentelemetry.context',
    ):
        lg = logging.getLogger(name)
        lg.setLevel(logging.WARNING)
        if 'litellm' in name.lower():
            lg.propagate = False
            lg.handlers.clear()


configure_logging()

# Remove broken cutlass .pth hooks if present
for pth_pattern in (
    '/usr/local/lib/python*/dist-packages/*cutlass*.pth',
    '/usr/local/lib/python*/site-packages/*cutlass*.pth',
):
    for pth in glob.glob(pth_pattern):
        try:
            os.unlink(pth)
        except OSError:
            pass

# Restore PEP 440 '+cu128' wheel filenames stripped by Kaggle dataset uploads
tmp_whl = Path('/tmp/wheelhouse')
tmp_whl.mkdir(parents=True, exist_ok=True)
for w in WHEELHOUSE_DIR.glob('*.whl'):
    if 'cutlass' in w.name.lower():
        continue
    target_name = (
        w.name.replace('cu128', '+cu128')
        if ('cu128' in w.name and '+' not in w.name)
        else w.name
    )
    target = tmp_whl / target_name
    if not target.exists():
        try:
            os.symlink(w, target)
        except OSError:
            shutil.copyfile(w, target)

wheels = sorted(str(w) for w in tmp_whl.glob('*.whl'))
print(f'Installing {len(wheels)} wheels from {WHEELHOUSE_DIR} (takes ~1-2 min on Colab)...')
subprocess.run(
    [sys.executable, '-m', 'pip', 'install', '-q', '--no-deps', '--force-reinstall', *wheels],
    check=True,
)
importlib.invalidate_caches()
configure_logging()

import torch
import vllm
import swegemma
import adk_submission
print(f'Installed OK: torch={torch.__version__}, cuda={torch.version.cuda}, vllm={vllm.__version__}')
assert torch.cuda.is_available(), 'CUDA not available after wheelhouse install!'


import json
import os
import shutil
import litellm
import torch
from pathlib import Path
from adk_submission import VllmConfig, VllmServer, discover_adapters
from swegemma.config import ALLOWED_ADAPTER_EXTENSIONS

litellm.drop_params = True

# ── Experiment Configuration ─────────────────────────────────────────────────
RUN_V15B_LORA_T1024 = False  # Set True to also test v15 LoRA + think=1024 (adds ~10-15 min)
WORKING_DIR = Path('/content/heldout24_results')
WORKING_DIR.mkdir(parents=True, exist_ok=True)

HIGH_SIGNAL_16_IDS = ['fastapi_12942', 'fastapi_14360', 'fastapi_14430', 'fastapi_14459', 'fastapi_14482', 'fastapi_14485', 'fastapi_14487', 'fastapi_14605', 'fastapi_14609', 'fastapi_14616', 'fastapi_15785', 'fastapi_9753', 'requests_7315', 'requests_7328', 'requests_7505', 'rich_3064', 'rich_3105', 'rich_3468', 'rich_3471', 'rich_3506', 'rich_3935', 'rich_3944', 'rich_4075', 'rich_4077']

VARIANTS = {
  "v13_budget_fit_t1024": {
    "agent.yaml": "name: gemma4_e4b_v13_budget_fit_t1024\nmodel: gemma-4-e4b-it-qat-w4a16-ct\ndescription: Budget-Fit v2 single-agent (28 calls, 5 min, edit by call 12) + E4B sweet spot (think=1024, include_thoughts=false, max_output_tokens=2560) + verify-patch v6.2.\ninstruction: !include prompts/system.md\nskills:\n  - skills/verify-patch\ntools:\n  - run_command\n  - read_file\n  - edit_file\n  - write_file\n  - get_status\n  - submit_patch\ngenerate_content_config: !include configs/sampling.yaml\n",
    "configs/sampling.yaml": "temperature: 0.2\ntop_p: 0.95\nmax_output_tokens: 2560\nthinking_config:\n  thinking_budget: 1024\n  include_thoughts: false\n",
    "eval_config.yaml": "evaluation:\n  timeout_seconds: 240\n  max_tool_calls: 28\n  max_time_minutes: 5\n  max_turns: 80\n",
    "prompts/system.md": "You are an autonomous software engineer. Your job: make the smallest correct change to SOURCE code in `/workspace` so that the hidden tests for the issue below pass. The hidden tests were written from the issue text, so exact names, messages, types, and behaviours in the issue are the specification.\n\n# HARD LIMITS (28 tool calls, 5 minutes)\n- You have at most **28 tool calls and 5 minutes** total. When either runs out, `/workspace` is graded as-is. An edit that exists counts even if you never call `submit_patch`; an unedited tree always scores 0.\n- NEVER create, modify, or delete test files (`tests/`, `test_*.py`, `*_test.py`) or runner configs (`conftest.py`, `pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`).\n- NEVER run bare `pytest`, `pytest .`, `unittest discover`, `pip install`, or `git commit`.\n- NEVER look outside `/workspace` for packages.\n\n# CALL PLAN\n- **Calls 1\u20138 (Locate & Read)**: Extract exact filenames, symbols, exception types, and edge cases from the issue. Run `git grep -n -F -e '<Symbol1>' -e '<Symbol2>' -- '*.py' | head -25`. Before editing a file, also grep ALL occurrences of the target symbol/attribute inside that file (`git grep -n '<symbol>' <file>`) so you check render/helper methods too (do not stop at `__init__`). Read the target 80\u2013120 lines plus ONE relevant caller or existing test case (`read_file` on `tests/` is encouraged \u2014 just never edit `tests/`).\n- **Calls 9\u201312 (Edit \u2014 HARD DEADLINE)**: You **MUST** have edited a source file by call 12. If unsure, make your best-guess minimal edit in the most likely source location now. Pure reading cannot score.\n  - Preserve existing signatures, public return types, and backward compatibility unless the issue asks otherwise. For FastAPI doc-code issues, edit under `docs_src/`.\n  - Use `edit_file` with a short, verbatim `old_string` (3\u201312 lines, exact indentation, `allow_multiple=false`).\n  - **After ONE failed `edit_file`**, do NOT repeat it \u2014 immediately apply the edit via a single-quoted Python heredoc in `run_command`:\n    ```bash\n    python3 - <<'EOF'\n    from pathlib import Path\n    p = Path(\"path/to/file.py\")\n    s = p.read_text()\n    old = \"\"\"exact old block\"\"\"\n    new = \"\"\"exact new block\"\"\"\n    assert s.count(old) == 1, f\"count={s.count(old)}\"\n    p.write_text(s.replace(old, new, 1))\n    EOF\n    ```\n  - NEVER use multiline `python3 -c \"...\"` and NEVER leave untracked scratch files in `/workspace`.\n- **Calls 13\u201320 (Verify & Fix)**:\n  - Run ONE inline `python3 - <<'EOF' ... EOF` check importing the real installed package (e.g., `from requests.utils import ...`, never `from src...`) verifying the reported bug + 2 edge cases (`None`/empty, leading `.`, `X | Y` `UnionType` if relevant).\n  - Run `run_skill_script(skill_name=\"verify-patch\", file_path=\"scripts/run_verify.py\")` (deterministic syntax/protected-file check + per-file baseline-diff pytest that auto-filters pre-existing sandbox failures).\n  - If `verify-patch` returns `FAIL` or your inline check fails, read the traceback, fix the source code, and re-verify once.\n  - NEVER call `verify-patch` twice in a row without editing source code in between.\n- **Calls 20\u201328 (Finalize & Submit)**: No new exploration. Run `git diff` to confirm only intended source lines changed and every literal name/type from the issue matches, then call `submit_patch`, verify `patch_size > 0`, and end with a 1-sentence summary.\n",
    "skills/verify-patch/SKILL.md": "---\nname: verify-patch\ndescription: Deterministic pre-submit verification gate that inspects git diff, checks Python syntax and protected files, auto-discovers matching real test files in tests/, runs per-file baseline-diff targeted pytest (filtering pre-existing sandbox failures), and outputs a clear PASS / UNVERIFIED / FAIL verdict.\n---\n\n# Verify Patch Skill\n\nRun `scripts/run_verify.py` via `run_skill_script(skill_name=\"verify-patch\", file_path=\"scripts/run_verify.py\")` after a source edit and before `submit_patch`.\n\nVerdicts:\n- `PASS`: Static checks + targeted pytest passed with zero new regressions.\n- `UNVERIFIED`: Static checks passed, but existing pytest could not verify the change (`ENV_SKIPPED` / `TIMEOUT` / `NO_MATCHING_TESTS`). You MUST pass a real inline `python3` repro before submitting.\n- `FAIL`: Syntax error, protected/untracked file, empty diff, or a CONFIRMED NEW REGRESSION in targeted pytest. Never submit on `FAIL`.\n",
    "skills/verify-patch/scripts/run_verify.py": "\"\"\"Deterministic pre-submit patch & per-file baseline-diff pytest verifier (v6.2).\"\"\"\nfrom __future__ import annotations\n\nimport argparse\nimport ast\nimport hashlib\nimport json\nimport os\nimport re\nimport shutil\nimport subprocess\nimport sys\nfrom pathlib import Path\n\nPROTECTED_CONFIGS = {\n    \"conftest.py\", \"pytest.ini\", \"pyproject.toml\", \"tox.ini\",\n    \"setup.cfg\", \".pytest.ini\", \"sitecustomize.py\", \"usercustomize.py\",\n    \"_swegemma_stubs.py\",\n}\n\nGENERIC_DIRS = {\"src\", \"lib\", \"pkg\", \"docs_src\", \"\"}\nGENERIC_STEMS = {\"init\", \"main\", \"base\", \"types\", \"typing\"}\n\n\ndef is_cache_artifact(rel_path: str) -> bool:\n    p = Path(rel_path)\n    parts = set(p.parts)\n    if parts & {\"__pycache__\", \".pytest_cache\", \".mypy_cache\", \".ruff_cache\"}:\n        return True\n    if p.suffix in {\".pyc\", \".pyo\"}:\n        return True\n    return False\n\n\ndef is_protected(rel_path: str) -> bool:\n    p = Path(rel_path)\n    name = p.name\n    if name in PROTECTED_CONFIGS or p.suffix == \".pth\":\n        return True\n    parts_l = {part.lower() for part in p.parts}\n    if parts_l & {\"tests\", \"test\", \"testing\"} and p.suffix == \".py\":\n        return True\n    if name.startswith(\"test_\") and name.endswith(\".py\"):\n        return True\n    if name.endswith(\"_test.py\"):\n        return True\n    return False\n\n\ndef _run(\n    root: Path,\n    cmd: list[str],\n    timeout: int = 30,\n    extra_env: dict[str, str] | None = None,\n    text: bool = True,\n) -> subprocess.CompletedProcess:\n    env = None\n    if extra_env:\n        env = dict(os.environ)\n        env.update(extra_env)\n    return subprocess.run(\n        cmd,\n        cwd=root,\n        capture_output=True,\n        text=text,\n        timeout=timeout,\n        env=env,\n        check=False,\n    )\n\n\ndef _git_out(root: Path, *args: str) -> str:\n    return _run(root, [\"git\", \"-c\", \"safe.directory=*\", *args], timeout=15).stdout.strip()\n\n\ndef _resolve_repo_root(explicit_root: str | None) -> Path:\n    candidates: list[Path] = []\n    if explicit_root:\n        candidates.append(Path(explicit_root))\n    cwd = Path.cwd()\n    for anc in (cwd, *cwd.parents):\n        candidates.append(anc)\n        candidates.append(anc / \"workspace\")\n    candidates.append(Path(\"/workspace\"))\n\n    for cand in candidates:\n        if cand.is_dir() and (cand / \".git\").exists():\n            return cand\n    return cwd if cwd.is_dir() else Path(\"/workspace\")\n\n\ndef _resolve_pytest_cmd(root: Path) -> list[str]:\n    \"\"\"Prefer the sandbox's isolated venv (sibling to workspace/) so editable repo imports resolve.\"\"\"\n    venv_dirs = [root.parent / \"venv\", root / \".venv\", root / \"venv\"]\n    for vd in venv_dirs:\n        py_bin = vd / \"bin\" / \"pytest\"\n        if py_bin.is_file():\n            return [str(py_bin)]\n        py_exe = vd / \"bin\" / \"python\"\n        if py_exe.is_file():\n            return [str(py_exe), \"-m\", \"pytest\"]\n    pytest_bin = shutil.which(\"pytest\")\n    if pytest_bin:\n        return [pytest_bin]\n    return [sys.executable, \"-m\", \"pytest\"]\n\n\ndef _cache_path(root: Path) -> Path:\n    digest = hashlib.sha1(str(root.resolve()).encode(\"utf-8\", errors=\"ignore\")).hexdigest()[:12]\n    return Path(f\"/tmp/_swegemma_verify_cache_{digest}.json\")\n\n\ndef _load_cache(root: Path) -> dict:\n    p = _cache_path(root)\n    if p.is_file():\n        try:\n            data = json.loads(p.read_text(encoding=\"utf-8\"))\n            if isinstance(data, dict):\n                return data\n        except Exception:\n            pass\n    return {\"preexisting_failed\": [], \"last_diff_hash\": \"\", \"same_diff_streak\": 0}\n\n\ndef _save_cache(root: Path, data: dict) -> None:\n    try:\n        _cache_path(root).write_text(json.dumps(data), encoding=\"utf-8\")\n    except Exception:\n        pass\n\n\ndef _extract_changed_symbols(diff_text: str) -> list[str]:\n    syms: list[str] = []\n    seen: set[str] = set()\n    for line in diff_text.splitlines():\n        if line.startswith(\"@@\"):\n            m = re.search(r\"@@\\s+(?:def|async def|class)\\s+([A-Za-z_][A-Za-z0-9_]*)\", line)\n            if m and m.group(1) not in seen and len(m.group(1)) >= 4:\n                seen.add(m.group(1))\n                syms.append(m.group(1))\n        elif line.startswith((\"+\", \"-\")) and not line.startswith((\"+++\", \"---\")):\n            m = re.match(r\"^[+-]\\s*(?:def|async def|class)\\s+([A-Za-z_][A-Za-z0-9_]*)\", line)\n            if m and m.group(1) not in seen and len(m.group(1)) >= 4:\n                seen.add(m.group(1))\n                syms.append(m.group(1))\n    return syms[:8]\n\n\ndef _discover_test_files(root: Path, source_files: list[str], diff_text: str, max_files: int = 2) -> list[str]:\n    test_dirs = [d for d in (\"tests\", \"test\") if (root / d).is_dir()]\n    if not test_dirs:\n        return []\n\n    all_tests: list[Path] = []\n    for td in test_dirs:\n        for p in (root / td).rglob(\"*.py\"):\n            nm = p.name\n            if nm.startswith(\"test_\") or nm.endswith(\"_test.py\"):\n                all_tests.append(p.relative_to(root))\n\n    if not all_tests:\n        return []\n\n    stems: set[str] = set()\n    subpkg_tokens: set[str] = set()\n    is_cli_edit = any(\"cli\" in rel.lower() for rel in source_files)\n\n    for rel in source_files:\n        p = Path(rel)\n        if p.suffix != \".py\":\n            continue\n        clean_stem = p.stem.lower().lstrip(\"_\")\n        if clean_stem and clean_stem not in GENERIC_STEMS:\n            stems.add(clean_stem)\n        pkg_dirs = [part.lower().lstrip(\"_\") for part in p.parts[:-1] if part.lower().lstrip(\"_\") not in GENERIC_DIRS]\n        for subpkg in pkg_dirs[1:]:\n            subpkg_tokens.add(subpkg)\n\n    sym_hits: dict[str, int] = {}\n    symbols = _extract_changed_symbols(diff_text)\n    if symbols:\n        grep_cmd = [\"git\", \"-c\", \"safe.directory=*\", \"grep\", \"-l\", \"-F\"]\n        for sym in symbols:\n            grep_cmd.extend([\"-e\", sym])\n        grep_cmd.append(\"--\")\n        grep_cmd.extend(test_dirs)\n        proc = _run(root, grep_cmd, timeout=10)\n        for line in proc.stdout.splitlines():\n            line = line.strip()\n            if line:\n                sym_hits[line] = sym_hits.get(line, 0) + 1\n\n    scored: list[tuple[int, int, str]] = []\n    for tp in all_tests:\n        rel_str = tp.as_posix()\n        name_l = tp.name.lower()\n        path_l = rel_str.lower()\n        if \"tutorial\" in path_l:\n            continue\n        if not is_cli_edit and (\"_cli\" in name_l or \"cli_\" in name_l or \"/cli/\" in path_l):\n            continue\n        score = 0\n\n        for st in stems:\n            if name_l in (f\"test_{st}.py\", f\"{st}_test.py\"):\n                score += 120\n            elif f\"_{st}.\" in name_l or f\"_{st}_\" in name_l or name_l.startswith(f\"test_{st}_\"):\n                score += 70\n            elif st in name_l:\n                score += 40\n            if f\"/{st}/\" in path_l:\n                score += 30\n\n        for pt in subpkg_tokens:\n            if pt in name_l or f\"test_{pt}\" in path_l or f\"/{pt}/\" in path_l:\n                score += 35\n\n        if rel_str in sym_hits:\n            score += 45 * sym_hits[rel_str]\n\n        if score > 0:\n            scored.append((score, -len(rel_str), rel_str))\n\n    scored.sort(reverse=True)\n    return [item[2] for item in scored[:max_files]]\n\n\ndef _extract_failed_nodes(output: str) -> list[str]:\n    nodes: list[str] = []\n    seen: set[str] = set()\n    for line in output.splitlines():\n        m = re.match(r\"^FAILED\\s+(.+?)(?:\\s+-\\s+.*)?$\", line.strip())\n        if m:\n            node = m.group(1).strip()\n            if \"::\" in node and node not in seen:\n                seen.add(node)\n                nodes.append(node)\n    return nodes[:8]\n\n\ndef _pytest_cache_dir(root: Path) -> str:\n    return str(root.parent / \".verify_pytest_cache\")\n\n\ndef _check_baseline_failures(\n    root: Path,\n    base_ref: str,\n    source_touched: list[str],\n    failed_nodes: list[str],\n    pytest_cmd: list[str],\n) -> set[str]:\n    \"\"\"Run ONLY the failed node IDs against base_ref to separate pre-existing sandbox failures from new regressions.\"\"\"\n    if not failed_nodes or not source_touched:\n        return set()\n\n    saved_bytes: dict[Path, bytes] = {}\n    baseline_bytes: dict[Path, bytes] = {}\n\n    for rel in source_touched:\n        full = root / rel\n        if not full.is_file():\n            continue\n        chk = _run(root, [\"git\", \"-c\", \"safe.directory=*\", \"cat-file\", \"-e\", f\"{base_ref}:{rel}\"], timeout=10)\n        if chk.returncode != 0:\n            continue\n        show = _run(root, [\"git\", \"-c\", \"safe.directory=*\", \"show\", f\"{base_ref}:{rel}\"], timeout=10, text=False)\n        if show.returncode != 0:\n            continue\n        saved_bytes[full] = full.read_bytes()\n        baseline_bytes[full] = show.stdout\n\n    if not saved_bytes:\n        return set()\n\n    try:\n        for full, bdata in baseline_bytes.items():\n            full.write_bytes(bdata)\n        cmd = [\n            *pytest_cmd,\n            *failed_nodes,\n            \"-q\", \"--tb=no\", \"--no-header\",\n            \"--continue-on-collection-errors\",\n            \"-o\", f\"cache_dir={_pytest_cache_dir(root)}\",\n        ]\n        proc = _run(root, cmd, timeout=20, extra_env={\"PYTHONDONTWRITEBYTECODE\": \"1\"})\n        combined = ((proc.stdout or \"\") + \"\\n\" + (proc.stderr or \"\")).strip()\n        return set(_extract_failed_nodes(combined))\n    except subprocess.TimeoutExpired:\n        return set()\n    finally:\n        for full, orig in saved_bytes.items():\n            full.write_bytes(orig)\n\n\ndef _run_single_test_file(\n    root: Path,\n    base_ref: str,\n    source_touched: list[str],\n    pytest_cmd: list[str],\n    test_file: str,\n    preexisting_failed: list[str],\n    filtered_preexisting: list[str],\n) -> tuple[str, str, list[str]]:\n    \"\"\"Run one test file independently so fixture/collection errors in file B never mask real results in file A.\"\"\"\n    new_regs_out: list[str] = []\n    try:\n        for attempt in range(2):\n            flags = [\n                \"--maxfail=5\", \"-q\", \"--tb=short\", \"--no-header\",\n                \"--continue-on-collection-errors\",\n                \"-o\", f\"cache_dir={_pytest_cache_dir(root)}\",\n            ]\n            for node in preexisting_failed:\n                if node.split(\"::\", 1)[0] == test_file:\n                    flags.extend([\"--deselect\", node])\n            proc = _run(root, [*pytest_cmd, test_file, *flags], timeout=25, extra_env={\"PYTHONDONTWRITEBYTECODE\": \"1\"})\n            rc = proc.returncode\n            combined = ((proc.stdout or \"\") + \"\\n\" + (proc.stderr or \"\")).strip()\n            has_real_failure = (\n                \"=== FAILURES ===\" in combined\n                or bool(re.search(r\"\\b\\d+\\s+failed\\b\", combined))\n            )\n            has_env_error = (\n                \"=== ERRORS ===\" in combined\n                or \"ERROR collecting\" in combined\n                or \"ERROR at setup\" in combined\n            )\n            if rc == 0:\n                lines = [ln for ln in combined.splitlines() if ln.strip()]\n                return \"PASS\", \"\\n\".join(lines[-3:]), []\n            if rc == 5:\n                return \"NO_TESTS_COLLECTED\", combined[-400:], []\n            if \"No module named pytest\" in combined or \"disabled\" in combined.lower():\n                return \"UNAVAILABLE\", \"pytest unavailable in sandbox.\", []\n            if has_real_failure:\n                failed_nodes = _extract_failed_nodes(combined)\n                base_failed = _check_baseline_failures(root, base_ref, source_touched, failed_nodes, pytest_cmd)\n                new_regressions = [n for n in failed_nodes if n not in base_failed]\n                if base_failed:\n                    for bf in sorted(base_failed):\n                        if bf not in preexisting_failed:\n                            preexisting_failed.append(bf)\n                        if bf not in filtered_preexisting:\n                            filtered_preexisting.append(bf)\n                if not new_regressions and failed_nodes:\n                    if attempt == 0:\n                        continue\n                    return \"PASS_PREEXISTING_ONLY\", f\"{test_file}: only pre-existing baseline failures ({len(filtered_preexisting)}).\", []\n                new_regs_out = new_regressions or failed_nodes\n                return \"FAIL\", combined[-1600:], new_regs_out\n            if has_env_error:\n                return \"ENV_SKIPPED\", f\"{test_file}: sandbox collection/fixture setup error (0 assertions failed).\", []\n            return \"FAIL\", combined[-1600:], [test_file]\n    except subprocess.TimeoutExpired:\n        return \"TIMEOUT\", f\"{test_file}: timed out after 25s.\", []\n    return \"ENV_SKIPPED\", f\"{test_file}: inconclusive.\", []\n\n\ndef _syntax_context(full: Path, lineno: int | None) -> str:\n    if not lineno or not full.is_file():\n        return \"\"\n    lines = full.read_text(encoding=\"utf-8\", errors=\"ignore\").splitlines()\n    start = max(1, lineno - 2)\n    end = min(len(lines), lineno + 2)\n    return \"\\n\".join(\n        f\"{'>>' if i == lineno else '  '} {i}: {lines[i - 1]}\"\n        for i in range(start, end + 1)\n    )\n\n\ndef verify_workspace(root: Path, run_pytest: bool = True) -> dict:\n    has_baseline = bool(_git_out(root, \"rev-parse\", \"--verify\", \"_swegemma_baseline\"))\n    base_ref = \"_swegemma_baseline\" if has_baseline else \"HEAD\"\n\n    changed = [\n        line for line in _git_out(root, \"diff\", \"--name-only\", base_ref).splitlines()\n        if line.strip() and not is_cache_artifact(line)\n    ]\n    untracked = [\n        line for line in _git_out(root, \"ls-files\", \"--others\", \"--exclude-standard\").splitlines()\n        if line.strip() and not is_cache_artifact(line)\n    ]\n    all_touched = sorted(set(changed) | set(untracked))\n\n    protected_touched = [p for p in all_touched if is_protected(p)]\n    source_touched = [p for p in all_touched if not is_protected(p)]\n\n    syntax_errors = []\n    for rel in source_touched:\n        full = root / rel\n        if full.suffix == \".py\" and full.is_file():\n            try:\n                ast.parse(full.read_text(encoding=\"utf-8\", errors=\"ignore\"), filename=rel)\n            except SyntaxError as exc:\n                syntax_errors.append({\n                    \"file\": rel,\n                    \"line\": exc.lineno,\n                    \"message\": exc.msg,\n                    \"context\": _syntax_context(full, exc.lineno),\n                })\n\n    diff_text = _git_out(root, \"diff\", \"-U0\", base_ref, \"--\", *source_touched) if source_touched else \"\"\n    diff_stat = _git_out(root, \"diff\", \"--stat\", base_ref)\n\n    cache = _load_cache(root)\n    preexisting_failed: list[str] = list(cache.get(\"preexisting_failed\") or [])\n    diff_hash = hashlib.sha256(diff_text.encode(\"utf-8\", errors=\"ignore\")).hexdigest()[:12]\n    same_diff_streak = (cache.get(\"same_diff_streak\", 0) + 1) if (diff_hash and diff_hash == cache.get(\"last_diff_hash\")) else 0\n\n    warnings: list[str] = []\n    if not source_touched:\n        warnings.append(\"No source files modified yet; submit_patch would be empty.\")\n    if protected_touched:\n        revert_cmd = \"git checkout -- \" + \" \".join(protected_touched)\n        warnings.append(f\"Protected test/config files touched (revert immediately with `{revert_cmd}`): {protected_touched}\")\n    if untracked:\n        warnings.append(f\"Untracked files in /workspace (remove scratch files): {untracked}\")\n    if syntax_errors:\n        bad_files = \" \".join(sorted({e[\"file\"] for e in syntax_errors}))\n        warnings.append(\n            f\"Python syntax error(s) detected. Inspect syntax_errors[].context and fix, \"\n            f\"or reset cleanly with `git checkout -- {bad_files}` and re-apply your edit (never leave empty diff).\"\n        )\n    if same_diff_streak >= 1:\n        warnings.append(\n            \"LOOP GUARD: Workspace diff has NOT changed since your previous verify-patch call. \"\n            \"Do NOT call verify-patch again without editing source code first.\"\n        )\n\n    matched_tests: list[str] = []\n    pytest_status = \"SKIPPED\"\n    pytest_summary = \"\"\n    filtered_preexisting: list[str] = []\n    per_file_results: dict[str, str] = {}\n\n    if run_pytest and source_touched and not syntax_errors:\n        matched_tests = _discover_test_files(root, source_touched, diff_text, max_files=2)\n        if not matched_tests:\n            pytest_status = \"NO_MATCHING_TESTS\"\n            pytest_summary = \"No matching test file found under tests/; run a real inline python3 repro instead.\"\n        else:\n            pytest_cmd = _resolve_pytest_cmd(root)\n            summaries: list[str] = []\n            any_fail = False\n            any_pass = False\n            any_preexisting_only = False\n            last_nonpass_status = \"ENV_SKIPPED\"\n            for tf in matched_tests:\n                st, out, regs = _run_single_test_file(\n                    root, base_ref, source_touched, pytest_cmd, tf, preexisting_failed, filtered_preexisting\n                )\n                per_file_results[tf] = st\n                summaries.append(f\"[{tf}: {st}]\\n{out}\".strip())\n                if st == \"FAIL\":\n                    any_fail = True\n                    warnings.append(\n                        f\"CONFIRMED NEW REGRESSION(S) in {tf}: {regs}. Inspect traceback and fix source before submitting.\"\n                    )\n                    break\n                elif st == \"PASS\":\n                    any_pass = True\n                elif st == \"PASS_PREEXISTING_ONLY\":\n                    any_preexisting_only = True\n                else:\n                    last_nonpass_status = st\n            if any_fail:\n                pytest_status = \"FAIL\"\n            elif any_pass:\n                pytest_status = \"PASS\"\n            elif any_preexisting_only:\n                pytest_status = \"PASS_PREEXISTING_ONLY\"\n            else:\n                pytest_status = last_nonpass_status\n            pytest_summary = \"\\n\\n\".join(summaries)[-1800:]\n\n    cache[\"preexisting_failed\"] = preexisting_failed[:20]\n    cache[\"last_diff_hash\"] = diff_hash\n    cache[\"same_diff_streak\"] = same_diff_streak\n    _save_cache(root, cache)\n\n    static_ok = bool(source_touched) and not protected_touched and not syntax_errors\n    pytest_verified = pytest_status in (\"PASS\", \"PASS_PREEXISTING_ONLY\")\n    tests_not_failed = pytest_status != \"FAIL\"\n    overall_ok = static_ok and tests_not_failed\n\n    if not overall_ok:\n        verdict = \"FAIL \u2014 DO NOT call submit_patch yet. Fix the errors/failures below and re-run verify-patch.\"\n    elif pytest_verified:\n        verdict = \"PASS \u2014 Targeted pytest passed with no new regressions. Confirm your inline bug repro + edge cases passed, then call submit_patch.\"\n    else:\n        verdict = (\n            f\"UNVERIFIED ({pytest_status}) \u2014 Static checks passed, BUT existing pytest did NOT verify your fix. \"\n            \"DO NOT submit until you run a PASSING inline python3 repro of the exact issue + edge cases!\"\n        )\n\n    return {\n        \"verdict\": verdict,\n        \"ok\": overall_ok,\n        \"pytest_verified\": pytest_verified,\n        \"repo_root\": str(root),\n        \"source_files\": source_touched,\n        \"protected_files\": protected_touched,\n        \"untracked_files\": untracked,\n        \"syntax_errors\": syntax_errors,\n        \"matched_tests\": matched_tests,\n        \"per_file_results\": per_file_results,\n        \"pytest_status\": pytest_status,\n        \"filtered_preexisting\": filtered_preexisting,\n        \"pytest_output\": pytest_summary,\n        \"diff_stat\": diff_stat[:800],\n        \"warnings\": warnings,\n    }\n\n\ndef main() -> int:\n    parser = argparse.ArgumentParser(description=\"Verify /workspace patch with per-file baseline-diff pytest.\")\n    parser.add_argument(\"--root\", default=None, help=\"Optional explicit repository root (auto-detects cwd/.git first)\")\n    parser.add_argument(\"--no-pytest\", action=\"store_true\", help=\"Skip running pytest\")\n    args = parser.parse_args()\n    root = _resolve_repo_root(args.root)\n    report = verify_workspace(root, run_pytest=not args.no_pytest)\n    print(json.dumps(report, ensure_ascii=False, indent=2)[:4200])\n    return 0 if report[\"ok\"] else 1\n\n\nif __name__ == \"__main__\":\n    raise SystemExit(main())\n"
  },
  "v14_pure_budget_fit_t1024": {
    "agent.yaml": "name: gemma4_e4b_v14_pure_budget_fit_t1024\nmodel: gemma-4-e4b-it-qat-w4a16-ct\ndescription: Pure 0-skill Budget-Fit v2 single-agent (28c/5m/edit<=12) + E4B private thinking sweet spot (think=1024, include_thoughts=false, max_output_tokens=2560).\ninstruction: !include prompts/system.md\ntools:\n  - run_command\n  - read_file\n  - edit_file\n  - write_file\n  - get_status\n  - submit_patch\ngenerate_content_config: !include configs/sampling.yaml\n",
    "configs/sampling.yaml": "temperature: 0.2\ntop_p: 0.95\nmax_output_tokens: 2560\nthinking_config:\n  thinking_budget: 1024\n  include_thoughts: false\n",
    "eval_config.yaml": "evaluation:\n  timeout_seconds: 240\n  max_tool_calls: 28\n  max_time_minutes: 5\n  max_turns: 80\n",
    "prompts/system.md": "You are an autonomous software engineer. Your job: make the smallest correct change to SOURCE code in `/workspace` so that the hidden tests for the issue below pass. The hidden tests were written from the issue text, so exact names, messages, types, and behaviours in the issue are the specification.\n\n# HARD LIMITS (28 tool calls, 5 minutes)\n- You have at most **28 tool calls and 5 minutes** total. When either runs out, `/workspace` is graded as-is. An edit that exists counts even if you never call `submit_patch`; an unedited tree always scores 0.\n- NEVER create, modify, or delete test files (`tests/`, `test_*.py`, `*_test.py`) or runner configs (`conftest.py`, `pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`).\n- NEVER run bare `pytest`, `pytest .`, `unittest discover`, `pip install`, or `git commit`.\n- NEVER look outside `/workspace` for packages.\n\n# CALL PLAN\n- **Calls 1\u20138 (Locate & Read)**:\n  - Extract exact filenames, symbols, exception types, error strings, and edge cases (`None`, empty, leading `.`, `X | Y` `UnionType`, defaults) from the issue.\n  - Run `git grep -n -F -e '<Symbol1>' -e '<Symbol2>' -- '*.py' | head -25`.\n  - Before editing a file, grep ALL occurrences of the target symbol/attribute inside that file (`git grep -n '<symbol>' <file>`) so you check render/helper methods too (do NOT stop at `__init__`).\n  - Read the target 80\u2013120 lines plus ONE relevant caller or existing test case (`read_file` on `tests/` is encouraged \u2014 just never edit `tests/`).\n- **Calls 9\u201312 (Edit \u2014 HARD DEADLINE)**:\n  - You **MUST** have edited a source file by call 12. If unsure, make your best-guess minimal edit in the most likely source location now. Pure reading cannot score.\n  - Preserve existing signatures, public return types, and backward compatibility unless the issue asks otherwise. For FastAPI doc-code issues, edit under `docs_src/`.\n  - Use `edit_file` with a short, verbatim `old_string` (3\u201312 lines, exact indentation, `allow_multiple=false`).\n  - **After ONE failed `edit_file`**, stop repeating it and immediately apply the edit via a single-quoted Python heredoc in `run_command`:\n    ```bash\n    python3 - <<'EOF'\n    from pathlib import Path\n    p = Path(\"path/to/file.py\")\n    s = p.read_text()\n    old = \"\"\"exact old block\"\"\"\n    new = \"\"\"exact new block\"\"\"\n    assert s.count(old) == 1, f\"count={s.count(old)}\"\n    p.write_text(s.replace(old, new, 1))\n    EOF\n    ```\n  - NEVER use multiline `python3 -c \"...\"` and NEVER leave untracked scratch files in `/workspace`.\n- **Calls 13\u201320 (Targeted Verify & Fix \u2014 at most 2\u20133 calls)**:\n  - Run `python3 -m py_compile <edited_file>` AND ONE inline `python3 - <<'EOF' ... EOF` repro importing the real installed package (e.g. `from requests.utils import ...`, NEVER `from src...`) asserting the exact bug + 2 edge cases.\n  - Find the exact matching test file with `find tests -name \"*<module>*.py\" | head -10`, then run `timeout 120 python3 -m pytest tests/path/to/test_file.py -x -q -k <keyword>` (never bare `pytest`).\n  - Pre-existing fixture/import failures unrelated to your change should be ignored \u2014 never edit tests. If a test related to your change fails, fix the source once and re-run once.\n- **Calls 20\u201328 (Final Check & Submit)**:\n  - No new exploration. Run `git status -s && git diff` to confirm only intended source lines changed and zero stray files remain.\n  - Call `submit_patch`, confirm `patch_size > 0` and `files_changed > 0`, and write a 1-sentence summary.\n"
  },
  "v15_onpolicy_lora_budget_fit": {
    "agent.yaml": "name: gemma4_e4b_v15_onpolicy_lora_budget_fit\nmodel: gemma-4-e4b-it-qat-w4a16-ct\nadapter: e4b_onpolicy_lr2e5\ndescription: Pure 0-skill Budget-Fit v2 single-agent (28c/5m) with Apache-2.0 on-policy E4B LoRA (lr=2e-5, r=16, alpha=8, scale=0.5, exact validated decoding).\ninstruction: !include prompts/system.md\ntools:\n  - run_command\n  - read_file\n  - edit_file\n  - write_file\n  - get_status\n  - submit_patch\ngenerate_content_config: !include configs/sampling.yaml\n",
    "configs/sampling.yaml": "temperature: 0.2\ntop_p: 0.95\nmax_output_tokens: 4096\nthinking_config:\n  thinking_budget: 0\n  include_thoughts: false\n",
    "eval_config.yaml": "evaluation:\n  timeout_seconds: 240\n  max_tool_calls: 28\n  max_time_minutes: 5\n  max_turns: 80\n",
    "prompts/system.md": "You are an autonomous software engineer. Your job: make the smallest correct change to SOURCE code in `/workspace` so that the hidden tests for the issue below pass. The hidden tests were written from the issue text, so exact names, messages, types, and behaviours in the issue are the specification.\n\n# HARD LIMITS (28 tool calls, 5 minutes)\n- You have at most **28 tool calls and 5 minutes** total. When either runs out, `/workspace` is graded as-is. An edit that exists counts even if you never call `submit_patch`; an unedited tree always scores 0.\n- NEVER create, modify, or delete test files (`tests/`, `test_*.py`, `*_test.py`) or runner configs (`conftest.py`, `pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`).\n- NEVER run bare `pytest`, `pytest .`, `unittest discover`, `pip install`, or `git commit`.\n- NEVER look outside `/workspace` for packages.\n\n# CALL PLAN\n- **Calls 1\u20138 (Locate & Read)**:\n  - Extract exact filenames, symbols, exception types, error strings, and edge cases (`None`, empty, leading `.`, `X | Y` `UnionType`, defaults) from the issue.\n  - Run `git grep -n -F -e '<Symbol1>' -e '<Symbol2>' -- '*.py' | head -25`.\n  - Before editing a file, grep ALL occurrences of the target symbol/attribute inside that file (`git grep -n '<symbol>' <file>`) so you check render/helper methods too (do NOT stop at `__init__`).\n  - Read the target 80\u2013120 lines plus ONE relevant caller or existing test case (`read_file` on `tests/` is encouraged \u2014 just never edit `tests/`).\n- **Calls 9\u201312 (Edit \u2014 HARD DEADLINE)**:\n  - You **MUST** have edited a source file by call 12. If unsure, make your best-guess minimal edit in the most likely source location now. Pure reading cannot score.\n  - Preserve existing signatures, public return types, and backward compatibility unless the issue asks otherwise. For FastAPI doc-code issues, edit under `docs_src/`.\n  - Use `edit_file` with a short, verbatim `old_string` (3\u201312 lines, exact indentation, `allow_multiple=false`).\n  - **After ONE failed `edit_file`**, stop repeating it and immediately apply the edit via a single-quoted Python heredoc in `run_command`:\n    ```bash\n    python3 - <<'EOF'\n    from pathlib import Path\n    p = Path(\"path/to/file.py\")\n    s = p.read_text()\n    old = \"\"\"exact old block\"\"\"\n    new = \"\"\"exact new block\"\"\"\n    assert s.count(old) == 1, f\"count={s.count(old)}\"\n    p.write_text(s.replace(old, new, 1))\n    EOF\n    ```\n  - NEVER use multiline `python3 -c \"...\"` and NEVER leave untracked scratch files in `/workspace`.\n- **Calls 13\u201320 (Targeted Verify & Fix \u2014 at most 2\u20133 calls)**:\n  - Run `python3 -m py_compile <edited_file>` AND ONE inline `python3 - <<'EOF' ... EOF` repro importing the real installed package (e.g. `from requests.utils import ...`, NEVER `from src...`) asserting the exact bug + 2 edge cases.\n  - Find the exact matching test file with `find tests -name \"*<module>*.py\" | head -10`, then run `timeout 120 python3 -m pytest tests/path/to/test_file.py -x -q -k <keyword>` (never bare `pytest`).\n  - Pre-existing fixture/import failures unrelated to your change should be ignored \u2014 never edit tests. If a test related to your change fails, fix the source once and re-run once.\n- **Calls 20\u201328 (Final Check & Submit)**:\n  - No new exploration. Run `git status -s && git diff` to confirm only intended source lines changed and zero stray files remain.\n  - Call `submit_patch`, confirm `patch_size > 0` and `files_changed > 0`, and write a 1-sentence summary.\n"
  }
}

if RUN_V15B_LORA_T1024:
    v15b = dict(VARIANTS['v15_onpolicy_lora_budget_fit'])
    v15b['agent.yaml'] = v15b['agent.yaml'].replace(
        'gemma4_e4b_v15_onpolicy_lora_budget_fit',
        'gemma4_e4b_v15b_onpolicy_lora_t1024',
    )
    v15b['configs/sampling.yaml'] = VARIANTS['v14_pure_budget_fit_t1024']['configs/sampling.yaml']
    VARIANTS['v15b_onpolicy_lora_t1024'] = v15b

# Materialize variant agent directories
AGENT_DIRS = {}
for vname, files_map in VARIANTS.items():
    adir = WORKING_DIR / 'agents' / vname
    if adir.exists():
        shutil.rmtree(adir)
    for rel, content in files_map.items():
        p = adir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf-8')
    if 'adapter: e4b_onpolicy_lr2e5' in files_map.get('agent.yaml', ''):
        dst = adir / 'adapters' / 'e4b_onpolicy_lr2e5'
        shutil.copytree(ADAPTER_SRC, dst, dirs_exist_ok=True)
    AGENT_DIRS[vname] = adir

for _k in ('v15_onpolicy_lora_budget_fit',):
    pass
# Discover LoRA adapter once from v15 so ONE vLLM server serves BOTH base E4B and LoRA!
v15_adapters = discover_adapters(
    str(AGENT_DIRS['v15_onpolicy_lora_budget_fit']),
    adapter_extensions=ALLOWED_ADAPTER_EXTENSIONS,
)
adapter_map = getattr(v15_adapters, 'adapters', None) or {}
assert 'e4b_onpolicy_lr2e5' in adapter_map, f'Expected e4b_onpolicy_lr2e5 in {adapter_map}'
print('Discovered LoRA adapter:', list(adapter_map.keys()))

if 'server_instance' in globals() and server_instance is not None:
    try:
        server_instance.stop()
    except Exception:
        pass

gpu_count = torch.cuda.device_count()
tp_size = 4 if gpu_count >= 4 else (2 if gpu_count >= 2 else 1)
vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
print(f'Starting single shared vLLM server (Base E4B + LoRA) on {gpu_count}x GPU ({vram_gb:.1f} GB, tp={tp_size})...')

vllm_cfg = VllmConfig(
    model=str(MODEL_PATH),
    port=8000,
    host='127.0.0.1',
    tool_call_parser='gemma4',
    reasoning_parser='gemma4',
    max_model_len=32768,
    dtype='bfloat16' if torch.cuda.is_bf16_supported() else 'auto',
    gpu_memory_utilization=0.90,
    enable_auto_tool_choice=True,
    enable_lora=True,
    max_loras=1,
    max_lora_rank=16,
    scheduler_reserve_full_isl=False,
    tensor_parallel_size=tp_size,
    startup_timeout=60 * 20,
    extra_args=(),
)
server_instance = VllmServer(vllm_cfg, adapter_manifest=v15_adapters)
server_instance.start()
print(f'vLLM server READY on {server_instance.base_url}')

models = server_instance.create_model_registry(
    aliases=[DECLARED_MODEL],
    model_prefix='openai/',
    api_key=os.environ.get('LOCAL_API_KEY', 'EMPTY'),
)
print('Registered model aliases:', repr(models))


import asyncio
import concurrent.futures
import json
import time
import pandas as pd
import torch
import yaml
from pathlib import Path
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps._configs import EventsCompactionConfig
from adk_submission import discover_adapters
from swegemma.config import EvalConfig, build_submission_limits, ALLOWED_ADAPTER_EXTENSIONS
from swegemma.evaluate import Evaluator
from swegemma.models import load_tasks

# Auto-scale concurrency: 2 on L4 (24 GB, proven sweet spot), 4 on A100/H100 (40+ GB)
vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
CONCURRENCY = 4 if vram_gb >= 38 else 2
# Keep exact eval_config.yaml max_time_minutes (5.0 min) to measure true LB budget compliance.
# Set to e.g. 8.0 only if you want to decouple timeouts from quality.
OVERRIDE_MAX_TIME_MINUTES = None

GRAPH_DIR = str(DATA_DIR / 'graphs') if (DATA_DIR / 'graphs').is_dir() else None
EMBEDDINGS_DIR = str(DATA_DIR / 'embeddings') if (DATA_DIR / 'embeddings').is_dir() else None
TASKS_PATH = DATA_DIR / 'tasks.jsonl'

tasks = load_tasks(TASKS_PATH)
wanted = set(HIGH_SIGNAL_16_IDS)
subset = sorted([t for t in tasks if t.instance_id in wanted], key=lambda t: t.instance_id)
assert len(subset) == len(HIGH_SIGNAL_16_IDS), f'Expected {len(HIGH_SIGNAL_16_IDS)}, got {len(subset)}'
print(f'Loaded {len(subset)} high-signal tasks | CONCURRENCY={CONCURRENCY} (VRAM={vram_gb:.1f} GB)')


def run_sync(coro_or_fn, *args, **kwargs):
    fn = (lambda: coro_or_fn(*args, **kwargs)) if (callable(coro_or_fn) and not asyncio.iscoroutine(coro_or_fn)) else (lambda: coro_or_fn)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(fn())).result()
    return asyncio.run(fn())


limits, gen_constraints = build_submission_limits()
all_summaries = []

RUN_ONLY = ('v13_budget_fit_t1024', 'v14_pure_budget_fit_t1024')
for run_name, agent_dir in [(k, v) for k, v in AGENT_DIRS.items() if k in RUN_ONLY]:
    print(f'\n================ RUNNING {run_name} ({len(subset)} tasks) ================', flush=True)
    run_dir = WORKING_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    patches_dir = run_dir / 'patches'
    patches_dir.mkdir(parents=True, exist_ok=True)
    rows_path = run_dir / 'per_task.jsonl'

    # Resume support: load any completed task rows from previous partial run
    done_by_id = {}
    if rows_path.is_file():
        for line in rows_path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                r = json.loads(line)
                done_by_id[r['id']] = r
        if done_by_id:
            print(f'  Resuming {run_name}: {len(done_by_id)}/{len(subset)} tasks already completed.', flush=True)

    ev_raw = yaml.safe_load((agent_dir / 'eval_config.yaml').read_text(encoding='utf-8')) or {}
    ev = ev_raw.get('evaluation', ev_raw)
    max_time_minutes = float(OVERRIDE_MAX_TIME_MINUTES or ev.get('max_time_minutes', 5.0))
    run_adapters = discover_adapters(str(agent_dir), adapter_extensions=ALLOWED_ADAPTER_EXTENSIONS)

    eval_config = EvalConfig(
        tasks_path=TASKS_PATH,
        snapshots_dir=DATA_DIR / 'snapshots',
        results_dir=run_dir / 'results',
        submission_dir=agent_dir,
        models=models,
        sandbox='subprocess',
        timeout_seconds=int(ev.get('timeout_seconds', 240)),
        verification_timeout_seconds=300,
        max_time_minutes=max_time_minutes,
        max_tool_calls=int(ev.get('max_tool_calls', 28)),
        max_turns=int(ev.get('max_turns', 80)),
        limits=limits,
        generation_constraints=gen_constraints,
        adapter_manifest=run_adapters,
        context_cache_config=ContextCacheConfig(min_tokens=2048, ttl_seconds=1800, cache_intervals=10),
        events_compaction_config=EventsCompactionConfig(
            compaction_interval=15, overlap_size=2, token_threshold=14336, event_retention_size=5),
        graph_dir=GRAPH_DIR,
        embeddings_dir=EMBEDDINGS_DIR,
        wheels_dir=DATA_DIR / 'wheels',
        verbose=False,
    )
    evaluator = Evaluator(eval_config)

    async def _eval_subset_parallel():
        sem = asyncio.Semaphore(CONCURRENCY)
        file_lock = asyncio.Lock()
        ordered = [None] * len(subset)

        async def _run_one(i, task):
            if task.instance_id in done_by_id:
                ordered[i] = done_by_id[task.instance_id]
                return
            async with sem:
                started = time.perf_counter()
                patch = ''
                try:
                    result = await evaluator.evaluate_task(task=task, task_index=i + 1, total_tasks=len(subset))
                    patch = (result.agent_patch or '').strip()
                    row = dict(
                        run=run_name, id=task.instance_id, repo=task.repo,
                        resolved=bool(result.resolved), patch_chars=len(patch),
                        tool_calls=result.tool_calls,
                        agent_seconds=round(float(result.duration_seconds or 0.0), 1),
                        test_exit_code=result.test_exit_code,
                        error=getattr(result, 'error', None),
                    )
                except Exception as exc:
                    row = dict(
                        run=run_name, id=task.instance_id, repo=task.repo,
                        resolved=False, patch_chars=0, tool_calls=None,
                        agent_seconds=None, test_exit_code=None, error=repr(exc)[:300],
                    )
                row['wall_seconds'] = round(time.perf_counter() - started, 1)
                if patch:
                    (patches_dir / f'{task.instance_id}.diff').write_text(patch + '\n', encoding='utf-8')
                ordered[i] = row
                async with file_lock:
                    with rows_path.open('a', encoding='utf-8') as f:
                        f.write(json.dumps(row) + '\n')
                    print(
                        f"[{run_name} {i+1}/{len(subset)}] {row['id']}: resolved={row['resolved']} "
                        f"patch={row['patch_chars']} calls={row['tool_calls']} "
                        f"agent={row['agent_seconds']}s wall={row['wall_seconds']}s err={row['error']}",
                        flush=True,
                    )

        await asyncio.gather(*(_run_one(i, task) for i, task in enumerate(subset)))
        return ordered

    variant_t0 = time.perf_counter()
    rows = run_sync(_eval_subset_parallel)
    wall_total = round(time.perf_counter() - variant_t0, 1)
    print(f'[{run_name}] Parallel (concurrency={CONCURRENCY}) wall total: {wall_total}s', flush=True)

    df = pd.DataFrame(rows)
    agent_s = df['agent_seconds'].dropna()
    calls_s = df['tool_calls'].dropna()
    summary = {
        'run': run_name,
        'tasks': len(df),
        'resolved_raw': int(df['resolved'].sum()),
        'resolution_rate_raw': round(float(df['resolved'].mean()), 3),
        'no_patch': int((df['patch_chars'] == 0).sum()),
        'errors': int(df['error'].notna().sum()),
        'mean_tool_calls': round(float(calls_s.mean()), 1) if len(calls_s) else None,
        'agent_seconds_median': round(float(agent_s.median()), 1) if len(agent_s) else None,
        'agent_seconds_mean': round(float(agent_s.mean()), 1) if len(agent_s) else None,
        'wall_total_seconds': wall_total,
    }
    (run_dir / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    all_summaries.append(summary)
    print(json.dumps(summary, indent=2), flush=True)

(WORKING_DIR / 'ablation_summary.json').write_text(json.dumps(all_summaries, indent=2), encoding='utf-8')
print('\n=== ALL VARIANTS COMPLETE ===', flush=True)
