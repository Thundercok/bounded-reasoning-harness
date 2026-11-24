# External Critique Packet — Gemma 4 Small Model (`E4B-QAT-W4A16`) SWE Agent

We are competing for the **Small Model Bonus Prize (\$10,000)** in Kaggle's *Google - The Gemma 4 Developer Agent Competition*.
- **Deadline**: Nov 26, 2026 (~3 days left, ~3 daily LB submissions remaining).
- **Target**: Beat #1 on the Small Model Leaderboard (**`0.12`**; #3–#21 are `0.10`, across 1,338 teams).
- **Fixed Model**: `gemma-4-e4b-it-qat-w4a16-ct` (4B INT4 quantized, **cannot change model**).
- **Hard Harness Constraints (Google ADK declarative-only)**:
  - Submission is strictly declarative YAML (`LlmAgent`, `SequentialAgent`, `LoopAgent`, `ParallelAgent`) + Markdown prompts + ADK `skills/` (Python scripts executed via `run_skill_script` inside the task sandbox) + `eval_config.yaml` + optional LoRA adapters (`< 3 GiB`, no internet during inference).
  - Context window: `32,768` tokens (`EventsCompactionConfig` auto-compacts at 14,336 tokens).
  - Official inference hardware: 4× L4 GPUs running vLLM (`enable_thinking` supported via `thinking_config: {thinking_budget, include_thoughts}`).
  - Available built-in tools: `run_command`, `read_file`, `edit_file`, `write_file`, `get_status`, `submit_patch`, plus **Code Intelligence Graph tools** (currently unused: `search_code` / symbol graph / embeddings).
  - Protected test/config files (`tests/`, `test_*.py`, `conftest.py`, `pytest.ini`, etc.) are stripped before scoring.
  - Note: We are NOT asking you to solve individual validation tasks — hidden test repos on Kaggle LB are unseen Python repos. We need **generalizable harness / skill / agent-architecture / decoding / LoRA insights** specifically for a **4B INT4 model**.

---

## 1. Empirical Ablation History (16 High-Signal Validation Tasks)
- Earlier full-24 runs: `v2c` (base, 2-stage, `think=0`) = `3/24` → **Public LB `0.03`**; `v3b` (LoRA r16 trained on non-thinking traces, 2-stage, `think=0`) = `3/24` → **Public LB `0.03`**.
- `v8a` (`7/16 = 43.8%`) is currently `PENDING` on Public LB (`ref 58746557`).
- `v10` (running now): `v10a` (`think=1024`, `max_output_tokens=2560`, TDD repro-first, per-file `verify-patch` v6.2 with honest `UNVERIFIED` verdict) vs `v10b` (2-stage: `v10a` → fresh-context Adversarial Verifier).

| Run | Solved (/16) | Err/TOUT | No Patch | Median s | Mean s | Key Change |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `v6b` | `5/16` (31.2%) | 1 | 2 | 71.6s | 106.8s | Clean single-agent, `think=0` |
| `v7a-v5` | `5/16` (31.2%) | 0 | 0 | 78.4s | 119.1s | + `verify-patch` skill (`think=0`) |
| `v8a` | **`7/16` (43.8%)** | 3 | 0 | 206.3s | 251.5s | + `thinking_budget: 1024`, `include_thoughts: false` (broke ceiling on `rich_3894`) |
| `v8b` | `5/16` (31.2%) | 0 | 0 | 80.3s | 122.4s | 2-stage: Coder (`think=0`) → Verifier (`think=1024`) (broke ceiling on `rich_3518`, lost 2 FastAPI tasks) |
| `v9a` | `6/16` (37.5%) | 2 | 1 | **66.8s** | **139.4s** | + `verify-patch` v6 (baseline-diff filtering of pre-existing sandbox failures; **3.1× faster!**) |
| `v9b` | `5/16` (31.2%) | 0 | 2 | 90.7s | 155.5s | Same as `v9a` but `thinking_budget: 512` (strictly worse & slower than 1024) |

### Per-Task Matrix (Union across `v8a ∪ v8b` = `8/16 = 50%`)
| Task | v6b (t=0) | v7v5 (t=0+v5) | v8a (t=1024+v5) | v8b (seq2) | v9a (t=1024+v6) | v9b (t=512+v6) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `fastapi_14297` | fail (5c,43s) | fail (10c,62s) | fail (5c,54s) | fail (13c,96s) | fail (6c,56s) | fail (8c,67s) |
| `fastapi_14301` | PASS (11c,73s) | PASS (4c,210s) | PASS (34c,364s) | fail (10c,52s) | PASS (9c,70s) | PASS (7c,57s) |
| `fastapi_14361` | fail (6c,32s) | fail (8c,55s) | fail (4c,47s) | fail (12c,95s) | fail (5c,81s) | fail (4c,37s) |
| `fastapi_14372` | fail (14c,55s) | fail (14c,139s) | TOUT (52c,487s) | fail (8c,90s) | fail (8c,105s) | fail (39c,272s) |
| `fastapi_14458` | fail (13c,91s) | fail (7c,68s) | fail (18c,168s) | fail (8c,64s) | fail (28c,334s) | NOP (12c,197s) |
| `fastapi_14463` | TOUT (24c,488s) | PASS (29c,167s) | PASS (7c,77s) | PASS (14c,51s) | PASS (14c,102s) | PASS (11c,484s) |
| `fastapi_14786` | PASS (5c,24s) | fail (60c,145s) | PASS (8c,71s) | fail (60c,277s) | PASS (5c,30s) | PASS (5c,32s) |
| `requests_6644` | PASS (14c,361s) | PASS (9c,54s) | PASS (52c,395s) | PASS (17c,92s) | PASS (7c,62s) | PASS (60c,360s) |
| `requests_7205` | NOP (20c,86s) | fail (11c,41s) | fail (9c,61s) | fail (10c,49s) | fail (6c,61s) | fail (53c,308s) |
| `requests_7309` | fail (7c,32s) | fail (4c,31s) | fail (60c,464s) | fail (60c,459s) | fail (4c,37s) | fail (6c,44s) |
| `requests_7427` | fail (8c,27s) | fail (11c,55s) | TOUT (60c,483s) | fail (50c,282s) | fail (5c,49s) | fail (17c,113s) |
| `rich_3006` | PASS (12c,32s) | PASS (6c,38s) | PASS (11c,105s) | PASS (9c,66s) | PASS (5c,41s) | PASS (8c,47s) |
| `rich_3296` | NOP (60c,207s) | fail (41c,174s) | TOUT (57c,485s) | fail (14c,93s) | TOUT (60c,483s) | NOP (26c,209s) |
| `rich_3518` | fail (8c,92s) | fail (59c,335s) | fail (29c,244s) | PASS (11c,70s) | TOUT (24c,486s) | fail (15c,154s) |
| `rich_3894` | fail (3c,22s) | fail (26c,262s) | PASS (51c,448s) | fail (10c,64s) | fail (27c,170s) | fail (6c,53s) |
| `rich_3905` | PASS (8c,36s) | PASS (11c,62s) | PASS (10c,62s) | PASS (12c,50s) | PASS (9c,56s) | fail (11c,45s) |

---

## 2. Verified Failure Taxonomy from `v9a` Traces (All 10 Failing Tasks)
- **Localization is mostly NOT the bottleneck**: `15/16` localized the right file (`rich_3296` was the only wrong-file edit: edited `rich/columns.py` on a `fix(syntax)` issue).
- **4 verified failure mechanisms observed in `v9a`**:
  1. **Runaway visible-text repetition (`rich_3518`)**: Hit `max_output_tokens=8192` **3 times** (steps 7, 19, 22 = 5.5 min lost, 0 tool calls) with `temperature=0.2, top_p=0.95` and no repetition/frequency penalty. (Fixed in `v10` via `max_output_tokens=2560`.)
  2. **False `"PASS"` on unverified pytest (`fastapi_14297/14361/14372`, `requests_7205`)**: `verify-patch` returned `ENV_SKIPPED` or `TIMEOUT` with `"PASS — Ready to submit"`, causing E4B to submit at calls 5–8 even after its own inline repro crashed (`requests_7205`: 2/2 repro crashes; `fastapi_14361`: 1/1 crash). (Fixed in `v10` via per-file pytest + honest `UNVERIFIED` verdict.)
  3. **Premature 1st-draft wrong logic with 0 repro (`fastapi_14372`, `requests_7309`, `requests_7427`)**: Submitted in 4–8 calls (37–105s), leaving 52+ calls & 6.5 min unused. Worst example: `fastapi_14372` replaced `params.Depends(...)` with a raw `tuple` instead of implementing `__hash__`!
  4. **Ignoring `verify-patch: FAIL` (`rich_3894`)**: `tests/test_inspect.py` failed on step 29, yet E4B still called `submit_patch` on step 31.

---

## 3. Exact `v9a` Failure Snapshots (10 Failing Tasks)

### `fastapi_14297` — v9a: `6 calls, 56s, patch=828B, err=None, verify=['ENV_SKIPPED(ok=True)']`
- **Issue**: Problem Statement: 🐛 Fix optional sequence handling in `serialize sequence value` with Pydantic V2  Currently the following test code leads to `TypeError: issubclass() arg 1 must be a class` (with Python 3.14 error is different, but the root cause is the same):  ```py from typing import List, Optional  from fastap
- **Last edit snippet**:
```diff
  --- a/fastapi/_compat/v2.py
  +++ b/fastapi/_compat/v2.py
  @@ -371,6 +371,11 @@
   
   def serialize_sequence_value(*, field: ModelField, value: Any) -> Sequence[Any]:
       origin_type = get_origin(field.field_info.annotation) or field.field_info.annotation
  +    if origin_type is Union:
  +        args = get_args(field.field_info.annotation)
  +        non_no
```
### `fastapi_14361` — v9a: `5 calls, 81s, patch=676B, err=None, verify=['ENV_SKIPPED(ok=True)']`
- **Issue**: Problem Statement: 🐛 Avoid accessing non-existing "$ref" key for Pydantic v2 compat remapping  cf Discussion #14265  ## Task Budget (Session terminates when any budget is exhausted) - Time allowance: 8.0 minutes - Tool calls allowance: 60 calls - Max loop iterations: 120 turns  ## Execution Environment Rules - Single c
- **Last edit snippet**:
```diff
  --- a/fastapi/_compat/v2.py
  +++ b/fastapi/_compat/v2.py
  @@ -262,9 +262,8 @@
       new_schema = deepcopy(schema)
       for key, value in new_schema.items():
           if key == "$ref":
  -            value = schema["$ref"]
               if isinstance(value, str):
  -                ref_name = schema["$ref"].split("/")[-1]
  +                ref_name = value.s
```
### `fastapi_14372` — v9a: `8 calls, 105s, patch=417B, err=None, verify=['TIMEOUT(ok=True)']`
- **Issue**: Problem Statement: ♻️ Make the result of `Depends()` and `Security()` hashable, as a workaround for other tools interacting with these internal parts  ♻️ Make the result of `Depends()` and `Security()` hashable, as a workaround for other tools interacting with these internal parts  ---  This comes from https://gith
- **Last edit snippet**:
```diff
  --- a/fastapi/param_functions.py
  +++ b/fastapi/param_functions.py
  @@ -2295,7 +2295,7 @@
           return commons
       ```
       """
  -    return params.Depends(dependency=dependency, use_cache=use_cache, scope=scope)
  +    return (dependency, use_cache, scope)
   
   
   def Security(  # noqa: N802
  
```
### `fastapi_14458` — v9a: `28 calls, 334s, patch=2793B, err=None, verify=['SKIPPED(ok=False)', 'SKIPPED(ok=False)', 'PASS(ok=True)']`
- **Issue**: Problem Statement: 🐛 Fix using class (not instance) dependency that has `__call__` method  As reported in https://github.com/fastapi/fastapi/discussions/14452, FastAPI 0.123.6 introduced a regression - if we use class (not instance) as dependency and this class has `__call__` method that is coroutine or generator, it i
- **Last edit snippet**:
```diff
  --- a/fastapi/dependencies/models.py
  +++ b/fastapi/dependencies/models.py
  @@ -106,6 +106,10 @@
       def is_gen_callable(self) -> bool:
           if self.call is None:
               return False  # pragma: no cover
  +        if inspect.isclass(self.call):
  +            if inspect.isgeneratorfunction(self.call.__call__):
  +                return True
  +   
```
### `requests_7205` — v9a: `6 calls, 61s, patch=948B, err=None, verify=['ENV_SKIPPED(ok=True)']`
- **Issue**: Problem Statement: Fix empty netrc entry usage  Prior to Python 3.11, a netrc entry without a username, account, or password entry was treated as malformed and raised a `NetrcParseError` disabling usage. For 3.11+, it now returns an empty tuple `('', '' ,'')` instead. That results in Requests sending an empty entry (`:
- **Last edit snippet**:
```diff
  --- a/src/requests/utils.py
  +++ b/src/requests/utils.py
  @@ -232,9 +232,11 @@
           try:
               _netrc = netrc(netrc_path).authenticators(host)
               if _netrc:
  -                # Return with login / password
  -                login_i = 0 if _netrc[0] else 1
  -                return (_netrc[login_i], _netrc[2])
  +                # Only 
```
### `requests_7309` — v9a: `4 calls, 37s, patch=524B, err=None, verify=['PASS(ok=True)']`
- **Issue**: Problem Statement: Fix malformed value parsing for Content-Type  This PR addresses https://github.com/psf/requests/issues/6646 by cleaning up `_parse_content_type_header`. Instead of setting a default that we can't use in practice, we now skip value assignment completely if the value is malformed. Every RFC from 1521 t
- **Last edit snippet**:
```diff
  --- a/src/requests/utils.py
  +++ b/src/requests/utils.py
  @@ -522,7 +522,7 @@
               if index_of_equals != -1:
                   key = param[:index_of_equals].strip(items_to_strip)
                   value = param[index_of_equals + 1 :].strip(items_to_strip)
  -            params_dict[key.lower()] = value
  +                params_dict[key.lower()] = 
```
### `requests_7427` — v9a: `5 calls, 49s, patch=715B, err=None, verify=['PASS_PREEXISTING_ONLY(ok=True)']`
- **Issue**: Problem Statement: Port bpo-39057 to Requests  This PR ports a [fix](https://bugs.python.org/issue39057) from CPython to constrain no_proxy behavior to domain boundaries. This was originally undefined behavior when Requests implemented `no_proxy` support but has since had bug fixes released in both CPython and Curl. We
- **Last edit snippet**:
```diff
  --- a/src/requests/utils.py
  +++ b/src/requests/utils.py
  @@ -851,7 +851,7 @@
                   host_with_port += f":{parsed.port}"
   
               for host in no_proxy_hosts:
  -                if hostname.endswith(host) or host_with_port.endswith(host):
  +                if hostname == host or hostname.endswith(f".{host}") or host_with_port == host or h
```
### `rich_3296` — v9a: `60 calls, 483s, patch=0B, err=Agent exceeded session timeout (8.0 min), verify=['FAIL(ok=False)', 'FAIL(ok=False)', 'SKIPPED(ok=False)', 'FAIL(ok=False)', 'FAIL(ok=False)', 'FAIL(ok=False)', 'SKIPPED(ok=False)']`
- **Issue**: Problem Statement: fix(syntax): include padding with background color override  ## Type of changes  - [x] Bug fix - [ ] New feature - [ ] Documentation / docstrings - [ ] Tests - [ ] Other  ## Checklist  - [x] I've run the latest [black](https://github.com/psf/black) with default args on new code. - [x] I've
- **Last edit snippet**:
```diff
  --- a/rich/columns.py
  +++ b/rich/columns.py
  @@ -116,7 +116,7 @@
                   for _ in range(column_count - (item_count % column_count)):
                       yield 0, None
   
  -        table = Table.grid(padding=self.padding, collapse_padding=True, pad_edge=True)
  +        table = Table.grid(padding=self.padding, collapse_padding=True, pad_edge=Fa
```
### `rich_3518` — v9a: `24 calls, 486s, patch=457B, err=Agent exceeded session timeout (8.0 min), verify=[]`
- **Issue**: Problem Statement: fix(table): highlight columns added by add_row  ## Type of changes  - [x] Bug fix - [ ] New feature - [ ] Documentation / docstrings - [ ] Tests - [ ] Other  ## Checklist  - [x] I've run the latest [black](https://github.com/psf/black) with default args on new code. - [x] I've updated CHAN
- **Last edit snippet**:
```diff
  --- a/rich/table.py
  +++ b/rich/table.py
  @@ -369,6 +369,7 @@
           *,
           header_style: Optional[StyleType] = None,
           highlight: Optional[bool] = None,
  +        default_highlight: Optional[bool] = None,
           footer_style: Optional[StyleType] = None,
           style: Optional[StyleType] = None,
           justify: "JustifyMethod" = "l
```
### `rich_3894` — v9a: `27 calls, 170s, patch=868B, err=None, verify=['FAIL(ok=False)', 'FAIL(ok=False)']`
- **Issue**: Problem Statement: Handle unusual __qualname__ in inspect  <!-- Please note that Rich isn't accepting any new features at this point.  If a feature can be implemented without modifying the core library, then they should be released as a third-party module. I can accept updates to the core library that make it easi
- **Last edit snippet**:
```diff
  --- a/rich/_inspect.py
  +++ b/rich/_inspect.py
  @@ -101,8 +101,10 @@
           signature_text = self.highlighter(_signature)
   
           qualname = getattr(obj, "__qualname__", None)
  -        if isinstance(qualname, str):
  -            qualname = qualname
  +        if not isinstance(qualname, str):
  +            qualname = getattr(obj, "__name__", name)
  +
```

---

## 4. Questions for Independent Critique (Be Brutally Honest & Specific to a 4B INT4 Model)
1. **Highest-leverage unused ADK / Skill / Decoding mechanism**: Given E4B (4B INT4, 32K ctx, declarative ADK YAML + Python skills in `skills/`, optional LoRA, 72 hours left), what **1–2 generalizable mechanisms** (NOT repo-specific hacks, NOT vague prompt polishing) have the highest expected jump from `7/16` → `10–12/16` (`>0.12` LB)? Specifically evaluate:
   - Deterministic **ADK skill tools** we haven't built yet (e.g., an **AST/patch-safety linter** checking public API/signature/return-type preservation, a **safe structured-edit skill**, a **best-patch incumbent/rollback skill** storing last passing diff in `/tmp`, or **automated test-generation/repro runner skill**).
   - **ADK `LoopAgent` / Multi-candidate best-of-N** (e.g., 2–3 independent patch attempts saved to `/tmp/cand_i.patch` + deterministic test/judge selection inside a single sandbox — feasible or too slow/complex for 4B?).
   - **Decoding & Thinking config** (`thinking_budget: 2048` vs `4096`, `include_thoughts: true` vs `false`, `temperature`, `frequency_penalty` / `presence_penalty`).
   - **Built-in Code Intelligence Graph tools** — worth exposing to a 4B model or do extra tool schemas hurt 4B tool-calling accuracy?
   - **Targeted LoRA** — is a small LoRA feasible & worth the GPU risk in 72 hours, or strictly dominated by inference-time harness/skill engineering?
2. **Failure-mode blind spots**: Looking at the 10 `v9a` failure snapshots above, what **underlying 4B cognitive failure pattern** are we misdiagnosing or overlooking?
3. **Strict 72-hour experimental plan (Nov 23 → Nov 26)**: Propose the **exact top 2 falsifiable 1-variable ablations** to run after `v10`, with clear pass/kill criteria so we don't waste our remaining ~8h L4 GPU quota or 3 LB submissions.
