"""FastAPI app. Run: cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent import service
from app.api.routes import router
from app.config import CORS_ORIGINS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "openai", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        service.warm_up()  # avoids a ~20-30 s first question after every (re)start
    except Exception:
        log.exception("warm-up failed; the first request will do the setup instead")
    yield


app = FastAPI(title="Data Assistant", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(router)
