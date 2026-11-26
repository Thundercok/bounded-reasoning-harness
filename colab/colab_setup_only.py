"""One-shot detached setup + launch for Colab L4 (setup only, heartbeat every 25s)."""
import glob
import os
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

t0 = time.time()
import threading
_stop_hb = threading.Event()
def _hb():
    s = time.time()
    while not _stop_hb.wait(25.0):
        print(f'[setup-heartbeat] {int(time.time() - s)}s elapsed', flush=True)
threading.Thread(target=_hb, daemon=True).start()
os.chmod('/root/.kaggle/credentials.json', 0o600)

# Colab may inject its own KAGGLE_* env creds that override credentials.json -> 403. Drop them.
_kenv = [k for k in os.environ if k.upper().startswith('KAGGLE')]
print('dropping env:', _kenv, flush=True)
for k in _kenv:
    os.environ.pop(k, None)

def run(cmd):
    print(f'>>> {cmd[:140]}', flush=True)
    tries = 4 if cmd.startswith('kaggle ') else 1
    for i in range(tries):
        if subprocess.run(cmd, shell=True).returncode == 0:
            return
        if i + 1 < tries:
            print(f'retry {i + 1}/{tries - 1} in 60s', flush=True)
            time.sleep(60)
    raise SystemExit(f'FAILED: {cmd[:140]}')

# 1. System python3.12-venv + kaggle + uv
run('apt-get update -qq && apt-get install -y -qq python3.12-venv')
run(f'{sys.executable} -m pip install -q -U kaggle uv')

# 2. Create /content/py312 venv
if not Path('/content/py312/bin/python').is_file():
    run('uv venv --python 3.12 --seed /content/py312')

# 3. Download wheelhouse, E4B model, and competition data
wh_dir = Path('/content/wheelhouse')
wh_dir.mkdir(parents=True, exist_ok=True)
if not list(wh_dir.glob('*.whl')):
    run('kaggle datasets download -d metric/gemma-4-developer-agent-wheelhouse -p /content/wheelhouse --unzip')

model_dir = Path('/content/model')
model_dir.mkdir(parents=True, exist_ok=True)
if not (model_dir / 'config.json').is_file():
    run('kaggle models instances versions download google/gemma-4/other/gemma-4-e4b-it-qat-w4a16-ct/2 -p /content/model --untar')
    for arc in list(model_dir.glob('*.tar.gz')) + list(model_dir.glob('*.tgz')):
        with tarfile.open(arc, 'r:*') as tf:
            tf.extractall(model_dir)
        arc.unlink()
    for arc in list(model_dir.glob('*.zip')):
        with zipfile.ZipFile(arc, 'r') as zf:
            zf.extractall(model_dir)
        arc.unlink()

comp_dir = Path('/content/comp')
comp_dir.mkdir(parents=True, exist_ok=True)
if not (comp_dir / 'tasks.jsonl').is_file():
    run('kaggle competitions download -c gemma-4-developer-agent -p /content/comp')
    for z in sorted(comp_dir.glob('*.zip')):
        run(f'unzip -q -o "{z}" -d /content/comp')
        z.unlink()

# 4. Install exact Kaggle py312 packages (NO torchaudio, huggingface-hub<2.0, no cutlass .pth)
run('uv pip install --python /content/py312/bin/python --index-url https://download.pytorch.org/whl/cu128 "torch==2.10.0" "torchvision==0.25.0"')

base_pkgs = [
    'litellm', 'pandas', 'pyyaml', 'peft', 'scipy', 'numba', 'einops', 'ray',
    'prometheus_client', 'pyzmq', 'uvloop', 'httptools', 'websockets', 'watchfiles',
    'fastapi', 'uvicorn', 'starlette<0.50', 'annotated-doc', 'pydantic', 'pydantic-settings', 'openai',
    'tiktoken', 'sentencepiece', 'protobuf', 'aiohttp', 'httpx', 'tenacity', 'tqdm',
    'cloudpickle', 'blake3', 'py-cpuinfo', 'ninja', 'filelock', 'huggingface-hub>=1.5.0,<2.0',
    'accelerate', 'pillow', 'sqlalchemy', 'aiosqlite', 'cachetools', 'click', 'rich',
    'packaging', 'regex', 'psutil', 'numpy', 'jsonschema', 'jinja2', 'typing_extensions',
    'google-auth', 'google-cloud-storage', 'google-api-core', 'google-api-python-client',
    'google-cloud-aiplatform', 'google-cloud-bigquery', 'google-cloud-bigquery-storage',
    'google-cloud-bigtable', 'google-cloud-dataplex', 'google-cloud-discoveryengine',
    'google-cloud-pubsub', 'google-cloud-secret-manager', 'google-cloud-spanner',
    'google-cloud-speech', 'opentelemetry-api', 'opentelemetry-sdk',
    'opentelemetry-exporter-otlp-proto-http', 'opentelemetry-exporter-otlp-proto-grpc',
    'opentelemetry-exporter-gcp-logging', 'opentelemetry-exporter-gcp-monitoring',
    'opentelemetry-exporter-gcp-trace', 'opentelemetry-resourcedetector-gcp',
    'authlib', 'graphviz', 'mcp', 'python-dotenv', 'tzlocal', 'watchdog', 'anyio',
    'sniffio', 'distro', 'jiter', 'colorama', 'attrs', 'referencing', 'rpds-py',
    'six', 'lark', 'interegular', 'python-multipart', 'prometheus-fastapi-instrumentator',
    'cbor2', 'triton', 'networkx', 'sympy', 'docker', 'gitpython', 'nvidia-ml-py',
    'opencv-python-headless', 'pyarrow', 'pytest', 'python-json-logger',
    'sqlalchemy-spanner', 'tabulate', 'unidiff', 'setproctitle', 'python-dateutil',
]
run('uv pip install --python /content/py312/bin/python ' + ' '.join(f'"{p}"' for p in base_pkgs))

wheels = sorted(str(w) for w in wh_dir.glob('*.whl') if 'cutlass' not in w.name.lower())
run('uv pip install --python /content/py312/bin/python --no-deps --reinstall ' + ' '.join(f'"{w}"' for w in wheels))
run('uv pip install --python /content/py312/bin/python "huggingface-hub>=1.5.0,<2.0"')
subprocess.run('uv pip uninstall --python /content/py312/bin/python torchaudio', shell=True, check=False)
for pth in glob.glob('/content/py312/lib/python*/site-packages/*cutlass*.pth'):
    try:
        os.unlink(pth)
    except OSError:
        pass

chk = subprocess.run(['/content/py312/bin/python', '-c', 'import torch, vllm, vllm.entrypoints.openai.api_server, swegemma, adk_submission, google.adk; print("SETUP_ALL_OK", torch.__version__, vllm.__version__)'], capture_output=True, text=True)
print(chk.stdout.strip(), chk.stderr[-2000:], flush=True)
if chk.returncode != 0:
    raise SystemExit(chk.returncode)
print(f'SETUP COMPLETE in {time.time() - t0:.1f}s! Starting /content/run_colab_v17_40tasks.py...', flush=True)
_stop_hb.set()
print('READY_FOR_EVAL', flush=True)
