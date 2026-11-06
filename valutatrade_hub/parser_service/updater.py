"""Coordinate API clients, tolerate a partial failure and save valid prices."""

import logging
import os

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import iso_time, number
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.parser_service.api_clients import (
    CoinGeckoClient,
    DemoClient,
    ExchangeRateApiClient,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage


class RatesUpdater:
    """Use injected clients and storage to keep network and persistence separate."""

    def __init__(self, clients, storage):
        self.clients, self.storage = clients, storage

    def run_update(self, source=None):
        """Update all or one source; preserve failed sources' old cache values."""
        if source not in (None, "coingecko", "exchangerate"):
            raise ValueError("Источник: coingecko или exchangerate")
        logger = logging.getLogger("valutatrade.parser")
        logger.info("Starting rates update")
        measurements, errors = [], []
        for client in self.clients:
            if source and source != client.name:
                continue
            try:
                rates = client.fetch_rates()
                batch = []
                if not rates:
                    raise ValueError("Пустой ответ")
                for pair, rate in rates.items():
                    left, right = pair.split("_")
                    details = client.metadata.get(pair, {})
                    timestamp = details.get("timestamp", iso_time())
                    batch.append(
                        {
                            "id": f"{pair}_{timestamp}",
                            "from_currency": left,
                            "to_currency": right,
                            "rate": number(rate, positive=True),
                            "timestamp": timestamp,
                            "source": client.source,
                            "meta": details.get("meta", {}),
                        }
                    )
                measurements.extend(batch)
                logger.info(
                    "Source updated",
                    extra={
                        "event": {
                            "source": client.name,
                            "count": len(batch),
                            "result": "OK",
                        }
                    },
                )
            except (ApiRequestError, ValueError, TypeError) as exc:
                errors.append(f"{client.name}: {exc}")
                logger.error(
                    "Source failed",
                    extra={
                        "event": {
                            "source": client.name,
                            "result": "ERROR",
                            "reason": str(exc),
                        }
                    },
                )
        if not measurements:
            raise ApiRequestError("; ".join(errors) or "нет выбранных источников")
        self.storage.save(measurements)
        logger.info("Rates saved", extra={"event": {"count": len(measurements)}})
        return {
            "updated": len(measurements),
            "errors": errors,
            "last_refresh": self.storage.database.read("rates")["last_refresh"],
        }


def make_updater():
    """Select real or explicitly requested demo clients."""
    config = ParserConfig()
    if os.getenv("VTH_DEMO") == "1":
        clients = [DemoClient(config, name) for name in ("coingecko", "exchangerate")]
    else:
        clients = [CoinGeckoClient(config), ExchangeRateApiClient(config)]
    return RatesUpdater(clients, RatesStorage(DatabaseManager()))
