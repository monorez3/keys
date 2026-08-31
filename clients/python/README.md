**English** · [Русский](README.ru.md)

# monokeys

A client for **Keys** — small smart functions, each callable in one line.
Standard library only: **no dependencies**, one file.

```bash
pip install monokeys
```

```python
from monokeys import Keys

k = Keys()                                    # nothing to configure

print(k.alive.text("@durov"))
# alive · channel · Pavel Durov · 10 998 851 subscribers

print(k.answer.text("weather in Haifa"))
# Haifa: 27.4°C, wind 8.0 m/s, humidity 68% · source: weather
```

No signup, no access key, no quota: the client picks up the public key itself,
and that key never expires and is never metered.

## `alive` — is this Telegram link alive?

No account, no Bot API, no token: it reads the public `t.me` preview page — the
same one messengers use to draw a link card.

```python
k.alive("@durov")                  # the whole answer as an object
k.alive.members_count("@durov")    # 11005185 — just the number, already an int
k.alive.kind("ru_python")          # group
k.alive.verified("@BotFather")     # True
```

Understands every shape a link comes in: `@durov`, `t.me/durov`,
`t.me/durov/123`, `t.me/s/durov`, `t.me/+AbCdEf…`, `t.me/joinchat/…`, `DUROV`.

Fields: `is_alive`, `kind` (channel / group / bot / user), `title`,
`description`, `members_count`, `members_label`, `online_count`, `verified`,
`is_private`, `needs_request`, `restricted`, `has_preview`, `avatar_url`,
`deep_link`, `action`, `username`, `url`, `error`.

The subtlety that makes naive checks lie: Telegram answers `200` for a deleted
channel and renders a placeholder. Alive and dead differ in the markup, not in
the status code.

## `answer` — one question, many sources at once

```python
k.answer.text("who is Pavel Durov")   # Pavel Valeryevich Durov is a technology…
k.answer.text("weather in Haifa")     # Haifa: 27.4°C, wind 8.0 m/s
k.answer.text("100 usd to eur")       # 100 USD = 86.23 EUR · rate 0.86
k.answer("Haifa", sources="osm,wiki") # only the map and Wikipedia
```

The question goes to several places in parallel, each answers in its own field,
and the best one is named separately along with links you can check it against.
Leave `sources` out and the key reads the question and decides for itself.

Thirteen sources, **not a single paid key**: Wikipedia, Wikidata, Wiktionary,
DuckDuckGo, OpenStreetMap, Open-Meteo, exchange rates, Crossref, arXiv,
Open Library, PyPI, npm, GitHub.

### Ask exactly the source you want

Every source is a method of its own. Same word, different answers:

```python
k.answer.weather("Haifa")   # Haifa: 27.4°C, wind 8.0 m/s, humidity 68%
k.answer.osm("Haifa")       # Haifa, Haifa Subdistrict, Haifa District, Israel
k.answer.wiki("Haifa")      # Haifa is the third-largest city in Israel…
k.answer.rates("100 usd to eur")    # 100 USD = 86.23 EUR
k.answer.github("telegram")         # DrKLO/Telegram ★29798
```

### Exchange rates that do the math

```python
k.answer.rates("100 dollars to shekels")   # 100 USD = 299.24 ILS · rate 2.99
k.answer.rates("50 eur to ils and usd")    # 50 EUR = 173.06 ILS, 58.22 USD
k.answer.rates("100 UZS to KGS")           # codes work for all 166 currencies
```

The amount, the currencies and their order are parsed out of the question.
**166 currencies**, including ones the European Central Bank does not publish.

### Two languages

The language is detected from the question itself: Cyrillic goes to Russian
sources, Latin to English ones.

## Three ways to call any key

```python
k.alive("@durov")                    # the whole answer: an object with fields
k.alive.members_count("@durov")      # a single field, in its own type
k.alive.text("@durov")               # a line for humans
```

## Arguments

### `Keys(...)` — the connection

| Argument | Default | What it does |
| --- | --- | --- |
| `token` | `KEYS_API_KEY`, else the public key from the server | access key; sent in a header only |
| `base` | `https://monoblock.casa/keys` | server address |
| `timeout` | `20.0` | how long to wait for an answer, seconds |
| `retries` | `1` | retries on a dropped connection (a server refusal is not retried) |
| `user_agent` | `monokeys/<version>` | how to introduce yourself to the server |

```python
k = Keys(timeout=5, retries=2)
```

### `k.<key>(...)` — the call

| Argument | Default | What it does |
| --- | --- | --- |
| `value` | — | the main value: a link for `alive`, a question for `answer` |
| `only` | `""` | return just this field instead of the whole answer |
| `fmt` | `"json"` | `json` — fields, `text` — a line for humans, `bool` — yes/no |
| `timeout` | as the client | override the wait for a single call |
| `**params` | — | the key's other parameters, by name |

```python
k.alive("@durov", only="members_count")   # 11005185
k.alive("@durov", fmt="bool")             # 'true'
k.alive("@durov", timeout=3)              # do not wait longer than three seconds
```

### What you can ask the client

| Call | What comes back |
| --- | --- |
| `k.names()` | names of every available key |
| `k.fields("alive")` | which fields a key returns |
| `k.alive.fields()` | the same, shorter |
| `k.catalog(refresh=True)` | the full catalogue with descriptions, fetched anew |
| `k.call("alive", "@durov")` | call a key whose name is only known at runtime |

## The answer

`Answer` is a dict that also answers like an object:

```python
res = k.alive("@durov")
res.title == res["title"]     # the same thing
bool(res)                     # True if the key answered in the affirmative
dict(res)                     # a plain dict
```

Typos do not stay silent:

```python
res.tittle
# AttributeError: no field 'tittle' in the answer; there is: username, url, is_alive, ...

k.alive.members_cout("@durov")
# AttributeError: key 'alive' has no field 'members_cout'; there is: is_alive, kind, ...
```

## Errors

| Exception | When |
| --- | --- |
| `AccessDenied` | the key is unknown, revoked, or was sent over plain HTTP |
| `Unavailable` | the server is busy or a source did not answer — worth retrying |
| `KeysError` | everything else: no such key, garbage input |

All of them carry `.status` and `.body`. The first two subclass `KeysError`, so
one `except` catches them all.

```python
from monokeys import Keys, AccessDenied, Unavailable

try:
    res = k.alive("@durov")
except AccessDenied:
    print("the access key was not accepted")
except Unavailable:
    print("busy right now, will retry later")
```

## Methods are not hard-coded

The list of keys and their fields comes from the server, so a new key is
available at once, without updating the package:

```python
k.names()          # ['alive', 'answer']
k.no_such_key      # AttributeError listing the ones that exist
```

## Access keys

You do not need one — the public key works for everybody and is never metered.
Your own is only for those who want a kill switch of their own; the service
owner issues it, and it never expires either. Put it in `.env`:

```
KEYS_API_KEY=kx_...
```

Revoke your own key if it leaks:

```bash
curl -X POST -H "Authorization: Bearer kx_..." https://monoblock.casa/keys/token/revoke
```

## If you would rather not install anything

The very same file can simply be downloaded — the package is built from it:

```bash
curl https://monoblock.casa/keys/sdk/python > monokeys.py
```

Or skip the client entirely — a key is just a link:

```
https://monoblock.casa/keys/alive/@durov  ->  alive · channel · Pavel Durov · …
```

---

Full documentation: **https://monoblock.casa/keys/client**

Source: **https://github.com/monorez3/keys**
