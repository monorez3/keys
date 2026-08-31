"""Что ключ получает на вход помимо параметров: сеть, кэш, троттлинг.

Замеры с боевого сервера (2 ядра, OVH Франция), на них построены числа ниже:
    одна страница t.me      ~10 КБ, ~75 мс
    восемь параллельно      171 мс на всю пачку
    разбор страницы         0.085 мс -> ~11 700 страниц/сек на ядро

То есть процессор и память не при чём: узкое место — терпение t.me к нашему
IP. Поэтому наружу мы ходим через общий кран (token bucket), а не «сколько
пришло запросов, столько и ушло». Кэш этот кран разгружает: один и тот же
популярный канал спрашивают многие, а наружу идём один раз.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

# Наружный бюджет. 5 запросов/сек = 432 000 проверок в сутки без кэша.
# Ставим заведомо ниже того, что железо тянет: банит нас не железо.
OUTBOUND_RPS = 5.0
OUTBOUND_BURST = 20

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class FetchError(RuntimeError):
    """Сеть не ответила. Это НЕ «мёртвый канал» — разница принципиальная."""


@dataclass(slots=True)
class Response:
    status: int
    text: str
    url: str


class Busy(RuntimeError):
    """Кран занят дольше, чем разумно ждать. Честный отказ лучше вечной очереди."""


class TokenBucket:
    """Общий кран наружу. Один на процесс, а не один на пользователя.

    Две тонкости, обе про поведение под нагрузкой:

    * спим ВНЕ замка. Если держать замок во сне, ждущие выстраиваются в
      очередь даже когда кран свободен, и задержка растёт на ровном месте;
    * ждём не дольше max_wait. Без этого поток запросов от одного наглеца
      копит бесконечную очередь: соединения висят, память течёт, а остальным
      достаётся таймаут вместо ответа. Лучше сразу сказать «занято».
    """

    def __init__(self, rps: float, burst: int, max_wait: float = 10.0) -> None:
        self.rps = rps
        self.burst = burst
        self.max_wait = max_wait
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def take(self) -> None:
        крайний_срок = time.monotonic() + self.max_wait
        while True:
            async with self._lock:
                now = time.monotonic()
                self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rps)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                ждать = (1 - self.tokens) / self.rps

            if time.monotonic() + ждать > крайний_срок:
                raise Busy("слишком много запросов прямо сейчас, попробуйте позже")
            await asyncio.sleep(ждать)


class Cache:
    """Двухслойный: словарь в памяти + sqlite, чтобы перезапуск не обнулял кэш."""

    def __init__(self, db_path: Path) -> None:
        self.mem: dict[str, tuple[float, dict]] = {}
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            "  k TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL NOT NULL)"
        )
        self.db.commit()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> dict | None:
        now = time.time()
        hit = self.mem.get(key)
        if hit and hit[0] > now:
            self.hits += 1
            return hit[1]
        row = self.db.execute(
            "SELECT value, expires_at FROM cache WHERE k = ?", (key,)
        ).fetchone()
        if row and row[1] > now:
            value = json.loads(row[0])
            self.mem[key] = (row[1], value)
            self.hits += 1
            return value
        self.misses += 1
        return None

    def put(self, key: str, value: dict, ttl: int) -> None:
        expires = time.time() + ttl
        self.mem[key] = (expires, value)
        self.db.execute(
            "INSERT OR REPLACE INTO cache (k, value, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False), expires),
        )
        self.db.commit()

    def clear(self) -> int:
        """Забыть всё — и в базе, и в памяти процесса.

        Чистить только базу мало: слой в памяти живёт отдельно и продолжает
        отдавать старое. На этом я и попался — стёр таблицу, а сервер полчаса
        возвращал ответ, которого там уже не было.
        """
        было = len(self.mem)
        self.mem.clear()
        cur = self.db.execute("DELETE FROM cache")
        self.db.commit()
        return max(было, cur.rowcount)

    def sweep(self) -> int:
        """Чистка протухшего. Раз в час хватает: строки маленькие."""
        now = time.time()
        self.mem = {k: v for k, v in self.mem.items() if v[0] > now}
        cur = self.db.execute("DELETE FROM cache WHERE expires_at < ?", (now,))
        self.db.commit()
        return cur.rowcount


# Сколько запросов в секунду не жалко конкретному источнику. По умолчанию
# щадящие 5 — столько же, сколько терпит t.me. Крупные открытые API выдерживают
# больше и сами это разрешают, поэтому душить их общим краном незачем: медленный
# сосед не должен тормозить остальных.
ПО_ИСТОЧНИКАМ = {
    "t.me": 5.0,
    "ru.wikipedia.org": 20.0,
    "en.wikipedia.org": 20.0,
    "www.wikidata.org": 15.0,
    "api.duckduckgo.com": 10.0,
    "nominatim.openstreetmap.org": 1.0,   # их правила: не чаще одного в секунду
    "api.groq.com": 5.0,
    # у CoinGecko бесплатный предел около 30 запросов в минуту, и он
    # отвечает 429, а не замедлением. Держимся заметно ниже.
    "api.coingecko.com": 0.3,
}


class Краны:
    """Отдельный кран на каждый источник.

    Раньше кран был один на всех, и это было верно, пока источник был один.
    С несколькими источниками общий кран означает, что запрос к Википедии
    ждёт очереди из-за Telegram, хотя Википедия готова отвечать вчетверо чаще.
    """

    def __init__(self, по_умолчанию: float = OUTBOUND_RPS) -> None:
        self.по_умолчанию = по_умолчанию
        self._краны: dict[str, TokenBucket] = {}

    def для(self, url: str) -> TokenBucket:
        хост = urlsplit(url).hostname or "?"
        if хост not in self._краны:
            rps = ПО_ИСТОЧНИКАМ.get(хост, self.по_умолчанию)
            self._краны[хост] = TokenBucket(rps, max(int(rps * 4), OUTBOUND_BURST))
        return self._краны[хост]


class Context:
    """Единственная дверь ключа во внешний мир."""

    def __init__(self, cache: Cache, bucket, client: httpx.AsyncClient) -> None:
        self.cache = cache
        self.bucket = bucket
        self.client = client

    async def fetch(self, url: str, *, timeout: float = 15.0,
                    headers: dict | None = None) -> Response:
        """Один поход наружу, с одной повторной попыткой.

        Повтор не роскошь: самый первый запрос после холодного старта регулярно
        падает на установке соединения, и человек видит 502 на первой же своей
        проверке. Повторяем только транспортные ошибки — ответ сервера, даже
        плохой, это ответ, и переспрашивать его незачем.
        """
        if self.client is None:
            raise FetchError(
                "нет сетевого клиента: приложение не прошло запуск "
                "(в тестах поднимайте его через TestClient как контекст)"
            )

        кран = self.bucket.для(url) if isinstance(self.bucket, Краны) else self.bucket
        last: Exception | None = None
        for попытка in range(2):
            await кран.take()
            try:
                resp = await self.client.get(
                    url, timeout=timeout, follow_redirects=True,
                    # свои заголовки нужны источникам вроде DNS-over-HTTPS:
                    # без Accept они отдают не то, что мы умеем разбирать
                    headers={"User-Agent": UA, **(headers or {})},
                )
                return Response(status=resp.status_code, text=resp.text, url=str(resp.url))
            except httpx.TransportError as exc:
                last = exc
                if попытка == 0:
                    await asyncio.sleep(0.3)
            except httpx.HTTPError as exc:
                last = exc
                break
        # у части ошибок httpx текст пустой — тогда «сеть не ответила: »
        # выглядит как наша поломка; имя класса всегда что-то говорит
        raise FetchError(str(last) or type(last).__name__) from last

    async def post(self, url: str, *, json_body: dict, headers: dict | None = None,
                   timeout: float = 30.0) -> Response:
        """POST — тем источникам, которые иначе не спросить (ИИ, например).

        Повторов тут нет намеренно: повторить POST — значит, возможно, сделать
        работу дважды. Для чтения это безобидно, для отправки нет.
        """
        if self.client is None:
            raise FetchError("нет сетевого клиента: приложение не прошло запуск")

        кран = self.bucket.для(url) if isinstance(self.bucket, Краны) else self.bucket
        await кран.take()
        try:
            resp = await self.client.post(
                url, json=json_body, timeout=timeout,
                headers={"User-Agent": UA, **(headers or {})},
            )
        except httpx.HTTPError as exc:
            raise FetchError(str(exc) or type(exc).__name__) from exc
        return Response(status=resp.status_code, text=resp.text, url=str(resp.url))
