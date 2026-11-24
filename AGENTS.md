# Notes for coding agents working on DAR Careers

- **Layout.** This is a modular monolith. `backend/app` holds the FastAPI app, agent, services, providers and worker. `frontend/src` holds the React app. `evals/` holds datasets, the runner and reports. `config/employers.yaml` is the employer registry.
- **Run things.** Use `make test` (pytest, offline, no model), `make eval` (offline evals) and `make eval-live` (needs `DAR_LLM_API_KEY`). Run the tests and the offline evals after any change to matching, learning, validation, tools or prompts. Run the live evals after changing prompts or models.
- **Invariants you must keep:**
  - Hard requirements change only through user routes (`set_requirement_trusted`). Never add an agent tool that writes them.
  - Every agent tool has a Pydantic argument model, and its permission check lives in code, not in the prompt.
  - Unknown values stay `None`: never 0 and never an assumed match. Remote is never assumed to be UK-eligible.
  - Generated documents cite fact ids. Titles, employers and dates are copied by code. Numbers and credentials must exist in the CV.
  - Never fabricate API results, agent activity, scores or evaluation results. Skipped cases are not passes.
  - Demo fixtures are synthetic and labelled `[DEMO]`. Never mix them with live data.
- **Prompts are versioned** with a `PROMPT_VERSION` constant in each module. Bump it when you change a prompt, and record that in the eval report.
- **No secrets in git.** `.env`, `storage/` contents and eval reports are ignored. Do not commit real CVs.
