# Cross-model bug hunt: prokb (`kb`) at commit be548c0

Date: 2026-11-26 (UTC). Repo type: library, not money-handling, so CLI hunters, ARS file reviewers and CLI write-mode verification were all allowed. Pinned commit: `be548c0` (prokb 0.3.0). No fix landed until every hunter had finished.

## Result in one paragraph

Three CLI hunters (Codex, Kimi, Gemini) and two ARS models (MiMo, DeepSeek) made claims that merged into 38 distinct items. 35 were proven by a reproduction test that the orchestrator ran: each test fails on `be548c0` for the claimed reason. Two were refuted as design choices (V23 score scale, V38 init overwrite under `--force`). One was folded into another (V33 into V13). Verification also surfaced a 36th, more urgent defect that no hunter had reported: `kb mcp` did not start at all on a fresh install, because the unbounded `mcp>=1.0` dependency resolved to mcp 2.x. That was fixed right away in 0.3.1 (commit `24eb0c3`). The 36 repro tests are on main as `xfail(strict=True)`, grouped into 8 tickets (#8 to #15).

## Hunters

| Hunter | Kind | Wall time | Claims | Distinct proven | Share of the 35 proven | Refuted |
|---|---|---|---|---|---|---|
| Codex `gpt-6-astra` | CLI, whole repo | 90 s | 10 | 10 | 29% | 0 |
| Kimi | CLI, whole repo | 142 s | 15 | 14 | 40% | 0 |
| Gemini `gemini-4-argon-high` (agy) | CLI, whole repo | 457 s | 15 (≈22 sub-claims) | 20 | 57% | 1 (V23) |
| ARS `mimo:mimo-v2.6-pro` + `ollama-cloud:deepseek-v4.1-flash` | per file, 8 files × 2 models | 16 calls, 10 s to 250 s each | 70 raw lines | 29 | 83% | 2 (V23, V38) |

- Found by all three CLI vendors: 8 (V1 to V8). Found by exactly one CLI: 7 (V10 Kimi; V12 Codex; V14 Kimi; V16, V18, V22, V24 Gemini; V12, V14, V22 and V24 were also found by ARS).
- Found ONLY by ARS: 12 (V25 to V32, V34 to V37). The CLIs together found 23 of the 35.
- The repo is small (8 source files, about 1,400 lines). Here the file-by-file ARS pass added more than any single CLI. That differs from the FundManager run (2026-11-22), where the repo was much larger.
- ARS usage: MiMo 95,328 tokens, USD 0.0705 (prepaid credit); DeepSeek 145,242 tokens (Ollama subscription, no per-call cost reported).

## Verification

- Kit template B. Each claim went to a verifier from a different vendor than the claimant:
  - 28 claims to Codex (write mode, test file only). It made none of them.
  - 10 claims found by all three CLIs or by Codex to a Claude sub-agent. No CLI was independent for those.
- The orchestrator ran every test in the project environment. Results on `be548c0`:
  - `test_bughunt_repro_v1.py` (Claude): 11 tests, all failed with the claimed message.
  - `test_bughunt_repro.py` (Codex): 25 tests, 23 failed with the claimed message. V19 and V28 errored at import because of the mcp 2.x break. After the 0.3.1 fix both failed with the claimed message: V19 showed real cross-talk, where query `a` got "No output" and query `b`'s JSON leaked to the real stdout.
- Final suite on main: 85 passed, 36 xfailed, 0 warnings (`filterwarnings=error`).

## Tickets (one per fix unit)

| Ticket | Unit | Claims |
|---|---|---|
| #8 | diary same-second overwrite | V1 |
| #9 | chunker: config ignored, oversized paragraph, split fences, lost header line, placeholder corruption | V2, V14, V15, V25, V26 |
| #10 | translation routing and manifest status | V3, V13 |
| #11 | index consistency: swallowed deletes, stale chunks, aborted runs | V4, V5, V8, V29, V30 |
| #12 | manifest durability, lock gaps, change detection | V6, V7, V12, V22, V24 |
| #13 | security: reading/deleting outside the project (symlink, `../` include, manifest key) | V9, V10, V11 |
| #14 | config robustness | V16, V17, V18, V34, V35, V36 |
| #15 | query/MCP output correctness | V19, V20, V21, V27, V28, V31, V32, V37 |

## Not verified (deliberately left out of this round)

These are ARS claims of low severity or platform scope. They are hypotheses, not tickets:
- parent header chain missing from chunk metadata;
- unknown `--preset` falls back to general;
- the `project_name` fallback uses an unresolved path;
- shared mutable defaults in `_deep_merge`;
- `fcntl` on Windows;
- lock-skipped auto-reindex returns stale results without a JSON flag;
- `--after` is not validated as a date;
- `detect_language` read errors default to the target language;
- a non-`hu` source without langdetect;
- a stat/hash TOCTOU in the scanner.

## Process lessons

- The verification step finds what the hunters do not. No hunter read the dependency pins, so none caught the `mcp>=1.0` break. A repro test importing the module did. Add "dependency pins and an import of every entry point" to the hunt brief for libraries.
- `fleet delegate` kept only the last 6,000 characters of the Kimi and Gemini reports and dropped the most severe findings. The full text was only in `run.log` (ticket SyncAgentFiles#43).
- Claude Code `isolation: "worktree"` is blocked by the RTK hook. A manual `git worktree add` at the pinned commit worked for the Claude verifier.
