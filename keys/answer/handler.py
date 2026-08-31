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

# Кому нужно больше: погода ходит дважды подряд (сначала карта за координатами,
# потом метео), поэтому с общим пределом она проигрывала гонку и молчала.
# Кому нужно больше: эти ходят дважды подряд — сначала на карту за
# координатами, потом к самим данным.
ЖДЁМ_ОСОБО = {"weather": 14.0, "sea": 14.0, "flood": 14.0, "history": 14.0,
              "time": 14.0, "arxiv": 12.0, "drug": 12.0}

ТЕГИ = re.compile(r"<[^>]+>")
ВОПРОСИТЕЛЬНЫЕ = re.compile(
    r"^\s*(кто такой|кто такая|кто такие|что такое|что значит|что это|"
    r"где находится|где расположен\w*|где\s|расскажи про|расскажи о|"
    r"сколько будет|переведи|посчитай|"
    r"who is|who was|what is|what are|what does|where is|where are|"
    r"tell me about|how much is|how many|convert|define|definition of|"
    r"meaning of|explain)\s+", re.I
)


КИРИЛЛИЦА = re.compile(r"[а-яёА-ЯЁ]")


def язык_вопроса(вопрос: str) -> str:
    """Русскими буквами — русские источники, латиницей — английские.

    Без этого «who is Pavel Durov» шёл в русскую Википедию и находил там
    заметно меньше, чем в английской.
    """
    return "ru" if КИРИЛЛИЦА.search(вопрос) else "en"


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
    r"^\s*(погода\s+(в|на)?\s*|weather\s+(in|at)?\s*|температура\s+(в|на)?\s*|"
    r"курс\s+|обмен\s+|exchange\s+rate\s+(of|for)?\s*|rate\s+of\s+|"
    r"книга\s+|роман\s+|book\s+|novel\s+|"
    r"пакет\s+|библиотека\s+|package\s+|library\s+|pypi\s+|npm\s+|"
    r"репозиторий\s+|repo\s+|repository\s+|github\s+|"
    r"стать[ияю]\s+(про|о)?\s*|исследовани[ея]\s+(про|о)?\s*|"
    r"paper[s]?\s+(on|about)?\s*|article[s]?\s+(on|about)?\s*|"
    r"research\s+(on|about)?\s*|study\s+(on|about)?\s*|"
    r"что\s+значит\s+слово\s+|значение\s+слова\s+|слово\s+|word\s+|"
    r"который\s+час\s+(в|на)?\s*|сколько\s+времени\s+(в|на)?\s*|время\s+(в|на)\s+|"
    r"what\s+time\s+(is\s+it\s+)?(in|at)?\s*|time\s+(in|at)\s+|"
    r"столица\s+|capital\s+of\s+|население\s+|population\s+of\s+|страна\s+|"
    r"игра\s+|игру\s+|игры\s+|\bgame\s+|видеоигра\s+|"
    r"море\s+(в|у|около)?\s*|морская\s+погода\s+(в|у)?\s*|волны\s+(в|у)?\s*|"
    r"sea\s+(at|in|near)?\s*|waves?\s+(at|in|near)?\s*|"
    r"паводок\s+(в|на)?\s*|наводнение\s+(в|на)?\s*|flood\s+(in|at)?\s*|"
    r"лекарство\s+|препарат\s+|таблетки\s+|drug\s+|medicine\s+)", re.I
)


def без_темы(вопрос: str) -> str:
    """Убрать слово, называющее тему: его ищет ключ, а не источник.

    Карта не знает места «погода в Хайфе», реестр пакетов не знает пакета
    «пакет monokeys». Тему уже распознали приметами — источнику она мешает.
    """
    очищено = ТЕМЫ.sub("", вопрос).strip(" ?!.,")
    # артикль — не часть названия: «the word key» ищется как «word key»
    очищено = re.sub(r"^(the|a|an)\s+", "", очищено, flags=re.I).strip()
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
            "action": "query", "list": "search",
            "srsearch": без_темы(суть(в)),
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


ЗАГОЛОВОК = re.compile(r"^\s*=+.*=+\s*$")
ЗНАЧЕНИЕ = re.compile(
    r"(значени[ея]|meaning|noun|verb|adjective|adverb|pronoun|interjection)", re.I
)
# «key (plural keys)» — это словоформа, а не толкование: у английских статей
# она стоит первой строкой раздела, а само значение идёт следующей
СЛОВОФОРМА = re.compile(
    r"\((?:[^)]*\b(plural|countable|uncountable|comparative|superlative|"
    r"singular|third-person|past tense)\b[^)]*)\)", re.I
)
СЛУЖЕБНОЕ = re.compile(
    r"^(морфологическ|синтаксическ|произношение|семантическ|этимологи|"
    r"pronunciation|etymology|declension|conjugation|anagrams|references|"
    r"alternative forms|see also|translations)", re.I
)


def _толкование(выдержка: str) -> str:
    """Из статьи Викисловаря — само значение, а не оглавление и не грамматика.

    Викисловарь отдаёт статью целиком: разделы «Морфологические свойства»,
    «Произношение», «Значение» и так далее. Человеку нужен раздел со
    значением; всё остальное — про то, как слово склоняется, а не что оно
    означает. Поэтому сначала ищем нужный раздел и берём первую строку
    после него, и только если его нет — довольствуемся первой осмысленной.
    """
    строки = [с.strip() for с in (выдержка or "").splitlines()]

    def годится(строка: str) -> bool:
        return (bool(строка) and not ЗАГОЛОВОК.match(строка)
                and len(строка) >= 12 and not СЛОВОФОРМА.search(строка))

    # раздел со значением: у русских статей «Значение», у английских — часть речи
    for i, строка in enumerate(строки):
        if ЗАГОЛОВОК.match(строка) and ЗНАЧЕНИЕ.search(строка):
            for следом in строки[i + 1:]:
                if ЗАГОЛОВОК.match(следом):
                    break
                if годится(следом):
                    return _чисто(следом, 300)

    for строка in строки:
        if годится(строка) and not СЛУЖЕБНОЕ.match(строка):
            return _чисто(строка, 300)
    return ""


async def _wiktionary(в: str, язык: str, ctx) -> tuple[str, str]:
    очищено = без_темы(суть(в))
    слово = очищено.split()[-1] if очищено else в
    данные = json.loads((await ctx.fetch(
        f"https://{язык}.wiktionary.org/w/api.php?" + urlencode({
            # exintro тут нельзя: у статей Викисловаря нет вводного раздела,
            # и с ним ответ всегда пустой — толкование лежит дальше
            "action": "query", "prop": "extracts", "titles": слово,
            "explaintext": 1, "format": "json"}))).text)
    страницы = данные.get("query", {}).get("pages", {})
    for стр in страницы.values():
        толкование = _толкование(стр.get("extract", ""))
        if толкование:
            return толкование, f"https://{язык}.wiktionary.org/wiki/{quote(слово)}"
    return "", ""


async def _на_карте(запрос: str, язык: str, ctx) -> list:
    """Найти место, с поправкой на русские падежи.

    Карта знает «Хайфа», но не знает «Хайфе»: русских окончаний она не
    разбирает. Обрезать окончание мало — «Хайф» находит посёлок в Йемене со
    значимостью 0.133, а настоящая Хайфа имеет 0.694. Поэтому пробуем
    несколько форм и берём самое значимое место, а совсем невзрачные
    отбрасываем: лучше промолчать, чем ответить про чужую деревню.
    """
    формы = [запрос]
    if len(запрос) > 4 and " " not in запрос:
        основа = запрос[:-1]
        формы += [основа + "а", основа + "я", основа]

    лучшее: list = []
    for форма in формы:
        try:
            найдено = json.loads((await ctx.fetch(
                "https://nominatim.openstreetmap.org/search?" + urlencode({
                    "q": форма, "format": "json", "limit": 3,
                    "accept-language": язык}))).text)
        except Exception:
            continue
        for м in найдено:
            м["_вес"] = float(м.get("importance") or 0)
        лучшее += найдено
        # исходная форма нашлась уверенно — дальше искать незачем.
        # Порог именно 0.6: «Москве» даёт институт с весом 0.554, а сам
        # город находится только по форме «Москва» и весит заметно больше.
        if найдено and max(м["_вес"] for м in найдено) >= 0.6:
            break

    лучшее.sort(key=lambda м: -м["_вес"])
    if not лучшее or лучшее[0]["_вес"] < 0.2:
        return []
    return лучшее[:1]


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


# Названия валют словами. Кодов из трёх букв тут нет намеренно: их 166, и
# они подхватываются сами из таблицы курсов — писать руками нужно только то,
# что человек называет словом.
# Названия валют словами. Кодов из трёх букв тут нет намеренно: их 166, и они
# подхватываются сами из таблицы курсов — словами описываем только то, что
# человек и правда называет словом.
#
# Чего тут нет специально: «сом», «сум», «драм», «бат», «вон», «реал», «лир».
# Их основы сидят внутри обычных слов — «сумма», «драма», «батарея», «лирика»,
# — и вопрос про сумму превращался бы в вопрос про узбекский сум. Эти валюты
# по-прежнему доступны трёхбуквенным кодом.
ВАЛЮТЫ = {
    "USD": ("доллар", "dollar", "бакс", "$"),
    "EUR": ("евро", "euro", "€"),
    "ILS": ("шекел", "shekel", "₪"),
    "RUB": ("рубл", "ruble", "rouble", "₽"),
    "UAH": ("грив", "hryvnia", "₴"),      # «гривен» — не «гривн», отсюда короткая основа
    "KZT": ("тенге", "tenge", "₸"),
    "GEL": ("лари", "lari", "₾"),
    "AZN": ("манат", "manat"),
    "GBP": ("фунт", "pound", "£"),
    "JPY": ("иен", "yen", "¥"),
    "CHF": ("франк", "franc"),
    "CNY": ("юан", "yuan"),
    "PLN": ("злот", "zloty"),
    "INR": ("рупи", "rupee", "₹"),
    "THB": ("тайск", "baht"),
    "VND": ("донг", "dong"),
    "MXN": ("песо", "peso"),
    "ZAR": ("рэнд", "rand"),
    "TRY": ("турецк", "turkish lira"),
}

# Таблица курсов относительно доллара: 166 валют, без ключа, обновляется раз
# в сутки. Европейский центробанк даёт всего тридцать и не публикует рубль —
# для «все возможные валюты» этого мало.
КУРСЫ_АДРЕС = "https://open.er-api.com/v6/latest/USD"

КОД = re.compile(r"\b([A-Za-z]{3})\b")
ЧИСЛО = re.compile(r"(\d[\d\s ]*(?:[.,]\d+)?)")


async def таблица_курсов(ctx) -> tuple[dict, str]:
    """Все курсы к доллару разом. Возвращает (курсы, дата обновления)."""
    данные = json.loads((await ctx.fetch(КУРСЫ_АДРЕС)).text)
    if данные.get("result") != "success":
        return {}, ""
    дата = (данные.get("time_last_update_utc") or "")[:16]
    return данные.get("rates", {}), дата


def валюты_из(текст: str, известные: set[str] | None = None) -> list[str]:
    """Найти валюты в том порядке, в каком они написаны.

    Порядок важен: «100 долларов в шекели» — это из USD в ILS, а не наоборот.
    Слова ищем по основе (склонения), коды из трёх букв — по таблице курсов,
    поэтому доступны все 166, а не только те, что перечислены словами.
    """
    низ = текст.lower()
    попадания: list[tuple[int, str]] = []
    занято: set[str] = set()

    for код, формы in ВАЛЮТЫ.items():
        место = min((низ.find(ф) for ф in формы if ф in низ), default=-1)
        if место >= 0:
            попадания.append((место, код))
            занято.add(код)

    if известные:
        for м in КОД.finditer(текст):
            код = м.group(1).upper()
            if код in известные and код not in занято:
                попадания.append((м.start(), код))
                занято.add(код)

    return [код for _, код in sorted(попадания)]


def сумма_из(текст: str) -> float:
    """«100 долларов» -> 100. Числа с пробелами и запятой тоже считаются."""
    найдено = ЧИСЛО.search(текст)
    if not найдено:
        return 1.0
    очищено = (найдено.group(1).replace(" ", "").replace(" ", "").replace(",", "."))
    try:
        return float(очищено)
    except ValueError:
        return 1.0


def _красиво(число: float) -> str:
    """Деньги пишем без хвоста нулей: 297.28, но 100, а не 100.00.

    У мелких курсов двух знаков мало: «1 RUB = 0.01 USD» — это округление,
    а не ответ. Чем меньше число, тем больше знаков после запятой.
    """
    знаков = 2 if abs(число) >= 1 else (4 if abs(число) >= 0.01 else 6)
    строка = f"{число:,.{знаков}f}".replace(",", " ")
    return строка.rstrip("0").rstrip(".") if "." in строка else строка


async def _rates(в: str, язык: str, ctx) -> tuple[str, str]:
    """Курс и пересчёт суммы. Все 166 валют, включая рубль.

    Умеет не только «курс доллара», но и «100 долларов в шекели»,
    «100 usd to eur ils» и «50 000 рублей в тенге» — сумму подставляет сам,
    целей может быть несколько.
    """
    курсы, дата = await таблица_курсов(ctx)
    if not курсы:
        return "", ""

    коды = валюты_из(в, set(курсы))
    if len(коды) < 2:
        return "", ""

    откуда, куда = коды[0], коды[1:4]
    сумма = сумма_из(в)

    части = []
    for код in куда:
        # таблица считает от доллара, поэтому курс пары — отношение двух строк
        ставка = курсы[код] / курсы[откуда]
        части.append(f"{_красиво(сумма * ставка)} {код}")

    итог = f"{_красиво(сумма)} {откуда} = " + ", ".join(части)
    if сумма != 1:
        итог += f" · курс {_красиво(курсы[куда[0]] / курсы[откуда])}"
    return f"{итог} · на {дата}", "https://www.exchangerate-api.com/"


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


# --------------------------------------------------------------------------- #
# источники, добавленные вторым заходом
# --------------------------------------------------------------------------- #

async def _dict(в: str, язык: str, ctx) -> tuple[str, str]:
    """Английский словарь с чистыми толкованиями.

    Живёт рядом с Викисловарём, а не вместо него: у Викисловаря есть русский
    и этимология, у этого — аккуратные значения и транскрипция. Кому что
    нужно, тот то и берёт.
    """
    очищено = без_темы(суть(в))
    слово = очищено.split()[-1] if очищено else в
    if КИРИЛЛИЦА.search(слово):
        return "", ""            # словарь только английский, не притворяемся

    ответ = await ctx.fetch(f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote(слово)}")
    if ответ.status != 200:
        return "", ""
    статьи = json.loads(ответ.text)
    if not статьи:
        return "", ""

    первая = статьи[0]
    звучание = первая.get("phonetic", "")
    for значение in первая.get("meanings", []):
        for опред in значение.get("definitions", []):
            текст = опред.get("definition", "")
            if текст:
                часть = значение.get("partOfSpeech", "")
                собрано = " · ".join(x for x in [слово, звучание, часть, текст] if x)
                return _чисто(собрано, 300), f"https://dictionaryapi.dev/"
    return "", ""


async def _time(в: str, язык: str, ctx) -> tuple[str, str]:
    """Который час в этом месте. Часовой пояс берём по координатам с карты."""
    найдено = await _на_карте(без_темы(суть(в)), язык, ctx)
    if not найдено:
        return "", ""
    м = найдено[0]
    данные = json.loads((await ctx.fetch(
        "https://timeapi.io/api/Time/current/coordinate?" + urlencode({
            "latitude": м["lat"], "longitude": м["lon"]}))).text)
    место = (м.get("display_name") or "").split(",")[0]
    часы = f"{данные.get('hour', 0):02d}:{данные.get('minute', 0):02d}"
    дата = f"{данные.get('day', 0):02d}.{данные.get('month', 0):02d}.{данные.get('year', 0)}"
    пояс = данные.get("timeZone", "")
    return f"{место}: {часы}, {дата} ({пояс})", "https://timeapi.io/"


async def _country(в: str, язык: str, ctx) -> tuple[str, str]:
    """Страна: столица, валюта, население.

    Источник сменился по нужде: restcountries, который напрашивался первым,
    объявлен устаревшим и на любой запрос отвечает «This API version has been
    deprecated». Здесь другой, живой, но он знает только английские названия —
    поэтому русское название сначала переводим через карту, она это умеет.
    """
    имя = без_темы(суть(в))
    if not имя:
        return "", ""

    if КИРИЛЛИЦА.search(имя):
        # карта отдаёт названия на запрошенном языке: спрашиваем по-английски
        места = await _на_карте(имя, "en", ctx)
        if not места:
            return "", ""
        имя = (места[0].get("display_name") or "").split(",")[-1].strip() or имя

    async def спросить(путь: str) -> dict:
        try:
            ответ = await ctx.fetch(
                f"https://countriesnow.space/api/v0.1/countries/{путь}?" +
                urlencode({"country": имя}))
            return json.loads(ответ.text)
        except Exception:
            return {}

    столица, валюта = await asyncio.gather(спросить("capital/q"), спросить("currency/q"))
    данные = столица.get("data") or {}
    если_валюта = (валюта.get("data") or {}).get("currency")

    название = данные.get("name") or имя
    куски = [название]
    if данные.get("capital"):
        куски.append(f"столица {данные['capital']}")
    if если_валюта:
        куски.append(f"валюта {если_валюта}")
    if данные.get("iso3"):
        куски.append(данные["iso3"])

    if len(куски) == 1:
        return "", ""            # ничего кроме имени не узнали — это не ответ
    return " · ".join(куски), "https://countriesnow.space/"


async def _steam(в: str, язык: str, ctx) -> tuple[str, str]:
    """Игра в Steam: цена, дата выхода, платформы."""
    имя = без_темы(суть(в))
    данные = json.loads((await ctx.fetch(
        "https://store.steampowered.com/api/storesearch/?" + urlencode({
            "term": имя, "l": "russian" if язык == "ru" else "english", "cc": "IL"}))).text)
    items = данные.get("items") or []
    if not items:
        return "", ""
    и = items[0]
    куски = [и.get("name", "")]
    цена = и.get("price")
    if isinstance(цена, dict) and цена.get("final") is not None:
        куски.append(f"{цена['final'] / 100:.2f} {цена.get('currency', '')}".strip())
    elif цена is None:
        куски.append("бесплатно")
    платформы = [п for п, есть in (и.get("platforms") or {}).items() if есть]
    if платформы:
        куски.append(", ".join(платформы))
    ид = и.get("id")
    return " · ".join(к for к in куски if к), f"https://store.steampowered.com/app/{ид}"


async def _sea(в: str, язык: str, ctx) -> tuple[str, str]:
    """Море: высота волн и температура воды."""
    найдено = await _на_карте(без_темы(суть(в)), язык, ctx)
    if not найдено:
        return "", ""
    м = найдено[0]
    данные = json.loads((await ctx.fetch(
        "https://marine-api.open-meteo.com/v1/marine?" + urlencode({
            "latitude": м["lat"], "longitude": м["lon"],
            "current": "wave_height,wave_period,sea_surface_temperature"}))).text)
    сейчас = данные.get("current") or {}
    волна = сейчас.get("wave_height")
    if волна is None:
        return "", ""            # не у берега — моря тут просто нет
    место = (м.get("display_name") or "").split(",")[0]
    куски = [f"{место}: волна {волна} м"]
    if сейчас.get("wave_period") is not None:
        куски.append(f"период {сейчас['wave_period']} с")
    if сейчас.get("sea_surface_temperature") is not None:
        куски.append(f"вода {сейчас['sea_surface_temperature']}°C")
    return " · ".join(куски), "https://open-meteo.com/"


ДАТА = re.compile(r"(\d{4})-(\d{2})-(\d{2})|(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})")


def дата_из(вопрос: str) -> str:
    """«погода 2026-08-01» или «01.08.2026» -> 2026-08-01. Иначе пусто."""
    м = ДАТА.search(вопрос)
    if not м:
        return ""
    if м.group(1):
        return f"{м.group(1)}-{м.group(2)}-{м.group(3)}"
    return f"{м.group(6)}-{int(м.group(5)):02d}-{int(м.group(4)):02d}"


async def _history(в: str, язык: str, ctx) -> tuple[str, str]:
    """Какая погода была в конкретный день. Без даты в вопросе молчит."""
    день = дата_из(в)
    if not день:
        return "", ""
    найдено = await _на_карте(без_темы(суть(ДАТА.sub("", в))), язык, ctx)
    if not найдено:
        return "", ""
    м = найдено[0]
    данные = json.loads((await ctx.fetch(
        "https://archive-api.open-meteo.com/v1/archive?" + urlencode({
            "latitude": м["lat"], "longitude": м["lon"],
            "start_date": день, "end_date": день,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum"}))).text)
    д = данные.get("daily") or {}
    если_есть = lambda имя: (д.get(имя) or [None])[0]
    макс, мин = если_есть("temperature_2m_max"), если_есть("temperature_2m_min")
    if макс is None:
        return "", ""
    место = (м.get("display_name") or "").split(",")[0]
    куски = [f"{место} {день}: от {мин}°C до {макс}°C"]
    осадки = если_есть("precipitation_sum")
    if осадки:
        куски.append(f"осадки {осадки} мм")
    return " · ".join(куски), "https://open-meteo.com/"


async def _flood(в: str, язык: str, ctx) -> tuple[str, str]:
    """Расход воды в реке рядом — грубый признак паводка."""
    найдено = await _на_карте(без_темы(суть(в)), язык, ctx)
    if not найдено:
        return "", ""
    м = найдено[0]
    данные = json.loads((await ctx.fetch(
        "https://flood-api.open-meteo.com/v1/flood?" + urlencode({
            "latitude": м["lat"], "longitude": м["lon"],
            "daily": "river_discharge"}))).text)
    поток = ((данные.get("daily") or {}).get("river_discharge") or [None])[0]
    if поток is None:
        return "", ""
    место = (м.get("display_name") or "").split(",")[0]
    return f"{место}: расход реки {поток} м³/с", "https://open-meteo.com/"


async def _drug(в: str, язык: str, ctx) -> tuple[str, str]:
    """Лекарство: для чего, предупреждения. Источник — FDA США."""
    имя = без_темы(суть(в))
    if КИРИЛЛИЦА.search(имя):
        return "", ""            # у FDA только английские названия
    ответ = await ctx.fetch("https://api.fda.gov/drug/label.json?" + urlencode({
        "search": f"openfda.brand_name:{имя} OR openfda.generic_name:{имя}", "limit": 1}))
    if ответ.status != 200:
        return "", ""
    записи = json.loads(ответ.text).get("results") or []
    if не_пусто := (записи[0] if записи else None):
        for поле in ("indications_and_usage", "purpose", "description"):
            текст = (не_пусто.get(поле) or [""])[0]
            if текст:
                return _чисто(f"{имя}: {текст}", 350), "https://open.fda.gov/"
    return "", ""


ИСТОЧНИКИ = {
    "wiki": _wiki, "ddg": _ddg, "wikidata": _wikidata, "wiktionary": _wiktionary,
    "dict": _dict, "osm": _osm, "weather": _weather, "sea": _sea, "history": _history,
    "flood": _flood, "time": _time, "country": _country, "rates": _rates,
    "steam": _steam, "drug": _drug, "crossref": _crossref, "arxiv": _arxiv,
    "books": _books, "pypi": _pypi, "npm": _npm, "github": _github,
}
ВСЕ = list(ИСТОЧНИКИ)

# Порядок доверия: чем выше, тем раньше берём его ответ как лучший.
# Точный факт впереди общей справки — «погода 24°C» полезнее, чем статья
# про город, если спрашивали именно погоду.
# Порядок доверия: сначала те, кто отвечает ровно на заданный вопрос, потом
# общая справка. Внутри — от узкого к широкому.
ДОВЕРИЕ = ["history", "sea", "flood", "weather", "time", "rates", "steam", "drug",
           "pypi", "npm", "github", "country", "osm", "dict", "wiktionary",
           "wiki", "ddg", "wikidata", "books", "crossref", "arxiv"]

ВСЕГДА = ["wiki", "ddg", "wikidata"]  # общая справка спрашивается при любом вопросе

ПРИМЕТЫ = [
    (r"погода|температур|weather|temperature|forecast", ["weather"]),
    # список валют собираем из того же ВАЛЮТЫ, что и разбор: иначе примета
    # и разбор разъедутся, и вопрос про тенге просто не дойдёт до курсов
    # Коды валют строчными («100 usd to eur») ловим отдельно: в ВАЛЮТЫ они
    # словами не описаны, а вопрос без них уходил в общую справку и получал
    # статью про минимальные зарплаты вместо курса.
    ("|".join([r"курс", r"обмен", r"exchange", r"convert",
               r"\b(usd|eur|ils|rub|gbp|jpy|chf|cny|uah|kzt|gel|amd|try|pln|inr)\b"]
              + [ф for формы in ВАЛЮТЫ.values() for ф in формы if ф.isalpha()]),
     ["rates"]),
    (r"\bгде\b|город|улиц|адрес|страна|координат|"
     r"\bwhere\b|\bcity\b|street|address|country|location", ["osm"]),
    (r"пакет|библиотек|package|library|pypi|pip install", ["pypi", "npm"]),
    (r"\bnpm\b|node|javascript|typescript", ["npm"]),
    (r"репозитор|исходник|github|\brepo\b|repository|source code", ["github"]),
    (r"стать|исследован|препринт|наук|"
     r"paper|article|research|study|doi|arxiv|preprint|science", ["crossref", "arxiv"]),
    (r"книг|роман|автор|\bbook\b|novel|author|isbn", ["books"]),
    (r"что значит|значение слова|перевод слова|"
     r"define|definition|meaning of|\bword\b", ["wiktionary", "dict"]),
    (r"который час|сколько времени|время в|часовой пояс|"
     r"what time|time in|timezone|time zone", ["time"]),
    (r"столиц|населен|площадь стран|валюта стран|"
     r"capital of|population of|which country", ["country"]),
    (r"\bигра\b|\bигры\b|steam|поиграть|видеоигр|"
     r"\bgame\b|\bgames\b|videogame", ["steam"]),
    (r"море|морск|волн|прибой|\bsea\b|wave|marine|surf", ["sea"]),
    (r"паводок|наводнен|разлив реки|flood|river discharge", ["flood"]),
    (r"лекарств|таблетк|препарат|дозировк|побочн|"
     r"\bdrug\b|medicine|dosage|side effect", ["drug"]),
]


def подобрать(вопрос: str) -> list[str]:
    """Кого спрашивать, если человек не указал сам.

    Общая справка идёт всегда, к ней добавляются источники по приметам в
    самом вопросе. Спрашивать все тринадцать на каждый вопрос — расточительно
    и медленно, а половина из них к теме отношения не имеет.
    """
    выбор = list(ВСЕГДА)
    for примета, добавить in ПРИМЕТЫ:
        if re.search(примета, вопрос, re.I):
            выбор += [и for и in добавить if и not in выбор]

    # Код валюты пишут заглавными: «100 UZS в KGS». Искать любые три буквы
    # без учёта регистра нельзя — тогда «who is…» и «the…» тоже стали бы
    # вопросом про курсы.
    if "rates" not in выбор and len(re.findall(r"\b[A-Z]{3}\b", вопрос)) >= 2:
        выбор.append("rates")
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

    язык = (params.get("lang") or язык_вопроса(вопрос)).strip().lower()
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
                ИСТОЧНИКИ[имя](вопрос, язык, ctx),
                timeout=ЖДЁМ_ОСОБО.get(имя, ЖДЁМ_ИСТОЧНИК))
        except Exception:
            # упавший или задумавшийся источник не роняет весь ответ:
            # остальные уже принесли своё, три ответа лучше одной ошибки
            return имя, ("", "")

    собрано = dict(await asyncio.gather(*(спросить(и) for и in выбор)))

    # общая справка всегда что-нибудь находит — и потому обязана доказать,
    # что нашла про то самое; точные источники доказывать не должны, они
    # отвечают ровно на то, о чём их спросили
    # Общая справка всегда что-нибудь находит — и потому обязана доказать,
    # что нашла про то самое. Послаблений для чужого языка тут нет намеренно:
    # спросили английскую Википедию русским словом «Хайфа» — она отдаёт
    # футболиста, и без проверки он прошёл бы за честный ответ. Молчание
    # правильнее: несовпадение языка с источником — ошибка спросившего.
    ОБЩИЕ = {"wiki", "ddg", "wikidata"}
    ответы = {
        имя: (текст if имя not in ОБЩИЕ or похоже(вопрос, текст) else "")
        for имя, (текст, _) in собрано.items()
    }
    ссылки = [с for _, с in собрано.values() if с]

    # Источник, выбранный по примете в вопросе, отвечает именно на то, о чём
    # спросили, — он и должен побеждать общую справку. Спросили про значение
    # слова, а лучшей оказывалась Википедия со статьёй «Значение»: формально
    # похоже, по делу мимо.
    по_примете = [и for и in ДОВЕРИЕ if и in выбор and и not in ВСЕГДА]
    остальные = [и for и in ДОВЕРИЕ if и not in по_примете]

    лучший, откуда = "", ""
    for имя in по_примете + остальные:
        if ответы.get(имя):
            лучший, откуда = ответы[имя], имя
            break

    # Если источники названы явно и их несколько — показываем всех, кто
    # ответил. Человек перечислил их не для того, чтобы увидеть одного:
    # он просил именно эти, значит хочет сравнить.
    if params.get("sources") and len(выбор) > 1:
        собранное = [f"{имя}: {ответы[имя]}" for имя in выбор if ответы.get(имя)]
        if len(собранное) > 1:
            лучший = " · ".join(собранное)
            откуда = ", ".join(имя for имя in выбор if ответы.get(имя))

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
