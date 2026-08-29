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


# --- короткая форма: имя ключа + строка ------------------------------------- #

def test_первичный_параметр_существует():
    for m in all_manifests():
        assert m.primary_param().name in {p.name for p in m.params}


def test_ответ_приходит_человеческой_строкой():
    m = all_manifests()[0]
    живой = m.short_text(
        {"is_alive": True, "kind": "channel", "title": "Pavel Durov", "members_count": 11005185}
    )
    assert живой.startswith("жив")
    assert "11 005 185" in живой, "число для человека разбивается пробелами"
    assert m.short_text({"is_alive": False, "error": "нет такого"}).startswith("мёртв")


def test_пустое_поле_не_оставляет_хвоста():
    """У бота нет подписчиков — кусок шаблона должен исчезнуть целиком."""
    m = all_manifests()[0]
    ответ = m.short_text({"is_alive": True, "kind": "bot", "title": "BotFather", "members_count": None})
    assert "подписчиков" not in ответ
    assert "··" not in ответ and not ответ.rstrip().endswith("·")


def test_примеры_используют_короткую_форму():
    """Ни в одном примере не должно остаться сборки query-строки."""
    for m in all_manifests():
        link = snippets.key_link(m, BASE)
        for lang, text in snippets.render(m, BASE).items():
            assert link in text, f"{m.id}/{lang}: пример не через короткую форму"
            assert "urlencode" not in text and "URLSearchParams" not in text, f"{m.id}/{lang}"


# --- ключ доступа и клиент -------------------------------------------------- #

sys.path.insert(0, str(ROOT / "clients" / "python"))

import monokeys as client_lib  # noqa: E402
from tokens import Tokens  # noqa: E402


def test_ключ_доступа_выдаётся_и_проверяется(tmp_path):
    store = Tokens(tmp_path / "t.db")
    token = store.issue(issued_to="1.2.3.4")
    assert token.startswith("kx_")
    assert store.valid(token) is True


def test_чужая_строка_не_проходит_за_ключ(tmp_path):
    store = Tokens(tmp_path / "t.db")
    store.issue()
    for мусор in ["", "kx_подделка", "Bearer", "kx_"]:
        assert store.valid(мусор) is False


def test_ключ_хранится_хэшем(tmp_path):
    """База может утечь — сам ключ из неё достать не должны."""
    path = tmp_path / "t.db"
    store = Tokens(path)
    token = store.issue()
    assert token.encode() not in path.read_bytes()


def test_нельзя_штамповать_ключи_с_одного_адреса(tmp_path):
    store = Tokens(tmp_path / "t.db")
    for _ in range(5):
        store.issue(issued_to="1.2.3.4")
    assert store.issued_today("1.2.3.4") == 5
    assert store.issued_today("5.6.7.8") == 0


def test_клиент_зовёт_короткую_форму_и_шлёт_ключ(monkeypatch):
    """Без сети: подменяем urlopen и смотрим, что клиент собрал."""
    отправлено = {}

    class FakeResponse:
        def read(self):
            return b'{"is_alive": true, "title": "Pavel Durov"}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        отправлено["url"] = request.full_url
        отправлено["auth"] = request.get_header("Authorization")
        return FakeResponse()

    monkeypatch.setattr(client_lib.urllib.request, "urlopen", fake_urlopen)

    k = client_lib.Keys(token="kx_тест", base="https://example.test")
    res = k.call("alive", "@durov", fmt="json")

    # @ и + в адресе не кодируем: так ссылка остаётся читаемой глазом
    assert отправлено["url"].startswith("https://example.test/alive/@durov?")
    assert "fmt=json" in отправлено["url"]
    assert отправлено["auth"] == "Bearer kx_тест"
    assert client_lib.Answer(res).title == "Pavel Durov"


def test_ответ_клиента_ведёт_себя_как_объект_и_как_словарь():
    res = client_lib.Answer({"is_alive": True, "title": "Pavel Durov"})
    assert res.title == "Pavel Durov" and res["title"] == "Pavel Durov"
    assert bool(res) is True
    assert bool(client_lib.Answer({"is_alive": False})) is False
    with pytest.raises(AttributeError):
        res.нет_такого_поля
