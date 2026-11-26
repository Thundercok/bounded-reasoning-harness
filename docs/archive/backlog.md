# Backlog — Gemma 4 E4B Developer Agent

## Active
- **v7a (`gemma4-e4b-v7a-verify-skill`)**: Single-variable ablation on top of `v6b_single_clean` (`5/24`) adding deterministic `skills/verify_patch/scripts/run_verify.py` (auto test discovery + syntax/diff gate + targeted `pytest` + `PASS/FAIL` verdict). Currently running on Kaggle L4.

## Queued Ideas (After `v7a` Results)
1. **ALNS Incumbent & Rollback (from VRPTW):** Snapshot best verified patch to `/tmp/best.patch` whenever `verify_patch` passes; rollback via `git checkout -- .` if a subsequent edit regresses tests (addresses self-destructive follow-up edits like `fastapi_14360`).
2. **Stagnation Escape / Destroy-and-Repair (from VRPTW):** Pair `verify_patch` with fresh-context Stage 2 (`v6_seq2_clean` → `v7b`) or `LoopAgent` reset when 2 edit attempts still fail verification, instead of repeating broken operators.
3. **Variance Control (from VRPTW cold-start discipline):** Confirm any winning E4B variant on a larger slice or repeated seed before final Kaggle submission selection (since only ~13/24 tasks are discriminating).
4. **PLE Transfer (Post Dec 2, 2026):** Reuse hard-gate verifier + trace-driven ablation methodology for Physical-Engineering's Last Exam (`Jan 6, 2027` deadline).
