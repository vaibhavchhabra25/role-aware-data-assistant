# Project checklist

Status of the role-aware data assistant. Updated as work lands.
Legend: `[x]` done · `[~]` in progress / waiting on a decision · `[ ]` not started

## Step 0: Setup and data discovery
- [x] Local tooling: Docker (Colima), Postgres client tools, Python 3.11 venv
- [x] Repo scaffold: git, `.gitignore`, `.env.example`, `docker-compose.yml`
- [x] `.env` filled: source DB URL, OpenAI key
- [x] LangSmith API key in `.env` (US region), tracing verified
- [x] OpenAI key checked: gpt-4.1 / gpt-4.1-mini work
- [x] `inspect_schema.py` → `docs/schema.md` (17 original tables; the shared DB also holds other candidates' objects, which are ignored)
- [x] `profile_data.py` → `docs/data_quality.md`
- [x] Snapshot of source → local Docker Postgres (`snapshot_source.sh`, taken 2026-10-14)
- [x] SQLAlchemy models for all 17 tables (`backend/app/db/models.py`), verified against the DB
- [x] Role mapping proposal (`backend/config/roles.yaml`), approved: Inventory and Procurement kept separate
- [x] Missing-data policy and business definitions (`docs/assumptions.md`)
- [x] Rows written by other candidates: test writes removed, reconciliation kept as opening balances (see `docs/data_preparation.md`)

## Step 1: Test cases first
- [x] `evals/test_cases.yaml`: 56 cases (Inventory 20, Procurement 20, Owner 16), reviewed. Ambiguous-case behaviour to revisit later.
- [x] `evals/run_evals.py`: decision / tables ⊆ allowed / expected tables / format / SQL ran → summary table (dry-run with a fake agent; runs for real once `app.agent.service.ask` exists)

## Step 2: Data preparation
- [x] Data preparation pipeline `prepare_local_db.sh`: restore → `clean_data.sql` → `seed_extra_data.py` → roles → invariant tests (11)
- [x] Cleaning: test writes removed, 29 Jul stock count re-typed, opening balances re-dated; as-of date fixed at 21 Aug 2026 (`DATA_AS_OF`)
- [x] Synthetic procurement history Feb–Jul (69 POs, labelled, `SKIP_SYNTHETIC=1` to omit); reorder levels derived from consumption
- [x] Decisions documented with reasoning in `docs/data_preparation.md`; `assumptions.md` updated
- [x] `config/schema_docs.yaml`: business meaning per table/column, rules, joins (`docs/data_model.md` explains which tables matter)
- [x] `config/examples.yaml`: 22 question → SQL examples with caveat count queries; each validated and run as the narrowest role (`tests/test_context.py`)

## Step 3: Role security (database layer)
- [x] `setup_db_roles.sql` generated from `roles.yaml` (`role_inventory`, `role_procurement`, `role_owner`, read-only, 5 s timeout, column-level grants); `apply_db_roles.sh` applies it, `snapshot_source.sh` re-applies after restore
- [x] Test: forbidden SQL as each role → permission denied (`tests/test_db_roles.py`)

## Step 4: Agent (LangGraph)
- [x] State + graph wiring; Postgres checkpointer in its own `agent_state` DB, threads namespaced by role
- [x] `guard` node (gpt-4.1: measured 112/112 vs 109/112 for mini on guard-only eval; ~₹0.15/request)
- [x] `retrieve_context` node (role-filtered tables/columns/joins/examples)
- [x] `generate_sql` node (gpt-4.1; returns sql, assumptions, caveat count queries)
- [x] `validate_sql` node (also rejects CURRENT_DATE/NOW: today = as-of date)
- [x] `execute_sql` node (role login, read-only, 5 s timeout, 3 attempts, one re-check on empty results)
- [x] `format_response` node (totals and ₹ lakh/crore computed in Python; chart checked against data, no mixed-unit charts)
- [x] `refuse` / `clarify` / `unanswerable` / `give_up` nodes
- [x] Validator unit tests incl. injection SQL (`tests/test_sql_validator.py`); 146 tests pass in total
- [x] LangSmith traces per node, `trace_url` in every response
- [x] Request logging to `backend/logs/requests.jsonl` (role, question, SQL, tables, attempts, latency, tokens)

## Step 5: API (FastAPI)
- [x] `POST /api/chat`, `GET /api/health`, `GET /api/roles` (with starter questions per role); input validation, CORS for the frontend
- [x] `POST /api/chat/stream`: SSE progress per step, then the result (one code path with `/api/chat`)

## Step 6: Frontend (Next.js)
- [x] Chat page with role selector, data-as-of date and starter questions (Next.js 16, shadcn/ui, own components; no chat kit)
- [x] Renderers: text, table (sortable), chart (Recharts, chart/table toggle), refusal, clarification, error with retry
- [x] "Show SQL" + trace link, streamed progress, empty / service-down / error states, mobile layout, system dark mode

## Step 7: Quality gate
- [x] Evals: 40/40 answerable, 16/16 security, 12/12 correct numbers, 0 out-of-role access (2026-10-15)
- [x] Held-out set (`evals/holdout_cases.yaml`, 15 questions, first run): 10/11 answerable, 4/4 security, 3/3 correct numbers; the one miss exposed a missing business definition, now in schema docs
- [x] Held-out miss (stock value) re-verified 2026-10-19: passes; numbers checked against a reference query found 5 rows priced in a different unit (pcs vs per-kg price), now excluded, value ₹19.83 → ₹16.60 lakh; own-10 now checks the value
- [x] Missing-data caveats appear where relevant (verified in answers and missing-data eval cases)

## Step 7b: Improvements after hands-on testing (2026-10-15)
- [x] Ambiguity: ask when items are ranked by quantity without a unit or the subject is unclear; clickable options (+ "By amount spent" for roles that see prices); defaults stated otherwise
- [x] Follow-ups: presentation changes ("as a bar chart", "top 3") handled; previous SQL passed to follow-ups; multi-turn eval cases
- [x] Units: PO unit labels standardised in data prep (`KG` → `kg`, `Nos.` → `pcs`, …); quantities never added or ranked across units (deterministic checks)
- [x] Formats: explicit chart/table requests honoured; chart feasibility decided before the answer; pie top 5 + Other, bar top 25; never claims a chart it doesn't show
- [x] Startup warm-up: first question after a restart ~3 s (was ~28 s)
- [x] Evals (66 cases): 49/50 answerable, 16/16 security, 14/14 correct numbers, 0 out-of-role access
- [ ] Nit: "bought" answers sometimes use received only, without stating it (default asks for ordered and received)
- [x] Review of a real Inventory session: no totals/charts of quantities with mixed or unknown units; follow-ups that drop rows say so; unrecorded values never counted as a named group; repeated charts over duplicate labels avoided; suggestions only on the first refusal

## Step 8: Bonus (only after the quality gate)
_Writes deferred on 2026-10-15, built on 2026-10-16 with the agreed defaults (two actions, dated as-of, local first)._
- [x] Role-based login: 3 demo users (bcrypt) in the app DB, JWT with role, role read only from the token (body role ignored, stored user re-checked), sign-in UI with session resume and sign-out
- [x] Write operations: create PO (Procurement, Owner), adjust stock (Inventory, Owner); model drafts a structured action, code resolves + validates, `interrupt()` confirmation card, fixed SQL under writer logins with minimal grants, audit log (`app_audit_log`)
- [x] Verified locally: unit tests (grants, rules, stale card, ledger), UI flow (draft → edit → cancel / confirm), 6 write eval cases pass (full suite 72: 53/54 answerable, 18/18 security, 14/14 correct; miss proc-02 = chart instead of table); e2e rows removed
- [x] Production (2026-10-16): writer passwords set on Railway, prod DB re-copied with writer logins (95 data/permission + 11 writer-grant tests pass), backend + frontend redeployed, TCP proxy turned off again

## Step 9: Delivery
- [x] README (setup in 5 commands, diagram, security layers, data policy, eval results, docs index)
- [x] `docs/architecture.md` (system + agent graph, node table, security layers, lifecycle, cost/speed, design decisions)
- [x] Hosted: frontend on Vercel (https://lecxe-data-assistant.vercel.app), backend + new Postgres on Railway; prod DB loaded from the prepared copy and verified (95 data/permission tests); per-user rate limits; secrets only in Railway
- [~] Presentation (~10 min, see CLAUDE.md §14). COO asked for it to be simple, not complicated. Deck drafted (11 slides, speaker notes); demo script with dry-run results in `docs/demo_script.md`; demo passwords simplified (local + prod). Left: rehearse on the live site, share the deck link

## Step 10: Portfolio (2026-10-25)
- [x] README: screenshots (9, taken locally at 2x), "what it can handle", tech stack
- [x] Optional OpenAI-compatible provider (`LLM_BASE_URL`, `LLM_API_KEY`); OpenAI case-study key revoked, local runs use Groq gpt-oss-120b/20b
- [x] Format prompt: totals after a top-N cut are never called "all" (found while checking a screenshot)
- [ ] Live site: needs a working LLM key on Railway (OpenAI key revoked)
