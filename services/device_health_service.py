"""Clasificación de salud local para checadores configurados.

Este módulo no intenta conectarse a dispositivos. Su información proviene de
la configuración local y de los metadatos de la última descarga guardados en
``attendance_cache.json``.
"""
from __future__ import annotations

from datetime import datetime


STATUS_HEALTHY = "healthy"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"
STATUS_NEVER_SYNCED = "never_synced"


_STATUS_DETAILS = {
    STATUS_HEALTHY: ("Correcto", "La última sincronización terminó correctamente."),
    STATUS_WARNING: ("Advertencia", "Los metadatos de sincronización requieren revisión."),
    STATUS_ERROR: ("Error", "La última operación registrada tuvo un error."),
    STATUS_NEVER_SYNCED: ("Nunca sincronizado", "No hay una sincronización correcta registrada."),
}


def _text(value):
    return str(value or "").strip()


def _is_valid_iso_datetime(value):
    value = _text(value)
    if not value:
        return True
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def classify_sync_status(sync):
    """Clasifica un estado sin usar antigüedad como criterio de alerta."""
    sync = sync if isinstance(sync, dict) else {}
    if _text(sync.get("last_error")):
        return STATUS_ERROR
    if not _text(sync.get("last_successful_sync")):
        return STATUS_NEVER_SYNCED
    if not all(
        _is_valid_iso_datetime(sync.get(field))
        for field in ("last_successful_sync", "last_record_timestamp", "last_error_at")
    ):
        return STATUS_WARNING
    return STATUS_HEALTHY


def status_label(status):
    return _STATUS_DETAILS.get(status, _STATUS_DETAILS[STATUS_WARNING])[0]


def status_description(status):
    return _STATUS_DETAILS.get(status, _STATUS_DETAILS[STATUS_WARNING])[1]


def _sync_for_device(sync_by_device, device_name):
    target = _text(device_name).casefold()
    for key, value in (sync_by_device or {}).items():
        candidate = value if isinstance(value, dict) else {}
        name = _text(candidate.get("device", key))
        if name.casefold() == target:
            return candidate
    return {}


def build_device_health_rows(config, attendance):
    """Combina checadores configurados con su estado local de sincronización.

    Solo se muestran checadores existentes en la configuración activa. Las
    entradas antiguas de caché de dispositivos eliminados no se presentan como
    equipos actuales.
    """
    config = config if isinstance(config, dict) else {}
    attendance = attendance if isinstance(attendance, dict) else {}
    sync_by_device = attendance.get("device_sync", {})
    rows = []
    for device in config.get("checadores", []):
        if not isinstance(device, dict):
            continue
        name = _text(device.get("nombre"))
        if not name:
            continue
        sync = _sync_for_device(sync_by_device, name)
        status = classify_sync_status(sync)
        rows.append({
            "device": name,
            "brand": _text(device.get("marca", device.get("tipo", "ZKTeco"))) or "ZKTeco",
            "ip": _text(device.get("ip")),
            "port": _text(device.get("port")),
            "status": status,
            "status_label": status_label(status),
            "status_description": status_description(status),
            "last_successful_sync": _text(sync.get("last_successful_sync")),
            "last_record_timestamp": _text(sync.get("last_record_timestamp")),
            "last_received": sync.get("last_received", 0),
            "last_added": sync.get("last_added", 0),
            "last_total": sync.get("last_total", 0),
            "last_error": _text(sync.get("last_error")),
            "last_error_at": _text(sync.get("last_error_at")),
        })
    return sorted(rows, key=lambda row: row["device"].casefold())