"""Клиент «Ключей» — один файл, ноль зависимостей.

Работает так же, как клиенты ИИ-сервисов: ключ доступа лежит в окружении,
клиент подставляет его сам, а каждый ключ — обычный метод.

    # .env
    KEYS_API_KEY=kx_...

    from monokeys import Keys

    k = Keys()                      # ключ берётся из KEYS_API_KEY
    res = k.alive("@durov")

    print(res.is_alive)             # True
    print(res.title)                # Pavel Durov
    print(k.alive.members_count("@durov"))   # 11005185, уже числом
    print(k.alive.text("@durov"))            # жив · channel · Pavel Durov · ...

Ключа доступа заводить не нужно: если своего нет, клиент возьмёт публичный —
он напечатан открыто, работает у всех и без счётчика. Свой ключ нужен только
тем, кто хочет собственный рубильник.

Методы не перечислены в коде: их список приходит с сервера. Появился новый
ключ — он сразу доступен, обновлять клиент не нужно.

Все настройки — аргументы, ничего не прячется в глобальных переменных:

    Keys(token=…, base=…, timeout=…, retries=…, user_agent=…)
    k.alive(значение, only=…, fmt=…, timeout=…, **параметры)

Полное описание аргументов — в docstring каждого метода и в README пакета.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://monoblock.casa/keys"  # BASE-MARKER: подменяется при отдаче с сервера
VERSION = "0.2.0"

__all__ = ["Keys", "Answer", "KeysError", "AccessDenied", "Unavailable"]


class KeysError(RuntimeError):
    """Сервер отказал: нет такого ключа, мусор на входе, недоступен источник."""

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class AccessDenied(KeysError):
    """Ключ доступа не подошёл: неизвестен, отозван или отправлен не по HTTPS."""


class Unavailable(KeysError):
    """Сервер сейчас занят или источник не ответил. Осмысленно повторить позже."""


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
        k.alive.fields()                  -> что этот ключ умеет вернуть

    Имена полей не зашиты: они приходят с сервера вместе со списком ключей,
    поэтому опечатка ловится сразу и с подсказкой, а не отдаёт молча None.
    """

    def __init__(self, keys: "Keys", name: str) -> None:
        self._keys = keys
        self._name = name

    def __call__(self, value: str | None = None, *, only: str = "",
                 fmt: str = "json", timeout: float | None = None, **params):
        """Позвать ключ.

        value    — главное значение (для alive это ссылка или @username);
                   можно не давать, если передаёте параметры по именам.
        only     — вернуть только это поле вместо всего ответа.
        fmt      — 'json' (поля), 'text' (строка для человека), 'bool' (да/нет).
        timeout  — сколько ждать ответа, секунд; по умолчанию как у клиента.
        **params — остальные параметры ключа по именам.
        """
        ответ = self._keys.call(
            self._name, value, fmt=fmt, only=only, timeout=timeout, **params
        )
        if only or fmt != "json":
            return ответ.get(only) if isinstance(ответ, dict) and only else ответ
        return Answer(ответ)

    def text(self, value: str | None = None, *, timeout: float | None = None, **params) -> str:
        """Готовая человеческая строка вместо полей."""
        return self._keys.call(self._name, value, fmt="text", timeout=timeout, **params)

    def fields(self) -> list[str]:
        """Что этот ключ вообще умеет вернуть."""
        return self._keys.fields(self._name)

    def __getattr__(self, field: str):
        if field.startswith("_"):
            raise AttributeError(field)
        known = self.fields()
        if known and field not in known:
            raise AttributeError(
                f"у ключа '{self._name}' нет поля '{field}'; есть: {', '.join(known)}"
            )

        def получить(value: str | None = None, *, timeout: float | None = None, **params):
            ответ = self._keys.call(
                self._name, value, fmt="json", only=field, timeout=timeout, **params
            )
            return ответ.get(field)

        получить.__name__ = field
        получить.__doc__ = f"Только поле '{field}' ключа '{self._name}'."
        return получить

    def __dir__(self):
        return list(super().__dir__()) + self.fields()

    def __repr__(self) -> str:
        return f"<ключ {self._name}: {', '.join(self.fields())}>"


class Keys:
    """Подключение к «Ключам».

    token      — ключ доступа. По умолчанию берётся из KEYS_API_KEY, а если и
                 её нет — у сервера спрашивается публичный ключ. То есть
                 настраивать ничего не надо: Keys() работает сразу. Свой ключ
                 нужен, только если хочется собственный рубильник. Ни у того,
                 ни у другого нет ни счётчика, ни срока.
    base       — адрес сервера. По умолчанию тот, с которого скачан клиент.
    timeout    — сколько ждать ответа, секунд. Можно переопределить в вызове.
    retries    — сколько раз повторить при обрыве связи (не при отказе сервера:
                 отказ повторять бессмысленно).
    user_agent — как представляться; полезно, чтобы владелец сервиса видел,
                 кто ходит.
    """

    def __init__(self, token: str | None = None, base: str = BASE, *,
                 timeout: float = 20.0, retries: int = 1,
                 user_agent: str = f"monokeys/{VERSION}") -> None:
        self.token = token if token is not None else os.environ.get("KEYS_API_KEY", "")
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.retries = max(0, retries)
        self.user_agent = user_agent
        self._catalog: dict | None = None
        self._public: str | None = None

    def _ключ(self) -> str:
        """Свой ключ, а если своего нет — публичный.

        Публичный ключ не зашит в пакет намеренно: его спрашивают у сервера,
        поэтому смена ключа доходит до всех сразу и не требует нового релиза.
        Спрашиваем один раз за время жизни объекта.
        """
        if self.token:
            return self.token
        if self._public is None:
            try:
                request = urllib.request.Request(
                    f"{self.base}/public-token", headers={"User-Agent": self.user_agent}
                )
                self._public = self._open(request, self.timeout).strip()
            except KeysError:
                self._public = ""  # сервер не дал — пойдём как аноним
        return self._public

    # --- то, ради чего клиент существует ------------------------------- #

    def call(self, name: str, value: str | None = None, *, fmt: str = "json",
             only: str = "", timeout: float | None = None, **params):
        """Позвать любой ключ по имени.

        Возвращает словарь при fmt='json' и строку при fmt='text'/'bool'.
        Обычно вызывают не это, а k.<имя ключа>(...) — но здесь ничего не
        спрятано, и можно звать ключ, имя которого известно только в рантайме.
        """
        query = {k: v for k, v in params.items() if v is not None}
        query["fmt"] = fmt
        if only:
            query["only"] = only

        url = f"{self.base}/{urllib.parse.quote(name)}/"
        if value is not None:
            url += urllib.parse.quote(str(value), safe="@+")
        url += "?" + urllib.parse.urlencode(query)

        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        ключ = self._ключ()
        if ключ:
            # только заголовком: в query-строке ключ виден в логах и в истории
            request.add_header("Authorization", f"Bearer {ключ}")

        body = self._open(request, timeout if timeout is not None else self.timeout)
        return json.loads(body) if fmt == "json" else body

    def _open(self, request, timeout: float) -> str:
        последняя: Exception | None = None
        for попытка in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace").strip()
                if exc.code in (401, 403):
                    raise AccessDenied(f"{exc.code}: {detail}", status=exc.code,
                                       body=detail) from exc
                if exc.code in (502, 503):
                    raise Unavailable(f"{exc.code}: {detail}", status=exc.code,
                                      body=detail) from exc
                raise KeysError(f"{exc.code}: {detail}", status=exc.code, body=detail) from exc
            except urllib.error.URLError as exc:
                последняя = exc
                if попытка < self.retries:
                    time.sleep(0.3)
        raise Unavailable(f"не дозвонился до {self.base}: {последняя}")

    # --- что вообще есть на сервере -------------------------------------- #

    def catalog(self, *, refresh: bool = False) -> dict:
        """Список ключей с описаниями. refresh=True — спросить заново."""
        if self._catalog is None or refresh:
            request = urllib.request.Request(
                f"{self.base}/keys", headers={"User-Agent": self.user_agent}
            )
            self._catalog = json.loads(self._open(request, self.timeout))
        return self._catalog

    def names(self) -> list[str]:
        """Имена всех доступных ключей."""
        return [k["id"] for k in self.catalog()["keys"]]

    def fields(self, name: str) -> list[str]:
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
            raise AttributeError(f"ключа '{name}' нет; есть: {', '.join(available)}")
        return _Key(self, name)

    def __dir__(self):
        try:
            return list(super().__dir__()) + self.names()
        except KeysError:
            return list(super().__dir__())

    def __repr__(self) -> str:
        if self.token:
            чем = "свой ключ"
        elif self._public:
            чем = "публичный ключ"
        elif self._public == "":
            чем = "без ключа (сервер не дал публичный)"
        else:
            чем = "ключ ещё не спрашивали"
        return f"<Keys {self.base}, {чем}>"
