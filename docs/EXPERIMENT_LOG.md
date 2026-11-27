# Experiment log (v16 → v23)

Start here when you look back. This page records what each variant changed, how it scored and what we concluded.
The README covers v13 and v14 and the harness itself.

## How to read the numbers

- **40-task panel**: 16 dev tasks plus 24 held-out tasks from fastapi, requests and rich, run on one Colab L4.
  Each patch is regraded locally with the gold tests by `tools/regrade_results.py`.
- **The panel is noisy.** The same v14 zip scored 7/40 on one VM and 13/40 on another.
  Treat any gap under about 5 tasks as noise. Only compare arms that ran **on the same VM in the same wave** (the "control" column).
- **The public LB** has about 58 hidden tasks, so 1 task ≈ 0.017. A LB score is roughly a quarter to a third of the local pass rate.
- Columns: `pass` = true passes out of 40, `HO` = held-out passes out of 24, `empty` = no patch at all,
  `TO` = hit the time cap, `med` = median agent seconds per task.

## Kaggle LB submissions

| Ref | Variant | What it is | Public LB |
|---|---|---|---|
| 58746557 | v8a | think=1024 + verify-patch skill | 0.03 |
| 58817076 | v14 | pure 0-skill Budget-Fit, 28 calls / 5 min, think=1024 | **0.06** |
| 58896800 | v18d | v14 + SUBMIT GATE prompt paragraph | 0.03 |

The small-model LB on 2026-11-26 had 0.12 ×2, 0.10 ×34, 0.08 ×196 and 0.06 ×414.

## Variant tree

Every variant changes **one thing** relative to its parent.

```
v14 (0-skill Budget-Fit, 28c/5m, think=1024, max_out=2560)
├── v18a  max_output_tokens 4096
├── v18b  + on-policy LoRA adapter
├── v18c  + code-intel tools
├── v24   {problem_description} pinned at the end of the system prompt (no SUBMIT GATE)
├── v25   2-stage SequentialAgent on exact v14 (Stage 1 no submit_patch -> Stage 2 shared-context live repro & submit)
└── v18d  + SUBMIT GATE paragraph            ← submitted (LB 0.03)
    ├── v19a  + on-policy LoRA adapter
    ├── v19b  + code-intel tools
    ├── v20a  EDIT EARLY (first edit by call 8)
    ├── v20b  thinking_budget 512
    ├── v20c  targeted pytest timeout 45s
    ├── v21a  + LoRA trained on the kit's native 31B trajectories (built on the VM by colab/lora_pipeline.sh)
    ├── v22   max_time_minutes 8 (was 5)
    ├── v23a  submit-gate check: real module import instead of py_compile
    └── v23b  {problem_description} pinned at the end of the system prompt
v13 (README) ── v16  + on-policy LoRA adapter
v17a  surgical Budget-Fit prompt rewrite     v17b  2-stage sequential agent with a fresh critic
```

## Results by wave

| Wave | Variant | pass /40 | HO /24 | empty | TO | med s | Control on same VM | Verdict |
|---|---|---|---|---|---|---|---|---|
| v17 | v17a surgical prompt | 7 | 1 | 4 | 10 | 226 | v14 14/40 | worse |
| v17 | v17b seq2 fresh critic | 8 | 0 | 6 | 16 | 263 | v14 14/40 | worse, slower |
| W1 | v18a max_out 4096 | 8 | 2 | 8 | 13 | 151 | – | no gain |
| W1 | v18b LoRA | 11 | 3 | 2 | 14 | 194 | – | noise |
| W1 | v18c code-intel | 11 | 2 | 5 | 13 | 169 | – | noise |
| W2 | **v18d submit gate** | **11** | 3 | **1** | 10 | 158 | v14 7/40 | best of W2, fewest empty → **submitted** |
| W3 | v19a v18d+LoRA | 11 | 2 | 5 | 16 | 228 | v14 13/40 | no gain |
| W3 | v19b v18d+code-intel | 13 | 2 | 2 | 9 | 155 | v14 13/40 | tie |
| W5 | v21a native 31B LoRA | 10 | 2 | 7 | 23 | 309 | – | worse: copies 31B's long exploration, times out |
| W4b | v22 8-minute cap | 8 | 2 | 2 | **6** | 224 | v18d 9/40 | fewer timeouts, but the extra patches are wrong |
| W5b | v23a import gate | 10 | 2 | **3** | 12 | 234 | v18d 9/40 | tie (+1, noise); `import_broken` 4 vs 5 (did not stop crashes), halved empty (3 vs 6) |
| W5b | v23b pinned issue | **12** | **3** | 6 | **11** | **165** | v18d 9/40 | best of W5 (+3, fastest, fewest TO); still built on v18d (LB 0.03) |
| W6 | v24 v14+pinned issue | 10 | 1 | 5 | 11 | **174** | v14 10/40 | tie (10 vs 10); faster (174s vs 210s) but lower HO (1 vs 2) and more runtime_exc (10 vs 7) — not worth a LB slot |
| W7 | **v25 seq2 v14+repro** | **11** | **2** | **1** | **8** | 166 | v14 9/40 | **best structural win**: +2 pass (11/23 informative, first-ever `fastapi_14297` pass), 4x fewer empty (1 vs 4), -5 TO (8 vs 13), -3 runtime_exc (6 vs 9) |

v16 and v20a/b/c were built but have no 40-task result. Their wave was lost to Colab VM failures.

## Why patches fail (Wave 4b–Wave 7, regraded locally)

| Failure class | W4b v22 | W4b v18d | W5 v23a | W5 v23b | W5 v18d | W6 v24 | W6 v14 | W7 v25 | W7 v14 |
|---|---|---|---|---|---|---|---|---|---|
| PASS | 8 | 9 | 10 | **12** | 9 | 10 | 10 | **11** | 9 |
| patch breaks import (collection `NameError`/`ImportError`) | 5 | 4 | 4 | 4 | 5 | **2** | 3 | 5 | 5 |
| runtime exception in the patched code | 7 | 6 | 6 | 6 | 7 | 10 | 7 | **6** | 9 |
| wrong logic (tests run, assertions fail) | 18 | 15 | 17 | **12** | 13 | 13 | 16 | 17 | 13 |
| empty patch | 2 | 6 | **3** | 6 | 6 | 5 | 4 | **1** | 4 |

In the crashing cases the traces show the agent ran **only `py_compile`** and then submitted. It skipped the
repro step that the SUBMIT GATE asks for. E4B reliably runs the exact commands a prompt spells out, and
it skips vague asks like "write a repro". v23a tests whether swapping in a stronger exact command helps.

### Where the ceiling is (`tools/analyze_failures.py`)

- **17 of the 40 panel issues are just a PR title** under 400 characters with no code, such as "fix for padding width"
  or "empty with end", plus a GitHub link the offline sandbox cannot open. Neither arm passes them (0–1 of 17).
  The git history up to `base_commit` has no clue either. About 40% of the tasks are underspecified for any E4B agent.
- 10–12 failures per arm edit the **wrong file**, mostly on those short issues. Example: "padding width" goes to `padding.py`,
  but the fix belongs in `table.py`.
- **Only the import-breaking failures (4–5 per arm) fail tests that already exist.** Every runtime-exception and
  wrong-logic failure fails only the *new* hidden tests, so running more of the existing tests before submitting cannot catch them.
  The import gate (v23a) is the only cheap pre-submit check with something to catch.

## Lessons so far

1. **LoRA distillation from 31B does not work on E4B** (v16, v18b, v19a, v21a, and at least two public teams).
   The adapter learns the long 31B exploration style and runs out of calls or time.
2. **More time is not the bottleneck.** An 8-minute cap cut timeouts from 16 to 6 but did not add passes.
   The bottleneck is patch quality.
3. **Prompt tweaks sit inside the panel noise.** Only structural changes are worth a LB slot.
4. **Fewer empty patches is the most LB-relevant local signal.** That is why v18d was submitted.

## Running a wave

```bash
# creates an L4 VM, uploads creds and agents, runs setup detached, launches the eval,
# polls it, downloads and regrades the results, then always stops the VM
colab/bootstrap_vm.sh <session> <work_dir> <results_root> <agents.tar.gz> <out_name>:<launch.py>
```

Guards that each fixed a real failure:

- Build tars with `COPYFILE_DISABLE=1` so macOS `._*` files don't make the harness reject every task.
- Wrap every `colab exec` in a timeout. Run setup detached and poll it with short execs, because long streaming execs die with "Connection was lost".
- Retry every upload and exec, and check its output.
- Only declare a VM lost after 3 failed checks in a row. A single 503 can say "terminated" while the VM is fine.
- **Kaggle OAuth rotates the refresh token.** Never run a local `kaggle` command while `~/.kaggle/vm_setup.lock` exists,
  or the VM's copy of the creds dies with a 403.

`tools/regrade_results.py <results_dir>` regrades the patches with the gold tests and compares them with v14.
`tools/gold_sanity.py` checks that the gold patches pass in the local grader.
