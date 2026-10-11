# researchfrontier-backend

FastAPI service for **ResearchFrontier**. Aggregates recent papers, classifies
them against the OpenAlex taxonomy, derives a peer-review badge, and serves field
feeds, emerging directions, trends and digests. The frontend's typed client
(`src/lib/types.ts`) mirrors this service's `/openapi.json` by hand.

## Endpoints (slice)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | liveness |
| GET | `/api/taxonomy/tree` | domains → fields → subfields (the browse tree) |
| GET | `/api/taxonomy/subfields/{id}` | breadcrumb for a field |
| GET | `/api/fields/hot?limit=12` | hottest fields, ranked by recent output + momentum |
| GET | `/api/fields/cold?limit=12` | quietest fields by recent output |
| GET | `/api/fields/{id}/papers?window=30&status=&venue=&search=` | recent papers in a field (window ≤ 30 days, title search), with badges + DOI |
| GET | `/api/fields/{id}/directions` | topic breakdown — where the field is moving |
| GET | `/api/fields/{id}/digest` | the Mon/Wed/Fri in-app brief |
| GET | `/api/topics/{id}/papers?window=30&status=&venue=&search=` | recent papers in a direction (same filters) |
| GET | `/api/papers/{id}` | a single paper |
| GET | `/api/trends/hot?limit=12` | Trends: hottest fields with their yearly output curve |
| GET | `/api/trends/fields/{id}` | Trends: one field's yearly output, momentum, top directions, most-cited papers, most-active institutions/countries |
| GET | `/api/trends/institutions?q=` | institution search (live OpenAlex, cached) |
| GET | `/api/trends/institutions/{id}` | fields an institution is most active in (live OpenAlex, cached a day) |
| GET | `/api/limits` | free-plan limits of Neon / OpenAlex / Render, with live numbers where safe |

Interactive docs at `/docs`, schema at `/openapi.json`.

> "field" in the API == an OpenAlex **subfield** (a specific research area, e.g.
> `1702` Artificial Intelligence). Its **directions** are the topics beneath it.

## Peer-review badge

Derived in `app/services/badges.py` from OpenAlex signals (source type, version,
work type, `is_retracted`), Crossref as confirmation. Values: `peer_reviewed`,
`preprint_published`, `preprint`, `retracted`, `unknown` — each stored with a
confidence level.

## Stored papers

Paper lists are served from Postgres (the `work` table), never live from
OpenAlex. Only what the lists render is stored — title, the byline's first 4
authors, date, venue, citations, DOI (or landing page when there is none) and the
badge; no abstracts. The ingest job prunes works older than 30 days, the longest
window the UI offers, so the database stays bounded.

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
python -m app.jobs.ingest --days 7      # pull recent papers per field, prune those past 30 days
python -m app.jobs.sync_stats           # true 30d counts per field/topic + Trends momentum snapshot
python -m app.jobs.sync_rankings        # Trends: most-cited papers + most-active institutions/countries
python -m app.jobs.sync_year_counts     # Trends: yearly output curve per field
python -m app.jobs.digest --window 3    # build the Mon/Wed/Fri briefs (keeps the latest per field)
python -m app.jobs.qa_subfield_links    # report wrong field → Wikipedia/Wikidata intro links
```

In production these run as GitHub Actions cron (`.github/workflows/`), with the
DB URL and OpenAlex key provided as repo secrets (`RF_DATABASE_URL`,
`RF_OPENALEX_API_KEY`, `RF_CONTACT_EMAIL`).

## Config

All via `RF_`-prefixed env vars — see `.env.example`. The bundled demo seed needs
no API keys; live ingestion wants a (free) OpenAlex key and a contact email.
