"""Готовый код под каждый язык — тоже из манифеста, а не руками.

Смысл ключа в том, что подключать нечего: это обычный HTTP-запрос, который
умеет любой язык без единой зависимости. Поэтому примеры ниже намеренно
написаны на голой стандартной библиотеке — кроме C++, где без libcurl никуда.

Правило: добавили ключ — примеры под все языки появились сами, с настоящими
именами параметров и настоящими значениями из manifest.examples.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

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
    return {p.name: p.example if p.example is not None else "..." for p in m.params}


def _main_field(m: Manifest) -> str:
    """Поле, которое интереснее всего напечатать в примере."""
    return m.returns[0].name if m.returns else "result"


def render(m: Manifest, base_url: str) -> dict[str, str]:
    params = example_params(m)
    url = f"{base_url}/k/{m.id}?{urlencode(params)}"
    field = _main_field(m)
    py_args = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in params.items())
    js_args = json.dumps(params, ensure_ascii=False)
    php_args = ", ".join(
        f'"{k}" => {json.dumps(v, ensure_ascii=False)}' for k, v in params.items()
    )

    return {
        "curl": f"curl '{url}'",

        "python": f'''import json, urllib.parse, urllib.request

def key(name, **params):
    """Любой ключ. Ни одной зависимости — только стандартная библиотека."""
    url = "{base_url}/k/" + name + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)

res = key("{m.id}", {py_args})
print(res["{field}"])''',

        "javascript": f'''const res = await fetch(
  "{base_url}/k/{m.id}?" + new URLSearchParams({js_args})
).then(r => r.json());

console.log(res.{field});''',

        "cpp": f'''// сборка: g++ main.cpp -lcurl
#include <curl/curl.h>
#include <iostream>
#include <string>

static size_t sink(char* data, size_t size, size_t n, void* out) {{
    static_cast<std::string*>(out)->append(data, size * n);
    return size * n;
}}

std::string key(const std::string& url) {{
    CURL* curl = curl_easy_init();
    std::string body;
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, sink);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &body);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_perform(curl);
    curl_easy_cleanup(curl);
    return body;  // JSON строкой; разбирать — nlohmann/json по вкусу
}}

int main() {{
    std::cout << key("{url}") << std::endl;
}}''',

        "go": f'''resp, err := http.Get("{url}")
if err != nil {{
    log.Fatal(err)
}}
defer resp.Body.Close()

var res map[string]any
json.NewDecoder(resp.Body).Decode(&res)
fmt.Println(res["{field}"])''',

        "php": f'''$url = "{base_url}/k/{m.id}?" . http_build_query([{php_args}]);
$res = json_decode(file_get_contents($url), true);

echo $res["{field}"];''',
    }
