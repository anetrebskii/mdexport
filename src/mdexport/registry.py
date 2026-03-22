"""Registry for managing export sources."""

import json
from pathlib import Path
from datetime import datetime, timezone

REGISTRY_DIR = Path.home() / ".mdexport"
REGISTRY_FILE = REGISTRY_DIR / "registry.json"


def _load() -> dict:
    if not REGISTRY_FILE.exists():
        return {}
    return json.loads(REGISTRY_FILE.read_text())


def _save(data: dict):
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(data, indent=2))


def register(name: str, config: dict):
    data = _load()
    config["added_at"] = datetime.now(timezone.utc).isoformat()
    config.setdefault("synced_at", None)
    data[name] = config
    _save(data)


def unregister(name: str) -> bool:
    data = _load()
    if name not in data:
        return False
    del data[name]
    _save(data)
    return True


def get(name: str) -> dict | None:
    return _load().get(name)


def get_all() -> dict:
    return _load()


def update_synced(name: str):
    data = _load()
    if name in data:
        data[name]["synced_at"] = datetime.now(timezone.utc).isoformat()
        _save(data)
