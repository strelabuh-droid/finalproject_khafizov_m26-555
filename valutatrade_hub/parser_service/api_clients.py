"""Normalize two external APIs into prices expressed in USD."""

import time
from abc import ABC, abstractmethod

import requests

from valutatrade_hub.core.currencies import CryptoCurrency, get_currency
from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import iso_time, number


class BaseApiClient(ABC):
    """Every client returns pair-to-float rates and separate metadata."""

    def __init__(self, config):
        self.config = config
        self.metadata = {}

    @abstractmethod
    def fetch_rates(self) -> dict:
        """Return a mapping such as {'BTC_USD': 60000.0}."""

    def _request(self, url, **kwargs):
        start = time.perf_counter()
        try:
            response = requests.get(url, timeout=self.config.REQUEST_TIMEOUT, **kwargs)
            if response.status_code != 200:
                hints = {
                    401: "проверьте ключ",
                    403: "доступ запрещён",
                    429: "лимит запросов, повторите позже",
                }
                hint = hints.get(response.status_code, "сервис недоступен")
                raise ApiRequestError(
                    f"{self.name}: HTTP {response.status_code}, {hint}"
                )
            payload = response.json()
        except requests.exceptions.RequestException as exc:
            # Do not include str(exc): an ExchangeRate URL contains the secret key.
            raise ApiRequestError(
                f"{self.name}: сеть или JSON, {type(exc).__name__}"
            ) from None
        except ValueError:
            raise ApiRequestError(f"{self.name}: некорректный JSON") from None
        meta = {
            "request_ms": round((time.perf_counter() - start) * 1000, 2),
            "status_code": response.status_code,
            "etag": response.headers.get("ETag"),
        }
        if not isinstance(payload, dict):
            raise ApiRequestError(f"{self.name}: ожидался объект JSON")
        return payload, meta


class CoinGeckoClient(BaseApiClient):
    """Fetch selected cryptocurrency prices using coin IDs."""

    name = "coingecko"
    source = "CoinGecko"

    def fetch_rates(self) -> dict:
        """Return cryptocurrency-to-USD rates after validating all values."""
        ids = self.config.CRYPTO_ID_MAP
        headers = {}
        if self.config.COINGECKO_API_KEY:
            headers["x-cg-demo-api-key"] = self.config.COINGECKO_API_KEY
        payload, meta = self._request(
            self.config.COINGECKO_URL,
            headers=headers,
            params={
                "ids": ",".join(ids[code] for code in self.config.CRYPTO_CURRENCIES),
                "vs_currencies": "usd",
                "include_last_updated_at": "true",
                "include_market_cap": "true",
            },
        )
        rates, metadata = {}, {}
        timestamp = iso_time()
        try:
            for code in self.config.CRYPTO_CURRENCIES:
                row = payload[ids[code]]
                pair = f"{code}_USD"
                rates[pair] = number(row["usd"], positive=True)
                details = {
                    **meta,
                    "raw_id": ids[code],
                    "provider_timestamp": row.get("last_updated_at"),
                    "market_cap": row.get("usd_market_cap"),
                }
                metadata[pair] = {"timestamp": timestamp, "meta": details}
                currency = get_currency(code)
                if isinstance(currency, CryptoCurrency) and row.get("usd_market_cap"):
                    currency.market_cap = number(row["usd_market_cap"])
        except (KeyError, TypeError, ValueError):
            raise ApiRequestError(
                "CoinGecko: ответ не содержит корректных цен"
            ) from None
        self.metadata = metadata
        return rates


class ExchangeRateApiClient(BaseApiClient):
    """Invert USD-to-fiat API values to obtain fiat-to-USD prices."""

    name = "exchangerate"
    source = "ExchangeRate-API"

    def fetch_rates(self) -> dict:
        """Return EUR_USD, GBP_USD and RUB_USD, never USD_EUR mislabeled."""
        if not self.config.EXCHANGERATE_API_KEY:
            raise ApiRequestError("ExchangeRate-API: задайте EXCHANGERATE_API_KEY")
        base = self.config.BASE_FIAT_CURRENCY
        url = (
            f"{self.config.EXCHANGERATE_API_URL}/"
            f"{self.config.EXCHANGERATE_API_KEY}/latest/{base}"
        )
        payload, meta = self._request(url)
        if payload.get("result") != "success":
            # Only allow known error codes to avoid echoing arbitrary API text.
            reason = payload.get("error-type")
            known = {
                "invalid-key",
                "inactive-account",
                "quota-reached",
                "unsupported-code",
                "malformed-request",
            }
            reason = reason if reason in known else "неуспешный ответ"
            raise ApiRequestError(f"ExchangeRate-API: {reason}")
        try:
            if payload["base_code"] != base:
                raise ValueError("Неверная база ответа")
            raw = payload["conversion_rates"]
            usd = number(raw["USD"], positive=True)
            rates = {
                f"{code}_USD": usd / number(raw[code], positive=True)
                for code in self.config.FIAT_CURRENCIES
            }
        except (KeyError, TypeError, ValueError):
            raise ApiRequestError("ExchangeRate-API: некорректные курсы") from None
        self.metadata = {
            pair: {
                "timestamp": iso_time(),
                "meta": {
                    **meta,
                    "provider_timestamp": payload.get("time_last_update_utc"),
                },
            }
            for pair in rates
        }
        return rates


class DemoClient(BaseApiClient):
    """Provide deterministic educational prices without any network calls."""

    def __init__(self, config, name):
        super().__init__(config)
        self.name, self.source = name, f"Demo-{name}"

    def fetch_rates(self) -> dict:
        """Generate fresh receipt timestamps for explicitly marked demo data."""
        groups = {
            "coingecko": {"BTC_USD": 60000.0, "ETH_USD": 3000.0, "SOL_USD": 150.0},
            "exchangerate": {"EUR_USD": 1.1, "GBP_USD": 1.25, "RUB_USD": 0.01},
        }
        rates = groups[self.name]
        self.metadata = {
            pair: {"timestamp": iso_time(), "meta": {"demo": True}} for pair in rates
        }
        return rates.copy()
