import os
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from agent.engine import Agent
from agent.storage import ClickHouse
from agent.telemetry import Telemetry

security = HTTPBasic()
gate = threading.Lock()


def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
    password = os.getenv("DEMO_PASSWORD", "")
    if len(password) < 16:
        raise HTTPException(503, "Set DEMO_PASSWORD to at least 16 characters")
    if not (secrets.compare_digest(credentials.username.encode(), b"demo") and
            secrets.compare_digest(credentials.password.encode(), password.encode())):
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Basic"})


@asynccontextmanager
async def lifespan(app):
    app.state.telemetry = Telemetry(enabled=os.getenv("TELEMETRY_DISABLED") != "true")
    app.state.agent = Agent(app.state.telemetry)
    yield
    app.state.telemetry.shutdown()


app = FastAPI(title="Agent Black Box", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class RunRequest(BaseModel):
    scenario: Literal["retry_spiral", "stale_source", "silent_failure"]
    profile: Literal["baseline", "reference"] = "baseline"


@app.get("/healthz")
def health():
    return {"status": "ok", "version": os.getenv("AGENT_VERSION", "worktree")}


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(authenticate)])
def index():
    return (Path(__file__).parent / "index.html").read_text()


@app.post("/run", dependencies=[Depends(authenticate)])
def run(body: RunRequest):
    if not gate.acquire(blocking=False):
        raise HTTPException(429, "A run is already active; retry after it completes")
    try:
        result = app.state.agent.run(body.scenario, body.profile)
        result["sdk_flush_completed"] = app.state.telemetry.flush()
        result["ingestion_note"] = "SDK flush is not proof of ClickHouse persistence. Check /evidence/{trace_id}."
        return result
    finally:
        gate.release()


@app.get("/evidence/{trace_id}", dependencies=[Depends(authenticate)])
def evidence(trace_id: str):
    try:
        return ClickHouse().evidence(trace_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except (RuntimeError, KeyError):
        raise HTTPException(503, "Cannot query ClickHouse; check connection settings and schema") from None
