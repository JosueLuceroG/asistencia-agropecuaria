"""Autenticación, autorización y persistencia segura de usuarios locales."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from datetime import datetime

import config_manager


USER_SCHEMA_VERSION = 1
PBKDF2_ITERATIONS = 310_000
USER_PATH = os.path.join(config_manager.APP_DIR, "usuarios.json")

PERM_REPORTS_GENERATE = "reports.generate"
PERM_ATTENDANCE_DOWNLOAD = "attendance.download"
PERM_HISTORY_VIEW = "history.view"
PERM_DEVICE_HEALTH_VIEW = "device_health.view"
PERM_SCHEDULES_MANAGE = "schedules.manage"
PERM_NON_WORKING_DAYS_MANAGE = "non_working_days.manage"
PERM_PEOPLE_MANAGE = "people.manage"
PERM_DEVICES_MANAGE = "devices.manage"
PERM_BIOMETRICS_MANAGE = "biometrics.manage"
PERM_BACKUPS_CREATE = "backups.create"
PERM_BACKUPS_RESTORE = "backups.restore"
PERM_USERS_MANAGE = "users.manage"

PERMISSIONS = {
    PERM_REPORTS_GENERATE: "Generar reportes Excel",
    PERM_ATTENDANCE_DOWNLOAD: "Descargar asistencias",
    PERM_HISTORY_VIEW: "Consultar historial",
    PERM_DEVICE_HEALTH_VIEW: "Consultar estado de checadores",
    PERM_SCHEDULES_MANAGE: "Editar horarios",
    PERM_NON_WORKING_DAYS_MANAGE: "Administrar días no laborados",
    PERM_PEOPLE_MANAGE: "Asignar personas a checadores",
    PERM_DEVICES_MANAGE: "Gestionar dispositivos",
    PERM_BIOMETRICS_MANAGE: "Registro biométrico",
    PERM_BACKUPS_CREATE: "Crear respaldos",
    PERM_BACKUPS_RESTORE: "Restaurar respaldos",
    PERM_USERS_MANAGE: "Administrar usuarios y permisos",
}
ALL_PERMISSIONS = frozenset(PERMISSIONS)
NOMINA_PERMISSIONS = frozenset({
    PERM_REPORTS_GENERATE,
    PERM_ATTENDANCE_DOWNLOAD,
    PERM_HISTORY_VIEW,
    PERM_DEVICE_HEALTH_VIEW,
    PERM_SCHEDULES_MANAGE,
    PERM_NON_WORKING_DAYS_MANAGE,
    PERM_PEOPLE_MANAGE,
    PERM_BACKUPS_CREATE,
})

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,40}$")
_lock = threading.RLock()


class AuthError(ValueError):
    """Error controlado de autenticación o administración de usuarios."""


def user_path():
    """Ruta de usuarios calculada contra la carpeta de datos actual."""
    return os.path.join(config_manager.APP_DIR, "usuarios.json")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _b64(value):
    return base64.b64encode(value).decode("ascii")


def _unb64(value):
    return base64.b64decode(str(value).encode("ascii"), validate=True)


def normalize_username(username):
    return str(username or "").strip().casefold()


def validate_username(username):
    username = normalize_username(username)
    if not _USERNAME_RE.fullmatch(username):
        raise AuthError("El usuario debe tener de 3 a 40 caracteres: letras, números, punto, guion o guion bajo.")
    return username


def validate_password(password):
    password = str(password or "")
    if len(password) < 5:
        raise AuthError("La contraseña debe tener al menos 5 caracteres.")
    if len(password) > 512:
        raise AuthError("La contraseña es demasiado larga.")
    return password


def hash_password(password, *, salt=None, iterations=PBKDF2_ITERATIONS):
    password = validate_password(password)
    salt_bytes = secrets.token_bytes(16) if salt is None else salt
    if not isinstance(salt_bytes, bytes) or len(salt_bytes) < 16:
        raise AuthError("La sal de contraseña no es válida.")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, int(iterations))
    return {
        "algorithm": "pbkdf2_sha256",
        "iterations": int(iterations),
        "salt": _b64(salt_bytes),
        "hash": _b64(digest),
    }


def verify_password(password, password_data):
    if not isinstance(password_data, dict) or password_data.get("algorithm") != "pbkdf2_sha256":
        return False
    try:
        iterations = int(password_data.get("iterations", 0))
        if iterations < 100_000:
            return False
        salt = _unb64(password_data.get("salt", ""))
        expected = _unb64(password_data.get("hash", ""))
        actual = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError, base64.binascii.Error):
        return False


def _new_user(username, display_name, permissions, *, password_data=None, active=True, must_change_password=False):
    username = validate_username(username)
    now = _now()
    return {
        "username": username,
        "display_name": str(display_name or username).strip()[:100] or username,
        "active": bool(active),
        "permissions": sorted(set(permissions) & ALL_PERMISSIONS),
        "password": password_data,
        "must_change_password": bool(must_change_password),
        "created_at": now,
        "updated_at": now,
    }


def default_store():
    return {
        "schema_version": USER_SCHEMA_VERSION,
        "users": [
            _new_user("admin", "Administrador", ALL_PERMISSIONS, password_data=hash_password("admin"), must_change_password=True),
            _new_user(
                "nomina", "Nómina", NOMINA_PERMISSIONS,
                password_data=hash_password("123456"), must_change_password=True,
            ),
        ],
    }


def _normalize_user(item):
    if not isinstance(item, dict):
        return None
    try:
        username = validate_username(item.get("username", ""))
    except AuthError:
        return None
    password_data = item.get("password") if isinstance(item.get("password"), dict) else None
    permissions = set(item.get("permissions", [])) if isinstance(item.get("permissions"), list) else set()
    normalized = _new_user(
        username,
        item.get("display_name", username),
        permissions,
        password_data=password_data,
        active=item.get("active", True),
        must_change_password=item.get("must_change_password", False),
    )
    normalized["created_at"] = str(item.get("created_at") or normalized["created_at"])
    normalized["updated_at"] = str(item.get("updated_at") or normalized["updated_at"])
    return normalized


def normalize_store(store):
    users = []
    seen = set()
    for item in store.get("users", []) if isinstance(store, dict) else []:
        user = _normalize_user(item)
        if user is None or user["username"] in seen:
            continue
        seen.add(user["username"])
        users.append(user)
    if "admin" not in seen:
        users.insert(0, _new_user("admin", "Administrador", ALL_PERMISSIONS, password_data=hash_password("admin"), must_change_password=True))
    if "nomina" not in seen:
        users.append(_new_user("nomina", "Nómina", NOMINA_PERMISSIONS, password_data=hash_password("123456"), must_change_password=True))
    for user in users:
        if user["username"] == "admin":
            user["permissions"] = sorted(ALL_PERMISSIONS)
            # Migración de instalaciones anteriores, donde el administrador
            # se creaba sin contraseña y debía configurarse manualmente.
            # Una contraseña existente nunca se modifica.
            if not user.get("password"):
                user["password"] = hash_password("admin")
                user["must_change_password"] = True
                user["updated_at"] = _now()
    return {"schema_version": USER_SCHEMA_VERSION, "users": users}


def _atomic_write(path, payload):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temp_path = f"{path}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def load_store(path=None, *, create=True):
    target = path or user_path()
    with _lock:
        if not os.path.exists(target):
            store = default_store()
            if create:
                _atomic_write(target, store)
            return store
        try:
            with open(target, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise AuthError(f"No se pudo leer el archivo de usuarios: {exc}") from exc
        store = normalize_store(raw)
        if create and store != raw:
            _atomic_write(target, store)
        return store


def save_store(store, path=None):
    target = path or user_path()
    normalized = normalize_store(store)
    with _lock:
        _atomic_write(target, normalized)
    return normalized


def user_public_data(user):
    return {
        "username": user["username"],
        "display_name": user["display_name"],
        "active": user["active"],
        "permissions": list(user["permissions"]),
        "must_change_password": user["must_change_password"],
        "password_configured": bool(user.get("password")),
        "created_at": user.get("created_at", ""),
        "updated_at": user.get("updated_at", ""),
    }


def list_users():
    return [user_public_data(user) for user in load_store()["users"]]


def get_user(username):
    username = normalize_username(username)
    return next((user for user in load_store()["users"] if user["username"] == username), None)


def get_public_user(username):
    user = get_user(username)
    return user_public_data(user) if user is not None else None


def authenticate(username, password):
    user = get_user(username)
    if user is None or not user.get("active", False):
        raise AuthError("Usuario o contraseña incorrectos.")
    if not user.get("password"):
        if user["username"] == "admin":
            raise AuthError("El administrador todavía no tiene contraseña. Selecciona 'Configurar administrador'.")
        raise AuthError("El usuario no tiene una contraseña configurada.")
    if not verify_password(password, user["password"]):
        raise AuthError("Usuario o contraseña incorrectos.")
    return user_public_data(user)


def configure_initial_admin(password):
    store = load_store()
    admin = next(user for user in store["users"] if user["username"] == "admin")
    if admin.get("password"):
        raise AuthError("La contraseña del administrador ya fue configurada.")
    admin["password"] = hash_password(password)
    admin["must_change_password"] = False
    admin["updated_at"] = _now()
    save_store(store)
    return user_public_data(admin)


def change_password(username, password, *, require_change=False):
    username = normalize_username(username)
    store = load_store()
    user = next((item for item in store["users"] if item["username"] == username), None)
    if user is None:
        raise AuthError("El usuario no existe.")
    user["password"] = hash_password(password)
    user["must_change_password"] = bool(require_change)
    user["updated_at"] = _now()
    save_store(store)
    return user_public_data(user)


def _admin_count(users, *, excluding=None):
    return sum(
        1 for user in users
        if user["username"] != excluding and user.get("active") and PERM_USERS_MANAGE in user.get("permissions", [])
    )


def create_user(username, display_name, password, permissions, *, active=True, require_password_change=True):
    username = validate_username(username)
    store = load_store()
    if any(user["username"] == username for user in store["users"]):
        raise AuthError("Ya existe un usuario con ese nombre.")
    user = _new_user(
        username, display_name, permissions, password_data=hash_password(password), active=active,
        must_change_password=require_password_change,
    )
    store["users"].append(user)
    save_store(store)
    return user_public_data(user)


def update_user(username, *, display_name=None, permissions=None, active=None):
    username = normalize_username(username)
    store = load_store()
    user = next((item for item in store["users"] if item["username"] == username), None)
    if user is None:
        raise AuthError("El usuario no existe.")
    if display_name is not None:
        user["display_name"] = str(display_name).strip()[:100] or username
    if permissions is not None:
        requested = sorted(set(permissions) & ALL_PERMISSIONS)
        if username == "admin":
            requested = sorted(ALL_PERMISSIONS)
        if PERM_USERS_MANAGE in user["permissions"] and PERM_USERS_MANAGE not in requested and _admin_count(store["users"], excluding=username) == 0:
            raise AuthError("Debe permanecer al menos un administrador activo con permiso para usuarios.")
        user["permissions"] = requested
    if active is not None:
        active = bool(active)
        if user.get("active") and not active and PERM_USERS_MANAGE in user["permissions"] and _admin_count(store["users"], excluding=username) == 0:
            raise AuthError("No puedes desactivar al último administrador.")
        user["active"] = active
    user["updated_at"] = _now()
    save_store(store)
    return user_public_data(user)


def can(user, permission):
    return bool(user and user.get("active", True) and permission in set(user.get("permissions", [])))