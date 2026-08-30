"""Тесты не должны трогать боевые базы.

Без этого модуль приложения при импорте открывал тот же cache.db и tokens.db,
что и запущенный сервер: тест мог получить ответ, положенный в кэш вчера, и
пройти по чужой удаче. Один такой тест уже проходил локально и падал в CI —
разница была ровно в том, что у CI кэш пустой.

Каталог задаётся до импорта приложения, иначе поздно.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_ВРЕМЕННЫЙ = Path(tempfile.mkdtemp(prefix="keys-tests-"))
os.environ["KEYS_DATA_DIR"] = str(_ВРЕМЕННЫЙ)
