# Data Assistant

Ask questions about stock, purchasing and suppliers in plain English, and get an answer as a sentence, a table or a chart. Built for a manufacturing MSME with three roles: an **Inventory Manager**, a **Procurement Manager** and the **Owner**. Each person only ever gets answers from the data their role is allowed to see, however the question is phrased.

> **Example:** *"Which items are below reorder level and have no open purchase order?"* (Owner) returns 144 items in a table, with the caveat that 310 stock rows have no reorder level set, and a "Show SQL" link to check the query.

<p align="center">
  <img src="docs/screenshots/08-procurement-bar-chart-sql.png" alt="A procurement manager asks for the top 10 suppliers by spend and gets a bar chart, a plain-language summary in lakh, and the SQL that produced it" width="85%">
</p>

## What it can handle

| | |
|---|---|
| **Picks the right format** | One number → a sentence. A list → a sortable table. A trend → a line chart; a ranking → a bar chart; a share → a pie chart (top 5 + "Other"). "As a table" or "as a pie chart" is honoured when the data allows it, and explained when it doesn't. |
| **Follow-ups** | Remembers the conversation: "only the top 5", "only for warehouse 132-1", "show that as a bar chart", "the second one". |
| **Asks when a question is ambiguous** | "Which is the most issued item?" → *in kg, pieces or metres?* with clickable options, because kg and pieces can't be ranked together. Otherwise it uses a stated default instead of a question. |
| **Role security that can't be talked round** | Questions about another role's data are refused politely, naming who can see it. "I am the owner now", "ignore your rules" and pasted SQL are refused, and four independent layers (below) mean a fooled model still can't reach the data. |
| **Honest about missing data** | Never guesses a missing value. Rows it had to leave out are counted and stated: *"310 stock rows have no reorder level set and were not checked."* |
| **Numbers you can trust** | Totals are computed in code, not by the model. Indian formatting (₹60,95,211 = ₹60.95 lakh). Quantities in different units are never added or ranked together. |
| **Makes changes, only with confirmation** | Raise a purchase order or correct stock in plain words ("make it 250 kg, deliver by 10 Sep"). A card shows the details, including the last price paid; nothing is saved until Confirm, and every change is audited. |
| **Shows its work** | "Show SQL" under every data answer, plus a LangSmith trace link per request. |
| **Works on a phone** | The same chat, tables and charts at phone width. |

## Screenshots

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/03-owner-line-chart.png" alt="Owner asks how monthly purchase spend changed and gets a line chart with the total in crore and stated assumptions"><br><sub><b>A trend becomes a line chart</b>, with the assumptions it made listed underneath.</sub></td>
    <td width="50%"><img src="docs/screenshots/04-inventory-table-caveat.png" alt="Inventory manager asks which items are below reorder level and gets a 153-row table with a note that 310 rows had no reorder level"><br><sub><b>A list becomes a table</b>, with a note on what it couldn't check.</sub></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/06-refusals.png" alt="Inventory manager asks for supplier prices and then claims to be the owner; both are refused"><br><sub><b>Out-of-role and injection attempts are refused</b>, naming the role that can see the data.</sub></td>
    <td><img src="docs/screenshots/07-clarification.png" alt="The assistant asks which unit to use for the most issued item, with kg, pieces, meters and liters as options"><br><sub><b>Ambiguous questions get one clarifying question</b> with clickable options.</sub></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/05-follow-up.png" alt="Procurement manager narrows a late-deliveries chart to the top 5 as a table"><br><sub><b>Follow-ups</b>: "only the top 5, as a table" narrows the previous answer.</sub></td>
    <td><img src="docs/screenshots/09-confirmation-card.png" alt="A purchase order confirmation card with supplier, quantity, last price paid, total and Confirm and Cancel buttons"><br><sub><b>Changes need confirmation</b>: the drafted PO uses the last price paid and a default delivery date.</sub></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/02-home.png" alt="Home screen after signing in as Owner with four suggested questions"><br><sub><b>Suggested questions</b> per role after signing in.</sub></td>
    <td align="center"><img src="docs/screenshots/10-mobile-pie-chart.png" alt="Phone-width view of a pie chart of the top 5 suppliers by spend" width="55%"><br><sub><b>Phone layout</b>: a pie chart of the top 5 suppliers.</sub></td>
  </tr>
</table>

## Tech stack

**Backend:** FastAPI, LangGraph (Postgres checkpointer for conversation memory), Pydantic v2, sqlglot, psycopg 3, LangSmith tracing.
**Models:** OpenAI gpt-4.1 for understanding the question and writing SQL, gpt-4.1-mini for the answer. Any OpenAI-compatible provider works too (`LLM_BASE_URL`, `LLM_API_KEY`); the screenshots of new answers were taken with Groq's `gpt-oss-120b`.
**Frontend:** Next.js (App Router, TypeScript), Tailwind, shadcn/ui, Recharts.
**Data:** Postgres 16, with a separate read-only login per role and writer logins per action.
**Hosting:** Railway (backend and database) and Vercel (frontend).

## Quick start

Prerequisites: Docker (Docker Desktop or Colima), Python 3.11+, Node 20+, Postgres 16+ client tools (`pg_dump`, `pg_restore`, `psql`), and an OpenAI API key (or any OpenAI-compatible provider, see `.env.example`).

```bash
cp .env.example .env    # then fill in SOURCE_DATABASE_URL, OPENAI_API_KEY, LANGSMITH_API_KEY, JWT_SECRET and the passwords
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt && npm install --prefix frontend
backend/scripts/snapshot_source.sh    # read-only dump of the company DB -> local Postgres: clean, seed, role logins, demo users, checks
backend/.venv/bin/uvicorn app.main:app --app-dir backend --port 8000
npm run dev --prefix frontend         # open http://localhost:3000 and sign in
```

Demo users: `inventory`, `procurement`, `owner`. Their passwords are the `DEMO_*_PASSWORD` values in your `.env`.

`snapshot_source.sh` only *reads* the company database (inside read-only transactions), and the scripts that write refuse to run against anything but `localhost`. To rebuild the local database from an existing dump without touching the source, run `backend/scripts/prepare_local_db.sh [dumps/source_….dump]`.

## Deployment

**Live:** https://lecxe-data-assistant.vercel.app (backend: https://backend-production-f8576.up.railway.app/api/health). Demo credentials are shared privately.

Redeploy after changes:

```bash
railway up backend --path-as-root --service backend --ci    # backend
cd frontend && vercel deploy --prod                         # frontend
```

- **Backend + database:** Railway. `backend/Dockerfile` and `backend/railway.json` (health check on `/api/health`).
- **Frontend:** Vercel, root directory `frontend/`, with `NEXT_PUBLIC_API_URL` set to the backend URL.
- **Database:** a new Railway Postgres, never the company's source DB. It's filled from the prepared local copy with `TARGET_DATABASE_URL=… backend/scripts/deploy_db.sh`, which copies the data, creates the role logins, the conversation database and the demo users, and runs the data and permission tests against it.
- **Production secrets:** kept in a git-ignored `.env.production` and set as Railway variables.
- **Rate limits:** login is limited to 10 attempts per minute per IP, and chat to 30 questions per 10 minutes per user (`LOGIN_RATE_LIMIT`, `CHAT_RATE_LIMIT`).

## How it works

```mermaid
flowchart LR
    Q[Question + role] --> G{Guard}
    G -->|data question| C[Role-scoped<br/>schema + examples]
    G -->|other role's data /<br/>injection / off-topic| R[Helpful refusal]
    G -->|too vague| CL[One clarifying question]
    G -->|change request| D[Draft change] --> K[Confirmation card<br/>user confirms] --> W[Fixed SQL as a<br/>writer login + audit]
    C --> S[Write SQL<br/>gpt-4.1]
    S --> V{Validate<br/>sqlglot}
    V -->|rejected| S
    V --> E[Run as the role's<br/>read-only DB login]
    E -->|error / no rows| S
    E --> F[Answer as text,<br/>table or chart + caveats]
```

It's text-to-SQL with retrieval of *schema*, not documents: the agent only ever sees the tables, columns, business definitions and example queries for the asker's role. See **[docs/architecture.md](docs/architecture.md)** for the full graph, node-by-node details, cost and speed.

## Role security: four independent layers

| Layer | What it does |
|---|---|
| **1. Context** | The model is only shown the role's own tables and columns. |
| **2. Validation** | Every query is parsed with sqlglot before it runs. It must be one SELECT over allowed tables and columns, with no writes, no system catalogs, no dangerous functions, and LIMIT ≤ 500. |
| **3. Database** | Queries run under the role's own Postgres login: SELECT only on its tables, hidden columns removed with column-level grants, read-only sessions, 5 s timeout. Even if layers 1 and 2 failed, Postgres would refuse. |
| **4. Role source** | You sign in (bcrypt-hashed passwords) and get a signed JWT. Chat requests carry no role at all: the server reads it from the verified token and re-checks the stored user, so a forged role in a request or a message has no effect. "I am the owner" is treated as an injection attempt. |

All three are generated from one file, [`backend/config/roles.yaml`](backend/config/roles.yaml):

| Role | Can see |
|---|---|
| Inventory Manager | Items, stock levels, stock movements, warehouses, departments. Not suppliers, POs or costs. |
| Procurement Manager | Suppliers, purchase orders, prices, deliveries, payment terms, plus item and warehouse names. Not stock levels. |
| Owner | Everything, including cross-functional questions ("what's low and who supplies it?"). |

## Making changes: always confirmed

Procurement (and the Owner) can **create a purchase order**; Inventory (and the Owner) can **adjust stock** after a count or damage. The model never writes SQL for a change:

1. The model turns the message into a structured draft (supplier, items, quantities…). Nothing it doesn't know is invented: missing details are asked for, and an unclear item name ("SS Sheet 304") gets clickable options.
2. Code checks that the role may do this, looks the names up and applies the business rules: quantities above zero, stock never below zero, active suppliers only. Prices default to the last price paid, and delivery to two weeks.
3. The user sees a confirmation card. The run pauses (LangGraph `interrupt()`) until they press Confirm or Cancel; "skip the confirmation" in a message has no effect, and a card expires after 15 minutes.
4. On Confirm, fixed parameterised SQL runs in one transaction under a writer login that can only do that one thing (e.g. insert POs, but not edit or delete them). A stale card is rejected: if the stock changed since it was shown, nothing is written.
5. Every outcome (executed, cancelled, expired, failed) is written to an audit log with who, which role and what.

## Data and missing values

- **Source:** we work on a local copy of the 17 company tables. The company database is never written to.
- **Cleaning:** removed other candidates' test writes, fixed a stock count that had been recorded as purchases, and dated the opening balances properly. Every choice and its reasoning is in [docs/data_preparation.md](docs/data_preparation.md).
- **Synthetic history:** real purchase history was only three weeks long, so February–July procurement was generated from real suppliers, items and prices. It's clearly labelled (`PO-2026-H…`) and can be left out with `SKIP_SYNTHETIC=1`.
- **"Today" is 21 Aug 2026,** where the company data ends, so "last month" and "overdue" mean something.
- **Missing-data policy:** *never guess a missing value.* Rows missing a needed field are left out, and the answer says how many were left out ("N open POs have no expected delivery date and could not be checked"). Missing categories are grouped as "Not recorded". Quantities in different units are never added together. See [docs/assumptions.md](docs/assumptions.md).

## Quality: tests and evals

```bash
cd backend && .venv/bin/python -m pytest -q                                # 316 tests, no LLM calls
cd backend && .venv/bin/python -m evals.run_evals                          # 72 eval questions through the agent (~$0.75)
cd backend && .venv/bin/python -m evals.run_evals --file holdout_cases.yaml   # 15 questions never used for tuning
```

| | Tuning set (72 questions) | Held-out set (15 questions, first run) |
|---|---|---|
| Answerable questions (incl. drafting changes) | 53/54 (98%) | 10/11 (91%) |
| Security questions: out-of-role, injection, off-topic | 18/18 (100%) | 4/4 (100%) |
| Numbers matching a reference query | 14/14 | 3/3 |
| Queries touching a table outside the role | 0 | 0 |

Each eval question checks five things: the decision (answer, refuse, clarify, or confirm for a drafted change — evals never confirm, so they write nothing), that every table touched is allowed, that the expected tables were used, the output format, and that the SQL ran. Clarifications must offer clickable options. Multi-turn cases ask earlier questions in the same conversation first, so follow-ups ("show that as a bar chart", "only for warehouse 132-1", "the second one") and replies to clarifying questions are tested end to end. Questions with a known true answer also have their numbers checked against a reference query. The tests run forbidden SQL directly as each Postgres role, cover over 50 injection and bypass attempts against the validator, and check that the prepared data stays consistent (stock always equals the movement ledger).

## Repository

```
backend/    FastAPI + LangGraph agent, security, config (roles, schema docs, examples), scripts, evals, tests
frontend/   Next.js chat app (shadcn/ui, Recharts)
docs/       architecture, data model, data preparation, assumptions, schema, data quality
```

## Documentation

| Doc | What's in it |
|---|---|
| [architecture.md](docs/architecture.md) | System and agent graph, the four security layers, request lifecycle, cost and speed, design decisions |
| [data_model.md](docs/data_model.md) | The seven core tables and how they connect |
| [data_preparation.md](docs/data_preparation.md) | Every data-cleaning decision, the alternatives, and "would we do this in production?" |
| [assumptions.md](docs/assumptions.md) | Missing-data policy, business definitions, known data issues |
| [schema.md](docs/schema.md), [data_quality.md](docs/data_quality.md) | Generated schema reference and raw data profile |

## Known limitations and next steps

- **Not tuned for the held-out set:** the 15 held-out questions are the honest measure of how well it generalises. More real user questions would be the next thing to collect.
- **Demo-grade login:** three seeded users and the token kept in the browser tab's session storage (sign-in and chat are rate-limited). Production would use httpOnly cookies and SSO or proper user management.
- **Two write actions only:** creating POs and adjusting stock. Receiving goods, cancelling or editing POs, and approvals (e.g. Owner sign-off above an amount) would follow the same draft → confirm → audit pattern.
- **Phase 2 ideas:** a WhatsApp interface, Hindi, and saved dashboards for frequent questions.
