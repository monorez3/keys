"""Ключ «dev»: уязвимость, домен или адрес — по одной строке.

Три вещи, которые разработчик гуглит чаще всего и каждый раз открывает три
разных сайта:

    CVE-2021-44228   что за дыра, насколько опасна, что затронуто
    github.com       кто владелец, когда истекает, куда указывает, есть ли копия
    8.8.8.8          чей адрес, откуда, какой провайдер

Тип входа определяется сам: номер уязвимости, домен и адрес ни на что не
похожи друг на друга. Поэтому ключ один, а не три.

Все источники — с открытым интерфейсом и без ключей: CIRCL (зеркало базы CVE),
RDAP (замена устаревшему whois), DNS через Cloudflare, Веб-архив, ip-api.
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import quote, urlencode

CVE = re.compile(r"\bCVE[-\s]?(\d{4})[-\s]?(\d{4,7})\b", re.I)
IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
ДОМЕН = re.compile(r"^(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?:/.*)?$", re.I)

# Дольше остальных: RDAP у некоторых зон отвечает нехотя.
ЖДЁМ = 16.0


def распознать(строка: str) -> tuple[str, str]:
    """'CVE-2021-44228' -> ('cve', 'CVE-2021-44228'); 'github.com' -> ('domain', …).

    Порядок проверок важен: номер уязвимости содержит цифры и дефисы и мог бы
    сойти за что угодно, поэтому его ищем первым.
    """
    строка = (строка or "").strip()
    if not строка:
        raise ValueError("пустой запрос: ожидается CVE, домен или IP-адрес")

    м = CVE.search(строка)
    if м:
        return "cve", f"CVE-{м.group(1)}-{м.group(2)}"

    без_схемы = re.sub(r"^https?://", "", строка).split("/")[0].strip().lower()
    if IPV4.match(без_схемы):
        куски = без_схемы.split(".")
        if all(0 <= int(к) <= 255 for к in куски):
            return "ip", без_схемы
        raise ValueError(f"не похоже на адрес: {строка!r}")

    м = ДОМЕН.match(строка)
    if м:
        return "domain", м.group(1).lower()

    raise ValueError(f"не понял, что это: {строка!r}. Ожидается CVE, домен или IP-адрес")


# --------------------------------------------------------------------------- #
# уязвимость
# --------------------------------------------------------------------------- #

async def _уязвимость(номер: str, ctx) -> dict:
    ответ = await ctx.fetch(f"https://cve.circl.lu/api/cve/{quote(номер)}")
    if ответ.status != 200:
        return {"error": f"уязвимость {номер} не найдена"}
    д = json.loads(ответ.text)
    if not д:
        return {"error": f"уязвимость {номер} не найдена"}

    контейнер = (д.get("containers") or {}).get("cna") or {}
    описания = контейнер.get("descriptions") or []
    описание = next((о.get("value", "") for о in описания if о.get("lang", "").startswith("en")), "")

    # Оценку пишут по-разному: у одних записей это число CVSS с подписью,
    # у других — просто слово «critical» в свободном поле. Log4Shell как раз
    # второго вида, поэтому ищем оба.
    опасность = ""
    for метрика in контейнер.get("metrics") or []:
        for значение in метрика.values():
            if not isinstance(значение, dict):
                continue
            if значение.get("baseScore") is not None:
                опасность = f"{значение['baseScore']} {значение.get('baseSeverity', '')}".strip()
                break
            содержимое = значение.get("content")
            if isinstance(содержимое, dict):
                словом = next((str(v) for v in содержимое.values() if isinstance(v, str)), "")
                if словом:
                    опасность = словом
                    break
        if опасность:
            break

    затронуто = []
    for a in (контейнер.get("affected") or [])[:3]:
        имя = " ".join(x for x in [a.get("vendor"), a.get("product")] if x and x != "n/a")
        if имя:
            затронуто.append(имя)

    метаданные = д.get("cveMetadata") or {}
    return {
        "kind": "cve",
        "subject": номер,
        "severity": опасность,
        "published": (метаданные.get("datePublished") or "")[:10],
        "description": описание[:400],
        "affected": ", ".join(dict.fromkeys(затронуто)),
    }


# --------------------------------------------------------------------------- #
# домен
# --------------------------------------------------------------------------- #

async def _rdap(домен: str, ctx) -> dict:
    """Кто владелец и когда истекает. RDAP — то, чем заменили whois."""
    ответ = await ctx.fetch(f"https://rdap.org/domain/{quote(домен)}")
    if ответ.status != 200:
        return {}
    д = json.loads(ответ.text)

    события = {с.get("eventAction"): (с.get("eventDate") or "")[:10]
               for с in д.get("events") or []}
    регистратор = ""
    for лицо in д.get("entities") or []:
        if "registrar" in (лицо.get("roles") or []):
            for кусок in (лицо.get("vcardArray") or [None, []])[1]:
                if кусок and кусок[0] == "fn":
                    регистратор = кусок[3]
                    break
    серверы = [(с.get("ldhName") or "").lower() for с in д.get("nameservers") or []]
    return {
        "registrar": регистратор,
        "created": события.get("registration", ""),
        "expires": события.get("expiration", ""),
        "nameservers": ", ".join(серверы[:4]),
        "status": ", ".join(д.get("status") or [])[:120],
    }


async def _dns(домен: str, ctx) -> dict:
    """Куда указывает домен. Спрашиваем через Cloudflare по HTTPS."""
    async def запись(тип: str) -> list[str]:
        try:
            ответ = await ctx.fetch(
                "https://cloudflare-dns.com/dns-query?" + urlencode({"name": домен, "type": тип}),
                headers={"Accept": "application/dns-json"})
            д = json.loads(ответ.text)
            return [о.get("data", "") for о in д.get("Answer") or []]
        except Exception:
            return []

    адреса, почта = await asyncio.gather(запись("A"), запись("MX"))
    return {
        "ips": ", ".join(а for а in адреса if IPV4.match(а))[:200],
        "mx": ", ".join(п.split()[-1].rstrip(".") for п in почта if п)[:160],
    }


async def _архив(домен: str, ctx) -> dict:
    """Есть ли сохранённая копия в Веб-архиве и насколько свежая."""
    try:
        ответ = await ctx.fetch(
            "https://archive.org/wayback/available?" + urlencode({"url": домен}))
        снимок = ((json.loads(ответ.text).get("archived_snapshots") or {}).get("closest") or {})
        отметка = снимок.get("timestamp") or ""
        if not отметка:
            return {"archived": ""}
        return {"archived": f"{отметка[:4]}-{отметка[4:6]}-{отметка[6:8]}"}
    except Exception:
        return {"archived": ""}


async def _домен(домен: str, ctx) -> dict:
    """Три источника про домен, у каждого свой предел ожидания.

    Одним общим пределом нельзя: RDAP у редких зон отвечает нехотя — у .casa
    он вообще не уложился, — и утаскивал за собой DNS с архивом, которые к
    тому моменту уже всё принесли. Медленный не должен губить быстрых.
    """
    async def не_дольше(работа, сколько: float) -> dict:
        try:
            return await asyncio.wait_for(работа, timeout=сколько)
        except Exception:
            return {}

    владелец, куда, копия = await asyncio.gather(
        не_дольше(_rdap(домен, ctx), 10.0),
        не_дольше(_dns(домен, ctx), 6.0),
        не_дольше(_архив(домен, ctx), 8.0))
    итог = {"kind": "domain", "subject": домен, **владелец, **куда, **копия}
    if not (итог.get("registrar") or итог.get("ips")):
        итог["error"] = f"про домен {домен} ничего не нашлось"
    return итог


# --------------------------------------------------------------------------- #
# адрес
# --------------------------------------------------------------------------- #

async def _адрес(ip: str, ctx) -> dict:
    ответ = await ctx.fetch(
        f"http://ip-api.com/json/{quote(ip)}?fields=status,message,country,city,isp,org,as,reverse")
    д = json.loads(ответ.text)
    if д.get("status") != "success":
        return {"error": д.get("message") or f"про адрес {ip} ничего не нашлось"}
    return {
        "kind": "ip",
        "subject": ip,
        "country": д.get("country", ""),
        "city": д.get("city", ""),
        "isp": д.get("isp", ""),
        "org": д.get("org", ""),
        "asn": д.get("as", ""),
        "reverse": д.get("reverse", ""),
    }


# --------------------------------------------------------------------------- #

ПУСТО = {
    "kind": "", "subject": "", "severity": "", "published": "", "description": "",
    "affected": "", "registrar": "", "created": "", "expires": "", "nameservers": "",
    "status": "", "ips": "", "mx": "", "archived": "", "country": "", "city": "",
    "isp": "", "org": "", "asn": "", "reverse": "", "error": "",
}


def canonical(params: dict) -> dict:
    """Привести к канону до кэша: CVE-2021-44228 и cve 2021 44228 — одно и то же."""
    вид, значение = распознать(params.get("q", ""))
    return params | {"q": значение}


async def run(params: dict, ctx) -> dict:
    вид, значение = распознать(params["q"])
    добытчик = {"cve": _уязвимость, "domain": _домен, "ip": _адрес}[вид]

    try:
        найдено = await asyncio.wait_for(добытчик(значение, ctx), timeout=ЖДЁМ)
    except asyncio.TimeoutError:
        найдено = {"error": "источник не ответил вовремя"}
    except Exception as exc:
        найдено = {"error": f"источник не ответил: {type(exc).__name__}"}

    итог = ПУСТО | {"kind": вид, "subject": значение} | найдено
    итог["ok"] = not итог.get("error")
    return итог
