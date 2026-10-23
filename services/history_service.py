"""Persistence helpers for the processing history panel.
Optimized with in-memory cache to avoid redundant disk I/O.
"""
import json
import os
import logging
from datetime import datetime

from config_manager import APP_DIR
from models.processing_history import ProcessingHistoryItem


HISTORY_PATH = os.path.join(APP_DIR, "processing_history.json")
MAX_HISTORY_ITEMS = 500
logger = logging.getLogger(__name__)

# In-memory cache
_history_cache = None
_history_cache_dirty = False


def _ensure_dir():
    os.makedirs(APP_DIR, exist_ok=True)


def invalidar_cache():
    """Invalidate history cache - call after manual file changes outside this module."""
    global _history_cache, _history_cache_dirty
    _history_cache = None
    _history_cache_dirty = True


def load_history():
    global _history_cache, _history_cache_dirty
    if _history_cache is not None and not _history_cache_dirty:
        return list(_history_cache)
    if not os.path.exists(HISTORY_PATH):
        _history_cache = []
        _history_cache_dirty = False
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            _history_cache = [item for item in data if isinstance(item, dict)]
        else:
            _history_cache = []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("No se pudo leer el historial de procesos: %s", exc)
        _history_cache = []
    _history_cache_dirty = False
    return list(_history_cache)


def save_history(items):
    global _history_cache, _history_cache_dirty
    _ensure_dir()
    items = items[:MAX_HISTORY_ITEMS]
    temp_path = f"{HISTORY_PATH}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, HISTORY_PATH)
    except OSError:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise
    _history_cache = items
    _history_cache_dirty = False


def append_history(item):
    if isinstance(item, ProcessingHistoryItem):
        item = item.to_dict()
    item.setdefault("processed_at", datetime.now().isoformat(timespec="seconds"))
    items = [item] + load_history()
    save_history(items)
    return items


def clear_history():
    save_history([])


def history_summary(items=None):
    items = load_history() if items is None else items
    completed = [i for i in items if i.get("status") == "Completado"]
    errors = sum(int(i.get("errors", 0) or 0) for i in items)
    return {
        "files": len(items),
        "completed": len(completed),
        "records": sum(int(i.get("records", 0) or 0) for i in completed),
        "people": sum(int(i.get("people", 0) or 0) for i in completed),
        "meals": sum(int(i.get("meals", 0) or 0) for i in completed),
        "errors": errors,
    }