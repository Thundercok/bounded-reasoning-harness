#!/usr/bin/env bash
# Hands-off Colab waves on ONE VM: wait for setup -> for each wave: launch, heartbeat,
# download + local regrade -> STOP VM after the last wave (or on any failure).
# Uses `colab ssh -- "python3 -"` instead of `colab exec` to avoid flaky Jupyter WS
# "Connection was lost" errors and hanging non-daemon threads in jupyter-kernel-client.
# usage: orchestrate_wave.sh <session> <setup_log|""> <results_root> <out_name>:<launch.py> [...]
#   each launch.py must write /content/run.log, /content/run.pid and /content/<out_name>.zip
set -u
SESSION=$1; SETUP_LOG=$2; ROOT=$3; shift 3
C=${COLAB_CLI:-$(command -v colab)}
REPO=$(cd "$(dirname "$0")/.." && pwd)
MON=$REPO/colab/monitor_run.py
tmo() { local t=$1; shift; perl -e 'alarm shift; exec @ARGV' "$t" "$@"; }
log() { echo "[orch $(date +%H:%M:%S)] $*"; }
has_ssh_key() { [ -f ~/.ssh/id_ed25519.pub ] || [ -f ~/.ssh/id_ecdsa.pub ]; }
rexec() {
  local t=$1 f=$2 out rc
  for _try in 1 2; do
    if has_ssh_key; then
      out=$(tmo "$t" $C ssh -s "$SESSION" -- "python3 -" < "$f" 2>&1)
    else
      out=$(tmo "$t" $C exec -s "$SESSION" -f "$f" 2>&1)
    fi
    rc=$?
    if [ $rc -eq 0 ] && ! echo "$out" | grep -qiE "Connection was lost|WebSocket connection failed|Bad gateway"; then
      printf '%s\n' "$out"
      return 0
    fi
    [ $_try -lt 2 ] && sleep 10
  done
  printf '%s\n' "$out"
  return 1
}
dl() {
  local remote=$1 localp=$2 out
  for _try in 1 2 3; do
    out=$(tmo 300 $C download -s "$SESSION" "$remote" "$localp" 2>&1)
    echo "$out" | tail -1
    [ -s "$localp" ] && return 0
    sleep 15
  done
  return 1
}
# EXTRA_DL="remote1:local1 remote2:local2" downloads extra VM files (e.g. a trained adapter) before every stop
extra_dl() { for pair in ${EXTRA_DL:-}; do dl "${pair%%:*}" "${pair#*:}"; done; }
stop_vm() { extra_dl; log "STOPPING VM $SESSION"; $C stop -s "$SESSION" 2>&1 | tail -1; }
trap 'stop_vm' EXIT

if [ -n "$SETUP_LOG" ]; then
  for _ in $(seq 1 300); do grep -q "SETUP_EXIT" "$SETUP_LOG" 2>/dev/null && break; sleep 10; done
  if ! grep -q "SETUP_EXIT 0" "$SETUP_LOG"; then
    log "SETUP FAILED:"; tr '\r' '\n' < "$SETUP_LOG" | grep -v '%|' | tail -25; exit 2
  fi
  log "setup OK"
fi

for spec in "$@"; do
  OUT=${spec%%:*}; LAUNCH=${spec#*:}; LOCAL=$ROOT/$OUT
  log "=== WAVE $OUT ==="
  # a dropped connection can kill the exec before the launch runs (VM then idles for hours): retry until LAUNCHED
  launched=0
  for a in 1 2 3 4 5; do
    LO=$(rexec 180 "$LAUNCH"); echo "$LO" | tail -5
    if echo "$LO" | grep -q "LAUNCHED"; then launched=1; break; fi
    log "launch attempt $a did not report LAUNCHED, retrying in 60s"; sleep 60
  done
  [ $launched = 1 ] || { log "$OUT launch failed"; exit 5; }
  done_ok=0
  for n in $(seq 1 60); do
    [ $n = 1 ] && sleep 240 || sleep 600
    M=$(rexec 120 "$MON")
    echo "$M" | grep -E "^\[|SUMMARY|resolved_raw|PID=" | tail -4
    if echo "$M" | grep -q "PACKED_ZIP"; then log "$OUT complete"; done_ok=1; break; fi
    if echo "$M" | grep -qE "disallowed extension|SubmissionValidationError"; then log "$OUT VALIDATION ERROR"; echo "$M" | grep -m3 -E "disallowed|Validation"; break; fi
    if echo "$M" | grep -q "alive=False"; then log "$OUT process died"; echo "$M" | tail -40; break; fi
    # a single 503 can mention "terminated" while the VM is fine: only give up after 3 lost checks in a row
    if echo "$M" | grep -qiE "not found|terminated|No active session|Connection was lost|WebSocket|Bad gateway|503"; then
      lost=$(( ${lost:-0} + 1 )); log "monitor check failed ($lost/3)"; echo "$M" | tail -2
      [ $lost -ge 3 ] && { log "VM LOST"; exit 3; }
    else lost=0; fi
  done
  mkdir -p "$LOCAL"
  dl /content/run.log "$LOCAL/run.log"
  if [ $done_ok != 1 ]; then
    log "wave $OUT failed - salvaging partial results"; tail -40 "$LOCAL/run.log"
    printf 'import shutil, os\nshutil.make_archive("/content/%s", "zip", "/content/%s")\nprint("SALVAGED", os.path.getsize("/content/%s.zip"))\n' "$OUT" "$OUT" "$OUT" > "$LOCAL/salvage.py"
    rexec 180 "$LOCAL/salvage.py" | tail -1
    dl "/content/$OUT.zip" "$LOCAL/$OUT.zip"
    (cd "$LOCAL" && unzip -q -o "$OUT.zip" -x 'agents/*')
    exit 4
  fi
  dl "/content/$OUT.zip" "$LOCAL/$OUT.zip"
  (cd "$LOCAL" && unzip -q -o "$OUT.zip")
  # regrade in background locally while the next wave runs on the VM
  (cd "$REPO" && uv run python tools/regrade_results.py "$LOCAL" --out "$LOCAL/true_graded.json" > "$LOCAL/regrade.log" 2>&1; \
   grep -v "^  " "$LOCAL/regrade.log" | python3 -c "import sys; print(sys.stdin.read()[-3000:])") &
done
trap - EXIT
stop_vm
wait
log "ALL WAVES DONE"
