#!/usr/bin/env bash
# Fully hands-off: create L4 VM (retry on 503) -> upload creds/scripts/agent tars -> setup detached
# via colab ssh (avoids flaky Jupyter WS "Connection was lost" in colab exec) -> orchestrate_wave.sh.
# usage: bootstrap_vm.sh <session> <work_dir> <results_root> <tar1,tar2,...> <out:launch.py> [...]
set -u
SESSION=$1; W=$2; ROOT=$3; TARS=$4; shift 4
C=${COLAB_CLI:-$(command -v colab)}
REPO=$(cd "$(dirname "$0")/.." && pwd)
log() { echo "[boot $(date +%H:%M:%S)] $*"; }
tmo() { local t=$1; shift; perl -e 'alarm shift; exec @ARGV' "$t" "$@"; }
has_ssh_key() { [ -f ~/.ssh/id_ed25519.pub ] || [ -f ~/.ssh/id_ecdsa.pub ]; }
rexec() {
  local t=$1 f=$2
  if has_ssh_key; then
    tmo "$t" $C ssh -s "$SESSION" -- "python3 -" < "$f" 2>&1
  else
    tmo "$t" $C exec -s "$SESSION" -f "$f" 2>&1
  fi
}

ok=0
for i in $(seq 1 10); do
  OUTN=$(COLAB_CLI_RETRIES=1 $C new -s "$SESSION" --gpu L4 2>&1); echo "$OUTN" | tail -1
  if echo "$OUTN" | grep -q "READY"; then ok=1; break; fi
  log "VM create failed (try $i), waiting 5 min"; sleep 300
done
[ $ok = 1 ] || { log "could not get an L4 VM"; exit 1; }
trap 'rm -f ~/.kaggle/vm_setup.lock; $C stop -s "$SESSION" 2>&1 | tail -1' EXIT

printf 'import os\nos.makedirs("/root/.kaggle", exist_ok=True)\nos.makedirs("/content/agents", exist_ok=True)\nprint("dirs ok")\n' > "$W/mk.py"
# every step retries: a single transient drop used to leave creds missing and fail setup
retry() { local want=$1; shift; for k in 1 2 3 4 5; do O=$(tmo 180 "$@" 2>&1); echo "$O" | tail -1; echo "$O" | grep -q "$want" && return 0; sleep 20; done; log "FAILED after 5 tries: $*"; exit 6; }
retry_exec() { local want=$1 f=$2; for k in 1 2 3 4 5; do O=$(rexec 180 "$f"); echo "$O" | tail -1; echo "$O" | grep -q "$want" && return 0; sleep 20; done; log "FAILED exec after 5 tries: $f"; exit 6; }
up() { retry "Uploaded" $C upload -s "$SESSION" "$1" "$2"; }
retry_exec "dirs ok" "$W/mk.py"
# Kaggle OAuth rotates the refresh token: any local kaggle call while the VM sets up kills the VM's copy (403
# on GenerateAccessToken). Refresh locally now, upload the fresh file, and hold a lock until setup exits.
LOCK=~/.kaggle/vm_setup.lock
(cd "$REPO" && uv run kaggle competitions list -s gemma-4-developer-agent > /dev/null 2>&1)
echo "$SESSION $(date +%s)" > "$LOCK"
up ~/.kaggle/credentials.json /root/.kaggle/credentials.json
up "$REPO/colab/colab_setup_only.py" /content/colab_setup_only.py
up "$REPO/colab/run_colab_40tasks.py" /content/run_colab_40tasks.py
for t in ${TARS//,/ }; do up "$t" "/content/$(basename "$t")"; done

# Setup runs detached on the VM; poll it with short ssh/exec calls.
cat > "$W/setup_exec.py" <<'EOF'
import subprocess, os, sys
if os.path.exists('/content/setup.log'):
    print('SETUP_DETACHED (already started)'); sys.exit(0)
subprocess.Popen('nohup bash -c "python3 -u /content/colab_setup_only.py; echo SETUP_EXIT \\$?" > /content/setup.log 2>&1 &', shell=True)
print('SETUP_DETACHED', flush=True)
EOF
cat > "$W/setup_peek.py" <<'EOF'
import os
s = open('/content/setup.log', errors='replace').read() if os.path.exists('/content/setup.log') else ''
lines = [l for l in s.replace('\r', '\n').splitlines() if '%|' not in l and l.strip()]
print('\n'.join(x[:300] for x in lines[-30:]))
EOF
log "setup start"
retry_exec "SETUP_DETACHED" "$W/setup_exec.py"
for _ in $(seq 1 120); do
  sleep 30
  rexec 120 "$W/setup_peek.py" > "$W/setup.peek" 2>&1
  grep -q "SETUP_EXIT" "$W/setup.peek" && break
done
cp "$W/setup.peek" "$W/setup.log"
rm -f "$LOCK"
tail -3 "$W/setup.log"
trap - EXIT
exec "$REPO/colab/orchestrate_wave.sh" "$SESSION" "$W/setup.log" "$ROOT" "$@"
