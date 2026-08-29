"""Клиент «Ключей» — один файл, ноль зависимостей.

Работает так же, как клиенты ИИ-сервисов: ключ доступа лежит в окружении,
клиент подставляет его сам, а каждый ключ — обычный метод.

    # .env
    KEYS_API_KEY=kx_...

    from keys import Keys

    k = Keys()                      # ключ берётся из KEYS_API_KEY
    res = k.alive("@durov")

    print(res.is_alive)             # True
    print(res.title)                # Pavel Durov
    print(k.alive.text("@durov"))   # жив · channel · Pavel Durov · ...

Методы не перечислены в коде: их список приходит с сервера. Появился новый
ключ — он сразу доступен, обновлять клиент не нужно.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8110"  # BASE-MARKER: подменяется при отдаче с сервера

__all__ = ["Keys", "Answer", "KeysError"]


class KeysError(RuntimeError):
    """Сервер отказал: нет такого ключа, кончилась квота, мусор на входе."""


class Answer(dict):
    """Ответ ключа. Поля доступны и как res['title'], и как res.title."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(
                f"в ответе нет поля '{name}'; есть: {', '.join(self)}"
            ) from exc

    def __bool__(self) -> bool:
        """if res: ... — правда, когда ключ ответил утвердительно."""
        for field in ("is_alive", "ok", "result"):
            if field in self:
                return bool(self[field])
        return bool(dict(self))


class _Key:
    """Один ключ как вызываемый объект.

        k.alive("@durov")                 -> весь ответ
        k.alive.members_count("@durov")   -> только число, уже числом
        k.alive.text("@durov")            -> одной строкой для человека

    Имена полей не зашиты: они приходят с сервера вместе со списком ключей,
    поэтому опечатка ловится сразу и с подсказкой, а не отдаёт молча None.
    """

    def __init__(self, keys: "Keys", name: str) -> None:
        self._keys = keys
        self._name = name

    def __call__(self, value: str | None = None, **params) -> Answer:
        return Answer(self._keys.call(self._name, value, fmt="json", **params))

    def text(self, value: str | None = None, **params) -> str:
        """Готовая человеческая строка вместо полей."""
        return self._keys.call(self._name, value, fmt="text", **params)

    def fields(self) -> list[str]:
        """Что этот ключ вообще умеет вернуть."""
        return self._keys._fields(self._name)

    def __getattr__(self, field: str):
        if field.startswith("_"):
            raise AttributeError(field)
        known = self.fields()
        if known and field not in known:
            raise AttributeError(
                f"у ключа '{self._name}' нет поля '{field}'; есть: {', '.join(known)}"
            )

        def получить(value: str | None = None, **params):
            answer = self._keys.call(self._name, value, fmt="json", only=field, **params)
            return answer.get(field)

        получить.__name__ = field
        получить.__doc__ = f"Только поле '{field}' ключа '{self._name}'."
        return получить

    def __dir__(self):
        return list(super().__dir__()) + self.fields()

    def __repr__(self) -> str:
        return f"<ключ {self._name}: {', '.join(self.fields())}>"


class Keys:
    def __init__(self, token: str | None = None, base: str = BASE, timeout: float = 20.0) -> None:
        self.token = token or os.environ.get("KEYS_API_KEY", "")
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._catalog: dict | None = None

    # --- то, ради чего клиент существует ------------------------------- #

    def call(self, name: str, value: str | None = None, *, fmt: str = "json",
             only: str = "", **params):
        """Позвать ключ. value — главное значение, остальное по имени."""
        query = dict(params)
        query["fmt"] = fmt
        if only:
            query["only"] = only
        url = f"{self.base}/{name}/"
        if value is not None:
            url += urllib.parse.quote(str(value), safe="@+")
        url += "?" + urllib.parse.urlencode(query)

        request = urllib.request.Request(url)
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise KeysError(f"{exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise KeysError(f"не дозвонился до {self.base}: {exc.reason}") from exc

        return json.loads(body) if fmt == "json" else body

    def catalog(self) -> dict:
        """Список ключей с описаниями — тем же клиентом, без браузера."""
        if self._catalog is None:
            with urllib.request.urlopen(f"{self.base}/keys", timeout=self.timeout) as response:
                self._catalog = json.load(response)
        return self._catalog

    def names(self) -> list[str]:
        return [k["id"] for k in self.catalog()["keys"]]

    def _fields(self, name: str) -> list[str]:
        """Поля ответа конкретного ключа — из того же каталога."""
        for key in self.catalog()["keys"]:
            if key["id"] == name:
                return key.get("returns", [])
        return []

    def __getattr__(self, name: str) -> _Key:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            available = self.names()
        except KeysError:
            available = []  # сервер молчит — пусть падает уже на самом вызове
        if available and name not in available:
            raise AttributeError(
                f"ключа '{name}' нет; есть: {', '.join(available)}"
            )
        return _Key(self, name)

    def __dir__(self):
        try:
            return list(super().__dir__()) + self.names()
        except KeysError:
            return list(super().__dir__())
