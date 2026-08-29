"""Два лица одного манифеста: HTTP для кода, MCP для ассистентов.

Оба живут в одном процессе и на одном порту. Ни один список ключей не
поддерживается руками — всё берётся из реестра, реестр из папок.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

import docs                                    # noqa: E402
import spec                                    # noqa: E402
from limits import Quota                       # noqa: E402
from manifest import Manifest                  # noqa: E402
from registry import discover                  # noqa: E402
from runtime import (                          # noqa: E402
    OUTBOUND_BURST,
    OUTBOUND_RPS,
    Cache,
    Context,
    FetchError,
    TokenBucket,
)

MCP_PROTOCOL = "2025-06-18"

# openapi_url=None — путь /openapi.json занимаем сами: спеку собираем из
# манифестов, а не из питоновских сигнатур (маршруты у нас динамические).
app = FastAPI(title="Ключи", docs_url=None, redoc_url=None, openapi_url=None)

KEYS = discover(ROOT / "keys")
CACHE = Cache(ROOT / "cache.db")
BUCKET = TokenBucket(OUTBOUND_RPS, OUTBOUND_BURST)
QUOTA = Quota()
STARTED = time.time()
_client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def _startup() -> None:
    global _client
    _client = httpx.AsyncClient()


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _client:
        await _client.aclose()


def client_id(request: Request) -> str:
    """Токена пока нет — считаем по IP. Заголовок ставит nginx, не пользователь."""
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


async def call_key(key_id: str, params: dict, who: str) -> tuple[int, dict]:
    """Общий путь для обоих лиц: кэш -> квота -> сеть."""
    key = KEYS.get(key_id)
    if key is None:
        return 404, {"error": f"ключа '{key_id}' нет", "keys": sorted(KEYS)}

    missing = [p.name for p in key.manifest.params if p.required and not params.get(p.name)]
    if missing:
        return 400, {"error": "не хватает параметров: " + ", ".join(missing)}

    cache_key = key_id + ":" + json.dumps(params, sort_keys=True, ensure_ascii=False)
    cached = CACHE.get(cache_key)
    if cached is not None:
        return 200, cached | {"cached": True}

    ok, why = QUOTA.check(who)
    if not ok:
        return 429, {"error": why, "потрачено_за_сутки": QUOTA.used(who)}

    ctx = Context(CACHE, BUCKET, _client)
    try:
        result = await key.run(params, ctx)
    except FetchError as exc:
        return 502, {"error": f"сеть не ответила: {exc}"}
    except ValueError as exc:
        return 400, {"error": str(exc)}

    QUOTA.spend(who)
    CACHE.put(cache_key, result, key.manifest.ttl_for(bool(result.get("is_alive", True))))
    return 200, result | {"cached": False}


# --------------------------------------------------------------------------- #
# лицо 1: HTTP
# --------------------------------------------------------------------------- #

def base_url(request: Request) -> str:
    """Адрес, на который человек реально пришёл: локально одно, в проде другое.

    Так примеры кода на странице всегда рабочие — их можно копировать как есть,
    ничего не подставляя руками.
    """
    return str(request.base_url).rstrip("/")


def manifests() -> list[Manifest]:
    return [k.manifest for k in KEYS.values()]


@app.get("/", response_class=HTMLResponse)
async def catalog(request: Request) -> str:
    return docs.catalog_page(manifests(), base_url(request))


@app.get("/k/{key_id}/docs", response_class=HTMLResponse)
async def key_docs(key_id: str, request: Request) -> str:
    key = KEYS.get(key_id)
    if key is None:
        return docs.not_found(sorted(KEYS))
    return docs.key_page(key.manifest, base_url(request))


@app.get("/openapi.json")
async def openapi_spec(request: Request) -> dict:
    return spec.openapi(manifests(), base_url(request))


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt(request: Request) -> str:
    return spec.llms_txt(manifests(), base_url(request))


@app.get("/keys")
async def list_keys() -> dict:
    return {
        "keys": [
            {
                "id": m.id,
                "title": m.title,
                "summary": m.summary,
                "tags": m.tags,
                "params": [p.name for p in m.params],
                "docs": f"/k/{m.id}/docs",
            }
            for m in (k.manifest for k in KEYS.values())
        ]
    }


@app.get("/k/{key_id}")
async def run_get(key_id: str, request: Request) -> JSONResponse:
    status, body = await call_key(key_id, dict(request.query_params), client_id(request))
    return JSONResponse(body, status_code=status)


@app.post("/k/{key_id}")
async def run_post(key_id: str, request: Request) -> JSONResponse:
    try:
        params = await request.json()
    except Exception:
        params = {}
    status, body = await call_key(key_id, params, client_id(request))
    return JSONResponse(body, status_code=status)


@app.get("/health")
async def health() -> dict:
    total = CACHE.hits + CACHE.misses
    return {
        "keys": sorted(KEYS),
        "uptime_sec": round(time.time() - STARTED),
        "cache": {
            "hits": CACHE.hits,
            "misses": CACHE.misses,
            "hit_rate": round(CACHE.hits / total, 3) if total else None,
        },
        "outbound_budget_rps": OUTBOUND_RPS,
    }


# --------------------------------------------------------------------------- #
# лицо 2: MCP (JSON-RPC 2.0). Ассистент видит все ключи как обычные инструменты.
# --------------------------------------------------------------------------- #

def _tool(m: Manifest) -> dict:
    return {"name": m.id, "description": m.tool_description(), "inputSchema": m.input_schema()}


@app.post("/mcp")
async def mcp(request: Request):
    try:
        msg = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "битый JSON"}}
        )

    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        result = {
            "protocolVersion": MCP_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "Ключи", "version": "0.1.0"},
        }
    elif method in ("notifications/initialized", "notifications/cancelled"):
        return JSONResponse(None, status_code=202)
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [_tool(k.manifest) for k in KEYS.values()]}
    elif method == "tools/call":
        params = msg.get("params", {})
        status, body = await call_key(
            params.get("name", ""), params.get("arguments", {}) or {}, client_id(request)
        )
        result = {
            "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False, indent=2)}],
            "isError": status >= 400,
        }
    else:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": msg_id,
             "error": {"code": -32601, "message": f"нет метода {method}"}}
        )

    return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "result": result})
