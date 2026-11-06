"""Currency abstraction, two subclasses and a registry factory."""

from abc import ABC, abstractmethod

from valutatrade_hub.core.exceptions import CurrencyNotFoundError
from valutatrade_hub.core.utils import normalize_code, number


class Currency(ABC):
    """Describe a currency through a common display interface."""

    def __init__(self, name, code):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Название валюты не может быть пустым")
        if code != normalize_code(code):
            raise ValueError("В Currency передавайте код в верхнем регистре")
        self.name, self.code = name.strip(), code

    @abstractmethod
    def get_display_info(self):
        """Return human-readable currency information."""


class FiatCurrency(Currency):
    """A currency issued by a country or currency union."""

    def __init__(self, name, code, issuing_country):
        super().__init__(name, code)
        self.issuing_country = issuing_country

    def get_display_info(self):
        """Include the issuing country or union."""
        return f"[FIAT] {self.code} — {self.name} (Issuing: {self.issuing_country})"


class CryptoCurrency(Currency):
    """A cryptocurrency with algorithm and latest known market cap."""

    def __init__(self, name, code, algorithm, market_cap=0.0):
        super().__init__(name, code)
        self.algorithm = algorithm
        self.market_cap = number(market_cap)

    def get_display_info(self):
        """Include the algorithm and capitalization if known."""
        cap = f"{self.market_cap:.2e}" if self.market_cap else "unknown"
        return (
            f"[CRYPTO] {self.code} — {self.name} (Algo: {self.algorithm}, MCAP: {cap})"
        )


REGISTRY = {
    "USD": FiatCurrency("US Dollar", "USD", "United States"),
    "EUR": FiatCurrency("Euro", "EUR", "Eurozone"),
    "GBP": FiatCurrency("Pound Sterling", "GBP", "United Kingdom"),
    "RUB": FiatCurrency("Russian Ruble", "RUB", "Russia"),
    "BTC": CryptoCurrency("Bitcoin", "BTC", "SHA-256"),
    "ETH": CryptoCurrency("Ethereum", "ETH", "Keccak-256 and PoS"),
    "SOL": CryptoCurrency("Solana", "SOL", "SHA-256 and PoH"),
}


def get_currency(code):
    """Resolve a code, raising a domain exception for unknown currencies."""
    code = normalize_code(code)
    if code not in REGISTRY:
        raise CurrencyNotFoundError(code)
    return REGISTRY[code]
