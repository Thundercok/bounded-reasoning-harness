# `bounded-reasoning-harness`: Budget-Aware Verification & Bounded Thinking for Small SWE Agents

Research and engineering repository studying **bounded private reasoning (`thinking_budget`)**, **deterministic pre-submit verification (`verify-patch`)**, and **strict execution budgeting** for small open-weight software engineering agents (`Gemma 4 E4B` W4A16-CT, ~4B active parameters) under tight sequential runtime and tool-call limits.

---

## Contents

- [Problem Formulation](#1-problem-formulation)
- [Key Experimental Results](#2-key-experimental-results)
- [Design Principles](#3-design-principles)
- [Repository Structure](#4-repository-structure)
- [Quickstart](#5-quickstart)
- [License](#license)

---

## 1. Problem Formulation

Given a software repository snapshot $R$ at base commit $c_0$, an issue description $I$, and strict per-task execution budgets $(B_{\text{calls}}, B_{\text{time}}, B_{\text{out}})$, a small-model SWE agent produces a candidate source patch $\Delta R$ via multi-turn tool interaction (`run_command`, `read_file`, `edit_file`, `write_file`, `submit_patch`).

Small models (`~4B` active parameters, INT4 W4A16) fail on realistic repositories for three distinct, measurable reasons:
1. **Unbounded reasoning collapse:** High thinking budgets (`thinking_budget = 4096`) and uncapped output lengths trigger multi-minute runaway text loops and context bloat, degrading resolution rate below `think = 0`.
2. **Noisy sandbox feedback:** Pre-existing test failures in local evaluation environments cause small agents to abandon valid fixes or burn remaining tool budget debugging unrelated fixtures.
3. **Global wall-clock budget exhaustion:** Sequential evaluation across ~120 tasks under a hard 12-hour cap requires mean task latency $\le 5.88\text{ min}$ and bounded worst-case runtime.

We investigate whether **private bounded reasoning** (`thinking_budget = 1024`, `include_thoughts = false`, `max_output_tokens = 2560`) combined with a **deterministic baseline-diff verification skill** (`verify-patch`) under a tight **Budget-Fit** regime (`28 calls / 5 min`) outperforms both 0-skill thinking and on-policy LoRA fine-tuning.

---

## 2. Key Experimental Results

Controlled head-to-head ablations on real NVIDIA L4 (`24 GB`, `vLLM 0.19.1`, `Gemma 4 E4B W4A16-CT`) evaluated on a fixed 16-task high-signal panel (`fastapi`, `requests`, `rich`) and re-graded with a 16/16 gold-verified local test harness ([tools/local_m1_eval.py](tools/local_m1_eval.py)):

| Variant | Budget | Reasoning / Adapter | Verification Skill | Resolved (16 tasks) | Empty Patch | 5m Timeouts | Mean Calls | Mean / Median Latency | Worst-Case 120-Task Runtime |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| **`v13_budget_fit_t1024`** | `28c / 5m` | `think=1024`, `max_out=2560` | `verify-patch v6.2` | **`10/16` (`62.5%`)** | **0** | **2** | **8.5** | `153.7s / 140.4s` | **`10.25h` (< 12h)** |
| **`v14_pure_budget_fit_t1024`** | `28c / 5m` | `think=1024`, `max_out=2560` | None (0-skill) | `9/16` (`56.2%`) | 2 | 5 | 9.8 | `191.4s / 156.7s` | `10.25h` (< 12h) |
| **`v15_onpolicy_lora_budget_fit`** | `28c / 5m` | `think=0` + on-policy LoRA (`r=16, α=8`) | None (0-skill) | `8/16` (`50.0%`) | 2 | 3 | 9.2 | `148.3s / 106.8s` | `10.25h` (< 12h) |
| `v11a_seq2_v7_t1024` | 2-stage (`36c / 5m`) | `think=1024`, `max_out=2560` | `verify-patch v7` | `8/16` (`50.0%`) | 0 | 0 | — | — | `10.25h` (< 12h) |
| `v9a_think1024_verify_v6` | `60c / 8m` | `think=1024`, uncapped out | `verify-patch v6` | `8/16` (`50.0%`) | 1 | 2 | — | `— / 66.8s` | >12h risk |
| `v9b_think512_verify_v6` | `60c / 8m` | `think=512` | `verify-patch v6` | `5/16` raw | 2 | — | — | `— / 90.7s` | >12h risk |

### Verified Ablation Takeaways
- **Bounded private thinking sweet spot (`think=1024`, `include_thoughts=false`, `max_output_tokens=2560`):** Outperforms `think=0` (even with on-policy LoRA: `v14` `9/16` > `v15` `8/16`), `think=512`, `think=2048`, and `think=4096` while eliminating 8,192-token runaway loops.
- **Deterministic baseline-diff verification (`verify-patch v6.2`):** Adding `verify-patch` on top of identical Budget-Fit + `think=1024` (`v14 → v13`) improves resolution (`9/16 → 10/16`, including `4/4` on `requests`), eliminates empty patches (`2 → 0`), cuts 5-minute timeouts (`5 → 2`), and reduces mean latency by **20%** (`191.4s → 153.7s`).
- **Union diversity (`v13 ∪ v14`):** `11/16 (68.8%)` unique tasks solved across the two top Budget-Fit variants.

> **Later experiments (v16 → v23):** the 40-task Colab panel, Kaggle LB scores, failure analysis and lessons are in [`docs/EXPERIMENT_LOG.md`](docs/EXPERIMENT_LOG.md).

---

## 3. Design Principles

- **Strict budget accounting:** Every submission variant enforces `max_tool_calls = 28`, `max_time_minutes = 5`, `timeout_seconds = 240`, keeping worst-case 120-task runtime at `10.25h < 12h`.
- **Honest verification verdicts:** `run_verify.py` separates static checks (AST parse, protected test/config guards), per-file `pytest` execution with baseline-diff filtering of pre-existing sandbox failures, and explicit `PASS` / `UNVERIFIED` / `FAIL` verdicts so agents never confuse environment skips with passing tests.
- **Gold-verified local re-grading:** All candidate patches are re-graded against gold `test_patch` suites in isolated clean checkouts (`16/16` gold sanity pass) to eliminate local `SubprocessSandbox` false negatives.

---

## 4. Repository Structure

```text
bounded-reasoning-harness/
├── variants/                        # one folder = one agent bundle (agent.yaml + prompts + configs)
│   ├── v13_budget_fit_t1024/        # Budget-Fit + think=1024 + verify-patch v6.2 (10/16)
│   ├── v14_pure_budget_fit_t1024/   # 0-skill Budget-Fit, Kaggle LB 0.06
│   ├── v18d_v14_submit_gate/        # v14 + SUBMIT GATE, submitted to Kaggle
│   ├── v22_v18d_t8min/              # v18d with an 8-minute cap
│   ├── v23a_v18d_import_gate/       # v18d, gate check = real module import
│   ├── v23b_v18d_pin_issue/         # v18d, issue pinned at the end of the prompt
│   └── archive/                     # every earlier or dead-end variant (v2 → v21)
├── colab/                           # hands-off 40-task eval waves on a Colab L4 VM
├── tools/                           # local grader, regrade, gold sanity, packer
├── notebooks/
│   ├── colab/                       # self-contained Colab eval notebooks
│   └── kaggle/                      # Kaggle L4 ablation notebooks (v6 → v12, LoRA)
└── docs/
    ├── EXPERIMENT_LOG.md            # start here: all results v16 → v23, LB scores, lessons
    ├── HARNESS_README.md            # how the Kaggle harness works
    └── archive/                     # older analyses and backlog
```

---

## 5. Quickstart

```bash
# Package an agent folder into dist/submission.zip and validate it
uv run python tools/pack_and_validate.py variants/v18d_v14_submit_gate
uv run python data/lora_kit_docs/check_submission.py dist/submission.zip

# Regrade a Colab wave's patches locally with the gold tests
uv run python tools/regrade_results.py results/<wave_dir>

# Run a full 40-task wave on a Colab L4 VM (see docs/EXPERIMENT_LOG.md)
colab/bootstrap_vm.sh <session> <work_dir> <results_root> <agents.tar.gz> <out_name>:<launch.py>
```

To reproduce the `v13` vs `v14` vs `v15` head-to-head, open [`notebooks/colab/colab_v13_v14_v15_eval.ipynb`](notebooks/colab/colab_v13_v14_v15_eval.ipynb).

---

## License

MIT License (third-party LoRA weights, where used in ablations, remain under their original Apache 2.0 licenses and are excluded from git via `.gitignore`).
