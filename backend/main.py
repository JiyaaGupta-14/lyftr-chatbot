import hashlib
import json
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Optional

import litellm
from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

load_dotenv(find_dotenv())
litellm.suppress_debug_info = True
log = logging.getLogger("chat")
logging.basicConfig(level=logging.INFO)

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."

# In-memory response cache keyed on model + full message list
APP_CACHE: dict = {}


def cache_key(model: str, messages: list) -> str:
    return hashlib.sha256(json.dumps([model, messages], sort_keys=True).encode()).hexdigest()


# ---------- config helpers ----------
def get_model() -> str:
    return os.getenv("LLM_MODEL", "gemini/gemini-flash-lite-latest")


def get_keys() -> list[str]:
    return [k.strip() for k in os.getenv("LLM_API_KEYS", "").split(",") if k.strip()]


def db_path() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
    return url.replace("sqlite:///", "", 1)


def get_conn() -> sqlite3.Connection:
    path = db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                system_prompt TEXT
            )"""
        )
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)

origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


# ---------- models ----------
class ChatRequest(BaseModel):
    session_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    system_prompt: Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]] = None


class SimulatedProviderError(Exception):
    def __init__(self, status: int):
        super().__init__(f"simulated provider status {status}")
        self.status = status


def parse_simulation(header: Optional[str]) -> dict:
    if not header or os.getenv("ENABLE_FAILURE_SIMULATION", "false").lower() != "true":
        return {}
    out = {}
    for part in header.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if v in ("429", "500", "502", "503"):
            out[k] = int(v)
    return out


# ---------- endpoints ----------
@app.get("/health")
async def health():
    conn = get_conn()
    try:
        conn.execute("SELECT 1")
    finally:
        conn.close()
    return {"status": "ok", "keys_configured": len(get_keys()), "cache_enabled": True}


@app.post("/chat")
async def chat(req: ChatRequest, x_simulate_failure: Optional[str] = Header(default=None)):
    start = time.perf_counter()
    sim = parse_simulation(x_simulate_failure)
    model = get_model()

    conn = get_conn()
    try:
        row = conn.execute("SELECT system_prompt FROM sessions WHERE session_id=?", (req.session_id,)).fetchone()
        history = conn.execute(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY id", (req.session_id,)
        ).fetchall()
    finally:
        conn.close()

    system_prompt = req.system_prompt or (row["system_prompt"] if row else None) or DEFAULT_SYSTEM_PROMPT
    messages = [{"role": "system", "content": system_prompt}]
    messages += [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append({"role": "user", "content": req.message})

    ckey = cache_key(model, messages)
    result = None
    attempts = 0

    if not sim and ckey in APP_CACHE:
        c = APP_CACHE[ckey]
        result = {
            "reply": c["reply"],
            "cache_hit": True,
            "input_tokens": c["input_tokens"],
            "output_tokens": c["output_tokens"],
            "key_id": None,
            "fallback_attempts": 0,
        }
    else:
        for idx, key in enumerate(get_keys(), start=1):
            key_id = f"key_{idx}"
            try:
                status = sim.get(key_id) or sim.get("*")
                if status:
                    raise SimulatedProviderError(status)
                resp = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    api_key=key,
                    caching=False,
                    num_retries=0,
                    timeout=30,
                )
                reply = (resp.choices[0].message.content or "").strip()
                if not reply:
                    raise ValueError("empty reply")
                result = {
                    "reply": reply,
                    "cache_hit": False,
                    "input_tokens": int(resp.usage.prompt_tokens or 0),
                    "output_tokens": int(resp.usage.completion_tokens or 0),
                    "key_id": key_id,
                    "fallback_attempts": attempts,
                }
                if not sim:
                    APP_CACHE[ckey] = {
                        "reply": reply,
                        "input_tokens": result["input_tokens"],
                        "output_tokens": result["output_tokens"],
                    }
                break
            except Exception as e:
                attempts += 1
                log.warning("%s failed: %r", key_id, e)

    if result is None:
        return JSONResponse(status_code=503, content={"detail": "all providers unavailable"})

    latency_ms = int((time.perf_counter() - start) * 1000)
    metadata = {
        "resolution_source": "LLM_CACHE" if result["cache_hit"] else "LIVE_INFERENCE",
        "model": model,
        "latency_ms": latency_ms,
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "key_id": result["key_id"],
        "fallback_attempts": result["fallback_attempts"],
    }

    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO sessions(session_id, system_prompt) VALUES(?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET system_prompt=excluded.system_prompt",
            (req.session_id, system_prompt),
        )
        conn.execute(
            "INSERT INTO messages(session_id, role, content, metadata, created_at) VALUES(?,?,?,?,?)",
            (req.session_id, "user", req.message, None, now),
        )
        conn.execute(
            "INSERT INTO messages(session_id, role, content, metadata, created_at) VALUES(?,?,?,?,?)",
            (req.session_id, "assistant", result["reply"], json.dumps(metadata), now),
        )
        conn.commit()
    finally:
        conn.close()

    return {"session_id": req.session_id, "reply": result["reply"], "metadata": metadata}


@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT role, content, metadata FROM messages WHERE session_id=? ORDER BY id", (session_id,)
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise HTTPException(status_code=404, detail="session not found")
    out = []
    for r in rows:
        item = {"role": r["role"], "content": r["content"]}
        if r["metadata"]:
            item["metadata"] = json.loads(r["metadata"])
        out.append(item)
    return {"session_id": session_id, "messages": out}