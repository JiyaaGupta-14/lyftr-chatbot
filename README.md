# Lyftr AI — LLM Chatbot with Key Failover and Response Caching

## 1. Live URLs
- Frontend: https:https://lyftr-chatbot-xi.vercel.app/
- Backend API: https://lyftr-chatbot-x7uv.onrender.com

## 2. Run locally

### With Docker

Frontend: http://localhost:3000  
Backend: http://localhost:8000

### Without Docker

Backend

cd backend
python -m venv .venv
.venv\Scripts\activate # Windows
pip install -r requirements.txt
uvicorn main:app --port 8000

Frontend (new terminal)

cd frontend
npm install
npm run dev # http://localhost:5173

Copy `.env.example` to `.env` and fill in your keys before running.

## 3. Design decisions

**Key fallback:** `LLM_API_KEYS` holds a comma-separated key pool. On each request the backend loops over keys and calls `litellm.acompletion` with the current key. Any exception (rate limit, 5xx, simulated failure) increments `fallback_attempts` and moves to the next key. If every key fails the endpoint returns `503 {"detail":"all providers unavailable"}`. Failed requests are never written to SQLite.

**Caching:** An in-memory dictionary (`APP_CACHE`) is keyed on a SHA-256 hash of `[model, full_message_list]`. The same first message in any new session produces the same hash and returns `LLM_CACHE` with the stored reply and tokens. LiteLLM's built-in `local` cache raised missing-module errors (`orjson`, `soundfile`) in the pinned version 1.110.2, so an app-level cache was used instead. `cache_enabled: true` is reported in `/health`. Simulated-failure requests bypass the cache so failover can always be demonstrated.

**SQLite session storage:** Sessions and messages are stored in SQLite at `DATABASE_URL`. History is loaded per `session_id` on every request and prepended to the message list, giving the model full conversation context. The schema is created automatically on startup. The database file is mounted on a Docker named volume (`/data`) so it survives container restarts.

## 4. Verification

**Cache hit:**

curl -X POST https://lyftr-chatbot-x7uv.onrender.com/chat
-H "Content-Type: application/json"
-d '{"session_id":"v1","message":"What is the capital of France?"}'

returns LIVE_INFERENCE

curl -X POST https://lyftr-chatbot-x7uv.onrender.com/chat
-H "Content-Type: application/json"
-d '{"session_id":"v2","message":"What is the capital of France?"}'

returns LLM_CACHE, key_id null, latency ~1ms

**Failover:**

curl -X POST https://lyftr-chatbot-x7uv.onrender.com/chat
-H "Content-Type: application/json"
-H "X-Simulate-Failure: key_1=429"
-d '{"session_id":"v3","message":"Hello"}'

returns key_2, fallback_attempts 1

curl -X POST https://lyftr-chatbot-x7uv.onrender.com/chat
-H "Content-Type: application/json"
-H "X-Simulate-Failure: *=429"
-d '{"session_id":"v4","message":"Hello"}'

returns 503

**Token usage:** shown per reply in the UI and in the `/sessions/{id}/messages` response.

**Session history:**

curl https://lyftr-chatbot-x7uv.onrender.com/sessions/v1/messages


## 5. Setup used
- Editor: Notepad / VS Code
- AI tools: Claude (claude.ai) for code generation and debugging

## 6. Bonus features
- Optional system prompt field (`data-testid="system-prompt-input"`) persisted per session in SQLite
- Global exception handler ensures no stack traces or HTML error pages are ever returned
- Per-request token tracking shown inline with every assistant reply
