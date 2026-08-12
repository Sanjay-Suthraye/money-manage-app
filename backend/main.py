from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import deps, settings
from backend.db import engine, run_migrations
from backend.models import Base
from backend.routers import (
    accounts, auth, budgets, chat, export, importer, persons, recurring, summary, transactions,
)
from backend.settings import FRONTEND_DIR
from backend.users import ensure_default_user_and_backfill


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema only — seeding is explicit (python -m backend.seed) so a
    # freshly-wiped DB isn't repopulated with default categories on restart.
    Base.metadata.create_all(engine)
    run_migrations()
    ensure_default_user_and_backfill()
    if not settings.SESSION_SECRET_PERSISTENT:
        print("[auth] MONEY_SESSION_SECRET not set — sessions reset on every restart")
    yield


app = FastAPI(title="Money Manager", lifespan=lifespan)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Fail-closed: every /api route (present and future) requires a valid session
# token; /api/auth/* stays open (login must work logged-out). Identity (which
# user) is resolved per-route by deps.current_user. Static files stay public —
# the login page itself needs them, they hold no data.
@app.middleware("http")
async def require_auth(request: Request, call_next):
    path = request.url.path
    if (
        path.startswith("/api")
        and not path.startswith("/api/auth/")
        and not deps.token_valid(request)
    ):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return await call_next(request)


app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(persons.router)
app.include_router(summary.router)
app.include_router(budgets.router)
app.include_router(recurring.router)
app.include_router(export.router)
app.include_router(importer.router)
app.include_router(chat.router)

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
