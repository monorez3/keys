"""Квоты на одного пользователя.

Считать надо именно тут, а не в nginx: ограничение у нас не «сколько запросов
выдержит сервер» (он выдержит на порядки больше), а «сколько наружных походов
к t.me мы готовы потратить на одного». Кэш-попадания квоту не тратят — иначе
мы наказывали бы за вежливое поведение.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

PER_MINUTE = 60
PER_DAY = 2000
DAY = 86400


class Quota:
    def __init__(self, per_minute: int = PER_MINUTE, per_day: int = PER_DAY) -> None:
        self.per_minute = per_minute
        self.per_day = per_day
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, client: str) -> tuple[bool, str]:
        now = time.time()
        hits = self._hits[client]
        while hits and hits[0] < now - DAY:
            hits.popleft()

        minute_ago = now - 60
        in_minute = sum(1 for t in hits if t >= minute_ago)
        if in_minute >= self.per_minute:
            return False, f"не больше {self.per_minute} проверок в минуту"
        if len(hits) >= self.per_day:
            return False, f"не больше {self.per_day} проверок в сутки"
        return True, ""

    def spend(self, client: str) -> None:
        """Тратим только на промах кэша — реальный поход наружу."""
        self._hits[client].append(time.time())

    def used(self, client: str) -> int:
        now = time.time()
        return sum(1 for t in self._hits[client] if t >= now - DAY)
