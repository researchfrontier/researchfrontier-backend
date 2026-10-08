# researchfrontier-backend

FastAPI service for **ResearchFrontier**. Aggregates recent papers, classifies
them against the OpenAlex taxonomy, derives a peer-review badge, and serves field
feeds, emerging directions and digests. The frontend generates its typed client
from this service's `/openapi.json`.

## Endpoints (slice)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | liveness |
| GET | `/api/taxonomy/tree` | domains → fields → subfields (the browse tree) |
| GET | `/api/taxonomy/subfields/{id}` | breadcrumb for a field |
| GET | `/api/fields/hot?window=30&limit=12` | hottest fields, ranked by recent output + momentum |
| GET | `/api/fields/{id}/papers?window=7&status=` | recent papers in a field, with badges + DOI |
| GET | `/api/fields/{id}/directions?window=30` | topic breakdown — where the field is moving |
| GET | `/api/fields/{id}/digest` | the Mon/Wed/Fri in-app brief |
| GET | `/api/papers/{id}` | a single paper |

Interactive docs at `/docs`, schema at `/openapi.json`.

> "field" in the API == an OpenAlex **subfield** (a specific research area, e.g.
> `1702` Artificial Intelligence). Its **directions** are the topics beneath it.

## Peer-review badge

Derived in `app/services/badges.py` from OpenAlex signals (source type, version,
work type, `is_retracted`), Crossref as confirmation. Values: `peer_reviewed`,
`preprint_published`, `preprint`, `retracted`, `unknown` — each stored with a
confidence level and the evidence that produced it (shown as "why" in the UI).

## Run

Normally via `researchfrontier-infra` (brings up db + backend together):

```bash
cd ../researchfrontier-infra && docker compose up --build
```

Standalone against a running Postgres:

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows; use bin/activate on *nix
pip install -r requirements.txt
cp .env.example .env            # point RF_DATABASE_URL at your Postgres
uvicorn app.main:app --reload
```

## Scheduled jobs

```bash
python -m app.jobs.sync_taxonomy        # pull the full OpenAlex taxonomy (source of truth)
python -m app.jobs.ingest --days 7      # pull recent papers per field
python -m app.jobs.digest --window 3    # build the Mon/Wed/Fri briefs
```

In production these run as GitHub Actions cron (`.github/workflows/`), with the
DB URL and OpenAlex key provided as repo secrets (`RF_DATABASE_URL`,
`RF_OPENALEX_API_KEY`, `RF_CONTACT_EMAIL`).

## Config

All via `RF_`-prefixed env vars — see `.env.example`. The bundled demo seed needs
no API keys; live ingestion wants a (free) OpenAlex key and a contact email.
