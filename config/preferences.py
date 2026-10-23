"""Small JSON store for UI preferences that are not business config."""
import json
import os

from config_manager import APP_DIR_LOCAL


# Las preferencias visuales pertenecen al perfil de Windows.  En una
# instalación cliente con datos operativos en red no deben compartirse entre
# personas ni generar escrituras innecesarias sobre la carpeta central.
PREFERENCES_PATH = os.path.join(APP_DIR_LOCAL, "ui_preferences.json")
DEFAULT_PREFERENCES = {
    "theme": "light",
}


def _ensure_dir():
    os.makedirs(APP_DIR_LOCAL, exist_ok=True)


def load_preferences():
    if not os.path.exists(PREFERENCES_PATH):
        return dict(DEFAULT_PREFERENCES)
    try:
        with open(PREFERENCES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            merged = dict(DEFAULT_PREFERENCES)
            merged.update(data)
            return merged
    except Exception:
        pass
    return dict(DEFAULT_PREFERENCES)


def save_preferences(preferences):
    _ensure_dir()
    data = dict(DEFAULT_PREFERENCES)
    data.update(preferences or {})
    with open(PREFERENCES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_theme_preference():
    return load_preferences().get("theme", DEFAULT_PREFERENCES["theme"])


def save_theme_preference(theme_name):
    prefs = load_preferences()
    prefs["theme"] = "dark" if str(theme_name).lower().startswith("dark") else "light"
    save_preferences(prefs)

