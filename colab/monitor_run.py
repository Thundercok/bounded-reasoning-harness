import os, subprocess
pid = int(open('/content/run.pid').read().strip())
try:
    os.kill(pid, 0); alive = True
except OSError:
    alive = False
print(f'PID={pid} alive={alive}')
print(subprocess.check_output(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used', '--format=csv,noheader'], text=True).strip())
lines = open('/content/run.log').read().splitlines()
keep = [ln for ln in lines if any(k in ln for k in ('vLLM server', 'Server groups', '[v', '=== ', 'PACKED_ZIP', 'RUNNING ', 'Traceback', 'Error', 'resolved_raw', 'no_patch', 'wall_total'))]
print('\n'.join(keep[-60:]))
import glob, json
for f in sorted(glob.glob('/content/*_results/*/per_task.jsonl')):
    rows = [json.loads(x) for x in open(f) if x.strip()]
    print(f"[progress] {f.split('/')[-2]}: {len(rows)} done, resolved={sum(bool(r.get('resolved')) for r in rows)}, "
          f"empty={sum(r.get('patch_chars', 0) == 0 for r in rows)}, timeouts={sum('timeout' in str(r.get('error')) for r in rows)}")
if not keep: print('\n'.join(lines[-15:]))
