from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import get_settings
from .routers import fields, limits, papers, taxonomy, topics

settings = get_settings()

app = FastAPI(
    title="ResearchFrontier API",
    version=__version__,
    description=(
        "Aggregates recent research papers, classifies them against the OpenAlex "
        "taxonomy, derives a peer-review badge, and serves field feeds, emerging "
        "directions and digests. The frontend generates its typed client from this "
        "service's /openapi.json."
    ),
)

# Frontend is served cross-origin from GitHub Pages. We use bearer tokens (phase 2)
# rather than cookies, so credentials are off and we list exact origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(taxonomy.router)
app.include_router(fields.router)
app.include_router(papers.router)
app.include_router(topics.router)
app.include_router(limits.router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {"name": "ResearchFrontier API", "version": __version__, "docs": "/docs"}
