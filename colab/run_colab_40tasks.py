"""Generic 40-task runner (24 held-out first, then 16 dev) on Colab L4.

Usage: python run_colab_40tasks.py <out_name> <variant> [<variant> ...]
Variants are read verbatim from /content/agents/<variant>/ (uploaded tarball).
One vLLM boot per adapter group: base-only variants run on a server with
enable_lora=False (same as LB when no adapter is declared).
"""
import asyncio
import json
import logging
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

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

import litellm
import pandas as pd
import torch
import yaml
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps._configs import EventsCompactionConfig
from adk_submission import VllmConfig, VllmServer, discover_adapters
from swegemma.config import EvalConfig, build_submission_limits, ALLOWED_ADAPTER_EXTENSIONS
from swegemma.evaluate import Evaluator
from swegemma.models import load_tasks

litellm.drop_params = True

DATA_DIR = Path('/content/comp')
MODEL_PATH = Path('/content/model')
OUT_NAME = sys.argv[1]
RUN_NAMES = sys.argv[2:]
assert RUN_NAMES, 'pass variant names'
AGENTS_SRC = Path('/content/agents')
WORKING_DIR = Path(f'/content/{OUT_NAME}')
WORKING_DIR.mkdir(parents=True, exist_ok=True)
DECLARED_MODEL = 'gemma-4-e4b-it-qat-w4a16-ct'

HELDOUT24_IDS = ["fastapi_12942", "fastapi_14360", "fastapi_14430", "fastapi_14459", "fastapi_14482", "fastapi_14485", "fastapi_14487", "fastapi_14605", "fastapi_14609", "fastapi_14616", "fastapi_15785", "fastapi_9753", "requests_7315", "requests_7328", "requests_7505", "rich_3064", "rich_3105", "rich_3468", "rich_3471", "rich_3506", "rich_3935", "rich_3944", "rich_4075", "rich_4077"]
DEV16_IDS = ["fastapi_14297", "fastapi_14301", "fastapi_14361", "fastapi_14372", "fastapi_14458", "fastapi_14463", "fastapi_14786", "requests_6644", "requests_7205", "requests_7309", "requests_7427", "rich_3006", "rich_3296", "rich_3518", "rich_3894", "rich_3905"]
ORDERED_40_IDS = ["fastapi_12942", "fastapi_14360", "fastapi_14430", "fastapi_14459", "fastapi_14482", "fastapi_14485", "fastapi_14487", "fastapi_14605", "fastapi_14609", "fastapi_14616", "fastapi_15785", "fastapi_9753", "requests_7315", "requests_7328", "requests_7505", "rich_3064", "rich_3105", "rich_3468", "rich_3471", "rich_3506", "rich_3935", "rich_3944", "rich_4075", "rich_4077", "fastapi_14297", "fastapi_14301", "fastapi_14361", "fastapi_14372", "fastapi_14458", "fastapi_14463", "fastapi_14786", "requests_6644", "requests_7205", "requests_7309", "requests_7427", "rich_3006", "rich_3296", "rich_3518", "rich_3894", "rich_3905"]

AGENT_DIRS = {}
for vname in RUN_NAMES:
    src_dir = AGENTS_SRC / vname
    assert (src_dir / 'agent.yaml').is_file(), f'missing {src_dir}/agent.yaml'
    adir = WORKING_DIR / 'agents' / vname
    if adir.exists():
        shutil.rmtree(adir)
    shutil.copytree(src_dir, adir)
    AGENT_DIRS[vname] = adir

tasks_by_id = {t.instance_id: t for t in load_tasks(DATA_DIR / 'tasks.jsonl')}
subset = [tasks_by_id[tid] for tid in ORDERED_40_IDS]
assert len(subset) == 40

# Group variants by adapter set so each group gets one server boot.
GROUPS = {}
for vname, adir in AGENT_DIRS.items():
    man = discover_adapters(str(adir), adapter_extensions=ALLOWED_ADAPTER_EXTENSIONS)
    keys = tuple(sorted((getattr(man, 'adapters', None) or {}).keys()))
    GROUPS.setdefault(keys, {'manifest': man, 'runs': []})['runs'].append(vname)
print('Server groups:', {k: v['runs'] for k, v in GROUPS.items()}, flush=True)

limits, gen_constraints = build_submission_limits()
CONCURRENCY = 2
all_summaries = []

for adapter_keys, group in GROUPS.items():
    use_lora = bool(adapter_keys)
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
        enable_lora=use_lora,
        max_loras=max(1, len(adapter_keys)),
        max_lora_rank=16,
        scheduler_reserve_full_isl=False,
        tensor_parallel_size=1,
        startup_timeout=60 * 20,
        extra_args=(),
    )
    server_instance = VllmServer(vllm_cfg, adapter_manifest=group['manifest']) if use_lora else VllmServer(vllm_cfg)
    t_boot = time.perf_counter()
    server_instance.start()
    print(f'vLLM server READY (lora={adapter_keys}) in {time.perf_counter() - t_boot:.1f}s on {server_instance.base_url}', flush=True)
    try:
      models = server_instance.create_model_registry(
          aliases=[DECLARED_MODEL],
          model_prefix='openai/',
          api_key=os.environ.get('LOCAL_API_KEY', 'EMPTY'),
      )
      print('Registered models:', repr(models), flush=True)
      for run_name in group['runs']:
        agent_dir = AGENT_DIRS[run_name]
        print(f'\n================ RUNNING {run_name} (40 tasks: 24 held-out + 16 dev, concurrency={CONCURRENCY}) ================', flush=True)
        run_dir = WORKING_DIR / run_name
        run_dir.mkdir(parents=True, exist_ok=True)
        patches_dir = run_dir / 'patches'
        patches_dir.mkdir(parents=True, exist_ok=True)
        rows_path = run_dir / 'per_task.jsonl'

        done_by_id = {}
        if rows_path.is_file():
            for ln in rows_path.read_text(encoding='utf-8').splitlines():
                if ln.strip():
                    r = json.loads(ln)
                    done_by_id[r['id']] = r

        ev_raw = yaml.safe_load((agent_dir / 'eval_config.yaml').read_text(encoding='utf-8')) or {}
        ev = ev_raw.get('evaluation', ev_raw)
        run_adapters = discover_adapters(str(agent_dir), adapter_extensions=ALLOWED_ADAPTER_EXTENSIONS)

        eval_config = EvalConfig(
            tasks_path=DATA_DIR / 'tasks.jsonl',
            snapshots_dir=DATA_DIR / 'snapshots',
            results_dir=run_dir / 'results',
            submission_dir=agent_dir,
            models=models,
            sandbox='subprocess',
            timeout_seconds=int(ev.get('timeout_seconds', 240)),
            verification_timeout_seconds=300,
            max_time_minutes=float(ev.get('max_time_minutes', 5.0)),
            max_tool_calls=int(ev.get('max_tool_calls', 28)),
            max_turns=int(ev.get('max_turns', 80)),
            limits=limits,
            generation_constraints=gen_constraints,
            adapter_manifest=run_adapters,
            context_cache_config=ContextCacheConfig(min_tokens=2048, ttl_seconds=1800, cache_intervals=10),
            events_compaction_config=EventsCompactionConfig(
                compaction_interval=15, overlap_size=2, token_threshold=14336, event_retention_size=5),
            graph_dir=str(DATA_DIR / 'graphs') if (DATA_DIR / 'graphs').is_dir() else None,
            embeddings_dir=str(DATA_DIR / 'embeddings') if (DATA_DIR / 'embeddings').is_dir() else None,
            wheels_dir=DATA_DIR / 'wheels',
            verbose=False,
        )
        evaluator = Evaluator(eval_config)

        async def _eval_parallel():
            sem = asyncio.Semaphore(CONCURRENCY)
            file_lock = asyncio.Lock()
            ordered = [None] * len(subset)

            async def _one(i, task):
                if task.instance_id in done_by_id:
                    ordered[i] = done_by_id[task.instance_id]
                    return
                async with sem:
                    started = time.perf_counter()
                    patch = ''
                    try:
                        res = await evaluator.evaluate_task(task=task, task_index=i + 1, total_tasks=len(subset))
                        patch = (res.agent_patch or '').strip()
                        row = dict(
                            run=run_name, id=task.instance_id, repo=task.repo,
                            split='heldout24' if task.instance_id in set(HELDOUT24_IDS) else 'dev16',
                            resolved=bool(res.resolved), patch_chars=len(patch),
                            tool_calls=res.tool_calls,
                            agent_seconds=round(float(res.duration_seconds or 0.0), 1),
                            test_exit_code=res.test_exit_code,
                            error=getattr(res, 'error', None),
                        )
                    except Exception as exc:
                        row = dict(
                            run=run_name, id=task.instance_id, repo=task.repo,
                            split='heldout24' if task.instance_id in set(HELDOUT24_IDS) else 'dev16',
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
                            f"[{run_name} {i+1}/40 {row['split']}] {row['id']}: resolved={row['resolved']} "
                            f"patch={row['patch_chars']} calls={row['tool_calls']} "
                            f"agent={row['agent_seconds']}s err={row['error']}",
                            flush=True,
                        )

            await asyncio.gather(*(_one(i, t) for i, t in enumerate(subset)))
            return ordered

        vt0 = time.perf_counter()
        rows = asyncio.run(_eval_parallel())
        wall_total = round(time.perf_counter() - vt0, 1)
        df = pd.DataFrame(rows)
        agent_s = df['agent_seconds'].dropna()
        calls_s = df['tool_calls'].dropna()
        summary = {
            'run': run_name,
            'tasks': len(df),
            'resolved_raw': int(df['resolved'].sum()),
            'no_patch': int((df['patch_chars'] == 0).sum()),
            'errors': int(df['error'].notna().sum()),
            'mean_tool_calls': round(float(calls_s.mean()), 1) if len(calls_s) else None,
            'agent_seconds_median': round(float(agent_s.median()), 1) if len(agent_s) else None,
            'agent_seconds_mean': round(float(agent_s.mean()), 1) if len(agent_s) else None,
            'wall_total_seconds': wall_total,
        }
        (run_dir / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        all_summaries.append(summary)
        print(f'=== SUMMARY {run_name} ===\n' + json.dumps(summary, indent=2), flush=True)

    finally:
      try:
          server_instance.stop()
      except Exception:
          pass
      time.sleep(20)  # let the next group's vLLM boot reclaim GPU memory and port 8000

(WORKING_DIR / 'ablation_summary.json').write_text(json.dumps(all_summaries, indent=2), encoding='utf-8')
out_zip = Path(f'/content/{OUT_NAME}.zip')
with zipfile.ZipFile(out_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
    for fp in sorted(WORKING_DIR.rglob('*')):
        if not fp.is_file():
            continue
        rel = fp.relative_to(WORKING_DIR).as_posix()
        if rel.startswith('agents/'):
            continue
        if fp.stat().st_size > 10 * 1024 * 1024:
            continue
        zf.write(fp, arcname=rel)
print(f'PACKED_ZIP: {out_zip} ({out_zip.stat().st_size / 1e6:.2f} MB)', flush=True)
print('=== ALL VARIANTS COMPLETE ===', flush=True)
