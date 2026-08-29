"""Готовый код под каждый язык — тоже из манифеста, а не руками.

Задача не «показать пример HTTP-запроса», а свести код на стороне человека к
одной строке. Поэтому примеры используют короткую форму — имя ключа и сразу
значение — и просят текстовый ответ. Тогда на стороне человека не остаётся ни
сборки query-строки, ни разбора JSON: только «сходи по адресу и напечатай».

Правило: добавили ключ — примеры под все языки появились сами, с настоящим
адресом и настоящим значением из manifest.examples.
"""

from __future__ import annotations

from urllib.parse import quote

from manifest import Manifest

# Порядок = порядок вкладок на странице.
LANGS = ["curl", "python", "javascript", "cpp", "go", "php"]
TITLES = {
    "curl": "curl",
    "python": "Python",
    "javascript": "JavaScript",
    "cpp": "C++",
    "go": "Go",
    "php": "PHP",
}


def example_params(m: Manifest) -> dict:
    """Настоящие значения: из примера манифеста, иначе из полей params."""
    if m.examples:
        return dict(m.examples[0]["params"])
    return {p.name: p.example if p.example is not None else "нет-примера" for p in m.params}


def key_link(m: Manifest, base_url: str) -> str:
    """Сам ключ: адрес, к которому дописывают проверяемое значение."""
    return f"{base_url}/{m.id}/"


def example_link(m: Manifest, base_url: str) -> str:
    value = example_params(m).get(m.primary_param().name, "")
    return key_link(m, base_url) + quote(str(value), safe="@+")


def render(m: Manifest, base_url: str) -> dict[str, str]:
    url = example_link(m, base_url)

    return {
        "curl": f"curl '{url}'",

        "python": f'''import urllib.request

print(urllib.request.urlopen("{url}").read().decode())''',

        "javascript": f'''const answer = await (await fetch("{url}")).text();
console.log(answer);''',

        "cpp": f'''// сборка: g++ main.cpp -lcurl
#include <curl/curl.h>
#include <iostream>
#include <string>

static size_t sink(char* data, size_t size, size_t n, void* out) {{
    static_cast<std::string*>(out)->append(data, size * n);
    return size * n;
}}

int main() {{
    CURL* curl = curl_easy_init();
    std::string answer;
    curl_easy_setopt(curl, CURLOPT_URL, "{url}");
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, sink);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &answer);
    curl_easy_perform(curl);
    curl_easy_cleanup(curl);

    std::cout << answer << std::endl;  // готовая строка, разбирать нечего
}}''',

        "go": f'''resp, _ := http.Get("{url}")
defer resp.Body.Close()
answer, _ := io.ReadAll(resp.Body)

fmt.Println(string(answer))''',

        "php": f'echo file_get_contents("{url}");',
    }
