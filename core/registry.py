"""Находит ключи на диске и держит их загруженными."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

from manifest import Manifest, ManifestError, load


@dataclass(slots=True)
class Key:
    manifest: Manifest
    run: object              # async def run(params: dict, ctx: Context) -> dict
    canonical: object = None # необязательная: def canonical(params) -> params


def _load_handler(folder: Path, key_id: str):
    handler_path = folder / "handler.py"
    if not handler_path.exists():
        raise ManifestError(f"{folder}: нет handler.py")
    spec = importlib.util.spec_from_file_location(f"key_{key_id}", handler_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run"):
        raise ManifestError(f"{handler_path}: нет функции run(params, ctx)")
    return module.run, getattr(module, "canonical", None)


def discover(keys_dir: Path) -> dict[str, Key]:
    """Папка = ключ. Никакой регистрации в списках: положил папку — появился ключ."""
    found: dict[str, Key] = {}
    for manifest_path in sorted(keys_dir.glob("*/key.json")):
        m = load(manifest_path)
        run, canonical = _load_handler(manifest_path.parent, m.id)
        found[m.id] = Key(manifest=m, run=run, canonical=canonical)
    return found
