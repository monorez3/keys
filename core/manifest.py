"""Манифест ключа: единственный источник правды.

Из одного key.json рождаются три вещи и ни одну из них не пишут руками:
HTTP-эндпоинт, инструмент MCP и страница документации. Поэтому манифест
проверяется строго — кривой ключ не должен доехать до каталога.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Типы, которые ключ может принимать. Держим список коротким намеренно:
# каждый новый тип надо уметь показать в документации и в схеме MCP.
PARAM_TYPES = {"string", "integer", "number", "boolean"}

JSON_SCHEMA_TYPES = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
}


class ManifestError(ValueError):
    """Манифест не годится. Текст ошибки читает автор ключа, не пользователь."""


@dataclass(slots=True)
class Param:
    name: str
    type: str
    required: bool = False
    description: str = ""
    example: object = None


@dataclass(slots=True)
class Returns:
    name: str
    type: str
    description: str = ""


@dataclass(slots=True)
class Manifest:
    id: str
    title: str
    summary: str
    description: str
    params: list[Param]
    returns: list[Returns]
    tags: list[str] = field(default_factory=list)
    source: dict = field(default_factory=dict)
    cache_ttl: dict = field(default_factory=dict)
    cost: str = "cpu"
    examples: list[dict] = field(default_factory=list)
    primary: str = ""
    short: dict = field(default_factory=dict)
    path: Path | None = None

    # --- короткая форма: имя ключа + одна строка ------------------------- #

    def primary_param(self) -> Param:
        """Параметр, который подставляется в короткую форму /alive/<строка>.

        Ради него всё и затевалось: человек не должен собирать query-строку,
        он вставляет ссылку сразу в адрес.
        """
        for p in self.params:
            if p.name == self.primary:
                return p
        return self.params[0]

    def short_text(self, result: dict) -> str:
        """Человеческий однострочный ответ вместо JSON.

        Нужен там, где разбирать JSON дороже, чем сделать сам запрос:
        C++, PHP, шелл, ячейка таблицы.
        """
        if not self.short:
            return json.dumps(result, ensure_ascii=False)
        template = self.short["yes"] if result.get(self.short["field"]) else self.short["no"]
        return _fill(template, result)

    # --- то, ради чего всё затевалось: два лица из одних данных ---------- #

    def input_schema(self) -> dict:
        """JSON Schema для MCP: ассистент по ней сам поймёт, что подставлять."""
        props = {}
        for p in self.params:
            prop = {"type": JSON_SCHEMA_TYPES[p.type], "description": p.description}
            if p.example is not None:
                prop["examples"] = [p.example]
            props[p.name] = prop
        return {
            "type": "object",
            "properties": props,
            "required": [p.name for p in self.params if p.required],
        }

    def tool_description(self) -> str:
        """Описание для ассистента: коротко, но с примером — так он реже ошибается."""
        lines = [self.summary, "", self.description]
        if self.examples:
            ex = self.examples[0]
            lines += ["", f"Пример: {json.dumps(ex['params'], ensure_ascii=False)} — {ex.get('note', '')}"]
        return "\n".join(lines).strip()

    def ttl_for(self, alive: bool) -> int:
        """Мёртвое кэшируем короче: канал может воскреснуть, живой — вряд ли исчезнет."""
        return int(self.cache_ttl.get("alive" if alive else "dead", 3600))


def _fill(template: str, values: dict) -> str:
    """'{title} · {members_count}' -> 'Pavel Durov · 11 005 185'.

    Пустые поля выбрасываем вместе с разделителем: строка «жив · · ·» хуже,
    чем просто «жив». Числа разбиваем пробелами — это ответ для человека.
    """
    parts = []
    for chunk in template.split("·"):
        text = chunk.strip()
        names = re.findall(r"\{(\w+)\}", text)
        if any(values.get(n) in (None, "") for n in names):
            continue  # кусок опирается на пустое поле — целиком выкидываем
        for name in names:
            value = values[name]
            if isinstance(value, int) and not isinstance(value, bool):
                value = f"{value:,}".replace(",", " ")
            text = text.replace("{" + name + "}", str(value))
        if text:
            parts.append(text)
    return " · ".join(parts)


def _need(raw: dict, field_name: str, kind: type, where: str):
    if field_name not in raw:
        raise ManifestError(f"{where}: нет обязательного поля '{field_name}'")
    value = raw[field_name]
    if not isinstance(value, kind):
        raise ManifestError(f"{where}: поле '{field_name}' должно быть {kind.__name__}")
    return value


def load(path: Path) -> Manifest:
    where = str(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{where}: битый JSON — {exc}") from exc

    key_id = _need(raw, "id", str, where)
    if not key_id.replace("-", "").replace("_", "").isalnum():
        raise ManifestError(f"{where}: id '{key_id}' — только буквы, цифры, дефис, подчёркивание")
    if path.parent.name != key_id:
        raise ManifestError(f"{where}: id '{key_id}' не совпадает с именем папки '{path.parent.name}'")

    params = []
    for i, p in enumerate(_need(raw, "params", list, where)):
        name = _need(p, "name", str, f"{where} params[{i}]")
        ptype = _need(p, "type", str, f"{where} params[{i}]")
        if ptype not in PARAM_TYPES:
            raise ManifestError(f"{where}: тип '{ptype}' не поддержан, доступны {sorted(PARAM_TYPES)}")
        params.append(Param(
            name=name, type=ptype,
            required=bool(p.get("required", False)),
            description=p.get("description", ""),
            example=p.get("example"),
        ))

    returns = [
        Returns(name=r.get("name", ""), type=r.get("type", ""), description=r.get("description", ""))
        for r in raw.get("returns", [])
    ]

    return Manifest(
        id=key_id,
        title=_need(raw, "title", str, where),
        summary=_need(raw, "summary", str, where),
        description=raw.get("description", ""),
        params=params,
        returns=returns,
        tags=raw.get("tags", []),
        source=raw.get("source", {}),
        cache_ttl=raw.get("cache_ttl", {}),
        cost=raw.get("cost", "cpu"),
        examples=raw.get("examples", []),
        primary=raw.get("primary", ""),
        short=raw.get("short", {}),
        path=path.parent,
    )
