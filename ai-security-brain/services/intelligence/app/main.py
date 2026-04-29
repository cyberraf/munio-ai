"""AI Security Brain — Behavioral Intelligence Engine."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import postgres
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.routers import hotspots, patterns, health, recommendations, productivity, reports, maps, root_causes, floorplan_convert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("intelligence")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect DB, start scheduler. Shutdown: cleanup."""
    logger.info("Starting Intelligence Engine...")
    await postgres.get_pool()
    logger.info("PostgreSQL connected")
    start_scheduler()
    yield
    stop_scheduler()
    await postgres.close_pool()
    logger.info("Intelligence Engine stopped")


app = FastAPI(
    title="AI Security Brain — Intelligence Engine",
    description="Behavioral analysis, hotspot detection, robot health scoring, and report generation.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(hotspots.router)
app.include_router(patterns.router)
app.include_router(health.router)
app.include_router(recommendations.router)
app.include_router(productivity.router)
app.include_router(reports.router)
app.include_router(maps.router)
app.include_router(root_causes.router)
app.include_router(floorplan_convert.router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "intelligence-engine"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, reload=True)
