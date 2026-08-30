"""Тесты того, что ломается молча.

Три места, где ошибка не видна глазом: разбор страницы (мёртвый канал отдаёт
200 и выглядит как живой), нормализация ссылки и квота. Сеть тут не нужна —
страницы лежат в fixtures, поэтому тесты идут офлайн и за миллисекунды.
"""

from __future__ import annotations

import json
import sys
import time
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
        # хэш приглашения у Telegram длинный — коротышки вроде +AbC-123 не бывают
        ("t.me/+AbCdEfGh-123", "+AbCdEfGh-123"),
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

import docs      # noqa: E402
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
        {
            "is_alive": True,
            "kind": "channel",
            "title": "Pavel Durov",
            "members_count": 11005185,
            "members_label": "subscribers",
        }
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
    token, key_id = store.issue(issued_to="1.2.3.4")
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
    token, key_id = store.issue()
    assert token.encode() not in path.read_bytes()


def test_публичный_id_не_выдаёт_сам_ключ(tmp_path):
    """По id владелец видит и отзывает ключ, но восстановить ключ из id нельзя."""
    store = Tokens(tmp_path / "t.db")
    token, key_id = store.issue(label="форум")
    assert len(key_id) == 12
    assert key_id not in token
    assert token not in json.dumps(store.listing(), ensure_ascii=False)


def test_владелец_отзывает_по_id_не_зная_ключа(tmp_path):
    store = Tokens(tmp_path / "t.db")
    token, key_id = store.issue(label="форум")
    assert store.valid(token) is True
    assert store.revoke_by_id(key_id) is True
    assert store.valid(token) is False
    assert store.revoke_by_id(key_id) is False


def test_в_списке_видно_кому_и_сколько(tmp_path):
    """Счётчик тут для владельца — «этим пользуются, а этот мёртвый»,
    а не для того, чтобы кого-то урезать."""
    store = Tokens(tmp_path / "t.db")
    token, _ = store.issue(label="форум overclockers.ru")
    for _ in range(3):
        store.touch(token)
    строка = store.listing()[0]
    assert строка["кому"] == "форум overclockers.ru"
    assert строка["запросов"] == 3
    assert строка["отозван"] is False


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

    k = client_lib.Keys(token="kx_test123", base="https://example.test")
    res = k.call("alive", "@durov", fmt="json")

    # @ и + в адресе не кодируем: так ссылка остаётся читаемой глазом
    assert отправлено["url"].startswith("https://example.test/alive/@durov?")
    assert "fmt=json" in отправлено["url"]
    assert отправлено["auth"] == "Bearer kx_test123"
    assert client_lib.Answer(res).title == "Pavel Durov"


def test_ответ_клиента_ведёт_себя_как_объект_и_как_словарь():
    res = client_lib.Answer({"is_alive": True, "title": "Pavel Durov"})
    assert res.title == "Pavel Durov" and res["title"] == "Pavel Durov"
    assert bool(res) is True
    assert bool(client_lib.Answer({"is_alive": False})) is False
    with pytest.raises(AttributeError):
        res.нет_такого_поля


# --- полный разбор страницы ------------------------------------------------- #

def test_группа_отдаёт_участников_и_онлайн():
    res = handler.parse_preview(fixture("group.html"), "ru_python", "https://t.me/ru_python")
    assert res["kind"] == "group"
    assert res["members_label"] == "members"
    assert res["members_count"] > 1000
    assert res["online_count"] > 0


def test_у_бота_счётчик_называется_иначе():
    """У бота число лежит во ВТОРОМ блоке extra — первый занят под @username."""
    res = handler.parse_preview(fixture("bot.html"), "BotFather", "https://t.me/BotFather")
    assert res["kind"] == "bot"
    assert res["members_label"] == "monthly users"
    assert res["members_count"] > 1_000_000


def test_галочка_верификации_видна():
    channel = handler.parse_preview(fixture("alive_channel.html"), "durov", "u")
    group = handler.parse_preview(fixture("group.html"), "ru_python", "u")
    assert channel["verified"] is True
    assert group["verified"] is False


def test_спрятанный_счётчик_не_ломает_тип():
    """«no subscribers» — числа нет, но что это канал, всё равно понятно."""
    res = handler.parse_preview(fixture("hidden_count.html"), "python_beginners_chat", "u")
    assert res["kind"] == "channel"
    assert res["members_label"] == "subscribers"
    assert res["members_count"] is None


def test_есть_прямая_ссылка_в_приложение():
    res = handler.parse_preview(fixture("alive_channel.html"), "durov", "u")
    assert res["deep_link"] == "tg://resolve?domain=durov"
    assert res["has_preview"] is True
    assert res["action"] == "View in Telegram"


def test_приватный_инвайт_помечается():
    res = handler.parse_preview("", "+AbCdEf", "https://t.me/+AbCdEf", status=404)
    assert res["is_private"] is True


def test_манифест_описывает_все_поля_ответа():
    """Иначе only= и клиент не дадут забрать поле, которое ключ реально отдаёт."""
    res = handler.parse_preview(fixture("alive_channel.html"), "durov", "https://t.me/durov")
    описано = {r.name for r in all_manifests()[0].returns}
    assert set(res) <= описано, f"не описаны в манифесте: {set(res) - описано}"


def test_клиент_просит_одно_поле(monkeypatch):
    """k.alive.members_count(...) должен уходить с only= и возвращать значение."""
    отправлено = {}

    class FakeResponse:
        def read(self):
            return b'{"members_count": 11005185}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        отправлено["url"] = request.full_url
        return FakeResponse()

    k = client_lib.Keys(base="https://example.test")
    k._catalog = {"keys": [{"id": "alive", "returns": ["members_count", "kind"]}]}
    monkeypatch.setattr(client_lib.urllib.request, "urlopen", fake_urlopen)

    assert k.alive.members_count("@durov") == 11005185
    assert "only=members_count" in отправлено["url"]

    with pytest.raises(AttributeError, match="нет поля"):
        k.alive.members_cout("@durov")


# --- формы ссылок и грязный ввод (найдено живым прогоном) ------------------- #

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://t.me/durov/123", "durov"),       # ссылка на конкретный пост
        ("https://t.me/durov?single", "durov"),
        ("durov?x=1&y=2", "durov"),
        ("DUROV", "durov"),                        # регистр Telegram не различает
        ("  Durov  ", "durov"),
        ("http://telegram.me/DUROV", "durov"),
        ("https://t.me/joinchat/AAAAAEkk2WdoDrB4-Q8-gg", "+AAAAAEkk2WdoDrB4-Q8-gg"),
        ("t.me/+AbCdEfGhIjKl", "+AbCdEfGhIjKl"),
    ],
)
def test_любая_форма_ссылки_приводится_к_одному(raw, expected):
    assert handler.normalize(raw) == expected


def test_разный_регистр_это_одна_страница():
    """Иначе кэш держал бы две записи и дважды ходил бы наружу за одним и тем же."""
    assert handler.normalize("DUROV") == handler.normalize("durov") == "durov"


def test_старый_формат_приглашения_понимается():
    """t.me/joinchat/<hash> — тот же приватный вход, что и +hash."""
    имя = handler.normalize("https://t.me/joinchat/AAAAAEkk2WdoDrB4-Q8-gg")
    assert имя.startswith("+")
    res = handler.parse_preview("", имя, "https://t.me/" + имя, status=404)
    assert res["is_private"] is True


def test_заведомая_чушь_не_тратит_наружный_бюджет():
    """Длина имени у Telegram ограничена — проверяем до похода в сеть."""
    for мусор in ["a" * 300, "+short", "durov%00", "../../etc/passwd"]:
        with pytest.raises(ValueError):
            handler.normalize(мусор)


def test_хэш_приглашения_регистр_сохраняет():
    """У имени регистр не важен, у хэша важен — это разные приглашения."""
    assert handler.normalize("+AbCdEfGhIjKl") == "+AbCdEfGhIjKl"


# --- канон до кэша и повтор при сетевом сбое -------------------------------- #

import asyncio  # noqa: E402

import httpx  # noqa: E402

from runtime import Context, FetchError, TokenBucket  # noqa: E402


def test_разные_формы_ссылки_дают_один_ключ_кэша():
    """16 форм одной ссылки должны стоить один поход наружу, а не 16."""
    key = discover(ROOT / "keys")["alive"]
    формы = ["durov", "@durov", "DUROV", "  durov  ", "https://t.me/durov/123",
             "t.me/s/durov", "https://t.me/durov?single", "durov?x=1"]
    каноны = {key.canonical({"link": f})["link"] for f in формы}
    assert каноны == {"durov"}


def test_канон_отсеивает_мусор_до_сети():
    key = discover(ROOT / "keys")["alive"]
    with pytest.raises(ValueError):
        key.canonical({"link": "a" * 300})


def test_длинный_мусор_не_попадает_в_текст_ошибки():
    """Иначе трёхсотсимвольная портянка уезжает в ответ и в логи."""
    key = discover(ROOT / "keys")["alive"]
    try:
        key.canonical({"link": "a" * 300})
    except ValueError as exc:
        assert len(str(exc)) < 120


class _ПадаетПотомОтвечает:
    """Первый вызов — обрыв соединения, второй — нормальный ответ."""

    def __init__(self):
        self.вызовов = 0

    async def get(self, url, **kw):
        self.вызовов += 1
        if self.вызовов == 1:
            raise httpx.ConnectError("")
        return httpx.Response(200, text="страница", request=httpx.Request("GET", url))


def test_первый_обрыв_соединения_переспрашивается():
    """Холодный старт регулярно роняет самый первый запрос — человек не должен
    это видеть."""
    клиент = _ПадаетПотомОтвечает()
    ctx = Context(cache=None, bucket=TokenBucket(1000, 1000), client=клиент)
    resp = asyncio.run(ctx.fetch("https://t.me/durov"))
    assert клиент.вызовов == 2
    assert resp.text == "страница"


def test_ошибка_сети_не_остаётся_без_объяснения():
    """У части ошибок httpx текст пустой — «сеть не ответила: » читается как
    наша поломка."""

    class ВсегдаПадает:
        async def get(self, url, **kw):
            raise httpx.ConnectError("")

    ctx = Context(cache=None, bucket=TokenBucket(1000, 1000), client=ВсегдаПадает())
    with pytest.raises(FetchError) as exc:
        asyncio.run(ctx.fetch("https://t.me/durov"))
    assert str(exc.value).strip()


# --- закрытые группы и каналы по приглашению -------------------------------- #
#
# Фикстура обезличена: настоящий хэш приглашения и название заменены, иначе
# публичный репозиторий раздавал бы доступ в чужую закрытую группу.

def test_приглашение_живое_и_это_группа():
    """Приватность — не вид. За приглашением стоит обычная группа или канал."""
    res = handler.parse_preview(
        fixture("private_invite.html"), "+PRIVATEHASHEXAMPLE00",
        "https://t.me/+PRIVATEHASHEXAMPLE00",
    )
    assert res["is_alive"] is True
    assert res["kind"] == "group", "«Join Group» на кнопке прямо говорит, что это группа"
    assert res["is_private"] is True
    assert res["title"]


def test_у_приглашения_счётчик_спрятан_но_подпись_есть():
    res = handler.parse_preview(fixture("private_invite.html"), "+PRIVATEHASHEXAMPLE00", "u")
    assert res["members_count"] is None
    assert res["members_label"] == "members"


def test_ссылка_приглашения_ведёт_в_приложение():
    res = handler.parse_preview(fixture("private_invite.html"), "+PRIVATEHASHEXAMPLE00", "u")
    assert res["deep_link"].startswith("tg://join?invite=")
    assert res["has_preview"] is False, "у закрытой группы публичной ленты нет"


def test_пустое_описание_это_отсутствие_описания():
    """У приглашений og:description пустая строка — не должна выдаваться за текст."""
    res = handler.parse_preview(fixture("private_invite.html"), "+PRIVATEHASHEXAMPLE00", "u")
    assert res["description"] is None


def test_заявка_видна_по_кнопке():
    """«Request to Join» вместо «Join Group» — просто так не войти."""
    страница = fixture("private_invite.html").replace("Join Group", "Request to Join")
    res = handler.parse_preview(страница, "+PRIVATEHASHEXAMPLE00", "u")
    assert res["needs_request"] is True
    обычная = handler.parse_preview(fixture("private_invite.html"), "+PRIVATEHASHEXAMPLE00", "u")
    assert обычная["needs_request"] is False


def test_в_фикстуре_нет_рабочего_приглашения():
    """Страж: фикстура не должна once again начать раздавать чужой доступ."""
    текст = fixture("private_invite.html")
    assert "PRIVATEHASHEXAMPLE00" in текст
    assert "cdn4.telesco.pe/file/AVATAR.jpg" in текст


# --- защита ключа доступа --------------------------------------------------- #
#
# Каждый тест ниже закрывает дыру, которая реально открывалась на живом
# сервере, а не придуманную.

import security  # noqa: E402
from runtime import Busy  # noqa: E402
from tokens import Tokens  # noqa: E402


class ФейковыйЗапрос:
    """Минимум, который читают client_ip и over_https."""

    class _Клиент:
        def __init__(self, host):
            self.host = host

    class _Адрес:
        def __init__(self, scheme):
            self.scheme = scheme

    def __init__(self, peer="203.0.113.9", headers=None, scheme="http"):
        self.client = self._Клиент(peer)
        self.headers = headers or {}
        self.url = self._Адрес(scheme)


def test_заголовку_с_адресом_без_доверенного_прокси_не_верим(monkeypatch):
    """Пока верили — кто угодно печатал ключи пачками, обходя лимит на адрес."""
    monkeypatch.setattr(security, "TRUSTED_PROXIES", set())
    запрос = ФейковыйЗапрос(peer="203.0.113.9", headers={"x-real-ip": "10.0.0.1"})
    assert security.client_ip(запрос) == "203.0.113.9"


def test_заголовок_читается_только_от_своего_прокси(monkeypatch):
    monkeypatch.setattr(security, "TRUSTED_PROXIES", {"127.0.0.1"})
    свой = ФейковыйЗапрос(peer="127.0.0.1", headers={"x-real-ip": "10.0.0.1"})
    чужой = ФейковыйЗапрос(peer="198.51.100.7", headers={"x-real-ip": "10.0.0.1"})
    assert security.client_ip(свой) == "10.0.0.1"
    assert security.client_ip(чужой) == "198.51.100.7"


def test_ключ_вырезается_из_строки_лога():
    строка = 'GET /alive/durov?token=kx_SEKRET123&only=kind HTTP/1.1'
    вырезано = security.redact(строка)
    assert "kx_SEKRET123" not in вырезано
    assert "only=kind" in вырезано, "остальное трогать нельзя"


def test_фильтр_лога_чистит_запись():
    import logging

    запись = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
                               '%s - "%s"', ("1.2.3.4", "GET /alive/x?token=kx_ABC"), None)
    security.RedactTokens().filter(запись)
    assert "kx_ABC" not in str(запись.args)


def test_отозванный_ключ_перестаёт_работать_сразу(tmp_path):
    store = Tokens(tmp_path / "t.db")
    token, key_id = store.issue()
    assert store.valid(token) is True     # прогреваем кэш опознанных
    assert store.revoke(token) is True
    assert store.valid(token) is False, "кэш не должен воскрешать отозванный ключ"


def test_отозвать_дважды_нельзя(tmp_path):
    store = Tokens(tmp_path / "t.db")
    token, key_id = store.issue()
    assert store.revoke(token) is True
    assert store.revoke(token) is False


def test_отзыв_чужого_ключа_ничего_не_рассказывает(tmp_path):
    """Ответ на чужой и на несуществующий одинаковый — иначе это оракул."""
    store = Tokens(tmp_path / "t.db")
    store.issue()
    assert store.revoke("kx_" + "z" * 30) is False


def test_длинный_мусор_вместо_ключа_отбивается_сразу(tmp_path):
    store = Tokens(tmp_path / "t.db")
    assert store.valid("kx_" + "a" * 5000) is False
    assert store.valid("") is False
    assert store.valid("Bearer") is False


def test_память_под_опознанные_ключи_ограничена(tmp_path, monkeypatch):
    """Иначе её раздувают простой выдачей ключей."""
    import tokens as tokens_mod

    monkeypatch.setattr(tokens_mod, "KNOWN_CACHE_LIMIT", 4)
    store = Tokens(tmp_path / "t.db")
    for _ in range(12):
        store.valid(store.issue()[0])
    assert len(store._known) <= 4


def test_кран_отказывает_вместо_вечной_очереди():
    """Без предела ожидания поток запросов копит очередь: соединения висят,
    остальным достаётся таймаут вместо ответа."""
    bucket = TokenBucket(rps=1, burst=1, max_wait=0.2)
    asyncio.run(bucket.take())            # первый проходит
    with pytest.raises(Busy):
        asyncio.run(bucket.take())        # второму ждать дольше предела


def test_кран_не_держит_замок_во_сне():
    """Иначе ждущие стоят в очереди даже когда кран свободен."""
    bucket = TokenBucket(rps=100, burst=10, max_wait=5)

    async def десять_разом():
        await asyncio.gather(*(bucket.take() for _ in range(10)))

    начало = time.monotonic()
    asyncio.run(десять_разом())
    assert time.monotonic() - начало < 0.5


# --- документация по библиотеке --------------------------------------------- #

import clientdoc  # noqa: E402


def test_каждый_аргумент_клиента_описан_в_readme():
    """Аргумент появился в клиенте, а описать забыли — тест краснеет."""
    readme = (ROOT / "clients" / "python" / "README.md").read_text(encoding="utf-8")
    for имя in clientdoc.ОБЯЗАТЕЛЬНО_В_README:
        assert имя in readme, f"аргумент '{имя}' не описан в README пакета"


def test_описанные_аргументы_и_правда_есть_в_клиенте():
    """Обратная сторона: описали то, чего в коде нет."""
    import inspect

    подключение = set(inspect.signature(client_lib.Keys.__init__).parameters)
    for имя, _, _ in clientdoc.ПОДКЛЮЧЕНИЕ:
        assert имя in подключение, f"'{имя}' описан, но у Keys такого аргумента нет"

    вызов = set(inspect.signature(client_lib._Key.__call__).parameters)
    for имя, _, _ in clientdoc.ВЫЗОВ:
        assert имя.strip("*") in вызов or имя == "**params", f"'{имя}' описан, но вызов его не берёт"

    for имя, _ in clientdoc.ОШИБКИ:
        assert hasattr(client_lib, имя), f"исключения '{имя}' в клиенте нет"


def test_страница_библиотеки_рисуется():
    страница = docs.client_page(BASE)
    assert "monokeys" in страница
    for имя, _, _ in clientdoc.ПОДКЛЮЧЕНИЕ:
        assert имя in страница
    for имя, _ in clientdoc.ОШИБКИ:
        assert имя in страница


def test_аргументы_вызова_доезжают_до_адреса(monkeypatch):
    """only, fmt и timeout должны влиять на запрос, а не молча теряться."""
    отправлено = {}

    class FakeResponse:
        def read(self): return b'{"members_count": 1}'
        def __enter__(self): return self
        def __exit__(self, *exc): return False

    def fake_urlopen(request, timeout=None):
        отправлено["url"] = request.full_url
        отправлено["timeout"] = timeout
        отправлено["ua"] = request.get_header("User-agent")
        return FakeResponse()

    monkeypatch.setattr(client_lib.urllib.request, "urlopen", fake_urlopen)
    k = client_lib.Keys(base="https://example.test", timeout=20, user_agent="test/1")
    k._catalog = {"keys": [{"id": "alive", "returns": ["members_count"]}]}

    k.alive("@durov", only="members_count", timeout=3)
    assert "only=members_count" in отправлено["url"]
    assert отправлено["timeout"] == 3, "timeout вызова должен побеждать общий"
    assert отправлено["ua"] == "test/1"


def test_ошибки_разложены_по_смыслу(monkeypatch):
    """401 и 503 — разные беды, ловить их одинаково неудобно."""
    import urllib.error

    def падать(код):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, код, "нет", {}, None)
        return fake_urlopen

    k = client_lib.Keys(base="https://example.test", retries=0)
    k._catalog = {"keys": [{"id": "alive", "returns": []}]}

    monkeypatch.setattr(client_lib.urllib.request, "urlopen", падать(401))
    with pytest.raises(client_lib.AccessDenied):
        k.call("alive", "x")

    monkeypatch.setattr(client_lib.urllib.request, "urlopen", падать(503))
    with pytest.raises(client_lib.Unavailable):
        k.call("alive", "x")

    monkeypatch.setattr(client_lib.urllib.request, "urlopen", падать(400))
    with pytest.raises(client_lib.KeysError):
        k.call("alive", "x")


# --- публичный ключ: «мы сделали работу за вас» ----------------------------- #

def test_публичный_ключ_заводится_сам(tmp_path):
    """Без него обещание «просто скопируй и работает» было бы враньём."""
    store = Tokens(tmp_path / "t.db")
    assert store.public() is None
    ключ = store.ensure_public()
    assert ключ.startswith("kx_")
    assert store.ensure_public() == ключ, "второй раз новый заводить не надо"
    assert store.valid(ключ) is True


def test_публичный_ключ_единственный_кто_хранится_целиком(tmp_path):
    """Его печатают открыто — иначе отдать его было бы нечем.
    Все остальные ключи по-прежнему только хэшами."""
    store = Tokens(tmp_path / "t.db")
    публичный = store.ensure_public()
    личный, _ = store.issue(label="форум")

    сырая_база = (tmp_path / "t.db").read_bytes()
    assert публичный.encode() in сырая_база
    assert личный.encode() not in сырая_база


def test_смена_публичного_ключа_гасит_старый(tmp_path):
    """Рубильник для открытого ключа: злоупотребили — сменили."""
    store = Tokens(tmp_path / "t.db")
    старый = store.ensure_public()
    новый = store.rotate_public()

    assert новый != старый
    assert store.valid(старый) is False
    assert store.valid(новый) is True
    assert store.public() == новый


def test_клиент_берёт_публичный_ключ_когда_своего_нет(monkeypatch):
    """Keys() без всякой настройки должен ходить с публичным ключом."""
    отправлено = []

    class FakeResponse:
        def __init__(self, body): self.body = body
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *exc): return False

    def fake_urlopen(request, timeout=None):
        отправлено.append((request.full_url, request.get_header("Authorization")))
        if request.full_url.endswith("/public-token"):
            return FakeResponse(b"kx_publichniy")
        return FakeResponse(b'{"is_alive": true}')

    monkeypatch.setattr(client_lib.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("KEYS_API_KEY", raising=False)

    k = client_lib.Keys(base="https://example.test")
    k._catalog = {"keys": [{"id": "alive", "returns": ["is_alive"]}]}
    k.alive("@durov")

    спрошен_ключ = any(url.endswith("/public-token") for url, _ in отправлено)
    вызов = [pair for pair in отправлено if "/alive/" in pair[0]][0]
    assert спрошен_ключ, "клиент должен спросить публичный ключ у сервера"
    assert вызов[1] == "Bearer kx_publichniy"


def test_свой_ключ_важнее_публичного(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise AssertionError("за публичным ключом ходить не должны — есть свой")

    monkeypatch.setattr(client_lib.urllib.request, "urlopen", fake_urlopen)
    k = client_lib.Keys(token="kx_svoy", base="https://example.test")
    assert k._ключ() == "kx_svoy"


def test_публичный_ключ_спрашивается_один_раз(monkeypatch):
    запросов = []

    class FakeResponse:
        def read(self): return b"kx_publichniy"
        def __enter__(self): return self
        def __exit__(self, *exc): return False

    def fake_urlopen(request, timeout=None):
        запросов.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(client_lib.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("KEYS_API_KEY", raising=False)
    k = client_lib.Keys(base="https://example.test")
    k._ключ(); k._ключ(); k._ключ()
    assert len(запросов) == 1


# --- сами маршруты ---------------------------------------------------------- #
#
# Этих тестов не было, и из-за этого в прод уехал 500: в обработчике голого
# имени ключа обращались к request, которого не было в сигнатуре. Ошибку
# нашла живая проверка, а должен был находить тест.

from fastapi.testclient import TestClient  # noqa: E402

import app as приложение  # noqa: E402

клиент = TestClient(приложение.app)


def test_страницы_открываются():
    for путь in ["/", "/client", "/keys", "/llms.txt", "/openapi.json", "/health",
                 "/k/alive/docs", "/public-token", "/sdk/simple/"]:
        ответ = клиент.get(путь)
        assert ответ.status_code == 200, f"{путь} -> {ответ.status_code}"


def test_голое_имя_ключа_ведёт_на_его_страницу():
    """Тот самый случай, который падал пятисоткой."""
    for ключ in ["alive"]:
        ответ = клиент.get(f"/{ключ}", follow_redirects=False)
        assert ответ.status_code in (302, 307), f"{ключ} -> {ответ.status_code}"
        assert f"/k/{ключ}/docs" in ответ.headers["location"]


def test_несуществующий_ключ_не_роняет_сервер():
    ответ = клиент.get("/такого-ключа-нет")
    assert ответ.status_code == 404
    assert "keys" in ответ.json()


def test_выдача_ключей_закрыта_без_пароля_владельца():
    assert клиент.post("/token").status_code in (401, 503)
    assert клиент.get("/tokens").status_code in (401, 503)


# --- находки полного перебора аргументов ------------------------------------ #

def test_нелатинский_ключ_доступа_объясняет_себя():
    """Раньше кириллица в ключе падала UnicodeEncodeError из глубины urllib."""
    with pytest.raises(ValueError, match="только из латинских"):
        client_lib.Keys(token="kx_чужой")


def test_нелатинский_user_agent_объясняет_себя():
    with pytest.raises(ValueError, match="только из латинских"):
        client_lib.Keys(user_agent="тест/1")


def test_обычный_ключ_и_user_agent_проходят():
    k = client_lib.Keys(token="kx_ABCdef123-_", user_agent="monokeys/0.2.3")
    assert k.token == "kx_ABCdef123-_"
    assert k.user_agent == "monokeys/0.2.3"


def test_пустое_значение_в_пути_не_затирает_именованный_параметр(monkeypatch):
    """/alive/?link=durov — короткая форма подставляла пустое значение поверх
    параметра, и вызов молча превращался в «не хватает параметров».
    Найдено полным перебором аргументов.

    Сети тут нет намеренно: сам ключ подменён заглушкой, проверяется ровно
    маршрут. Первая версия этого теста ходила в Telegram и проходила лишь
    потому, что ответ лежал в кэше, — CI это и поймал.
    """
    дошло = {}

    async def подделка(params, ctx):
        дошло.update(params)
        return {"is_alive": True, "title": "заглушка"}

    monkeypatch.setattr(приложение.KEYS["alive"], "run", подделка)
    ответ = клиент.get("/alive/", params={"link": "durov", "fmt": "json"})

    assert ответ.status_code == 200, ответ.text
    assert дошло.get("link") == "durov", "именованный параметр не доехал до ключа"
