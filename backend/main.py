from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from backend.db import engine
from backend.models import Base
from backend.routers import accounts, chat, importer, persons, summary, transactions
from backend.settings import FRONTEND_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema only — seeding is explicit (python -m backend.seed) so a
    # freshly-wiped DB isn't repopulated with default categories on restart.
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Money Manager", lifespan=lifespan)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(persons.router)
app.include_router(summary.router)
app.include_router(importer.router)
app.include_router(chat.router)

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
