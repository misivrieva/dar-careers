# DAR Careers — agentic career companion

> **Less searching. More possibilities.** Job boards give you listings. DAR gives you a career agent.

DAR Careers is a hackathon MVP in the DAR brand (MASIV group). It's built ADHD-first for UK job seekers but works for anyone. You upload a CV and have a short conversation about what you want. A bounded career agent then searches real employer job boards (Ashby and Greenhouse), filters the results against your hard requirements, ranks and explains what's left, and shows up to ten jobs you can swipe. It learns from each decision and writes tailored, fact-checked CVs and cover letters in the background. It never submits anything for you.

See [STATUS.md](STATUS.md) for the honest list of what's implemented, tested, incomplete and blocked.

---

## Quick start

Prerequisites: macOS or Linux, [uv](https://docs.astral.sh/uv/) (it installs Python 3.12), Node 20+ and `make`.

```bash
make setup          # creates .env from .env.example, installs backend + frontend deps
```

Add your LLM key to `.env`. The default provider is Nebius Token Factory, which has an OpenAI-compatible API:

```
DAR_LLM_API_KEY=...
```

```bash
make dev            # API on http://127.0.0.1:8000, app on http://127.0.0.1:5173
```

Without a key the app still runs. It uses a rule-based planner, rule-based CV extraction and template drafts, and labels all of them as such in the UI. It never fakes AI output.

| Command | What it does |
|---|---|
| `make setup` | Install everything, create `.env` |
| `make dev` | Run the API and the web app |
| `make test` | Conventional automated tests (pytest, no network, no model) |
| `make eval` | Offline deterministic evaluation suite, which writes JSON + HTML reports |
| `make eval-live` | Live evaluations against the configured models (2 repetitions) |
| `make eval-report` | Re-render HTML for the latest reports |
| `make demo` | Run with **synthetic, clearly labelled** demo vacancies (separate DB, no live boards) |
| `make registry` | Re-verify the employer registry against the live Ashby/Greenhouse APIs |
| `make discover-boards` | Use Tavily (needs `TAVILY_API_KEY`) to find more employer boards for listed companies |

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `DAR_LLM_PROVIDER` | `openai_compatible` | `openai_compatible` or `anthropic` |
| `DAR_LLM_BASE_URL` | `https://api.tokenfactory.nebius.com/v1/` | OpenAI-compatible endpoint |
| `DAR_LLM_API_KEY` | — | Server-side only |
| `DAR_GENERATION_MODEL` | `Qwen/Qwen3-235B-A22B-Instruct-2507` | Agent, extraction, assessment, generation |
| `DAR_EVAL_MODEL` | `deepseek-ai/DeepSeek-V4.1-Flash-1017` | Independent fact-check validator and eval judge (different model family) |
| `DAR_DATABASE_URL` | `sqlite:///storage/dar.db` | SQLite (WAL mode, foreign keys) |
| `DAR_STORAGE_DIR` | `storage` | Uploads and application folders (outside the frontend) |
| `DAR_PROVIDER_TIMEOUT_S` / `_CONCURRENCY` / `_MAX_RETRIES` / `_CACHE_TTL_S` | 20 / 6 / 3 / 1800 | Job board retrieval |
| `DAR_AGENT_MAX_TOOL_CALLS` / `_MAX_LLM_CALLS` / `_MAX_BOARDS` / `_TIMEOUT_S` / `_TOKEN_BUDGET` | 30 / 14 / 30 / 240 / 400000 | Agent execution budgets |
| `DAR_MAX_APPLICATION_ATTEMPTS` | 3 | Generation retry cap |
| `DAR_DEMO_MODE` | `false` | Synthetic fixtures only, labelled `[DEMO]` |
| `DAR_WORKER_ENABLED` / `DAR_WORKER_POLL_S` | `true` / 1.5 | Durable background worker |
| `DAR_UPLOAD_MAX_BYTES` | 5000000 | CV upload limit |
| `DAR_LLM_PRICE_IN_PER_MTOK` / `_OUT_` | — | Optional, for cost estimates in run records and eval reports |

External prerequisites: an LLM key for AI features, and outbound HTTPS to `api.ashbyhq.com` and `boards-api.greenhouse.io`.

---

## Architecture (modular monolith)

```
frontend (React + TS + Vite + Tailwind + Framer Motion)  ──/api──▶  FastAPI
                                                                     │
  routes/ ─ profile · jobs · system                                  │
  agents/  orchestrator (bounded loop) · tools (schema-validated) · memory
  services/ cv_parser · onboarding · catalogue · matching · semantic · discovery
            feedback (learning) · applications · validation · document_generator · progress
  providers/ ashby · greenhouse · demo · registry (config/employers.yaml)
  workers/  durable SQLite task queue + in-process worker threads
  SQLite: users, uploaded_documents, candidate_profiles, candidate_facts, requirements, preferences,
          employers, jobs, job_matches, swipes, feedback, discovery_sessions, applications, documents,
          agent_runs, agent_tool_calls, background_tasks, search_strategies, eval_runs, eval_results
```

**The agent.** There's one Career Agent (`backend/app/agents/orchestrator.py`) with an explicit objective. It runs a bounded tool-calling loop: retrieve memory → analyse feedback → adapt strategy → refresh boards → filter and rank → deep-assess the shortlist → create the batch. It stops when it succeeds, when a budget runs out, when it needs clarification, or on failure. The model can only act through 30 explicitly defined tools (`agents/tools.py`). Every argument is validated by Pydantic in code, and every call, including rejected ones, is stored in `agent_tool_calls`. Hard requirements, batch validity, user permissions and budgets are enforced in application code, not in the prompt. No tool exists for shell access, files, arbitrary HTTP, submitting applications, sending email or editing hard requirements. When no key is configured, a deterministic rule planner drives the same tools.

**Matching** (`services/matching.py`) has three stages:
1. Deterministic eligibility against confirmed hard requirements. Each job comes out *eligible*, *ineligible* or *uncertain*. Missing information is never treated as a match, and remote is never assumed to be UK-eligible.
2. Transparent weighted scoring with configurable weights, plus learned feature adjustments.
3. LLM semantic assessment on a shortlist of up to 15 jobs, blended 70/30. It can't override stage 1.

**Learning** (`services/feedback.py`) recomputes evidence-backed hypotheses from persisted decisions, so undo is respected. Each hypothesis records supporting and contradicting decisions and a confidence. Nothing is acted on from a single decision or against contradicting evidence. Anything that touches a hard requirement becomes a question for you and is never applied silently.

**Application preparation** (`services/applications.py`) runs as background tasks:
1. Map the job's requirements to verified facts, each with an id (`[R1.B3]`).
2. Generate the CV and cover letter (citing ids).
3. Run deterministic validation: titles, employers and dates are copied by code; numbers must exist in the cited evidence; credentials must exist in the CV; skills must be verified; omitted roles are restored. Unsupported bullets are removed automatically.
4. Run an independent judge (a separate model and a separate call) for semantic factuality.
5. Render DOCX files into `storage/applications/<user>/<employer>/<title>_<job_id>/`.

Material issues mark the draft *Needs your check* instead of *Ready for review*.

**Providers.** Ashby uses `GET /posting-api/job-board/{board}?includeCompensation=true` and Greenhouse uses `GET /v1/boards/{token}/jobs?content=true`. Both response schemas were verified live on 2026-11-24. Retrieval uses async httpx with a concurrency cap, timeouts, bounded exponential backoff, `Retry-After` handling, a TTL cache with unchanged-board detection, partial success and per-board error reporting. Vacancies are normalised into one schema with per-field provenance (provider / extracted / AI / unknown). Cross-provider duplicates are merged with provenance kept. A job is marked *potentially closed* after one missed successful fetch and *closed* after a second. A failed fetch never closes anything.

**Employer registry** (`config/employers.yaml`, rebuilt by `scripts/build_registry.py`) covers 166 employers. The 100 Nasdaq-100 constituents come from Wikipedia's "List of NASDAQ-100 companies" (retrieved 2026-11-24). 12 of them have verified public Greenhouse boards: Airbnb, Alnylam, AppLovin, Astera Labs, Axon, CoreWeave, Datadog, DoorDash, Nebius, Rocket Lab, SpaceX and Take-Two. None were found on Ashby. The rest are recorded as `unsupported`, and no board was invented for any of them. 66 supplementary UK-relevant tech employers (Monzo, Stripe, GitLab, ElevenLabs, Synthesia, Wise-adjacent fintechs and others) are marked `nasdaq100: false`. You can add employers by editing the YAML; no code change is needed.

## Privacy and safety

- Secrets live only in `.env`, server-side. CV text is sent only to the configured LLM provider, after the consent shown on the upload screen. Nothing is sent to job boards except public GET requests.
- Upload type, size and magic-byte checks are in place. Stored files get random names, application paths are sanitised and traversal is blocked, HTML is sanitised (nh3), and logs contain minimal personal data.
- Job descriptions, CVs and comments are treated as untrusted. They are fenced in prompts and checked deterministically for embedded instructions. Embedded instructions can't authorise tools or change requirements.
- `DELETE /api/profile/data` removes all of your data and files.
- **Local single-user mode.** Identity comes from an `X-DAR-User` header (default 1) so data stays scoped per user and cross-user access is testable. This is **not authentication**. Do not deploy publicly as-is. Check UK GDPR obligations and job-data licensing before any commercial use.

## Evaluation

The `evals/` folder holds labelled synthetic datasets (development and held-out), scoring code, ranking metrics (P@10, precision among returned, recall, nDCG@10), an LLM judge for the application rubric, and the runner and reports. The five critical scenarios (EVAL-001 to EVAL-005) are covered offline using the exact production code, and live against the configured models. Reports are written to `evals/reports/*.json|html` and shown in the in-app **Agent Lab** (`/lab`), which displays only results from real report files. See STATUS.md for the latest numbers.

---

## Three-minute investor demo

1. **Problem (20s).** Job hunting is an executive-function marathon. DAR takes the searching, sorting and drafting off your plate, and you stay in charge.
2. **Personalisation (40s).** Upload `fixtures/cvs/synthetic_alex_morgan.txt` (a synthetic person). Answer a few questions in the chat, for example "UK remote or London hybrid", "75k" and "rather not do on-call". On the review screen, show the verified CV facts and the separate *AI interpretation* box, along with your hard requirements versus your preferences.
3. **Real discovery (40s).** Press *Find my matches*. The live activity feed shows the boards searched, the count of live Ashby and Greenhouse vacancies, the jobs excluded and why, and the in-depth reading of the shortlist. Open a card to show the salary or *Salary not disclosed*, the match explanation, concerns, and the original vacancy link.
4. **Human supervision (30s).** Swipe right on a TAM role, left on an on-call platform role (choose *On-call*), and down for Maybe. Use the buttons and arrow keys, then undo with **U**.
5. **Adaptation (30s).** Finish the batch and press *Discover more*. The activity feed and banner show the actual strategy change, such as lower on-call weighting, a higher customer-facing weighting, or a search widened to pre-sales titles. The *Learning* tab shows the hypotheses with their evidence and your untouched hard requirements.
6. **Tangible output (20s).** Go to *Jobs → Interested*. Open the package: CV and cover letter previews, the fact-check result (and any removed lines), DOCX downloads, the ZIP, and the employer-specific folder path.
7. **Evidence (20s).** Open *Lab*. Show the latest live evaluation run with model ids, pass/fail per case, critical failures (disclosed if any) and the quality gates with sample sizes.

> Most recruitment technology works for employers. DAR is an intelligent agent that works for the candidate.

Offline fallback: `make demo` runs the same flow on synthetic vacancies labelled `[DEMO]`.
