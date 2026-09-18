# Text-to-SQL Agent — Media Analytics

A conversational agent that answers natural language questions over a media analytics database. It writes SQL, executes it, and returns answers in text, tables, and charts — with full reasoning at every step.

Built for a *Marketing Mix Modeling* project: the agent is designed to **explore and audit data before modeling**, not to model.

---

## Demo

> *"How was the marketing budget distributed across channels last year?"*

The agent announces what it's looking for, writes its SQL query, and returns:

- a **written answer** — scope clearly stated, caveats included (e.g. SEO doesn't appear in the ranking because its cost is `NULL`: not purchased, not free)
- a **chart**, automatically chosen by the code based on the shape of the result, or declined with a reason when no honest figure is possible
- **every query executed** — reasoning written before running it, tables actually read, syntax-highlighted SQL, result

**Trick questions are part of the contract.** Asking for a ranking of the "best-performing" channels gets a reasoned refusal: GRP, clicks, and impressions are not comparable, and nothing in this data links a channel to a sale.

---

## Stack

| Layer | Technology |
|---|---|
| LLM | Claude (Anthropic) |
| Database | DuckDB |
| Backend | Python — FastAPI, Pandas |
| Frontend | React + Vite + TypeScript |
| Delivery | Docker |
| Tests | pytest, Vitest |

---

## Architecture

The guiding principle: **anything that must always be true cannot depend on the model**. SQL bounds, chart rules, total computation — all enforced in code. The prompt describes the world; it decides nothing critical.

```
src/
├── etl/          builds the database from source files
│   ├── transforms.py     pure functions — no side effects
│   ├── checks.py         data contract: blocking invariants + warnings
│   └── build_db.py       orchestration, atomic writes, CLI
├── db/
│   ├── connexion.py      hardened connection: read-only, network access closed
│   └── sql.py            single entry point — validate, bound, execute
├── agent/
│   ├── boucle.py         ask(question, history) → AgentResponse
│   └── prompt/           data description: generated from the DB + hand-written
├── charts/               decides whether there's a chart, and which one — pure functions
└── app/                  HTTP API — thin shell, no business logic

web/                      React + Vite + TypeScript interface
tests/
├── test_*.py             unit tests (~480 tests, 0 API calls)
└── eval/                 evaluation harness under real conditions
```

**Three properties enforced mechanically:**

- No module opens the database outside `src/db/connexion.py` — a test scans the sources to guarantee it
- The test suite makes zero API calls — a test fails on any attempt
- Observation does not change the result — `ask()` accepts an optional `trace=` callback, but two identical runs, observed or not, return the same answer

---

## Features

**Agentic loop**
The model has a single tool: execute a read SQL query. It can chain multiple queries per turn, recover from SQL errors, and stops when it has enough to answer. The last turn is always a written response — work is never discarded.

**Hardened SQL access**
`run_sql` validates using the engine's own parser (never a regex), rejects anything that isn't a single read statement, bounds row count and execution time, and reports any truncation. It also automatically **computes the sum of columns produced by a `SUM()`**, so the model never adds numbers in its head.

**Code-driven charts**
Four recognized shapes: time series or categorical (wide format), pivot (long format), scatter plot (correlation), histogram. Refusal is a first-class result — empty result, single row, too many categories, incomparable units: the module says so, it never forces a figure.

**Real-time interface**
Execution steps are streamed live (Server-Sent Events). Light/dark theme, interactive charts (zoom, toggle between line/bar/stacked), automatic dual axis when series have very different scales.

**Data upload from the UI**
A dedicated page lets you upload source files and re-run the pipeline without touching the terminal. Uploads go through a staging folder promoted only after a successful build — a malformed file corrupts neither the sources nor the database.

---

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- An Anthropic API key

### Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in ANTHROPIC_API_KEY
```

Drop source files into `data/raw/`, then build the database:

```bash
python -m src.etl.build_db
```

### Running

```bash
./run.sh                      # development — API + interface with hot reload
docker compose up --build     # production — single image, single address (:8000)
```

> ⚠️ Every question consumes billed API calls.

---

## Testing

```bash
pytest tests/ -q              # ~480 tests, ~10 s, 0 API calls
cd web && npm test            # frontend tests
```

### Evaluation under real conditions

```bash
python -m tests.eval --a-blanc          # replays the cache: 0 calls, $0
python -m tests.eval --k 1              # ⚠ billed run
```

The cache is keyed on everything that can change a response (model, prompt, bounds, loop version). **Always start from cache**: already-paid runs replay for free.

---

## Deployment

```bash
docker compose up --build
```

A single image serves the API and the static frontend. Data and the API key are mounted, never baked into a layer — the image contains no client data. The container runs as a non-root user.

---

## Conventions

- Code, comments, and commits are in **French** (client project convention)
- Lines ≤ 92 characters
- Comments explain **why**, not what
- No API keys in code — everything goes through environment variables
