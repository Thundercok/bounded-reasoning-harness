#!/usr/bin/env bash
# On the Colab VM after colab_setup_only.py: train an E4B LoRA on the kit's native 31B trajectories,
# package it as <variant> = <parent> + adapter, then run the 40-task eval of <variant> vs <parent>.
# usage: lora_pipeline.sh <parent_variant> <variant> <out_name>   (expects /content/agents/<parent> and /content/lora_license/)
set -euxo pipefail
PARENT=$1; VARIANT=$2; OUT=$3
PY=/content/py312/bin/python
ADAPTER=e4b_native_lr2e5
export ONPOLICY_LR=0.00002

# 1. trainer deps (no-deps so torch/transformers from the official wheelhouse stay untouched)
uv pip install --python $PY --no-deps peft accelerate psutil
$PY -c 'import peft, accelerate, transformers, compressed_tensors, torch; print("TRAIN_DEPS_OK", peft.__version__, accelerate.__version__, transformers.__version__, torch.__version__)'

# 2. native trajectories from the serving & LoRA verification kit
mkdir -p /content/kit
if [ ! -s /content/kit/lora_native_resolved.jsonl ]; then
  for i in 1 2 3 4; do
    kaggle datasets download -d dmitriigluzdov/gemma-4-serving-and-lora-verification-kit -f lora_native_resolved.jsonl -p /content/kit && break
    sleep 60
  done
  [ -f /content/kit/lora_native_resolved.jsonl.zip ] && (cd /content/kit && unzip -o -q lora_native_resolved.jsonl.zip)
fi
$PY -I /content/lora_prep_native.py /content/kit/lora_native_resolved.jsonl /content/lora /content/model

# 3. train (kit's e4b_train.py unchanged: r=16, alpha=8, scale 0.5, one pass, accumulation 8)
$PY -u /content/e4b_train.py --root /content/lora --mode pilot
cat /content/lora/out/pilot-result.json

# 4. package <variant> = <parent> + adapter
rm -rf /content/agents/$VARIANT
cp -r /content/agents/$PARENT /content/agents/$VARIANT
mkdir -p /content/agents/$VARIANT/adapters/$ADAPTER
cp /content/lora/out/pilot-adapter/adapter_model.safetensors /content/lora/out/pilot-adapter/adapter_config.json /content/agents/$VARIANT/adapters/$ADAPTER/
cp /content/lora_license/* /content/agents/$VARIANT/adapters/$ADAPTER/
$PY - "$VARIANT" "$ADAPTER" <<'EOF'
import sys
v, ad = sys.argv[1], sys.argv[2]
p = f'/content/agents/{v}/agent.yaml'
s = open(p).read()
lines = s.splitlines(keepends=True)
assert lines[0].startswith('name: ') and lines[1].startswith('model: ') and 'adapter:' not in s
lines[0] = f'name: gemma4_e4b_{v}\n'
lines.insert(2, f'adapter: {ad}\n')
open(p, 'w').write(''.join(lines))
print(''.join(lines[:4]))
EOF
find /content/agents -name "._*" -delete
mkdir -p /content/lora_export
cp -r /content/agents/$VARIANT /content/lora_export/
cp /content/lora/out/*.json /content/lora/out/training-curve.jsonl /content/lora_export/ 2>/dev/null || true
(cd /content && rm -f lora_export.zip && zip -q -r lora_export.zip lora_export)
ls -la /content/lora_export.zip
echo LORA_PACKAGED

# 5. 40-task eval: new LoRA arm vs its parent on the same VM
exec $PY -u /content/run_colab_40tasks.py "$OUT" "$VARIANT" "$PARENT"
