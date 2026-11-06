"""JSON storage with atomic file replacement and a process-local lock."""

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from threading import RLock

from valutatrade_hub.core.exceptions import StorageError
from valutatrade_hub.core.utils import create_cached_reader
from valutatrade_hub.infra.settings import SettingsLoader

DEFAULTS = {
    "users": [],
    "portfolios": [],
    "rates": {"pairs": {}, "last_refresh": None},
    "exchange_rates": [],
}


class DatabaseManager:
    """Keep one storage manager and one lock in the current process."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            obj = super().__new__(cls)
            obj.root = SettingsLoader().get("data_dir")
            obj.lock = RLock()
            obj._read_cached, obj._clear = create_cached_reader(obj._load)
            obj.root.mkdir(parents=True, exist_ok=True)
            cls._instance = obj
        return cls._instance

    def path(self, name):
        """Resolve only a known logical file name."""
        if name not in DEFAULTS:
            raise ValueError("Неизвестный файл данных")
        return self.root / f"{name}.json"

    @staticmethod
    def _load(path):
        return json.loads(path.read_text(encoding="utf-8"))

    def read(self, name):
        """Read independent data; never replace a damaged file with defaults."""
        with self.lock:
            path = self.path(name)
            try:
                if not path.exists():
                    self.write(name, deepcopy(DEFAULTS[name]))
                data = self._read_cached(path)
                if not isinstance(data, type(DEFAULTS[name])):
                    raise ValueError("Неверный тип корневого элемента JSON")
                return data
            except (OSError, ValueError, TypeError) as exc:
                raise StorageError(f"Ошибка чтения {path.name}: {exc}") from None

    def write(self, name, data):
        """Write UTF-8 into a sibling temp file, then atomically replace."""
        with self.lock:
            path = self.path(name)
            temp_name = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.root,
                    suffix=".tmp",
                    delete=False,
                ) as stream:
                    temp_name = stream.name
                    json.dump(
                        data, stream, ensure_ascii=False, indent=2, allow_nan=False
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, path)
                self._clear()
            except (OSError, ValueError, TypeError) as exc:
                raise StorageError(f"Ошибка записи {path.name}: {exc}") from None
            finally:
                if temp_name and Path(temp_name).exists():
                    Path(temp_name).unlink(missing_ok=True)
