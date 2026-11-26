#!/usr/bin/env python3
"""Turn the kit's native 31B Budget-Fit trajectories into e4b_train.py input files.

usage: lora_prep_native.py <lora_native_resolved.jsonl> <root> <model_dir>
writes <root>/data/{train,validation}.jsonl, <root>/run/model -> <model_dir>, <root>/transport-manifest.json

Each output row holds the served token IDs as saved (prompt + target, never retokenized), which is
what e4b_train.py's loss() needs: input_ids, prompt_tokens, context_tokens, target_tokens.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

MAX_CONTEXT = 8192      # e4b_train.py asserts every training example fits 8192 tokens
MAX_TARGET = 1024       # drop very long targets: serving runs thinking_budget=1024 on a 5-minute clock
VAL_EVERY = 10          # every 10th repository (sorted) is validation; split by repo as the kit recipe asks

src, root, model_dir = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
(root / 'data').mkdir(parents=True, exist_ok=True)
(root / 'run').mkdir(parents=True, exist_ok=True)
link = root / 'run' / 'model'
if not link.exists():
    os.symlink(model_dir, link)
assert (link / 'config.json').is_file() and (link / 'model.safetensors').is_file(), 'model dir must hold config.json + model.safetensors'

trajs = [json.loads(x) for x in src.open()]
repos = sorted({t['repository'] for t in trajs})
val_repos = set(repos[::VAL_EVERY])
rows = {'train': [], 'validation': []}
dropped = {'context': 0, 'target': 0, 'unresolved': 0}
for t in trajs:
    if not t.get('resolved', True):
        dropped['unresolved'] += 1
        continue
    split = 'validation' if t['repository'] in val_repos else 'train'
    for e in t['sft_examples']:
        p, c = e['prompt_token_ids'], e['completion_token_ids']
        if len(p) + len(c) > MAX_CONTEXT:
            dropped['context'] += 1
            continue
        if len(c) > MAX_TARGET:
            dropped['target'] += 1
            continue
        rows[split].append({'input_ids': p + c, 'prompt_tokens': len(p), 'context_tokens': len(p) + len(c),
                            'target_tokens': len(c), 'repository': t['repository'], 'turn': e['turn']})
for split, rs in rows.items():
    with (root / 'data' / f'{split}.jsonl').open('w') as f:
        for r in rs:
            f.write(json.dumps(r) + '\n')
# e4b_train.py records the base checkpoint hash from this file; hash the config (the 11.5GB weights are too slow to hash here)
sha = hashlib.sha256((link / 'config.json').read_bytes()).hexdigest()
(root / 'transport-manifest.json').write_text(json.dumps({'public_model': {'sha256': 'config:' + sha}}) + '\n')
print('PREP_OK', json.dumps({'train_steps': len(rows['train']), 'val_steps': len(rows['validation']),
      'train_target_tokens': sum(r['target_tokens'] for r in rows['train']), 'val_repos': sorted(val_repos),
      'dropped': dropped}), flush=True)
assert len(rows['train']) > 200 and len(rows['validation']) > 10
