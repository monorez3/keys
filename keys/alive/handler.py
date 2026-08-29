"""Ключ «жив ли канал»: t.me/<username> -> всё, что вообще есть на странице.

Разбор вырезан из TG Catalog (checker.py) и оставлен чистой функцией:
parse_preview(html) не ходит в сеть, поэтому тестируется без интернета.

Главная тонкость, из-за которой наивная проверка врёт: Telegram на удалённый
канал отвечает 200 и рисует заглушку. Отличие живого от мёртвого — в разметке,
а не в коде ответа.

Что страница отдаёт на самом деле (проверено на живых страницах):

    <div class="tgme_page_title"><span>Имя</span><i class="verified-icon">✔</i></div>
    <div class="tgme_page_extra">11 005 186 subscribers</div>      -> канал
    <div class="tgme_page_extra">15 121 members, 3 314 online</div> -> группа
    <div class="tgme_page_extra">@BotFather</div>                   -> и следом
    <div class="tgme_page_extra">8 705 765 monthly users</div>      -> бот
    <a class="tgme_action_button_new" href="tg://resolve?domain=…">View in Telegram</a>
    <a class="tgme_page_context_link" href="/s/durov">Preview channel</a>

Блоков tgme_page_extra бывает два, и у бота число лежит во втором — поэтому
их разбирают все, а не первый попавшийся.
"""

from __future__ import annotations

import html as html_mod
import re

TITLE_RE = re.compile(r'<div class="tgme_page_title"[^>]*>(.*?)</div>', re.S)
EXTRA_RE = re.compile(r'<div class="tgme_page_extra"[^>]*>(.*?)</div>', re.S)
DESC_RE = re.compile(r'<div class="tgme_page_description[^"]*"[^>]*>(.*?)</div>', re.S)
PHOTO_RE = re.compile(r'<img class="tgme_page_photo_image"[^>]*src="([^"]+)"')
ICON_RE = re.compile(r'class="tgme_page_icon"')
ACTION_RE = re.compile(r'<a class="tgme_action_button_new[^"]*"[^>]*>(.*?)</a>', re.S)
DEEPLINK_RE = re.compile(r'href="(tg://[^"]+)"')
PREVIEW_RE = re.compile(r'<a class="tgme_page_context_link"[^>]*href="(/s/[^"]+)"')
VERIFIED_RE = re.compile(r'<i class="verified-icon".*?</i>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
COUNT_RE = re.compile(r"([\d\s  ]+)\s*([a-zA-Zа-яА-Я ]+)")
ONLINE_RE = re.compile(r"([\d\s  ]+)\s*online", re.I)

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


def _digits(text: str) -> int | None:
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def _counts(extras: list[str]) -> tuple[int | None, str | None, int | None]:
    """Из всех блоков extra достаём число, его подпись и сколько онлайн.

    Подпись важна сама по себе: 'subscribers' у канала, 'members' у группы,
    'monthly users' у бота — по ней же уточняется тип.
    """
    count = label = online = None
    for extra in extras:
        if not extra or extra.startswith("@"):
            continue
        online_m = ONLINE_RE.search(extra)
        if online_m:
            online = _digits(online_m.group(1))
        head = extra.split(",")[0]
        m = COUNT_RE.match(head)
        if m and count is None:
            count = _digits(m.group(1))
            label = m.group(2).strip().lower() or None
        elif label is None and head.lower().startswith("no "):
            # «no subscribers» — счётчик спрятан владельцем, но тип этим и выдан
            label = head[3:].strip().lower() or None
    return count, label, online


def _kind(username: str, label: str | None, action: str | None, has_preview: bool,
          is_private: bool) -> str:
    """Тип определяем по нескольким независимым признакам, а не по одному.

    Подпись числа надёжнее всего, кнопка действия — второй свидетель:
    «Start Bot» у бота, «Send Message» у человека, «View in Telegram» у канала.
    """
    if is_private:
        return "private_invite"

    label = (label or "").lower()
    if "subscriber" in label or "подписчик" in label:
        return "channel"
    if "monthly user" in label:
        return "bot"
    if "member" in label or "участник" in label:
        return "group"

    act = (action or "").lower()
    if "start bot" in act:
        return "bot"
    if "join group" in act:
        return "group"
    if "join channel" in act or has_preview:
        return "channel"
    if "send message" in act:
        return "user"

    uname = username.lower()
    if uname.endswith("bot") or uname in LEGACY_BOTS:
        return "bot"
    return "unknown"


def parse_preview(page: str, username: str, url: str, status: int = 200) -> dict:
    """Чистая функция: HTML -> ответ ключа. Без сети, тестируется офлайн."""
    is_private = username.startswith("+")
    base = {
        "username": username,
        "url": url,
        "is_alive": False,
        "is_private": is_private,
    }
    if status != 200:
        return base | {"error": f"HTTP {status}"}

    page_low = page.lower()
    if any(marker in page_low for marker in RESTRICTED_MARKERS):
        return base | {"restricted": True, "error": "заблокирован Telegram"}

    title_m = TITLE_RE.search(page)
    title_raw = title_m.group(1) if title_m else None
    title = _clean(title_raw)
    if not title:
        return base | {"error": "не существует / удалён"}

    og_title = (_meta(page, "og:title") or "").lower()
    if any(og_title.startswith(p) for p in DEAD_TITLE_PREFIXES):
        return base | {"error": "заглушка Telegram"}

    extras = [_clean(e) or "" for e in EXTRA_RE.findall(page)]
    members_count, members_label, online_count = _counts(extras)

    action = _clean(ACTION_RE.search(page).group(1)) if ACTION_RE.search(page) else None
    preview_m = PREVIEW_RE.search(page)
    deeplink_m = DEEPLINK_RE.search(page)

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
        "kind": _kind(username, members_label, action, bool(preview_m), is_private),
        "title": title,
        "description": description,
        "members_count": members_count,
        "members_label": members_label,
        "online_count": online_count,
        "verified": bool(VERIFIED_RE.search(title_raw or "")),
        "is_private": is_private,
        "restricted": False,
        "avatar_url": avatar,
        "deep_link": deeplink_m.group(1) if deeplink_m else None,
        "has_preview": bool(preview_m),
        "action": action,
    }


async def run(params: dict, ctx) -> dict:
    username = normalize(params["link"])
    url = f"https://t.me/{username}"
    resp = await ctx.fetch(url)
    return parse_preview(resp.text, username, url, resp.status)
