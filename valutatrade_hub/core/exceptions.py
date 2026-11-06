"""Errors that can be shown to the user without a traceback."""


class AppError(Exception):
    """Base class for expected application errors."""


class InsufficientFundsError(AppError):
    """A wallet cannot pay the requested amount."""

    def __init__(self, available, required, code):
        super().__init__(
            f"Недостаточно средств: доступно {available:.8g} {code}, "
            f"требуется {required:.8g} {code}"
        )


class CurrencyNotFoundError(AppError):
    """A syntactically valid currency is absent from the registry."""

    def __init__(self, code):
        super().__init__(f"Неизвестная валюта '{code}'")


class ApiRequestError(AppError):
    """Rates cannot be obtained or validated."""

    def __init__(self, reason):
        super().__init__(f"Ошибка при обращении к внешнему API: {reason}")


class StorageError(AppError):
    """Stored data cannot be safely read or written."""
