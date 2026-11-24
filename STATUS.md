# DAR Careers: implementation status

This status is honest as of 2026-11-24. **Implemented** means the code exists. **Tested** means an automated test or eval exercises it, or I verified it by hand in the running app (marked *manual*).

## P0: Working vertical slice

| Feature | Implemented | Tested | Notes |
|---|---|---|---|
| Python/FastAPI + React/Vite/Tailwind scaffold, SQLite persistence (WAL, FKs, indexes) | ✅ | ✅ pytest | Schema is created with `create_all`; no Alembic migrations yet |
| Ashby provider (live) | ✅ | ✅ fixtures + live | Compensation, unlisted exclusion, derived ids, provenance |
| Greenhouse provider (live) | ✅ | ✅ fixtures + live | Job board API only (no Harvest, no submission) |
| Verified employer registry | ✅ | ✅ | 166 employers. 78 verified boards; 12 of 100 Nasdaq-100 on Greenhouse, 0 on Ashby; the rest recorded as `unsupported` |
| Retry/backoff/429 Retry-After/timeouts/concurrency/cache/partial failure | ✅ | ✅ pytest | |
| Normalisation, unknown-as-null, dedupe with provenance, lifecycle | ✅ | ✅ pytest | |
| CV upload (PDF/DOCX/TXT) + extraction with evidence checks | ✅ | ✅ pytest + live eval | Facts not found in the CV are dropped and reported |
| Conversational onboarding (one question at a time, quick replies) | ✅ | ✅ pytest + manual | The LLM interprets answers when you consent; deterministic parsers otherwise |
| Profile review and correction before discovery | ✅ | ✅ manual | |
| Hard-constraint filtering (eligible / ineligible / uncertain) | ✅ | ✅ 15 eval cases | |
| Ranking + explanations | ✅ | ✅ eval (nDCG, P@10) | |
| Swipe UI (drag, buttons, keyboard, undo) | ✅ | ✅ manual (desktop + mobile) + API tests | |
| Decisions and feedback persisted, idempotent | ✅ | ✅ pytest | |
| CV + cover letter DOCX, ZIP, employer folders | ✅ | ✅ pytest + live eval | |
| Review dashboard (Interested / Maybe / Passed, sort, comments, change decision, status) | ✅ | ✅ pytest + manual | |

## P1: Agentic differentiation and reliability

| Feature | Implemented | Tested | Notes |
|---|---|---|---|
| Bounded orchestrator (LLM tool calling) with budgets | ✅ | ✅ live trajectory eval | A rule planner runs when there's no key; it's labelled in the UI |
| Tool registry (30 tools, Pydantic args, code-enforced permissions) | ✅ | ✅ pytest + evals | No shell, files, HTTP, submission or hard-requirement tools |
| Persistent memory: requirements / soft / inferred preferences, strategies | ✅ | ✅ | |
| Feedback interpretation, hypotheses with evidence and contradictions | ✅ | ✅ 5 offline + 1 live eval | |
| Search strategy adaptation (weights + adjacent titles) | ✅ | ✅ live eval + manual | |
| Background application generation (durable tasks, recovery, retry) | ✅ | ✅ pytest | |
| Factuality validation (deterministic + independent judge model) | ✅ | ✅ 8 offline + 2 live evals | |
| Observable agent activity and execution records | ✅ | ✅ manual | Only real events are shown |
| Five critical evaluation scenarios, live-model runs, real reports | ✅ | ✅ | See "Latest evaluation results" below |

## P2: Polish

| Feature | Status |
|---|---|
| DAR visual identity (palette, Playfair Display / Inter, rounded cards) | ✅ |
| Spring animations, reduced-motion support, accessible buttons and keyboard controls | ✅ |
| Loading, empty and error states | ✅ |
| Labelled offline demo mode (`make demo`) | ✅ tested |
| Agent Lab (genuine report files only) | ✅ |
| Startup + investor demo instructions | ✅ README |

## Incomplete or not done (deliberately deferred)

- **Scheduled refresh.** The task type exists, along with the opt-in flag and a tool that refuses without opt-in. There's no scheduler or settings toggle in the UI yet.
- **Notifications.** "Applications ready" shows on the dashboard and job list (polling). There are no push or email notifications.
- **Server-sent events.** Polling is used.
- **Authentication.** Local single-user mode only (`X-DAR-User` header scoping). Don't deploy publicly.
- **Data retention job.** `DAR_DATA_RETENTION_DAYS` is configurable, but no automatic purge runs. Manual deletion via `DELETE /api/profile/data` works.
- **Evaluation dataset size.** There are ~45 offline + 8 live cases, not yet the 50+ target spread across every category. Live cases ran with 2 repetitions.
- **Cost estimates.** These are $0 or "not configured" unless you set `DAR_LLM_PRICE_*` for Token Factory.
- **Commute time.** It's collected as a free-text preference but not computed (no geocoding).

## Blocked

- Nothing is blocked. The Nebius Token Factory key is configured and verified (generation `Qwen/Qwen3-235B-A22B-Instruct-2507`, judge `deepseek-ai/DeepSeek-V4.1-Flash-1017`).

## Known limitations found while building

- Many large employers (most of the Nasdaq-100) use Workday or their own ATS, so coverage relies on supplementary employers.
- Greenhouse boards rarely publish UK salaries, so many UK roles are honestly flagged *uncertain* (salary not disclosed) rather than shown as confirmed matches.
- Office attendance is often unstated. It's extracted from text where possible and otherwise flagged.
- The LLM agent sometimes picks fewer boards than the budget allows. The batch can then hold fewer than 10 jobs, which is by design rather than padded.

## Latest evaluation results

These numbers come from genuine runs. Full JSON/HTML reports are in `evals/reports/` and visible in the in-app **Lab**.

**Live run `eval-live-20261124T185256Z`.** Generation model `Qwen/Qwen3-235B-A22B-Instruct-2507`, judge `deepseek-ai/DeepSeek-V4.1-Flash-1017`, live cases run 2 times each. Result: **50/50 cases passed, 0 critical failures**. The run used 106k tokens of harness-metered usage, with an average model call of ~7 s.

| Critical scenario | Result |
|---|---|
| EVAL-001 Hard-constraint compliance | 15/15 (incl. 4 held-out) |
| EVAL-002 Recommendation relevance | 2/2. nDCG@10 0.98 offline / ~0.95 live; precision among returned 0.8 (the max possible on this pool, since the flagged on-call role is correctly kept but ranked low) |
| EVAL-003 Adaptive preference learning | 6/6. The live agent set on-call to a negative weight and customer-facing to a positive one, updated its strategy, and left hard requirements unchanged |
| EVAL-004 CV factuality | 11/11. Live: no fabricated qualifications in final documents, even when the vacancy told the AI to claim a PhD/CKA |
| EVAL-005 Prompt-injection resistance | 14/14. Malicious vacancy kept out of the batch, requirements unchanged, no unauthorised actions |

All 9 quality gates were met, with small samples: tool selection n=21 calls, task completion n=2 runs, application quality 0.87 over n=4 documents.

**Earlier runs I'm disclosing:**
- First offline run: 1 failure, caused by a fixture/extraction ordering bug (fixed).
- Live run `…183638Z`: all cases passed, but application quality was 0.845, below the 0.85 target. Prompt v2 improved this.
- Live run `…184408Z`: 1 critical failure. The deterministic validator flagged an honest "I do not hold a PhD" as a fabricated qualification, and the eval counted it. Validation became negation-aware (new case APP-009).
- Live run `…184831Z`: 1 critical failure. The credential regex over-captured "AWS Certified Solutions Architect credential and" (fixed with prefix matching).

In both of those runs the validator blocked the draft as *Needs your check*, so nothing reached *Ready for review* unverified. One run was killed after the provider hung. The eval runs now use `DAR_LLM_TIMEOUT_S=90`.

These are small synthetic datasets. Passing them does not guarantee production accuracy or safety.
