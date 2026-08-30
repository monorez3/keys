# monokeys

Клиент «Ключей» — маленьких умных функций, которые зовутся одним вызовом.
Внутри только стандартная библиотека: **зависимостей нет**, один файл.

```bash
pip install monokeys
```

```python
from monokeys import Keys

k = Keys()                                    # настраивать нечего

print(k.alive.text("@durov"))
# жив · channel · Pavel Durov · 10 998 851 subscribers

print(k.alive.members_count("@durov"))
# 10998851 — только число, уже числом
```

Ни регистрации, ни ключа доступа, ни лимита: клиент сам берёт публичный ключ,
а он бессрочный и без счётчика.

## `alive` — жив ли канал Telegram

Без аккаунта, без Bot API, без токена: читается публичная превью-страница
`t.me`, та самая, по которой мессенджеры рисуют картинку ссылки.

```python
k.alive("@durov")                  # весь ответ объектом
k.alive.members_count("@durov")    # 11005185 — только число, уже числом
k.alive.kind("ru_python")          # group
k.alive.verified("@BotFather")     # True
```

Понимает любую форму ссылки: `@durov`, `t.me/durov`, `t.me/durov/123`,
`t.me/s/durov`, `t.me/+AbCdEf…`, `t.me/joinchat/…`, `DUROV`.

Поля ответа: `is_alive`, `kind` (channel / group / bot / user), `title`,
`description`, `members_count`, `members_label`, `online_count`, `verified`,
`is_private`, `needs_request`, `restricted`, `has_preview`, `avatar_url`,
`deep_link`, `action`, `username`, `url`, `error`.

Тонкость, из-за которой наивная проверка врёт: на удалённый канал Telegram
отвечает `200` и рисует заглушку. Живой от мёртвого отличается разметкой, а не
кодом ответа.

## Три способа позвать любой ключ

```python
k.alive("@durov")                    # весь ответ: объект с полями
k.alive.members_count("@durov")      # только одно поле, нужного типа
k.alive.text("@durov")               # строка для человека
```

## Аргументы

### `Keys(...)` — подключение

| Аргумент | По умолчанию | Что делает |
| --- | --- | --- |
| `token` | `KEYS_API_KEY`, иначе публичный ключ с сервера | ключ доступа; передаётся только заголовком |
| `base` | `https://monoblock.casa/keys` | адрес сервера |
| `timeout` | `20.0` | сколько ждать ответа, секунд |
| `retries` | `1` | повторов при обрыве связи (отказ сервера не повторяется) |
| `user_agent` | `monokeys/<версия>` | как представляться серверу |

```python
k = Keys(timeout=5, retries=2)
```

### `k.<ключ>(...)` — вызов

| Аргумент | По умолчанию | Что делает |
| --- | --- | --- |
| `value` | — | главное значение: ссылка или `@username` |
| `only` | `""` | вернуть только это поле вместо всего ответа |
| `fmt` | `"json"` | `json` — поля, `text` — строка для человека, `bool` — да/нет |
| `timeout` | как у клиента | переопределить ожидание для одного вызова |
| `**params` | — | остальные параметры ключа по именам |

```python
k.alive("@durov", only="members_count")   # 11005185
k.alive("@durov", fmt="bool")             # 'true'
k.alive("@durov", timeout=3)              # не ждать дольше трёх секунд
```

### Что можно спросить у клиента

| Вызов | Что вернёт |
| --- | --- |
| `k.names()` | имена всех доступных ключей |
| `k.fields("alive")` | какие поля возвращает ключ |
| `k.alive.fields()` | то же самое, короче |
| `k.catalog(refresh=True)` | полный каталог с описаниями, спросить заново |
| `k.call("alive", "@durov")` | позвать ключ, имя которого известно только в рантайме |

## Ответ

`Answer` — это словарь, который умеет отвечать и как объект:

```python
res = k.alive("@durov")
res.title == res["title"]     # одно и то же
bool(res)                     # True, если ключ ответил утвердительно
dict(res)                     # обычный словарь
```

Опечатка не молчит:

```python
res.tittle
# AttributeError: в ответе нет поля 'tittle'; есть: username, url, is_alive, ...

k.alive.members_cout("@durov")
# AttributeError: у ключа 'alive' нет поля 'members_cout'; есть: is_alive, kind, ...
```

## Ошибки

| Исключение | Когда |
| --- | --- |
| `AccessDenied` | ключ неизвестен, отозван или отправлен не по HTTPS |
| `Unavailable` | сервер занят или источник не ответил — осмысленно повторить |
| `KeysError` | всё остальное: нет такого ключа, мусор на входе |

У всех есть `.status` и `.body`. Первые два — потомки `KeysError`, ловятся
одним `except`.

```python
from monokeys import Keys, AccessDenied, Unavailable

try:
    res = k.alive("@durov")
except AccessDenied:
    print("ключ доступа не подошёл")
except Unavailable:
    print("сейчас занято, попробую позже")
```

## Методы не зашиты в клиент

Список ключей и их полей приходит с сервера, поэтому новый ключ доступен сразу,
без обновления пакета:

```python
k.names()          # ['alive']
k.несуществующий   # AttributeError со списком существующих
```

## Ключ доступа

Заводить не нужно — публичный работает у всех и без счётчика. Свой нужен только
тем, кому нужен собственный рубильник; его выдаёт владелец сервиса, и он тоже
бессрочный. Положите в `.env`:

```
KEYS_API_KEY=kx_...
```

Отозвать свой ключ, если он утёк:

```bash
curl -X POST -H "Authorization: Bearer kx_..." https://monoblock.casa/keys/token/revoke
```

## Если ставить пакет не хочется

Тот же самый файл можно просто скачать — из него и собран пакет:

```bash
curl https://monoblock.casa/keys/sdk/python > monokeys.py
```

А можно вообще без клиента — ключ это обычная ссылка:

```
https://monoblock.casa/keys/alive/@durov  ->  жив · channel · Pavel Durov · …
```

---

Документация целиком: **https://monoblock.casa/keys/client**

Исходники: **https://github.com/monorez3/keys**
