"""Ключи доступа — те самые, что кладут в .env.

Устроено как у любого ИИ-API: получил строку `kx_...`, положил в окружение,
клиент подставляет её сам. Разница только в том, что тут ключ бесплатный и
выдаётся без регистрации: он нужен не для денег, а чтобы считать квоту на
того, кто пришёл, а не на весь офис за одним IP.

Хранится хэш, а не сам ключ: базу может увидеть кто-то ещё, а ключ — это
пароль пользователя к нашей квоте.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path

PREFIX = "kx_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Tokens:
    def __init__(self, db_path: Path) -> None:
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS tokens ("
            "  hash TEXT PRIMARY KEY,"
            "  created_at REAL NOT NULL,"
            "  note TEXT,"
            "  issued_to TEXT)"
        )
        self.db.commit()
        self._known: set[str] = set()

    def issue(self, note: str = "", issued_to: str = "") -> str:
        """Новый ключ. Возвращается один раз — на нашей стороне только хэш."""
        token = PREFIX + secrets.token_urlsafe(24)
        self.db.execute(
            "INSERT INTO tokens (hash, created_at, note, issued_to) VALUES (?, ?, ?, ?)",
            (_hash(token), time.time(), note[:200], issued_to[:100]),
        )
        self.db.commit()
        self._known.add(_hash(token))
        return token

    def valid(self, token: str) -> bool:
        if not token or not token.startswith(PREFIX):
            return False
        digest = _hash(token)
        if digest in self._known:
            return True
        row = self.db.execute("SELECT 1 FROM tokens WHERE hash = ?", (digest,)).fetchone()
        if row:
            self._known.add(digest)
            return True
        return False

    def issued_today(self, issued_to: str) -> int:
        """Сколько ключей уже выдано этому адресу за сутки — против штамповки."""
        row = self.db.execute(
            "SELECT COUNT(*) FROM tokens WHERE issued_to = ? AND created_at > ?",
            (issued_to[:100], time.time() - 86400),
        ).fetchone()
        return row[0] if row else 0

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
