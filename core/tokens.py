"""Ключи доступа: выдаёт владелец, живут вечно, использование не считается.

Смысл ключа здесь не в том, чтобы что-то урезать. Ключ выдаётся форуму,
сервису или человеку, работает без счётчика и без срока — считать чужие
запросы владелец не собирается. Ключ решает две другие задачи:

    * узнать, кто пришёл (у ключа есть подпись — кому он выдан);
    * иметь рубильник — если конкретный ключ начал вредить, его отзывают,
      не трогая остальных.

Поэтому тут нет ни квот, ни срока годности, ни самообслуживания: раздаёт
владелец, а не кто угодно с улицы.

Что защищено:

* хранится sha256, а не сам ключ — база может утечь, ключ из неё не достать;
* у ключа есть публичный id (начало хэша) — по нему владелец видит и отзывает
  ключ, не зная самого ключа; в списке ключ никогда не показывается целиком;
* отзыв действует сразу и не воскресает из кэша опознанных;
* память под опознанные ключи ограничена.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from pathlib import Path

PREFIX = "kx_"
# 24 байта случайности = 192 бита. Перебрать нельзя, поэтому соль не нужна:
# радужные таблицы строят на слабых паролях, а не на таком.
ENTROPY_BYTES = 24
KNOWN_CACHE_LIMIT = 10_000
ID_LENGTH = 12


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_id(token: str) -> str:
    """Короткий опознавательный номер ключа. Из него сам ключ не восстановить."""
    return _hash(token)[:ID_LENGTH]


class Tokens:
    def __init__(self, db_path: Path) -> None:
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS tokens ("
            "  hash TEXT PRIMARY KEY,"
            "  created_at REAL NOT NULL,"
            "  label TEXT,"
            "  note TEXT,"
            "  issued_to TEXT,"
            "  revoked_at REAL,"
            "  last_used_at REAL,"
            "  used_count INTEGER NOT NULL DEFAULT 0)"
        )
        existing = {row[1] for row in self.db.execute("PRAGMA table_info(tokens)")}
        for column, kind in (
            ("revoked_at", "REAL"), ("last_used_at", "REAL"),
            ("label", "TEXT"), ("used_count", "INTEGER"),
        ):
            if column not in existing:
                self.db.execute(f"ALTER TABLE tokens ADD COLUMN {column} {kind}")
        self.db.commit()
        self._known: dict[str, float] = {}

    # --- выдача ---------------------------------------------------------- #

    def issue(self, label: str = "", note: str = "", issued_to: str = "") -> tuple[str, str]:
        """Новый ключ. Возвращает (сам ключ, публичный id).

        Ключ показывается один раз: на сервере остаётся только хэш. Публичный
        id остаётся навсегда — по нему ключ потом видно в списке и отзывается.
        """
        token = PREFIX + secrets.token_urlsafe(ENTROPY_BYTES)
        self.db.execute(
            "INSERT INTO tokens (hash, created_at, label, note, issued_to)"
            " VALUES (?, ?, ?, ?, ?)",
            (_hash(token), time.time(), label[:100], note[:200], issued_to[:100]),
        )
        self.db.commit()
        return token, public_id(token)

    # --- проверка -------------------------------------------------------- #

    def valid(self, token: str) -> bool:
        """Годен ли ключ. Отозванный — не годен, и это не кэшируется в «да»."""
        if not token or not token.startswith(PREFIX) or len(token) > 128:
            return False

        digest = _hash(token)
        свежесть = self._known.get(digest)
        if свежесть is not None and свежесть > time.time() - 60:
            return True

        row = self.db.execute(
            "SELECT hash, revoked_at FROM tokens WHERE hash = ?", (digest,)
        ).fetchone()
        if row is None or row[1] is not None:
            self._known.pop(digest, None)
            return False
        if not hmac.compare_digest(row[0], digest):
            return False

        if len(self._known) >= KNOWN_CACHE_LIMIT:
            self._known.clear()  # память ограничена, иначе её раздувают выдачей
        self._known[digest] = time.time()
        return True

    def touch(self, token: str) -> None:
        """Отметить использование. Счётчик тут для владельца — «этим ключом
        пользуются, а этот мёртвый», — а не для того, чтобы кого-то урезать."""
        self.db.execute(
            "UPDATE tokens SET last_used_at = ?, used_count = used_count + 1"
            " WHERE hash = ?",
            (time.time(), _hash(token)),
        )
        self.db.commit()

    # --- рубильник ------------------------------------------------------- #

    def revoke(self, token: str) -> bool:
        """Отозвать по самому ключу — так владелец ключа отзывает свой."""
        return self._revoke_where("hash = ?", (_hash(token),), _hash(token))

    def revoke_by_id(self, key_id: str) -> bool:
        """Отозвать по публичному id — так владелец сервиса отзывает чужой,
        не зная самого ключа."""
        row = self.db.execute(
            "SELECT hash FROM tokens WHERE hash LIKE ? AND revoked_at IS NULL",
            (key_id[:ID_LENGTH] + "%",),
        ).fetchone()
        if row is None:
            return False
        return self._revoke_where("hash = ?", (row[0],), row[0])

    def _revoke_where(self, where: str, args: tuple, digest: str) -> bool:
        cur = self.db.execute(
            f"UPDATE tokens SET revoked_at = ? WHERE {where} AND revoked_at IS NULL",
            (time.time(), *args),
        )
        self.db.commit()
        self._known.pop(digest, None)  # отзыв действует сразу, а не через минуту
        return cur.rowcount > 0

    # --- обзор для владельца --------------------------------------------- #

    def listing(self) -> list[dict]:
        """Все выданные ключи. Самих ключей тут нет и быть не может."""
        rows = self.db.execute(
            "SELECT hash, label, note, created_at, last_used_at, used_count, revoked_at"
            " FROM tokens ORDER BY created_at DESC"
        )
        return [
            {
                "id": row[0][:ID_LENGTH],
                "кому": row[1] or "",
                "заметка": row[2] or "",
                "выдан": time.strftime("%Y-%m-%d %H:%M", time.localtime(row[3])),
                "последний_раз": (
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(row[4])) if row[4] else None
                ),
                "запросов": row[5] or 0,
                "отозван": bool(row[6]),
            }
            for row in rows
        ]

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
