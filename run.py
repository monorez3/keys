"""Запуск обоих лиц: python run.py  ->  http://127.0.0.1:8110"""

import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).parent / "core"))

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8110, app_dir="core")
