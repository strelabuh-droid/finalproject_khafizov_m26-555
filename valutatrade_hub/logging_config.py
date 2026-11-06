"""UTF-8 JSON logs with file size rotation."""

import json
import logging
from logging.handlers import RotatingFileHandler

from valutatrade_hub.core.utils import iso_time
from valutatrade_hub.infra.settings import SettingsLoader


class JsonFormatter(logging.Formatter):
    """Serialize one structured event per line."""

    def format(self, record):
        """Include standard fields and explicitly supplied event context."""
        data = {
            "timestamp": iso_time(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        data.update(getattr(record, "event", {}))
        return json.dumps(data, ensure_ascii=False, allow_nan=False, default=str)


def setup_logging():
    """Configure separate action and parser loggers once."""
    settings = SettingsLoader()
    root = settings.get("log_dir")
    root.mkdir(parents=True, exist_ok=True)
    for name in ("actions", "parser"):
        logger = logging.getLogger(f"valutatrade.{name}")
        logger.setLevel(settings.get("log_level"))
        logger.propagate = False
        if not logger.handlers:
            handler = RotatingFileHandler(
                root / f"{name}.log",
                encoding="utf-8",
                maxBytes=settings.get("log_max_bytes"),
                backupCount=settings.get("log_backup_count"),
            )
            handler.setFormatter(JsonFormatter())
            logger.addHandler(handler)
