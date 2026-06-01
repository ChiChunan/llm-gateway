# LLM Gateway

Intelligent LLM routing gateway with complexity-based classification, automatic failover, and real-time observability. Route requests across multiple LLM providers (Volcengine ARK, Kimi, MiniMax, DeepSeek, Xiaomi MiMo, LongCat) with a single endpoint.

## Features

- **Complexity-based routing**: Lightweight classifier determines request complexity → automatic model selection
- **Session-affinity hashing**: Same conversation always routes to the same model, different sessions load-balanced
- **Multi-provider failover**: Automatic cascade across providers (ARK → Kimi → MiniMax → DeepSeek → LongCat)
- **Plan-level circuit breaker**: When one model hits rate limits, all models in the same plan marked unavailable simultaneously
- **Precise 429 recovery**: Parses `reset at` from rate-limit responses for exact-time recovery
- **Thinking parameter injection**: Auto-injects correct thinking/reasoning params per model
- **Full observability**: SQLite usage tracking + dark-themed dashboard with charts and real-time stats

## Architecture

```
Client → Router :8000 (FastAPI)
           │
           ├─ Classifier (doubao-seed-2-0-lite / fallback)
           │   └─ simple / complex
           │
           ├─ Session-affinity hash → fixed starting model
           │
           ├─ Iterate candidates → first available model
           │
           └─ Direct to provider
               ├─ ARK     → glm-5-1, doubao-seed-2-0-pro/lite
               ├─ Kimi    → kimi-for-coding
               ├─ MiniMax → MiniMax-M2.7-highspeed
               ├─ DeepSeek → deepseek-v4-pro/flash
               ├─ MiMo    → mimo-v2.5-pro
               └─ LongCat → LongCat-2.0-Preview
```

## Quick Start

### 1. Configure providers

```bash
cp .env.example .env
# Fill in API keys for providers you want to use
```

### 2. Deploy with Docker (recommended)

```bash
docker compose up -d --build
```

### 3. Or run locally

```bash
cd router
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Usage

Point any OpenAI-compatible client at the gateway:

```bash
# Claude Code / any OpenAI SDK
export ANTHROPIC_BASE_URL=http://your-server:8000

# OpenAI-compatible base_url
base_url: http://your-server:8000/v1
api_key:  any-string
model:    auto
```

### Smart routing (recommended)

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

### Direct model passthrough (bypasses classifier)

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"Design a microservice architecture"}]}'
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check with provider status |
| `/v1/models` | GET | Available models list |
| `/v1/chat/completions` | POST | Chat completion (OpenAI compatible) |
| `/dashboard` | GET | Usage dashboard with charts |
| `/quota/codex` | GET | Codex usage quota details |
| `/quota/codex/simple` | GET | Lightweight quota for mobile devices |

## Routing Configuration

Edit `router/routing.yaml` to adjust routing strategy (hot-reload via Docker volume mount):

```yaml
routing:
  classifier: "doubao-seed-2-0-lite"
  simple_candidates:
    - "MiniMax-M2.7-highspeed"
    - "doubao-seed-2-0-pro"
  complex_candidates:
    - "glm-5-1"
    - "kimi-for-coding"
    - "MiniMax-M2.7-highspeed"
  simple_fallback_candidates:
    - "kimi-for-coding"

plans:
  ark:
    channels: ["doubao-seed-2-0-pro", "glm-5-1"]
  kimi:
    channels: ["kimi-for-coding"]
  minimax:
    channels: ["MiniMax-M2.7-highspeed"]
```

## Dashboard

Dark-themed web dashboard at `http://your-server:8000/dashboard` with:
- Total requests, input/output tokens, cache hits
- Daily request charts (by model)
- Token consumption distribution
- Model usage summary table
- Real-time request logs

## Project Structure

```
├── docker-compose.yml
├── .env.example
└── router/
    ├── main.py              # FastAPI app, routing logic
    ├── classifier.py        # Complexity classifier
    ├── channel.py           # Provider health management (429 circuit breakers)
    ├── proxy.py             # Request forwarding, SSE streaming
    ├── providers.py         # Provider configs, model→channel mapping
    ├── provider_state.py    # Runtime provider state tracking
    ├── usage.py             # SQLite usage recording
    ├── api_stats.py         # Stats query API
    ├── dashboard.py         # Web dashboard
    ├── routing.yaml         # Routing strategy config
    ├── requirements.txt
    ├── requirements-dev.txt
    ├── Dockerfile
    └── tests/
```

## Testing

```bash
cd router
pip install -r requirements-dev.txt
pytest tests/ -v
```

## License

MIT
