import os, subprocess
pid = int(open('/content/run.pid').read().strip())
try:
    os.kill(pid, 0); alive = True
except OSError:
    alive = False
print(f'PID={pid} alive={alive}')
print(subprocess.check_output(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used', '--format=csv,noheader'], text=True).strip())
lines = open('/content/run.log').read().splitlines()
keep = [ln for ln in lines if any(k in ln for k in ('vLLM server', 'Server groups', '[v18', '=== ', 'PACKED_ZIP', 'RUNNING ', 'Traceback', 'Error', 'resolved_raw', 'no_patch', 'wall_total'))]
print('\n'.join(keep[-60:]))
if not keep: print('\n'.join(lines[-15:]))
