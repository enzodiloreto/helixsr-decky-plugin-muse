import os
import json
import decky


class SettingsManager:
    """Minimal persistent settings store backed by a JSON file.

    Mirrors the API used across Decky plugins:
      settings.getSetting(key, default)
      settings.setSetting(key, value)
      settings.read()
      settings.commit()
    """

    def __init__(self, name: str = "settings", settings_directory: str = "") -> None:
        self._name = name
        self._dir = settings_directory
        self._path = os.path.join(settings_directory, f"{name}.json") if settings_directory else f"{name}.json"
        self._settings: dict = {}

    def read(self) -> dict:
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._settings = data if isinstance(data, dict) else {}
            else:
                self._settings = {}
        except Exception as e:
            decky.logger.warning(f"SettingsManager.read failed ({self._path}): {e}")
            self._settings = {}
        return self._settings

    def commit(self) -> None:
        try:
            if self._dir:
                os.makedirs(self._dir, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2)
        except Exception as e:
            decky.logger.error(f"SettingsManager.commit failed ({self._path}): {e}")

    def getSetting(self, key: str, default=None):
        return self._settings.get(key, default)

    def setSetting(self, key: str, value) -> None:
        self._settings[key] = value
