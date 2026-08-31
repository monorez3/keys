"""Ключ «ответ»: один вопрос — все источники, у которых есть открытый интерфейс.

Не обёртка над одним сайтом и не поисковик. Вопрос уходит параллельно в
несколько мест, каждое отвечает своим полем, отдельно называется лучший ответ
и ссылки, которыми его можно проверить.

Что здесь есть и чего намеренно нет:

* нет моделей. Все бесплатные упираются в лимит, а ответ модели нечем
  проверить — первые же источники отвечают ссылкой, которую видно;
* нет выдачи поисковиков. Её нельзя брать по их правилам, и IP у сервера
  один на все продукты: бан положит всё разом;
* есть только источники с настоящим открытым интерфейсом, без ключей,
  которые сами разрешают к себе ходить.

Если `sources` не указан, ключ смотрит на вопрос и сам решает, кого
спрашивать: про город — карту, про валюту — курсы, про пакет — реестры
пакетов. Общая справка спрашивается всегда.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from urllib.parse import quote, urlencode

# Сколько ждём один источник. Медленный не должен держать быстрых: лучше
# отдать четыре ответа из пяти, чем заставить человека ждать самого вялого.
ЖДЁМ_ИСТОЧНИК = 8.0

ТЕГИ = re.compile(r"<[^>]+>")
ВОПРОСИТЕЛЬНЫЕ = re.compile(
    r"^\s*(кто такой|кто такая|кто такие|что такое|что значит|что это|"
    r"где находится|где расположен\w*|где\s|расскажи про|расскажи о|"
    r"who is|what is|where is|define)\s+", re.I
)


def без_ударений(текст: str) -> str:
    """«Па́вел» -> «Павел».

    Википедия расставляет ударения отдельными знаками поверх букв. Для глаза
    разницы нет, для сравнения строк — полная: «Па́вел» и «Павел» не равны.
    """
    return "".join(з for з in unicodedata.normalize("NFD", текст)
                   if unicodedata.category(з) != "Mn")


def _чисто(текст: str | None, предел: int = 400) -> str:
    if not текст:
        return ""
    текст = ТЕГИ.sub(" ", str(текст))
    текст = re.sub(r"\s+", " ", текст).strip()
    return текст[:предел].rstrip() + ("…" if len(текст) > предел else "")


ТЕМЫ = re.compile(
    r"^\s*(погода\s+(в|на)?|weather\s+in|курс\s+|книга\s+|роман\s+|book\s+|"
    r"пакет\s+|библиотека\s+|package\s+|pypi\s+|npm\s+|репозиторий\s+|github\s+|"
    r"стать[ияю]\s+(про|о)?\s*|исследовани[ея]\s+(про|о)?\s*|"
    r"что\s+значит\s+слово\s+|значение\s+слова\s+|слово\s+)", re.I
)


def без_темы(вопрос: str) -> str:
    """Убрать слово, называющее тему: его ищет ключ, а не источник.

    Карта не знает места «погода в Хайфе», реестр пакетов не знает пакета
    «пакет monokeys». Тему уже распознали приметами — источнику она мешает.
    """
    очищено = ТЕМЫ.sub("", вопрос).strip(" ?!.,")
    return очищено or вопрос


def суть(вопрос: str) -> str:
    """«кто такой Павел Дуров» -> «Павел Дуров».

    Половина источников ищет сущность, а не фразу: на полный вопрос они
    честно отвечают «ничего не нашёл». Проверено на DuckDuckGo — по фразе
    пусто, по имени полная справка.
    """
    return ВОПРОСИТЕЛЬНЫЕ.sub("", вопрос).strip(" ?!.") or вопрос


# --------------------------------------------------------------------------- #
# источники: каждый возвращает (текст, ссылка)
# --------------------------------------------------------------------------- #

async def _wiki(в: str, язык: str, ctx) -> tuple[str, str]:
    найдено = json.loads((await ctx.fetch(
        f"https://{язык}.wikipedia.org/w/api.php?" + urlencode({
            "action": "query", "list": "search", "srsearch": без_темы(суть(в)),
            "srlimit": 1, "format": "json"})
    )).text).get("query", {}).get("search", [])
    if not найдено:
        return "", ""
    статья = quote(найдено[0]["title"], safe="")
    данные = json.loads((await ctx.fetch(
        f"https://{язык}.wikipedia.org/api/rest_v1/page/summary/{статья}")).text)
    return (_чисто(данные.get("extract")),
            данные.get("content_urls", {}).get("desktop", {}).get("page", ""))


async def _ddg(в: str, язык: str, ctx) -> tuple[str, str]:
    данные = json.loads((await ctx.fetch("https://api.duckduckgo.com/?" + urlencode({
        "q": без_темы(суть(в)), "format": "json", "no_html": 1, "skip_disambig": 1}))).text)
    текст = данные.get("AbstractText") or данные.get("Answer") or ""
    if not текст and данные.get("RelatedTopics"):
        первый = данные["RelatedTopics"][0]
        текст = первый.get("Text", "") if isinstance(первый, dict) else ""
    return _чисто(текст), данные.get("AbstractURL", "")


async def _wikidata(в: str, язык: str, ctx) -> tuple[str, str]:
    найдено = json.loads((await ctx.fetch(
        "https://www.wikidata.org/w/api.php?" + urlencode({
            "action": "wbsearchentities", "search": без_темы(суть(в)), "language": язык,
            "uselang": язык, "limit": 1, "format": "json"}))).text).get("search", [])
    if not найдено:
        return "", ""
    п = найдено[0]
    описание = п.get("description", "")
    текст = f"{п.get('label', '')} — {описание}" if описание else п.get("label", "")
    return _чисто(текст), f"https://www.wikidata.org/wiki/{п.get('id', '')}"


async def _wiktionary(в: str, язык: str, ctx) -> tuple[str, str]:
    очищено = без_темы(суть(в))
    слово = очищено.split()[-1] if очищено else в
    данные = json.loads((await ctx.fetch(
        f"https://{язык}.wiktionary.org/w/api.php?" + urlencode({
            "action": "query", "prop": "extracts", "titles": слово,
            "explaintext": 1, "exintro": 1, "format": "json"}))).text)
    страницы = данные.get("query", {}).get("pages", {})
    for стр in страницы.values():
        if стр.get("extract"):
            return _чисто(стр["extract"], 300), f"https://{язык}.wiktionary.org/wiki/{quote(слово)}"
    return "", ""


async def _на_карте(запрос: str, язык: str, ctx) -> list:
    """Найти место, с поправкой на падежи.

    Карта знает «Хайфа», но не знает «Хайфе». Русские окончания она не
    разбирает, зато ищет по началу слова — поэтому при неудаче пробуем
    ещё раз, отрезав окончание. Проверено: «Хайфе» пусто, «Хайф» находит.
    """
    async def спросить(q: str) -> list:
        return json.loads((await ctx.fetch(
            "https://nominatim.openstreetmap.org/search?" + urlencode({
                "q": q, "format": "json", "limit": 1,
                "accept-language": язык}))).text)

    найдено = await спросить(запрос)
    if not найдено and len(запрос) > 4 and " " not in запрос:
        найдено = await спросить(запрос[:-1])
    return найдено


async def _osm(в: str, язык: str, ctx) -> tuple[str, str]:
    найдено = await _на_карте(без_темы(суть(в)), язык, ctx)
    if not найдено:
        return "", ""
    м = найдено[0]
    return (_чисто(м.get("display_name")),
            f"https://www.openstreetmap.org/?mlat={м.get('lat')}&mlon={м.get('lon')}")


async def _weather(в: str, язык: str, ctx) -> tuple[str, str]:
    """Погода требует координат — сначала находим место на карте."""
    место = без_темы(суть(в))
    найдено = await _на_карте(место, язык, ctx)
    if not найдено:
        return "", ""
    ш, д = найдено[0]["lat"], найдено[0]["lon"]
    п = json.loads((await ctx.fetch("https://api.open-meteo.com/v1/forecast?" + urlencode({
        "latitude": ш, "longitude": д,
        "current": "temperature_2m,wind_speed_10m,relative_humidity_2m"}))).text)
    т = п.get("current", {})
    if not т:
        return "", ""
    return (f"{место}: {т.get('temperature_2m')}°C, ветер {т.get('wind_speed_10m')} м/с, "
            f"влажность {т.get('relative_humidity_2m')}%", "https://open-meteo.com/")


async def _rates(в: str, язык: str, ctx) -> tuple[str, str]:
    """Курс валют по данным Европейского центробанка."""
    # по-русски валюты склоняются: «доллара к шекелю» — это USD и ILS,
    # а точный список форм пришлось бы писать бесконечно
    валюты = re.findall(r"\b([A-Z]{3}|доллар\w*|евро|шекел\w*|рубл\w*|фунт\w*|иен\w*)\b",
                        в, re.I)
    основы = {"доллар": "USD", "евро": "EUR", "шекел": "ILS", "рубл": "RUB",
              "фунт": "GBP", "иен": "JPY"}

    def код(слово: str) -> str:
        низ = слово.lower()
        for основа, знак in основы.items():
            if низ.startswith(основа):
                return знак
        return слово.upper()

    коды = [код(с) for с in валюты][:2]
    if len(коды) < 2:
        return "", ""
    # frankfurter.app отвечает редиректом на .dev — ходим сразу куда надо
    п = json.loads((await ctx.fetch("https://api.frankfurter.dev/v1/latest?" + urlencode({
        "from": коды[0], "to": коды[1]}))).text)
    ставка = п.get("rates", {}).get(коды[1])
    if ставка is None:
        return "", ""
    return f"1 {коды[0]} = {ставка} {коды[1]} (на {п.get('date')}, ЕЦБ)", "https://frankfurter.dev/"


async def _crossref(в: str, язык: str, ctx) -> tuple[str, str]:
    п = json.loads((await ctx.fetch("https://api.crossref.org/works?" + urlencode({
        "query": без_темы(суть(в)), "rows": 1, "select": "title,author,issued,URL"}))).text)
    статьи = п.get("message", {}).get("items", [])
    if not статьи:
        return "", ""
    с = статьи[0]
    год = с.get("issued", {}).get("date-parts", [[None]])[0][0]
    авторы = ", ".join(
        f"{a.get('family','')}" for a in с.get("author", [])[:3] if a.get("family"))
    название = (с.get("title") or [""])[0]
    return _чисто(f"{название} — {авторы or 'без авторов'}, {год or 'год неизвестен'}"), с.get("URL", "")


async def _arxiv(в: str, язык: str, ctx) -> tuple[str, str]:
    текст = (await ctx.fetch("https://export.arxiv.org/api/query?" + urlencode({
        "search_query": f"all:{без_темы(суть(в))}", "max_results": 1}))).text
    название = re.search(r"<entry>.*?<title>(.*?)</title>", текст, re.S)
    ссылка = re.search(r"<entry>.*?<id>(.*?)</id>", текст, re.S)
    if not название:
        return "", ""
    сводка = re.search(r"<summary>(.*?)</summary>", текст, re.S)
    итог = _чисто(название.group(1)) + (" — " + _чисто(сводка.group(1), 200) if сводка else "")
    return итог, ссылка.group(1).strip() if ссылка else ""


async def _books(в: str, язык: str, ctx) -> tuple[str, str]:
    п = json.loads((await ctx.fetch("https://openlibrary.org/search.json?" + urlencode({
        "q": без_темы(суть(в)), "limit": 1}))).text)
    книги = п.get("docs", [])
    if not книги:
        return "", ""
    к = книги[0]
    автор = ", ".join(к.get("author_name", [])[:2])
    return (_чисто(f"{к.get('title','')} — {автор or 'автор неизвестен'}, "
                   f"{к.get('first_publish_year', 'год неизвестен')}"),
            f"https://openlibrary.org{к.get('key', '')}")


async def _pypi(в: str, язык: str, ctx) -> tuple[str, str]:
    имя = без_темы(суть(в)).split()[0]
    ответ = await ctx.fetch(f"https://pypi.org/pypi/{quote(имя)}/json")
    if ответ.status != 200:
        return "", ""
    и = json.loads(ответ.text)["info"]
    return (_чисто(f"{и['name']} {и['version']} — {и.get('summary') or 'без описания'}"),
            f"https://pypi.org/project/{и['name']}/")


async def _npm(в: str, язык: str, ctx) -> tuple[str, str]:
    имя = без_темы(суть(в)).split()[0]
    ответ = await ctx.fetch(f"https://registry.npmjs.org/{quote(имя, safe='@/')}/latest")
    if ответ.status != 200:
        return "", ""
    п = json.loads(ответ.text)
    return (_чисто(f"{п.get('name')} {п.get('version')} — "
                   f"{п.get('description') or 'без описания'}"),
            f"https://www.npmjs.com/package/{п.get('name')}")


async def _github(в: str, язык: str, ctx) -> tuple[str, str]:
    п = json.loads((await ctx.fetch("https://api.github.com/search/repositories?" + urlencode({
        "q": без_темы(суть(в)), "per_page": 1}))).text)
    репо = п.get("items", [])
    if not репо:
        return "", ""
    р = репо[0]
    return (_чисто(f"{р['full_name']} ★{р['stargazers_count']} — "
                   f"{р.get('description') or 'без описания'}"), р["html_url"])


def похоже(вопрос: str, ответ: str) -> bool:
    """Правда ли ответ про то, о чём спрашивали.

    Википедия всегда что-нибудь находит: на «погоду в Хайфе» она выдала
    театрального режиссёра, на «курс доллара» — египетский фунт. Уверенный
    ответ не по делу хуже честного «не нашёл», поэтому требуем, чтобы у
    вопроса и ответа было хоть одно общее значимое слово.
    """
    def слова(текст: str) -> set[str]:
        return {с[:5].lower() for с in re.findall(r"\w{4,}", без_ударений(текст))}

    спросили = слова(без_темы(суть(вопрос)))
    return not спросили or bool(спросили & слова(ответ))


ИСТОЧНИКИ = {
    "wiki": _wiki, "ddg": _ddg, "wikidata": _wikidata, "wiktionary": _wiktionary,
    "osm": _osm, "weather": _weather, "rates": _rates, "crossref": _crossref,
    "arxiv": _arxiv, "books": _books, "pypi": _pypi, "npm": _npm, "github": _github,
}
ВСЕ = list(ИСТОЧНИКИ)

# Порядок доверия: чем выше, тем раньше берём его ответ как лучший.
# Точный факт впереди общей справки — «погода 24°C» полезнее, чем статья
# про город, если спрашивали именно погоду.
ДОВЕРИЕ = ["weather", "rates", "pypi", "npm", "github", "osm", "wiki", "ddg",
           "wikidata", "wiktionary", "books", "crossref", "arxiv"]

ВСЕГДА = ["wiki", "ddg", "wikidata"]  # общая справка спрашивается при любом вопросе

ПРИМЕТЫ = [
    (r"погода|weather|температур", ["weather"]),
    (r"курс|\b(usd|eur|ils|rub|gbp)\b|доллар|евро|шекел|рубл", ["rates"]),
    (r"\bгде\b|город|улиц|адрес|страна|where is", ["osm"]),
    (r"пакет|библиотек|package|pypi|pip install", ["pypi", "npm"]),
    (r"\bnpm\b|node|javascript", ["npm"]),
    (r"репозитор|github|исходник", ["github"]),
    (r"стать|исследован|paper|doi|препринт|arxiv|наук", ["crossref", "arxiv"]),
    (r"книг|роман|book|автор", ["books"]),
    (r"что значит|значение слова|define|перевод слова", ["wiktionary"]),
]


def подобрать(вопрос: str) -> list[str]:
    """Кого спрашивать, если человек не указал сам.

    Общая справка идёт всегда, к ней добавляются источники по приметам в
    самом вопросе. Спрашивать все тринадцать на каждый вопрос — расточительно
    и медленно, а половина из них к теме отношения не имеет.
    """
    выбор = list(ВСЕГДА)
    низ = вопрос.lower()
    for примета, добавить in ПРИМЕТЫ:
        if re.search(примета, низ):
            выбор += [и for и in добавить if и not in выбор]
    return выбор


def выбрать_источники(строка: str, вопрос: str) -> list[str]:
    """'wiki,osm' -> список; пусто -> подбираем сами; 'all' -> все."""
    строка = (строка or "").strip().lower()
    if not строка:
        return подобрать(вопрос)
    if строка in ("all", "все", "*"):
        return list(ВСЕ)

    выбор = [ч for ч in re.split(r"[,\s;]+", строка) if ч]
    чужие = [ч for ч in выбор if ч not in ИСТОЧНИКИ]
    if чужие:
        raise ValueError(f"источника {чужие[0]!r} нет; есть: {', '.join(ВСЕ)}")
    return выбор


def canonical(params: dict) -> dict:
    вопрос = re.sub(r"\s+", " ", (params.get("q") or "").strip())
    if not вопрос:
        raise ValueError("пустой вопрос")
    if len(вопрос) > 300:
        raise ValueError("вопрос длиннее 300 символов — это уже не вопрос")

    язык = (params.get("lang") or "ru").strip().lower()
    if язык not in ("ru", "en"):
        raise ValueError(f"язык {язык!r} не поддержан; есть: ru, en")

    # порядок источников на ответ не влияет — приводим к канону, иначе
    # «wiki,ddg» и «ddg, wiki» станут двумя записями кэша
    источники = ",".join(sorted(выбрать_источники(params.get("sources", ""), вопрос)))
    return params | {"q": вопрос, "lang": язык, "sources": источники}


async def run(params: dict, ctx) -> dict:
    вопрос = params["q"]
    язык = params.get("lang", "ru")
    выбор = выбрать_источники(params.get("sources", ""), вопрос)

    async def спросить(имя: str):
        try:
            return имя, await asyncio.wait_for(
                ИСТОЧНИКИ[имя](вопрос, язык, ctx), timeout=ЖДЁМ_ИСТОЧНИК)
        except Exception:
            # упавший или задумавшийся источник не роняет весь ответ:
            # остальные уже принесли своё, три ответа лучше одной ошибки
            return имя, ("", "")

    собрано = dict(await asyncio.gather(*(спросить(и) for и in выбор)))

    # общая справка всегда что-нибудь находит — и потому обязана доказать,
    # что нашла про то самое; точные источники доказывать не должны, они
    # отвечают ровно на то, о чём их спросили
    ОБЩИЕ = {"wiki", "ddg", "wikidata"}
    ответы = {
        имя: (текст if имя not in ОБЩИЕ or похоже(вопрос, текст) else "")
        for имя, (текст, _) in собрано.items()
    }
    ссылки = [с for _, с in собрано.values() if с]

    лучший, откуда = "", ""
    for имя in ДОВЕРИЕ:
        if ответы.get(имя):
            лучший, откуда = ответы[имя], имя
            break

    итог = {
        "ok": bool(лучший),
        "answer": лучший or "ничего не нашлось ни в одном источнике",
        "source": откуда,
        "asked": ", ".join(выбор),
        "found": ", ".join(и for и in выбор if ответы.get(и)),
        "links": " · ".join(ссылки),
    }
    итог.update({и: ответы.get(и, "") for и in ВСЕ})
    return итог
