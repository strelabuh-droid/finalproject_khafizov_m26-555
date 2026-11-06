"""API endpoints, selected currencies and environment-based credentials."""

import os
from dataclasses import dataclass, field

from valutatrade_hub.infra.settings import SettingsLoader


@dataclass
class ParserConfig:
    """Keep mutable dictionaries separate for each configuration object."""

    EXCHANGERATE_API_KEY: str = field(
        default_factory=lambda: os.getenv("EXCHANGERATE_API_KEY", "")
    )
    COINGECKO_API_KEY: str = field(
        default_factory=lambda: os.getenv("COINGECKO_API_KEY", "")
    )
    COINGECKO_URL: str = "https://api.coingecko.com/api/v3/simple/price"
    EXCHANGERATE_API_URL: str = "https://v6.exchangerate-api.com/v6"
    BASE_FIAT_CURRENCY: str = "USD"
    FIAT_CURRENCIES: tuple = ("EUR", "GBP", "RUB")
    CRYPTO_CURRENCIES: tuple = ("BTC", "ETH", "SOL")
    CRYPTO_ID_MAP: dict = field(
        default_factory=lambda: {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "SOL": "solana",
        }
    )
    REQUEST_TIMEOUT: int = 10

    @property
    def RATES_FILE_PATH(self):
        """Return the same path used by Core Service."""
        return SettingsLoader().get("data_dir") / "rates.json"

    @property
    def HISTORY_FILE_PATH(self):
        """Return the parser history path."""
        return SettingsLoader().get("data_dir") / "exchange_rates.json"
