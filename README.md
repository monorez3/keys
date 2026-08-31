**English** · [Русский](README.ru.md)

# Keys

Small smart functions, each callable in one line. Live at
**https://monoblock.casa/keys/**

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

Returns: `is_alive`, `kind` (channel / group / bot / user), `title`,
`description`, `members_count`, `members_label`, `online_count`, `verified`,
`is_private`, `needs_request`, `restricted`, `has_preview`, `avatar_url`,
`deep_link`, `action`, `username`, `url`, `error`.

The subtlety that makes naive checks lie: Telegram answers `200` for a deleted
channel and renders a placeholder. Alive and dead differ in the markup, not in
the status code.

## `answer` — one question, many sources at once

```python
k.answer.text("who is Pavel Durov")
# Pavel Valeryevich Durov is a technology entrepreneur… · source: ddg

k.answer.text("weather in Haifa")        # Haifa: 27.4°C, wind 8.0 m/s
k.answer.text("100 usd to eur")          # 100 USD = 86.23 EUR · rate 0.86
k.answer("Haifa", sources="osm,wiki")    # only the map and Wikipedia
```

The question goes to several places **in parallel**, each answers in its own
field, and the best one is named separately along with links you can check it
against. Leave `sources` out and the key reads the question and decides for
itself: a city goes to the map, a currency to exchange rates, a package to the
package registries. General reference is always asked.

Thirteen sources, **not a single paid key**: Wikipedia, Wikidata, Wiktionary,
DuckDuckGo, OpenStreetMap, Open-Meteo, exchange rates, Crossref, arXiv,
Open Library, PyPI, npm, GitHub.

What is deliberately absent: **language models** — every free one runs into a
quota, and there is no way to check what a model says; and **search engine
results** — scraping them is against their terms, and the server's IP is shared
by a dozen other products, so one ban would take everything down.

### Ask exactly the source you want

Every source is a method of its own. Same word, different answers:

```python
k.answer.weather("Haifa")   # Haifa: 27.4°C, wind 8.0 m/s, humidity 68%
k.answer.osm("Haifa")       # Haifa, Haifa Subdistrict, Haifa District, Israel
k.answer.wiki("Haifa")      # Haifa is the third-largest city in Israel…
k.answer.wikidata("Haifa")  # Haifa — city in northern Israel
k.answer.rates("100 usd to eur")    # 100 USD = 86.23 EUR
k.answer.github("telegram")         # DrKLO/Telegram ★29798
```

Over a plain link: `?only=weather` or `?sources=weather`.

Asking for a source's field *is* asking that source: the word "weather" may not
appear in your question, and you still want the weather.

### Exchange rates that do the math

```python
k.answer.rates("100 dollars to shekels")
# 100 USD = 299.24 ILS · rate 2.99 · as of Mon, 31 Aug 2026

k.answer.rates("50 eur to ils and usd")
# 50 EUR = 173.06 ILS, 58.22 USD · rate 3.46

k.answer.rates("100 UZS to KGS")     # three-letter codes work for all 166
```

The amount, the currencies and their order are parsed out of the question:
"100 dollars **to** shekels" means USD into ILS, not the other way round. There
can be several targets. **166 currencies**, including ones the European Central
Bank does not publish at all.

### Works in two languages

The language is detected from the question itself: Cyrillic goes to Russian
sources, Latin to English ones. Hints and topic words are covered on both sides:

```python
k.answer.text("погода в Хайфе")  ==  k.answer.text("weather in Haifa")   # both go to weather
k.answer.text("где находится Хайфа") == k.answer.text("where is Haifa")  # both go to the map
```

### How `wiki` differs from `wikidata`

They are different things, useful in different situations:

| | what it is | for "Haifa" |
| --- | --- | --- |
| `wiki` | an article summary written by people — connected prose, a few sentences | Haifa is the third-largest city in Israel, after Jerusalem and Tel Aviv, with a mixed Jewish-Arab population… |
| `wikidata` | a structured fact: a label plus one line of "what kind of thing this is", plus a Q-code | Haifa — city in northern Israel, third-largest in the country |

Put simply: `wiki` is for reading, `wikidata` is for parsing. The second one is
shorter, identical across languages, and useful when you need the **type** of a
thing rather than its description.

## `dev` — a vulnerability, a domain or an address

```python
k.dev.text("CVE-2021-44228")
# cve CVE-2021-44228 · critical · Apache Log4j2 2.0-beta9 through 2.15.0 …

k.dev.text("github.com")
# domain github.com · MarkMonitor Inc. · expires 2026-10-09 · 140.82.121.3

k.dev.text("8.8.8.8")
# ip 8.8.8.8 · Ashburn United States · Google LLC
```

The key works out what you gave it: a CVE number, a domain and an IP address
look nothing like each other. Three things a developer looks up constantly,
opening three different sites every time.

Open sources, no keys: CIRCL (a CVE database mirror), RDAP instead of the
retired whois, DNS over Cloudflare, the Wayback Machine, ip-api.

## `crypto` — what a coin costs

```python
k.crypto.text("bitcoin")     # Bitcoin · 78 293 USD · 24h +0.08% · rank 1
k.crypto("ETH", vs="usd")
```

Deliberately separate from ordinary exchange rates: a currency has one central
bank number per day, a coin has a price in several currencies at once, a 24-hour
move and a market cap — and all of it changes by the minute.

## Library arguments

```python
Keys(token=…, base=…, timeout=…, retries=…, user_agent=…)
k.alive(value, only=…, fmt=…, timeout=…, **params)
```

| Argument | Default | What it does |
| --- | --- | --- |
| `token` | the public key from the server | your own access key, if you want one |
| `base` | `https://monoblock.casa/keys` | server address |
| `timeout` | `20.0` | how long to wait for an answer, seconds |
| `retries` | `1` | retries on a dropped connection |
| `only` | — | return just this field |
| `fmt` | `json` | `json`, `text` (a line for humans) or `bool` |

Typos do not stay silent: `k.alive.members_cout(...)` says immediately that
there is no such field and lists the real ones. Key and field names come from
the server, so a new key is available at once, without updating the package.

Full reference with examples: **https://monoblock.casa/keys/client**

## You can skip the library entirely

A key is just a link — anything can open it:

```
https://monoblock.casa/keys/alive/@durov   ->  alive · channel · Pavel Durov · …
```

```php
echo file_get_contents("https://monoblock.casa/keys/alive/@durov");
```

Add `?fmt=json` for all the fields, `?only=members_count` for a single number,
`?fmt=bool` for a bare `true`/`false`. Every key's page carries ready-made code
for curl, Python, JavaScript, C++, Go and PHP.

## Inside an assistant

One address, and every key shows up inside Claude, Cursor or your editor as an
ordinary tool:

```json
{ "mcpServers": { "keys": { "url": "https://monoblock.casa/keys/mcp" } } }
```

## Access keys

You do not need one: the public key works for everybody and is never metered,
and the library fetches it itself from `/public-token`. It is deliberately not
baked into the package — that way it can be rotated with one command and
everyone picks up the new one without a release.

Your own key is only for those who want a kill switch of their own. The owner
issues them:

```bash
curl -X POST -H "X-Admin-Token: $KEYS_ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"label": "forum overclockers.ru"}' https://monoblock.casa/keys/token

curl -H "X-Admin-Token: $KEYS_ADMIN_TOKEN" https://monoblock.casa/keys/tokens

curl -X POST -H "X-Admin-Token: $KEYS_ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"id": "a11ea8e0ea55"}' https://monoblock.casa/keys/token/revoke

# forget cached answers after changing a key
curl -X POST -H "X-Admin-Token: $KEYS_ADMIN_TOKEN" https://monoblock.casa/keys/cache/clear
```

An issued key never expires and is never counted — nobody here intends to meter
anyone's requests. Instead of a quota there is a kill switch: revoke by public
id, instantly, without touching the other keys.

---

# If you want to add a key of your own

## How it is put together

A key is one folder. Out of its manifest come, by themselves: the HTTP
endpoint, the MCP tool, the documentation page, `openapi.json`, `llms.txt` and
code examples in six languages. None of that is written by hand.

```
keys/
  my-key/
    key.json      # what it does, what it takes, what it returns, how long to cache
    handler.py    # async def run(params, ctx) -> dict
```

`ctx` is the only door to the outside world: `await ctx.fetch(url)` already goes
through a per-host rate limiter. The optional `canonical(params)` brings input
to a canonical form **before** the cache — without it `durov`, `DUROV` and
`t.me/durov/123` would be three separate entries and three trips to the network.

The loader checks: `id` matches the folder name, parameter types come from a
fixed list, there is a short summary, and the field used for the one-line answer
is described in `returns`.

Note for contributors: the source comments are written in Russian, the same
language the project is developed in. Everything a user reads — this file, the
package page, the site — is English.

## Running it

```bash
pip install -r requirements.txt
python run.py            # http://127.0.0.1:8110
pytest tests/ -q         # 131 tests, offline, under two seconds
```

## Production

Docker on 8105, nginx serves it under `/keys/`, databases on the `keys-data`
volume.

```bash
tar czf keys.tgz . && scp keys.tgz ubuntu@SERVER:/tmp/
ssh ubuntu@SERVER 'rm -rf ~/keys && mkdir ~/keys && tar xzf /tmp/keys.tgz -C ~/keys \
  && cd ~/keys && docker build -t keys:latest . && docker rm -f keys \
  && docker run -d --name keys --restart unless-stopped -p 127.0.0.1:8105:8105 \
     --env-file ~/keys.env -v keys-data:/app/data keys:latest'
```

Secrets live in `~/keys.env` on the server and never reach the repository:

```
KEYS_ADMIN_TOKEN=…               # without it, key issuing is closed
KEYS_TRUSTED_PROXIES=127.0.0.1   # nginx address; without it we trust no headers
KEYS_REQUIRE_HTTPS=1             # access keys over HTTPS only
```

The app knows its own prefix through `--root-path /keys`, so links and code
samples on the pages come out correct by themselves.

## Releasing the library

No tokens involved: PyPI trusts GitHub directly over OIDC.

```bash
# bump version in clients/python/pyproject.toml, then
git tag v0.3.0 && git push origin v0.3.0
```

Tests run before the upload — a version on PyPI cannot be replaced, and broken
code can only be fixed by a new number.

## What protects an access key

| Against | How |
| --- | --- |
| Spoofing the address via a header | `X-Real-IP` is read only from proxies listed in `KEYS_TRUSTED_PROXIES` |
| Leaking a key into logs | `token=` is cut out of the request line before it is written |
| A leaked database | sha256 is stored; only the public key is kept in the clear |
| A stolen key | self-service revocation, effective immediately, not resurrected from cache |
| Silent downgrade | an unknown or revoked key gets a 401, not a quiet drop to anonymous |
| Anyone handing out keys | issuing requires `KEYS_ADMIN_TOKEN` |
| Clogging the queue | waiting on the shared tap is bounded: busy means 503, not a hanging connection |

## What the server can take

Measurements on the production machine (2 cores, 3.8 GB, a dozen other
containers alongside):

| What was measured | Result |
| --- | --- |
| One `t.me` page | ~10 KB, **75 ms** |
| Eight in parallel | **171 ms** for the whole batch |
| Parsing a page | **0.085 ms** → ~11 700 pages/sec on one core |

The CPU is not the issue — parsing is 900 times cheaper than the network trip.
There is exactly one bottleneck: how much `t.me` tolerates from our IP. So we go
out through a tap of 5 requests per second — that is not a per-user limit but
insurance against a ban. The cache relieves it: a live channel is kept for six
hours, a dead one for an hour.

---

## Who wrote this

The code was written by Claude (Opus 5) under the direction of the repository
owner: the tasks, the design decisions and every check against live data are
his; the implementation is the model's. The Telegram preview parsing came out of
his own earlier project, TG Catalog.

Commits carry `Co-Authored-By` where this applies.
