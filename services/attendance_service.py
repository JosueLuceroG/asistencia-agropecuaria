"""Persistencia local de movimientos descargados de los checadores."""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime

import config_manager


SCHEMA_VERSION = 2
ATTENDANCE_PATH = os.path.join(config_manager.APP_DIR, "attendance_cache.json")
_lock = threading.RLock()
_cache = None


def empty_attendance_data():
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": "",
        "records": [],
        "device_sync": {},
    }


def _iso_datetime(value):
    if value is None:
        return ""
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()).isoformat(timespec="seconds")
    text = str(value).strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat(timespec="seconds")
    except ValueError:
        return text


def _non_negative_int(value, default=0):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _normalize_device_sync(data):
    if not isinstance(data, dict):
        return {}
    normalized = {}
    for key, item in data.items():
        item = item if isinstance(item, dict) else {}
        device = str(item.get("device", key)).strip()
        if not device:
            continue
        normalized[device] = {
            "device": device,
            "last_successful_sync": _iso_datetime(item.get("last_successful_sync")),
            "last_record_timestamp": _iso_datetime(item.get("last_record_timestamp")),
            "last_received": _non_negative_int(item.get("last_received")),
            "last_added": _non_negative_int(item.get("last_added")),
            "last_total": _non_negative_int(item.get("last_total")),
            "last_error": str(item.get("last_error", "")).strip()[:1000],
            "last_error_at": _iso_datetime(item.get("last_error_at")),
        }
    return normalized


def _sync_entry(data, device):
    device = str(device or "").strip()
    if not device:
        return None
    statuses = data.setdefault("device_sync", {})
    existing_key = next((key for key in statuses if key.casefold() == device.casefold()), device)
    entry = dict(statuses.get(existing_key, {}))
    if existing_key != device:
        statuses.pop(existing_key, None)
    entry["device"] = device
    statuses[device] = entry
    return entry


def _latest_timestamp(records):
    timestamps = [_iso_datetime(record.get("timestamp")) for record in records]
    timestamps = [timestamp for timestamp in timestamps if timestamp]
    return max(timestamps, default="")


def normalize_attendance_data(data):
    if not isinstance(data, dict):
        raise ValueError("La caché de checadas debe ser un objeto JSON.")
    records = data.get("records", [])
    if not isinstance(records, list):
        raise ValueError("La colección de checadas no es válida.")
    normalized = []
    seen = set()
    for item in records:
        if not isinstance(item, dict):
            continue
        device = str(item.get("device", item.get("checador", ""))).strip()
        name = str(item.get("name", item.get("nombre", ""))).strip()
        timestamp = _iso_datetime(item.get("timestamp", item.get("fecha_hora")))
        if not name or not timestamp:
            continue
        key = (device.casefold(), name.casefold(), timestamp)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({
            "device": device,
            "name": name,
            "timestamp": timestamp,
            "source": str(item.get("source", item.get("origen", ""))).strip(),
            "downloaded_at": _iso_datetime(item.get("downloaded_at")),
        })
    normalized.sort(key=lambda item: (item["timestamp"], item["device"].casefold(), item["name"].casefold()))
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _iso_datetime(data.get("updated_at")),
        "records": normalized,
        "device_sync": _normalize_device_sync(data.get("device_sync")),
    }


def invalidate_cache():
    global _cache
    with _lock:
        _cache = None


def load_attendance(path=None):
    global _cache
    target = path or ATTENDANCE_PATH
    with _lock:
        if path is None and _cache is not None:
            return json.loads(json.dumps(_cache, ensure_ascii=False))
        if not os.path.exists(target):
            data = empty_attendance_data()
        else:
            try:
                with open(target, "r", encoding="utf-8") as handle:
                    data = normalize_attendance_data(json.load(handle))
            except (OSError, json.JSONDecodeError, ValueError):
                data = empty_attendance_data()
        if path is None:
            _cache = data
        return json.loads(json.dumps(data, ensure_ascii=False))


def save_attendance(data, path=None):
    global _cache
    target = path or ATTENDANCE_PATH
    normalized = normalize_attendance_data(data)
    normalized["updated_at"] = datetime.now().isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    temp_path = f"{target}.tmp"
    with _lock:
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(normalized, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        except OSError:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            raise
        if path is None:
            _cache = normalized
    return json.loads(json.dumps(normalized, ensure_ascii=False))


def append_attendance(records, device="", source=""):
    """Agrega movimientos y omite duplicados por checador, persona y fecha/hora."""
    with _lock:
        current = load_attendance()
        downloaded_at = datetime.now().isoformat(timespec="seconds")
        incoming = []
        for record in records or []:
            if not isinstance(record, dict):
                continue
            incoming.append({
                "device": device or record.get("device", record.get("checador", "")),
                "name": record.get("nombre", record.get("name", "")),
                "timestamp": record.get("fecha_hora", record.get("timestamp")),
                "source": source or record.get("source", record.get("origen", "")),
                "downloaded_at": downloaded_at,
            })
        before = len(current["records"])
        current["records"].extend(incoming)
        current = normalize_attendance_data(current)
        added = len(current["records"]) - before
        entry = _sync_entry(current, device)
        if entry is not None:
            entry.update({
                "last_successful_sync": downloaded_at,
                "last_record_timestamp": _latest_timestamp(incoming) or entry.get("last_record_timestamp", ""),
                "last_received": len(incoming),
                "last_added": added,
                "last_total": len(current["records"]),
                "last_error": "",
                "last_error_at": "",
            })
        saved = save_attendance(current)
        return {
            "received": len(incoming),
            "added": added,
            "total": len(saved["records"]),
        }


def record_sync_success(device, received=0, added=0, last_record_timestamp=None):
    """Registra una sincronización correcta incluso si el dispositivo no tuvo movimientos."""
    with _lock:
        current = load_attendance()
        entry = _sync_entry(current, device)
        if entry is None:
            return current
        entry.update({
            "last_successful_sync": datetime.now().isoformat(timespec="seconds"),
            "last_record_timestamp": _iso_datetime(last_record_timestamp) or entry.get("last_record_timestamp", ""),
            "last_received": _non_negative_int(received),
            "last_added": _non_negative_int(added),
            "last_total": len(current["records"]),
            "last_error": "",
            "last_error_at": "",
        })
        return save_attendance(current)


def record_sync_error(device, error):
    """Conserva el último error de comunicación sin alterar el último éxito conocido."""
    with _lock:
        current = load_attendance()
        entry = _sync_entry(current, device)
        if entry is None:
            return current
        entry.update({
            "last_error": str(error or "Error de sincronización.").strip()[:1000],
            "last_error_at": datetime.now().isoformat(timespec="seconds"),
            "last_total": len(current["records"]),
        })
        return save_attendance(current)


def records_for_device(device):
    selected = str(device or "").strip().casefold()
    result = []
    for item in load_attendance()["records"]:
        if selected and item["device"].casefold() != selected:
            continue
        try:
            timestamp = datetime.fromisoformat(item["timestamp"])
        except ValueError:
            timestamp = item["timestamp"]
        result.append({"nombre": item["name"], "fecha_hora": timestamp})
    return result