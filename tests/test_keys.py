"""Тесты того, что ломается молча.

Три места, где ошибка не видна глазом: разбор страницы (мёртвый канал отдаёт
200 и выглядит как живой), нормализация ссылки и квота. Сеть тут не нужна —
страницы лежат в fixtures, поэтому тесты идут офлайн и за миллисекунды.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "keys" / "alive"))

import handler  # noqa: E402
import manifest  # noqa: E402
from limits import Quota  # noqa: E402
from registry import discover  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- разбор ---------------------------------------------------------------- #

def test_живой_канал_разбирается_целиком():
    res = handler.parse_preview(fixture("alive_channel.html"), "durov", "https://t.me/durov")
    assert res["is_alive"] is True
    assert res["kind"] == "channel"
    assert res["title"]
    assert res["members_count"] > 1_000_000
    assert res["avatar_url"].startswith("http")


def test_удалённый_канал_не_считается_живым():
    """Telegram отдаёт 200 и заглушку — самая частая причина ложного 'жив'."""
    res = handler.parse_preview(fixture("dead.html"), "nope12345xyz", "https://t.me/nope12345xyz")
    assert res["is_alive"] is False
    assert res["error"]


def test_бот_определяется_по_суффиксу():
    res = handler.parse_preview(fixture("bot.html"), "BotFather", "https://t.me/BotFather")
    assert res["is_alive"] is True
    assert res["kind"] == "bot"


def test_ошибка_сети_не_равна_мёртвому_каналу():
    res = handler.parse_preview("", "x", "https://t.me/x", status=503)
    assert res["is_alive"] is False
    assert "503" in res["error"]


# --- нормализация ---------------------------------------------------------- #

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("durov", "durov"),
        ("@durov", "durov"),
        ("https://t.me/durov", "durov"),
        ("http://telegram.me/durov?x=1", "durov"),
        ("t.me/s/durov", "durov"),
        ("t.me/+AbC-123", "+AbC-123"),
    ],
)
def test_ссылка_приводится_к_username(raw, expected):
    assert handler.normalize(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "https://example.com/durov", "не ссылка"])
def test_мусор_отвергается(raw):
    with pytest.raises(ValueError):
        handler.normalize(raw)


# --- манифест и реестр ----------------------------------------------------- #

def test_все_ключи_загружаются():
    keys = discover(ROOT / "keys")
    assert "alive" in keys
    for key in keys.values():
        assert key.manifest.summary, "у ключа должно быть короткое описание"
        assert key.manifest.params, "ключ без параметров бесполезен"


def test_схема_для_ассистента_валидна():
    keys = discover(ROOT / "keys")
    schema = keys["alive"].manifest.input_schema()
    assert schema["type"] == "object"
    assert "link" in schema["properties"]
    assert schema["required"] == ["link"]


def test_кривой_манифест_не_проходит(tmp_path):
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "key.json").write_text('{"id": "broken", "title": "x"}', encoding="utf-8")
    with pytest.raises(manifest.ManifestError):
        manifest.load(folder / "key.json")


def test_id_должен_совпадать_с_папкой(tmp_path):
    folder = tmp_path / "folder_name"
    folder.mkdir()
    (folder / "key.json").write_text(
        '{"id":"other","title":"x","summary":"x","params":[]}', encoding="utf-8"
    )
    with pytest.raises(manifest.ManifestError):
        manifest.load(folder / "key.json")


# --- квота ----------------------------------------------------------------- #

def test_квота_режет_после_лимита():
    q = Quota(per_minute=3, per_day=10)
    for _ in range(3):
        assert q.check("ip")[0] is True
        q.spend("ip")
    ok, why = q.check("ip")
    assert ok is False
    assert "минуту" in why


def test_разные_клиенты_не_мешают_друг_другу():
    q = Quota(per_minute=1, per_day=10)
    q.spend("a")
    assert q.check("a")[0] is False
    assert q.check("b")[0] is True


# --- примеры кода и машинные описания -------------------------------------- #

import snippets  # noqa: E402
import spec      # noqa: E402

BASE = "https://example.test"


def all_manifests():
    return [k.manifest for k in discover(ROOT / "keys").values()]


def test_примеры_есть_для_всех_языков():
    for m in all_manifests():
        code = snippets.render(m, BASE)
        assert set(code) == set(snippets.LANGS)
        for lang, text in code.items():
            assert BASE in text, f"{m.id}/{lang}: пример без адреса, копировать нечего"
            assert m.id in text


def test_в_примерах_нет_заглушек():
    """Копипаста должна работать как есть — 'ВАШ-АДРЕС' подставлять никто не будет."""
    for m in all_manifests():
        for lang, text in snippets.render(m, BASE).items():
            assert "ВАШ-АДРЕС" not in text, f"{m.id}/{lang}"
            assert "..." not in text, f"{m.id}/{lang}: параметр без примера в манифесте"


def test_питоновский_пример_компилируется():
    for m in all_manifests():
        compile(snippets.render(m, BASE)["python"], f"<{m.id}>", "exec")


def test_openapi_описывает_каждый_ключ():
    doc = spec.openapi(all_manifests(), BASE)
    assert doc["openapi"].startswith("3.")
    for m in all_manifests():
        op = doc["paths"][f"/k/{m.id}"]["get"]
        assert op["operationId"] == m.id
        assert {p["name"] for p in op["parameters"]} == {p.name for p in m.params}


def test_llms_txt_содержит_все_ключи():
    text = spec.llms_txt(all_manifests(), BASE)
    for m in all_manifests():
        assert m.id in text and m.summary in text
        for p in m.params:
            assert p.name in text
