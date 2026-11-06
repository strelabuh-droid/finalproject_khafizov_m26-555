"""One cached settings object per Python process."""

import json
import os
import tomllib
from pathlib import Path

from valutatrade_hub.core.exceptions import StorageError


class SettingsLoader:
    """Load defaults, TOML, optional JSON and environment overrides."""

    _instance = None

    def __new__(cls):
        # __new__ is sufficient here and easier to read than a metaclass.
        if cls._instance is None:
            instance = super().__new__(cls)
            instance.reload()
            cls._instance = instance
        return cls._instance

    def reload(self):
        """Reload configuration relative to the current working directory."""
        values = {
            "data_dir": "data",
            "log_dir": "logs",
            "rates_ttl_seconds": 300,
            "default_base_currency": "USD",
            "log_format": "json",
            "log_level": "INFO",
            "log_max_bytes": 1048576,
            "log_backup_count": 3,
        }
        try:
            path = Path("pyproject.toml")
            if path.exists():
                with path.open("rb") as stream:
                    config = tomllib.load(stream)
                values.update(config.get("tool", {}).get("valutatrade", {}))
            path = Path("config.json")
            if path.exists():
                values.update(json.loads(path.read_text(encoding="utf-8")))
            for key in ("data_dir", "log_dir", "rates_ttl_seconds"):
                override = os.getenv(f"VTH_{key.upper()}")
                if override is not None:
                    values[key] = override
            values["rates_ttl_seconds"] = int(values["rates_ttl_seconds"])
            if values["rates_ttl_seconds"] <= 0:
                raise ValueError("TTL должен быть больше нуля")
            if values["log_format"] != "json":
                raise ValueError("В этом проекте поддерживается log_format=json")
            for key in ("data_dir", "log_dir"):
                values[key] = Path(values[key]).resolve()
        except (OSError, ValueError, TypeError) as exc:
            raise StorageError(f"Не удалось прочитать настройки: {exc}") from None
        self._values = values

    def get(self, key, default=None):
        """Return a setting or a supplied default."""
        return self._values.get(key, default)
