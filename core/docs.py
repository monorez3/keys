"""Документация, которую никто не пишет руками.

Каталог и страница ключа целиком собираются из манифестов. Добавил папку с
key.json — сайт обновился сам. Это ответ на «придётся создавать документацию»:
не придётся, если единственный источник правды один.
"""

from __future__ import annotations

import html
import json

from manifest import Manifest

STYLE = """
:root { color-scheme: light dark; --bg:#fbfbfa; --fg:#1a1a19; --muted:#6b6b68;
        --card:#fff; --line:#e6e5e1; --accent:#3d6fd6; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#191918; --fg:#f2f2ef; --muted:#a0a09a; --card:#232322;
          --line:#33332f; --accent:#8fb0f0; } }
* { box-sizing: border-box; }
body { margin:0; padding:40px 20px; background:var(--bg); color:var(--fg);
       font:16px/1.7 -apple-system,Segoe UI,Roboto,sans-serif; }
main { max-width: 760px; margin: 0 auto; }
h1 { font-size:26px; font-weight:500; margin:0 0 6px; }
h2 { font-size:18px; font-weight:500; margin:32px 0 10px; }
.sub { color:var(--muted); margin:0 0 32px; }
a { color:var(--accent); }
.key { display:block; text-decoration:none; color:inherit; background:var(--card);
       border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin-bottom:12px; }
.key:hover { border-color:var(--accent); }
.key h3 { margin:0 0 4px; font-size:16px; font-weight:500; }
.key p { margin:0; color:var(--muted); font-size:14px; }
.tags { margin-top:10px; }
.tag { display:inline-block; font-size:12px; color:var(--muted);
       border:1px solid var(--line); border-radius:99px; padding:1px 9px; margin-right:6px; }
table { width:100%; border-collapse:collapse; font-size:14px; }
th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:400; }
code,pre { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:13px; }
pre { background:var(--card); border:1px solid var(--line); border-radius:10px;
      padding:14px 16px; overflow-x:auto; }
.req { color:var(--accent); }
footer { margin-top:48px; color:var(--muted); font-size:13px; }
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=ru><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def catalog_page(manifests: list[Manifest]) -> str:
    cards = []
    for m in manifests:
        tags = "".join(f"<span class=tag>{html.escape(t)}</span>" for t in m.tags)
        cards.append(
            f"<a class=key href='/k/{m.id}/docs'><h3>{html.escape(m.title)}</h3>"
            f"<p>{html.escape(m.summary)}</p><div class=tags>{tags}</div></a>"
        )
    body = (
        "<h1>Ключи</h1>"
        "<p class=sub>Маленькие умные функции. Дёргаются по HTTP из любого кода "
        "или подключаются целиком в ассистента одним адресом.</p>"
        + "".join(cards)
        + "<h2>Подключить в ассистента</h2>"
        "<p>Один адрес — и все ключи появляются как обычные инструменты:</p>"
        "<pre>{\n  \"mcpServers\": {\n    \"keys\": { \"url\": \"https://ВАШ-АДРЕС/mcp\" }\n  }\n}</pre>"
        "<footer>Бесплатно. Лимит — 60 проверок в минуту и 2000 в сутки на адрес; "
        "ответы из кэша лимит не тратят.</footer>"
    )
    return _page("Ключи", body)


def key_page(m: Manifest) -> str:
    params = "".join(
        f"<tr><td><code>{html.escape(p.name)}</code>"
        + ("<span class=req> *</span>" if p.required else "")
        + f"</td><td>{html.escape(p.type)}</td>"
        f"<td>{html.escape(p.description)}</td></tr>"
        for p in m.params
    )
    returns = "".join(
        f"<tr><td><code>{html.escape(r.name)}</code></td><td>{html.escape(r.type)}</td>"
        f"<td>{html.escape(r.description)}</td></tr>"
        for r in m.returns
    )
    example = m.examples[0]["params"] if m.examples else {p.name: p.example for p in m.params}
    query = "&".join(f"{k}={v}" for k, v in example.items())
    curl = f"curl 'https://ВАШ-АДРЕС/k/{m.id}?{query}'"
    body_json = json.dumps(example, ensure_ascii=False)
    source = ""
    if m.source:
        src = m.source.get("project", "")
        note = m.source.get("note", "")
        source = f"<h2>Откуда</h2><p class=sub>{html.escape(src)} — {html.escape(note)}</p>"

    body = (
        f"<p><a href='/'>← все ключи</a></p><h1>{html.escape(m.title)}</h1>"
        f"<p class=sub>{html.escape(m.summary)}</p>"
        f"<p>{html.escape(m.description)}</p>"
        "<h2>Вход</h2><table><tr><th>параметр</th><th>тип</th><th>что это</th></tr>"
        f"{params}</table>"
        "<h2>Ответ</h2><table><tr><th>поле</th><th>тип</th><th>что это</th></tr>"
        f"{returns}</table>"
        f"<h2>Позвать</h2><pre>{html.escape(curl)}</pre>"
        f"<pre>POST /k/{m.id}\n{html.escape(body_json)}</pre>"
        "<h2>В ассистенте</h2>"
        f"<p>Инструмент называется <code>{html.escape(m.id)}</code> — просто попросите словами, "
        "подставлять параметры ассистент будет сам.</p>"
        f"{source}"
    )
    return _page(m.title, body)


def not_found(available: list[str]) -> str:
    items = ", ".join(f"<code>{html.escape(k)}</code>" for k in available)
    return _page("Нет такого ключа", f"<h1>Нет такого ключа</h1><p>Есть: {items}</p>")
