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


class TokenBucket:
    """Общий кран наружу. Один на процесс, а не один на пользователя."""

    def __init__(self, rps: float, burst: int) -> None:
        self.rps = rps
        self.burst = burst
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def take(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rps)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rps)


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

    def sweep(self) -> int:
        """Чистка протухшего. Раз в час хватает: строки маленькие."""
        now = time.time()
        self.mem = {k: v for k, v in self.mem.items() if v[0] > now}
        cur = self.db.execute("DELETE FROM cache WHERE expires_at < ?", (now,))
        self.db.commit()
        return cur.rowcount


class Context:
    """Единственная дверь ключа во внешний мир."""

    def __init__(self, cache: Cache, bucket: TokenBucket, client: httpx.AsyncClient) -> None:
        self.cache = cache
        self.bucket = bucket
        self.client = client

    async def fetch(self, url: str, *, timeout: float = 15.0) -> Response:
        """Один поход наружу, с одной повторной попыткой.

        Повтор не роскошь: самый первый запрос после холодного старта регулярно
        падает на установке соединения, и человек видит 502 на первой же своей
        проверке. Повторяем только транспортные ошибки — ответ сервера, даже
        плохой, это ответ, и переспрашивать его незачем.
        """
        last: Exception | None = None
        for попытка in range(2):
            await self.bucket.take()
            try:
                resp = await self.client.get(
                    url, timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True
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
