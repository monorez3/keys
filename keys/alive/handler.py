"""Ключ «жив ли канал»: t.me/<username> -> живой/мёртвый + карточка.

Разбор вырезан из TG Catalog (checker.py) и оставлен чистой функцией:
parse_preview(html) не ходит в сеть, поэтому тестируется без интернета.

Главная тонкость, из-за которой наивная проверка врёт: Telegram на удалённый
канал отвечает 200 и рисует заглушку. Отличие живого от мёртвого — в разметке,
а не в коде ответа.
"""

from __future__ import annotations

import html as html_mod
import re

TITLE_RE = re.compile(r'<div class="tgme_page_title"[^>]*>(.*?)</div>', re.S)
EXTRA_RE = re.compile(r'<div class="tgme_page_extra"[^>]*>(.*?)</div>', re.S)
DESC_RE = re.compile(r'<div class="tgme_page_description[^"]*"[^>]*>(.*?)</div>', re.S)
PHOTO_RE = re.compile(r'<img class="tgme_page_photo_image"[^>]*src="([^"]+)"')
ICON_RE = re.compile(r'class="tgme_page_icon"')
TAG_RE = re.compile(r"<[^>]+>")
VERIFIED_RE = re.compile(r'<i class="verified-icon".*?</i>', re.S)
NUM_RE = re.compile(r"^([\d\s  ]+)")

DEAD_TITLE_PREFIXES = (
    "telegram: contact",
    "telegram: join",
    "join group chat on telegram",
    "join channel on telegram",
    "telegram messenger",
)
RESTRICTED_MARKERS = (
    "was used to spread",
    "this channel is not accessible",
    "this group is not accessible",
)
SUBSCRIBER_WORDS = ("subscriber", "подписчик")
MEMBER_WORDS = ("member", "участник")
LEGACY_BOTS = {"botfather", "stickers", "gif", "vid", "pic", "bing", "wiki", "imdb"}

LINK_RE = re.compile(
    r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/(?:s/)?(\+?[\w\d_-]+)", re.I
)


def normalize(link: str) -> str:
    """'@durov', 'https://t.me/durov?x=1', 't.me/+abc' -> username или +hash."""
    value = (link or "").strip()
    if not value:
        raise ValueError("пустая ссылка")
    m = LINK_RE.match(value)
    if m:
        return m.group(1)
    value = value.lstrip("@")
    if not re.fullmatch(r"\+?[\w\d_-]+", value):
        raise ValueError(f"не похоже на ссылку Telegram: {link!r}")
    return value


def _clean(fragment: str | None) -> str | None:
    if not fragment:
        return None
    text = VERIFIED_RE.sub(" ", fragment)
    text = TAG_RE.sub(" ", text)
    text = html_mod.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _meta(page: str, prop: str) -> str | None:
    m = re.search(
        rf'<meta[^>]+property="{re.escape(prop)}"[^>]+content="([^"]*)"', page
    ) or re.search(
        rf'<meta[^>]+content="([^"]*)"[^>]+property="{re.escape(prop)}"', page
    )
    return html_mod.unescape(m.group(1)).strip() if m else None


def _parse_members(extra: str | None) -> int | None:
    if not extra:
        return None
    m = NUM_RE.match(extra)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return int(digits) if digits else None


def _guess_kind(username: str, extra: str | None, is_invite: bool) -> str:
    if is_invite:
        return "private_invite"
    low = (extra or "").lower()
    if any(w in low for w in SUBSCRIBER_WORDS):
        return "channel"
    if any(w in low for w in MEMBER_WORDS):
        return "group"
    uname = username.lower()
    if uname.endswith("bot") or uname in LEGACY_BOTS:
        return "bot"
    if low.startswith("@"):
        return "user"
    return "unknown"


def parse_preview(page: str, username: str, url: str, status: int = 200) -> dict:
    """Чистая функция: HTML -> ответ ключа. Без сети, тестируется офлайн."""
    base = {"username": username, "url": url, "is_alive": False}
    if status != 200:
        return base | {"error": f"HTTP {status}"}

    page_low = page.lower()
    if any(marker in page_low for marker in RESTRICTED_MARKERS):
        return base | {"error": "заблокирован Telegram"}

    title_m = TITLE_RE.search(page)
    title = _clean(title_m.group(1) if title_m else None)
    if not title:
        return base | {"error": "не существует / удалён"}

    og_title = (_meta(page, "og:title") or "").lower()
    if any(og_title.startswith(p) for p in DEAD_TITLE_PREFIXES):
        return base | {"error": "заглушка Telegram"}

    extra_m = EXTRA_RE.search(page)
    extra = _clean(extra_m.group(1) if extra_m else None)
    desc_m = DESC_RE.search(page)
    description = _clean(desc_m.group(1) if desc_m else None) or _meta(page, "og:description")

    photo = PHOTO_RE.search(page)
    avatar = photo.group(1) if photo else None
    if not avatar:
        og_image = _meta(page, "og:image")
        if og_image and "telegram.org/img/t_logo" not in og_image:
            avatar = og_image

    return {
        "username": username,
        "url": url,
        "is_alive": True,
        "kind": _guess_kind(username, extra, username.startswith("+")),
        "title": title,
        "description": description,
        "members_count": _parse_members(extra),
        "avatar_url": avatar,
    }


async def run(params: dict, ctx) -> dict:
    username = normalize(params["link"])
    url = f"https://t.me/{username}"
    resp = await ctx.fetch(url)
    return parse_preview(resp.text, username, url, resp.status)
