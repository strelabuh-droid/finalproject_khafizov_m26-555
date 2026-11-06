"""A separate process that refreshes rates periodically without a web server."""

import argparse
import sys
import time

from valutatrade_hub.core.exceptions import AppError
from valutatrade_hub.logging_config import setup_logging
from valutatrade_hub.parser_service.updater import make_updater


def main():
    """Refresh once or every configured interval; stop cleanly with Ctrl+C."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=3600)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval < 60:
        parser.error("--interval должен быть не меньше 60 секунд")
    setup_logging()
    updater = make_updater()
    try:
        while True:
            try:
                result = updater.run_update()
                print(
                    f"Обновлено: {result['updated']}; ошибок: {len(result['errors'])}",
                    flush=True,
                )
            except AppError as exc:
                print(exc, flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nПланировщик остановлен")


if __name__ == "__main__":
    main()
