"""Два лица одного манифеста: HTTP для кода, MCP для ассистентов.

Оба живут в одном процессе и на одном порту. Ни один список ключей не
поддерживается руками — всё берётся из реестра, реестр из папок.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

import docs                                    # noqa: E402
import spec                                    # noqa: E402
from limits import Quota                       # noqa: E402
from manifest import Manifest                  # noqa: E402
from registry import discover                  # noqa: E402
from security import (                         # noqa: E402
    REQUIRE_HTTPS,
    client_ip,
    install_log_redaction,
    over_https,
)
from tokens import Tokens, public_id           # noqa: E402
from runtime import (                          # noqa: E402
    Busy,
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

# Базы держим отдельно от кода: в докере это том, иначе пересборка образа
# стирала бы выданные ключи и весь накопленный кэш.
DATA = Path(os.environ.get("KEYS_DATA_DIR", ROOT))
DATA.mkdir(parents=True, exist_ok=True)

CACHE = Cache(DATA / "cache.db")
BUCKET = TokenBucket(OUTBOUND_RPS, OUTBOUND_BURST)
TOKENS = Tokens(DATA / "tokens.db")
STARTED = time.time()
_client: httpx.AsyncClient | None = None

# С ключом — без счётчика вообще: ключ выдаёт владелец, и считать чужие
# запросы он не собирается. Без ключа остаётся проба, чтобы случайный прохожий
# мог попробовать, но не мог занять собой весь кран.
QUOTA_ANON = Quota(per_minute=20, per_day=100)
QUOTA_NONE = None  # у ключа квоты нет — это не оговорка, это решение

# Ключи раздаёт владелец. Без этой переменной выдача просто закрыта: открытое
# самообслуживание — это раздача бессрочных ключей кому попало.
ADMIN_TOKEN = os.environ.get("KEYS_ADMIN_TOKEN", "")


@app.on_event("startup")
async def _startup() -> None:
    global _client
    _client = httpx.AsyncClient()
    # лог доступа пишет полную строку запроса; ключ из query оттуда вырезаем
    install_log_redaction()
    # публичный ключ должен существовать всегда, иначе «просто скопируй» врёт
    TOKENS.ensure_public()


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _client:
        await _client.aclose()


def bearer(request: Request) -> str:
    """Ключ доступа из заголовка, как у ИИ-сервисов; из query — как поблажка.

    Query оставлен потому, что иногда позвать неоткуда, кроме адресной строки
    (браузер, ячейка таблицы). Расплата за это — он виден в логах, поэтому
    оттуда его вырезает фильтр из security.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.query_params.get("token", "")


def caller(request: Request) -> tuple[str, Quota | None]:
    """Кто пришёл и считать ли его вообще. Для ключа — не считать."""
    token = bearer(request)
    if token and REQUIRE_HTTPS and not over_https(request):
        # молча понизить до анонима нельзя: человек будет думать, что ключ
        # работает, и продолжит слать его открытым текстом
        raise HTTPException(status_code=400, detail="ключ доступа только по HTTPS")

    if token:
        if not TOKENS.valid(token):
            # молчаливое понижение до анонима — худший вариант: человек с
            # опечаткой или с отозванным ключом думает, что всё в порядке, и
            # узнаёт правду только упёршись в лимит для анонимов
            raise HTTPException(status_code=401, detail="ключ доступа неизвестен или отозван")
        TOKENS.touch(token)
        # в опознавателе держим хвост хэша, а не сам ключ: он попадёт в память,
        # в дампы и в отладочные распечатки
        return "tok:" + public_id(token), QUOTA_NONE
    return "ip:" + client_ip(request), QUOTA_ANON


async def call_key(key_id: str, params: dict, who: str, quota: Quota | None) -> tuple[int, dict]:
    """Общий путь для обоих лиц: кэш -> квота -> сеть."""
    key = KEYS.get(key_id)
    if key is None:
        return 404, {"error": f"ключа '{key_id}' нет", "keys": sorted(KEYS)}

    missing = [p.name for p in key.manifest.params if p.required and not params.get(p.name)]
    if missing:
        return 400, {"error": "не хватает параметров: " + ", ".join(missing)}

    # канон до кэша: иначе durov, DUROV и t.me/durov/123 — три записи и три
    # похода наружу за одной и той же страницей
    if key.canonical:
        try:
            params = key.canonical(params)
        except ValueError as exc:
            return 400, {"error": str(exc)}

    cache_key = key_id + ":" + json.dumps(params, sort_keys=True, ensure_ascii=False)
    cached = CACHE.get(cache_key)
    if cached is not None:
        return 200, cached | {"cached": True}

    if quota is not None:  # None = пришли с ключом, запросы не считаем
        ok, why = quota.check(who)
        if not ok:
            return 429, {
                "error": why,
                "потрачено_за_сутки": quota.used(who),
                "подсказка": "с ключом доступа лимита нет — попросите ключ у владельца",
            }

    ctx = Context(CACHE, BUCKET, _client)
    try:
        result = await key.run(params, ctx)
    except Busy as exc:
        return 503, {"error": str(exc)}
    except FetchError as exc:
        return 502, {"error": f"сеть не ответила: {exc}"}
    except ValueError as exc:
        return 400, {"error": str(exc)}

    if quota is not None:
        quota.spend(who)
    удачно = bool(result.get(key.manifest.short.get("field", ""), True))
    CACHE.put(cache_key, result, key.manifest.ttl_for(удачно))
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
    return docs.catalog_page(manifests(), base_url(request), TOKENS.ensure_public())


@app.get("/k/{key_id}/docs", response_class=HTMLResponse)
async def key_docs(key_id: str, request: Request) -> str:
    key = KEYS.get(key_id)
    if key is None:
        return docs.not_found(sorted(KEYS))
    return docs.key_page(key.manifest, base_url(request))


@app.get("/client", response_class=HTMLResponse)
async def client_docs(request: Request) -> str:
    """Документация по библиотеке: аргументы, методы, ошибки."""
    return docs.client_page(base_url(request), TOKENS.ensure_public())


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
                "returns": [r.name for r in m.returns],
                "docs": f"/k/{m.id}/docs",
            }
            for m in (k.manifest for k in KEYS.values())
        ]
    }


def respond(key_id: str, status: int, body: dict, fmt: str, only: str = ""):
    """Один результат — несколько форм ответа.

    only=<поле> отдаёт ровно одно значение и ничего вокруг: чаще всего нужен
    не весь ответ, а число подписчиков. Имя поля сверяется с манифестом, иначе
    опечатка возвращала бы пустоту, которую легко принять за ноль.
    """
    key = KEYS.get(key_id)
    if key is None:
        return JSONResponse(body, status_code=status)

    if only:
        known = {r.name for r in key.manifest.returns}
        if only not in known:
            return respond(
                key_id, 400,
                {"error": f"поля '{only}' у ключа нет", "поля": sorted(known)},
                "json",
            )
        if status >= 400:
            return PlainTextResponse(body.get("error", "ошибка"), status_code=status)
        value = body.get(only)
        if fmt == "json":
            return JSONResponse({only: value}, status_code=status)
        return PlainTextResponse("" if value is None else str(value), status_code=status)

    if fmt == "json":
        return JSONResponse(body, status_code=status)
    if status >= 400:
        return PlainTextResponse(body.get("error", "ошибка"), status_code=status)
    if fmt == "bool":
        field = key.manifest.short.get("field")
        return PlainTextResponse(str(bool(body.get(field))).lower(), status_code=status)
    return PlainTextResponse(key.manifest.short_text(body), status_code=status)


@app.get("/k/{key_id}")
async def run_get(key_id: str, request: Request):
    params = dict(request.query_params)
    fmt = params.pop("fmt", "json")
    only = params.pop("only", "")
    params.pop("token", None)
    status, body = await call_key(key_id, params, *caller(request))
    return respond(key_id, status, body, fmt, only)


@app.post("/k/{key_id}")
async def run_post(key_id: str, request: Request) -> JSONResponse:
    try:
        params = await request.json()
    except Exception:
        params = {}
    status, body = await call_key(key_id, params, *caller(request))
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
        who, quota = caller(request)
        status, body = await call_key(
            params.get("name", ""), params.get("arguments", {}) or {}, who, quota
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


# --------------------------------------------------------------------------- #
# ключ доступа и клиент
# --------------------------------------------------------------------------- #

def require_admin(request: Request) -> None:
    """Выдача и обзор ключей — только владельцу.

    Без KEYS_ADMIN_TOKEN выдача закрыта совсем: пустой пароль хуже
    отсутствующего, потому что выглядит как защита.
    """
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="выдача ключей выключена: на сервере не задан KEYS_ADMIN_TOKEN",
        )
    предъявлен = request.headers.get("x-admin-token", "") or bearer(request)
    if not hmac.compare_digest(предъявлен, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="нужен ключ владельца")


@app.post("/token")
async def issue_token(request: Request) -> JSONResponse:
    """Выдать ключ. Только владелец сервиса.

    Ключ бессрочный и без счётчика: кому выдали — тот пользуется сколько
    нужно. Единственное, что с ним можно сделать потом, — отозвать.
    """
    require_admin(request)

    тело = {}
    try:
        тело = await request.json()
    except Exception:
        pass
    подпись = str(тело.get("кому") or тело.get("label") or "")
    заметка = str(тело.get("заметка") or тело.get("note") or "")

    token, key_id = TOKENS.issue(label=подпись, note=заметка, issued_to=client_ip(request))
    return JSONResponse({
        "token": token,
        "id": key_id,
        "кому": подпись,
        "лимит": "нет — ни по количеству, ни по сроку",
        "как_использовать": "положите получателю в .env как KEYS_API_KEY",
        "внимание": "ключ показывается один раз, на сервере остаётся только хэш",
        "если_придётся_отозвать": f"POST /token/revoke с id {key_id}",
    })


@app.get("/public-token", response_class=PlainTextResponse)
async def public_token() -> str:
    """Ключ, напечатанный открыто: работает у всех и без счётчика.

    Библиотека спрашивает его сама, если своего ключа нет. Поэтому смена
    публичного ключа доходит до всех сразу и не требует нового релиза пакета.
    """
    return TOKENS.ensure_public()


@app.post("/token/public/rotate")
async def rotate_public(request: Request) -> JSONResponse:
    """Сменить публичный ключ: старый гаснет, новый начинает работать.

    Рубильник для публичного ключа — на случай, если открытым ключом начали
    злоупотреблять. У всех, кто ходит библиотекой, он подхватится сам.
    """
    require_admin(request)
    новый = TOKENS.rotate_public()
    return JSONResponse({
        "публичный_ключ": новый,
        "старый": "отозван",
        "впишите_в_README": новый,
    })


@app.get("/tokens")
async def list_tokens(request: Request) -> JSONResponse:
    """Кому что выдано и чем пользуются. Самих ключей тут нет и быть не может."""
    require_admin(request)
    return JSONResponse({"ключи": TOKENS.listing()})


@app.post("/token/revoke")
async def revoke_token(request: Request) -> JSONResponse:
    """Отозвать ключ. Два способа, потому что стороны две.

    Владелец ключа отзывает свой, предъявив сам ключ. Владелец сервиса
    отзывает любой по публичному id, не зная самого ключа.
    """
    тело = {}
    try:
        тело = await request.json()
    except Exception:
        pass
    key_id = str(тело.get("id") or "")

    if key_id:
        require_admin(request)
        отозван = TOKENS.revoke_by_id(key_id)
    else:
        token = bearer(request)
        if not token:
            return JSONResponse(
                {"error": "нужен либо свой ключ в Authorization, либо id ключа в теле"}, 400
            )
        отозван = TOKENS.revoke(token)

    if отозван:
        return JSONResponse({"отозван": True, "действует": "сразу"})
    # не говорим, существовал ключ или нет: иначе это способ проверять чужие
    return JSONResponse({"отозван": False, "причина": "ключ неизвестен или уже отозван"}, 404)


DIST = ROOT / "clients" / "python" / "dist"


def колёса() -> list[Path]:
    return sorted(DIST.glob("*.whl"))


@app.get("/sdk/simple/", response_class=HTMLResponse)
async def simple_index() -> str:
    """Индекс пакетов по PEP 503.

    Нужен, чтобы адрес установки не менялся с версией:
        pip install --index-url АДРЕС/sdk/simple monokeys
    Прямая ссылка на файл тоже работает, но pip требует, чтобы в её имени
    была версия, — а такую ссылку пришлось бы править в документации каждый раз.
    """
    return "<!DOCTYPE html><html><body><a href='monokeys/'>monokeys</a></body></html>"


@app.get("/sdk/simple/monokeys/", response_class=HTMLResponse)
async def simple_monokeys(request: Request) -> str:
    ссылки = "".join(
        f"<a href='{base_url(request)}/sdk/{w.name}'>{w.name}</a><br>" for w in колёса()
    )
    return f"<!DOCTYPE html><html><body>{ссылки}</body></html>"


@app.get("/sdk/python", response_class=PlainTextResponse)
async def sdk_python(request: Request) -> str:
    """Клиент одним файлом, с уже подставленным адресом этого сервера.

        curl АДРЕС/sdk/python > monokeys.py
    """
    source = (ROOT / "clients" / "python" / "monokeys" / "__init__.py").read_text(encoding="utf-8")
    return re.sub(
        r'^BASE = "[^"]*"',
        lambda _: f'BASE = "{base_url(request)}"',
        source,
        count=1,
        flags=re.M,
    )

@app.get("/sdk/{filename}")
async def sdk_file(filename: str):
    """Сам файл пакета. Собран с уже подставленным адресом этого сервера,
    поэтому у скачавшего Keys() работает сразу, без настройки."""
    if not filename.endswith(".whl"):
        raise HTTPException(status_code=404, detail="нет такого файла")
    файл = DIST / Path(filename).name  # имя без путей: наружу оно приходит от чужого
    if not файл.is_file():
        raise HTTPException(status_code=404, detail="колесо не собрано на этом сервере")
    return FileResponse(файл, media_type="application/octet-stream", filename=файл.name)




# --------------------------------------------------------------------------- #
# короткая форма: /<ключ>/<строка>
#
# Регистрируется последней — все точные маршруты выше уже разобраны, поэтому
# /keys, /health и /llms.txt сюда не проваливаются. Смысл в том, чтобы человек
# ничего не собирал: имя ключа, слэш, то что проверяем. Ссылку можно вставить
# куда угодно — в код, в браузер, в ячейку таблицы.
# --------------------------------------------------------------------------- #

@app.get("/{key_id}")
async def key_root(key_id: str, request: Request):
    """Голое имя ключа — показываем, что он умеет."""
    if key_id not in KEYS:
        return JSONResponse({"error": f"ключа '{key_id}' нет", "keys": sorted(KEYS)}, 404)
    return RedirectResponse(f"{base_url(request)}/k/{key_id}/docs")


@app.get("/{key_id}/{value:path}")
async def run_short(key_id: str, value: str, request: Request):
    key = KEYS.get(key_id)
    if key is None:
        return JSONResponse({"error": f"ключа '{key_id}' нет", "keys": sorted(KEYS)}, 404)

    params = dict(request.query_params)
    fmt = params.pop("fmt", "text")  # короткая форма по умолчанию отвечает строкой
    only = params.pop("only", "")
    params.pop("token", None)
    params[key.manifest.primary_param().name] = value

    status, body = await call_key(key_id, params, *caller(request))
    return respond(key_id, status, body, fmt, only)
