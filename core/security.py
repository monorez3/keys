"""Всё, что защищает ключ доступа от использования не по назначению.

Три вещи, которые проверены атакой на живом сервере, а не придуманы:

1. Заголовку X-Real-IP верить нельзя. Пока мы ему верили, кто угодно мог
   подставить любой адрес и печатать токены пачками, обходя лимит «5 в сутки
   с адреса». Теперь заголовок читается только от прокси, который назван явно
   в KEYS_TRUSTED_PROXIES. По умолчанию не доверяем никому: сервер, открытый
   наружу без прокси, должен быть безопасен из коробки.

2. Токен в query-строке попадает в лог доступа открытым текстом — а логи
   живут долго, лежат в бэкапах и их читают посторонние. Отдавать его из
   адресной строки иногда всё же нужно (браузер, ячейка таблицы), поэтому
   он вырезается из логов.

3. Bearer-токен по обычному HTTP слушается по дороге кем угодно. На проде
   включается KEYS_REQUIRE_HTTPS=1 — тогда токен, пришедший не по HTTPS,
   не принимается и человек об этом узнаёт, а не думает, что защищён.
"""

from __future__ import annotations

import logging
import os
import re

# Пусто по умолчанию — заголовкам не верим, пока прокси не назван явно.
TRUSTED_PROXIES = {
    p.strip() for p in os.environ.get("KEYS_TRUSTED_PROXIES", "").split(",") if p.strip()
}
REQUIRE_HTTPS = os.environ.get("KEYS_REQUIRE_HTTPS", "") == "1"

TOKEN_IN_TEXT = re.compile(r"(token=)(kx_)?[A-Za-z0-9_\-]+")
REDACTED = r"\1<вырезано>"


def client_ip(request) -> str:
    """Настоящий адрес звонящего.

    Заголовок подставляет кто угодно, сокет — нет. Поэтому заголовок читаем
    только тогда, когда соединение пришло от прокси, которому мы верим.
    """
    peer = request.client.host if request.client else ""
    if peer and peer in TRUSTED_PROXIES:
        forwarded = request.headers.get("x-real-ip") or ""
        if not forwarded:
            chain = request.headers.get("x-forwarded-for", "")
            forwarded = chain.split(",")[0].strip()
        if forwarded:
            return forwarded
    return peer or "?"


def over_https(request) -> bool:
    """HTTPS ли это на самом деле — с поправкой на прокси, который его снимает."""
    peer = request.client.host if request.client else ""
    if peer and peer in TRUSTED_PROXIES:
        proto = request.headers.get("x-forwarded-proto", "")
        if proto:
            return proto.split(",")[0].strip() == "https"
    return request.url.scheme == "https"


def redact(text: str) -> str:
    """Вырезать токен из любой строки перед тем, как она куда-то уедет."""
    return TOKEN_IN_TEXT.sub(REDACTED, text)


class RedactTokens(logging.Filter):
    """Фильтр лога доступа: строка запроса приезжает в record.args."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                redact(a) if isinstance(a, str) and "token=" in a else a
                for a in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        if isinstance(record.msg, str) and "token=" in record.msg:
            record.msg = redact(record.msg)
        return True


def install_log_redaction() -> None:
    """Повесить фильтр на все логгеры, через которые проходят запросы."""
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        logging.getLogger(name).addFilter(RedactTokens())
