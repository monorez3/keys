"""Ключ «crypto»: цена монеты и что с ней за сутки.

Отдельно от обычных курсов намеренно. У обычных валют курс — это одно число
от центробанка, меняется раз в день и никого не удивляет. У монеты есть цена
в нескольких валютах сразу, движение за сутки, объём торгов и капитализация —
и меняется всё это ежеминутно. Складывать это в один ответ с «100 долларов в
шекели» значило бы делать хуже и тем, и другим.

Источник — CoinGecko: открытый, без ключа, знает больше десяти тысяч монет.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode

# В каких валютах показываем цену, если человек не попросил другую.
ПО_УМОЛЧАНИЮ = ["usd", "eur", "ils"]

# Названия монет словами: коды вроде BTC монеты и так понимают, а вот «биткоин»
# нужно перевести. Список короткий намеренно — остальное ищется поиском.
СЛОВАМИ = {
    "bitcoin": ("биткоин", "биток", "btc"),
    "ethereum": ("эфир", "эфириум", "eth"),
    "tether": ("тезер", "юсдт", "usdt"),
    "solana": ("солана", "sol"),
    "dogecoin": ("додж", "догикоин", "doge"),
    # у Toncoin имя в CoinGecko не «ton»: под этим кодом там сидит другая
    # монета, и поиск честно отдавал её первой
    "the-open-network": ("тон", "тонкоин", "toncoin"),
    "litecoin": ("лайткоин", "ltc"),
    "monero": ("монеро", "xmr"),
}

ВАЛЮТЫ_СЛОВАМИ = {
    "usd": ("доллар", "dollar", "usd", "$"),
    "eur": ("евро", "euro", "eur", "€"),
    "ils": ("шекел", "shekel", "ils", "₪"),
    "rub": ("рубл", "ruble", "rub", "₽"),
    "uah": ("грив", "hryvnia", "uah"),
    "kzt": ("тенге", "tenge", "kzt"),
    "gbp": ("фунт", "pound", "gbp"),
}

ШУМ = re.compile(
    r"\b(курс|цена|стоимость|сколько стоит|почём|price|cost|rate|of|в|to|за)\b", re.I)


def разобрать(вопрос: str) -> tuple[str, list[str]]:
    """«курс биткоина в шекелях» -> ('bitcoin', ['ils']).

    Валюту выдёргиваем первой: иначе слово «шекелях» осталось бы в названии
    монеты и поиск ушёл бы искать несуществующий «биткоин в шекелях».
    """
    низ = (вопрос or "").strip().lower()
    if not низ:
        raise ValueError("пустой запрос: назовите монету")

    валюты = []
    for код, формы in ВАЛЮТЫ_СЛОВАМИ.items():
        if any(ф in низ for ф in формы):
            валюты.append(код)
            for ф in формы:
                низ = низ.replace(ф, " ")

    низ = ШУМ.sub(" ", низ)
    имя = " ".join(низ.split()).strip(" ?!.,")

    for монета, формы in СЛОВАМИ.items():
        if any(ф in имя for ф in формы):
            return монета, валюты or ПО_УМОЛЧАНИЮ

    if not имя:
        raise ValueError("не понял, какая монета")
    return имя, валюты or ПО_УМОЛЧАНИЮ


class Занято(RuntimeError):
    """Источник ограничил частоту. Это не «монета не нашлась», и врать нельзя."""


def _проверить(ответ) -> dict:
    """Разобрать ответ CoinGecko, отличая занятость от отсутствия монеты."""
    if ответ.status == 429:
        raise Занято("CoinGecko ограничил частоту запросов, попробуйте через минуту")
    if ответ.status != 200:
        raise Занято(f"CoinGecko ответил {ответ.status}")
    return json.loads(ответ.text)


async def _найти_монету(имя: str, ctx) -> dict:
    """Название -> запись CoinGecko. Точное совпадение важнее похожего."""
    ответ = await ctx.fetch("https://api.coingecko.com/api/v3/search?" + urlencode({"query": имя}))
    монеты = _проверить(ответ).get("coins") or []
    if not монеты:
        return {}
    точные = [м for м in монеты
              if имя in (м.get("id", ""), (м.get("symbol") or "").lower(),
                         (м.get("name") or "").lower())]
    return (точные or монеты)[0]


def _красиво(число: float) -> str:
    """У монет цены разного порядка: от десятков тысяч до тысячных долей."""
    если = abs(число)
    знаков = 2 if если >= 1 else (4 if если >= 0.01 else 8)
    строка = f"{число:,.{знаков}f}".replace(",", " ")
    return строка.rstrip("0").rstrip(".") if "." in строка else строка


def canonical(params: dict) -> dict:
    """Канон до кэша. Явно переданный vs= сильнее угаданного из вопроса —
    иначе аргумент молча ничего не менял."""
    монета, из_вопроса = разобрать(params.get("q", ""))
    явно = [в.strip().lower() for в in (params.get("vs") or "").split(",") if в.strip()]
    return params | {"q": монета, "vs": ",".join(sorted(явно or из_вопроса))}


async def run(params: dict, ctx) -> dict:
    try:
        return await _посчитать(params, ctx)
    except Занято as почему:
        return {"ok": False, "error": str(почему)}


async def _посчитать(params: dict, ctx) -> dict:
    монета, валюты = разобрать(params["q"])
    if params.get("vs"):
        валюты = [в.strip().lower() for в in params["vs"].split(",") if в.strip()]

    # если название узнали по словарю, имя монеты уже точное — искать незачем,
    # а поиск ещё и путает монеты с одинаковым кодом
    известная = монета in СЛОВАМИ
    # код монеты берём из словаря: там он всегда стоит последним среди форм
    код = next((ф for ф in СЛОВАМИ.get(монета, ()) if ф.isascii()), "")
    запись = ({"id": монета, "symbol": код} if известная
              else await _найти_монету(монета, ctx))
    if not запись:
        return {"ok": False, "error": f"монета {монета!r} не нашлась"}

    ид = запись.get("id")
    async def цены_для(имя_монеты: str) -> dict:
        ответ = await ctx.fetch(
            "https://api.coingecko.com/api/v3/simple/price?" + urlencode({
                "ids": имя_монеты, "vs_currencies": ",".join(валюты),
                "include_24hr_change": "true", "include_market_cap": "true",
                "include_24hr_vol": "true"}))
        return _проверить(ответ).get(имя_монеты) or {}

    цены = await цены_для(ид)
    if not цены and известная:
        # словарь мог устареть — тогда всё-таки идём искать
        запись = await _найти_монету(монета, ctx)
        ид = запись.get("id", ид)
        цены = await цены_для(ид)
    if not цены:
        return {"ok": False, "error": f"цены для {ид!r} не отдали"}

    основная = валюты[0]
    движение = цены.get(f"{основная}_24h_change")
    капитализация = цены.get(f"{основная}_market_cap")

    сколько = " · ".join(
        f"{_красиво(цены[в])} {в.upper()}" for в in валюты if цены.get(в) is not None)

    return {
        "ok": True,
        "coin": запись.get("name") or ид.replace("-", " ").title(),
        "symbol": (запись.get("symbol") or "").upper(),
        "id": ид,
        "price": сколько,
        "change_24h": f"{движение:+.2f}%" if движение is not None else "",
        "market_cap": _красиво(капитализация) + " " + основная.upper() if капитализация else "",
        "volume_24h": (_красиво(цены[f"{основная}_24h_vol"]) + " " + основная.upper()
                       if цены.get(f"{основная}_24h_vol") else ""),
        "rank": запись.get("market_cap_rank") or "",
        "error": "",
    }
