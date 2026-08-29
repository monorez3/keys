"""Ключи доступа — те самые, что кладут в .env.

Устроено как у любого ИИ-API: получил строку `kx_...`, положил в окружение,
клиент подставляет её сам. Разница только в том, что тут ключ бесплатный и
выдаётся без регистрации: он нужен не для денег, а чтобы считать квоту на
того, кто пришёл, а не на весь офис за одним IP.

Что сделано против злоупотреблений:

* хранится sha256, а не сам ключ — база может утечь, ключ из неё не достать;
* ключ можно отозвать самому, если он куда-то попал; отозванный не воскресает;
* сравнение идёт по хэшу через compare_digest, а не по самой строке;
* память под опознанные ключи ограничена — иначе её можно раздуть выдачей;
* при выдаче считается адрес, а адрес берётся из сокета, не из заголовка
  (см. security.client_ip — заголовок подделывается тривиально).

Чего тут намеренно нет: срока жизни. Для бесплатного ключа он только злит
человека, а защиты не добавляет — украденный ключ вредит в первые же минуты.
От кражи спасает отзыв, а не то, что ключ протухнет через год.
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
            "  issued_to TEXT,"
            "  revoked_at REAL,"
            "  last_used_at REAL)"
        )
        # старые базы без новых столбцов
        existing = {row[1] for row in self.db.execute("PRAGMA table_info(tokens)")}
        for column in ("revoked_at", "last_used_at"):
            if column not in existing:
                self.db.execute(f"ALTER TABLE tokens ADD COLUMN {column} REAL")
        self.db.commit()
        self._known: dict[str, float] = {}

    # --- выдача и проверка ---------------------------------------------- #

    def issue(self, note: str = "", issued_to: str = "") -> str:
        """Новый ключ. Возвращается один раз — на нашей стороне только хэш."""
        token = PREFIX + secrets.token_urlsafe(ENTROPY_BYTES)
        self.db.execute(
            "INSERT INTO tokens (hash, created_at, note, issued_to) VALUES (?, ?, ?, ?)",
            (_hash(token), time.time(), note[:200], issued_to[:100]),
        )
        self.db.commit()
        return token

    def valid(self, token: str) -> bool:
        """Годен ли ключ. Отозванный — не годен, и это не кэшируется в «да»."""
        if not token or not token.startswith(PREFIX):
            return False
        if len(token) > 128:  # заведомо не наш формат, в базу не ходим
            return False

        digest = _hash(token)
        кэш = self._known.get(digest)
        if кэш is not None and кэш > time.time() - 60:
            return True

        row = self.db.execute(
            "SELECT hash, revoked_at FROM tokens WHERE hash = ?", (digest,)
        ).fetchone()
        if row is None or row[1] is not None:
            self._known.pop(digest, None)
            return False
        # сравнение хэшей постоянным временем: дешевле, чем доказывать,
        # что тут утечки времени точно нет
        if not hmac.compare_digest(row[0], digest):
            return False

        if len(self._known) >= KNOWN_CACHE_LIMIT:
            self._known.clear()  # память ограничена, иначе её раздувают выдачей
        self._known[digest] = time.time()
        return True

    def touch(self, token: str) -> None:
        """Отметить, что ключом только что пользовались — для гигиены."""
        self.db.execute(
            "UPDATE tokens SET last_used_at = ? WHERE hash = ?", (time.time(), _hash(token))
        )
        self.db.commit()

    def revoke(self, token: str) -> bool:
        """Отозвать свой ключ. Отозвать чужой нельзя: нужно знать сам ключ."""
        digest = _hash(token)
        cur = self.db.execute(
            "UPDATE tokens SET revoked_at = ? WHERE hash = ? AND revoked_at IS NULL",
            (time.time(), digest),
        )
        self.db.commit()
        self._known.pop(digest, None)  # чтобы отзыв работал сразу, а не через минуту
        return cur.rowcount > 0

    # --- защита самой выдачи --------------------------------------------- #

    def issued_today(self, issued_to: str) -> int:
        """Сколько ключей уже выдано этому адресу за сутки — против штамповки."""
        row = self.db.execute(
            "SELECT COUNT(*) FROM tokens WHERE issued_to = ? AND created_at > ?",
            (issued_to[:100], time.time() - 86400),
        ).fetchone()
        return row[0] if row else 0

    def issued_total_today(self) -> int:
        """Сколько выдано всего за сутки — потолок против раздачи с ботнета."""
        row = self.db.execute(
            "SELECT COUNT(*) FROM tokens WHERE created_at > ?", (time.time() - 86400,)
        ).fetchone()
        return row[0] if row else 0

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
