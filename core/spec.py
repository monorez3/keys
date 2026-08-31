"""Два машинных описания всего набора сразу.

/openapi.json — стандартная спека. По ней генератор клиентов сделает готовую
библиотеку под любой язык (openapi-generator знает их полсотни), а редактор
покажет подсказки. То есть «универсальный клиент» писать не нужно: его
собирают из спеки.

/llms.txt — вся документация одним простым текстом. Это для случая, когда
ключи читает не человек, а ассистент: страницу с вкладками он разбирает
плохо, а такой файл проглатывает целиком и сразу знает всё про все ключи.
"""

from __future__ import annotations

from pathlib import Path

from manifest import Manifest
from snippets import example_params


def _версия() -> str:
    """Версия из pyproject клиента — единственное место, где она объявлена."""
    файл = Path(__file__).resolve().parent.parent / "clients" / "python" / "pyproject.toml"
    try:
        for строка in файл.read_text(encoding="utf-8").splitlines():
            if строка.startswith("version"):
                return строка.split('"')[1]
    except Exception:
        pass
    return "0"


ВЕРСИЯ = _версия()

JSON_TYPES = {"string": "string", "integer": "integer", "number": "number", "boolean": "boolean"}


def openapi(manifests: list[Manifest], base_url: str) -> dict:
    paths = {}
    for m in manifests:
        query = [
            {
                "name": p.name,
                "in": "query",
                "required": p.required,
                "description": p.description,
                "schema": {"type": JSON_TYPES[p.type]},
                "example": p.example,
            }
            for p in m.params
        ]
        props = {
            r.name: {"type": JSON_TYPES.get(r.type, "string"), "description": r.description}
            for r in m.returns
        }
        props["cached"] = {"type": "boolean", "description": "ответ пришёл из кэша"}

        paths[f"/k/{m.id}"] = {
            "get": {
                "operationId": m.id,
                "summary": m.title,
                "description": m.description,
                "tags": m.tags or ["ключи"],
                "parameters": query,
                "responses": {
                    "200": {
                        "description": "результат",
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": props}
                            }
                        },
                    },
                    "400": {"description": "не хватает параметров или мусор на входе"},
                    "429": {"description": "превышена квота"},
                    "502": {"description": "внешний сервис не ответил"},
                },
            }
        }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Ключи",
            # берём из библиотеки: иначе спека годами обещает 0.1.0
            "version": ВЕРСИЯ,
            "description": (
                "Маленькие умные функции. Обычный HTTP — работает из любого языка "
                "без единой зависимости. Лимит: 60 запросов в минуту и 2000 в сутки "
                "на адрес, ответы из кэша не считаются."
            ),
        },
        "servers": [{"url": base_url}],
        "paths": paths,
    }


def llms_txt(manifests: list[Manifest], base_url: str) -> str:
    """Вся документация одним текстом — читается и человеком, и ассистентом."""
    out = [
        "# Ключи",
        "",
        "Маленькие умные функции, доступные обычным HTTP-запросом.",
        "Никаких библиотек и ключей доступа. Работает из любого языка.",
        "",
        f"Каталог: {base_url}/",
        f"Список в JSON: {base_url}/keys",
        f"Машинная спека: {base_url}/openapi.json",
        f"Для ассистентов (MCP): {base_url}/mcp",
        "",
        "Лимит: 60 запросов в минуту, 2000 в сутки на адрес.",
        "Ответы из кэша лимит не тратят (в ответе есть поле cached).",
        "",
        "---",
        "",
    ]
    for m in manifests:
        params = example_params(m)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        out += [f"## {m.id} — {m.title}", "", m.summary, "", m.description, "", "Параметры:"]
        for p in m.params:
            mark = "обязательный" if p.required else "необязательный"
            out.append(f"  {p.name} ({p.type}, {mark}) — {p.description}")
        out += ["", "Возвращает:"]
        for r in m.returns:
            out.append(f"  {r.name} ({r.type}) — {r.description}")
        out += ["", "Пример:", f"  GET {base_url}/k/{m.id}?{query}", ""]
        for ex in m.examples:
            note = ex.get("note", "")
            args = ", ".join(f"{k}={v}" for k, v in ex["params"].items())
            out.append(f"  {args} -> {note}")
        out += ["", "---", ""]
    return "\n".join(out)
