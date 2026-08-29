# monokeys

Клиент «Ключей» — маленьких умных функций, которые зовутся одним вызовом.
Зависимостей нет: внутри только стандартная библиотека.

```bash
pip install monokeys
```

```python
from monokeys import Keys

k = Keys()                      # ключ доступа берётся из KEYS_API_KEY
res = k.alive("@durov")

print(res.is_alive)             # True
print(res.title)                # Pavel Durov
print(k.alive.text("@durov"))   # жив · channel · Pavel Durov · 11 005 185 подписчиков
```

Ключ доступа бесплатный и без регистрации:

```bash
curl -X POST https://monoblock.casa/keys/token
```

Положите его в `.env` как `KEYS_API_KEY`. Без ключа тоже работает — просто
лимит ниже.

## Методы не зашиты в клиент

Список ключей приходит с сервера, поэтому новый ключ доступен сразу, без
обновления пакета:

```python
k.names()          # ['alive', ...]
k.несуществующий   # AttributeError со списком существующих
```

## Если ставить пакет не хочется

Клиент — один файл, его можно просто скачать:

```bash
curl https://monoblock.casa/keys/sdk/python > monokeys.py
```

А можно вообще без клиента — ключ это обычная ссылка:

```
https://monoblock.casa/keys/alive/@durov  ->  жив · channel · Pavel Durov · ...
```
