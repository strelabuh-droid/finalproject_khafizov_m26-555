"""Reusable operation logging and interactive confirmation."""

import inspect
import logging
import math
from functools import wraps


def log_action(action, verbose=False):
    """Log a domain call without logging passwords or swallowing errors."""

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            bound = inspect.signature(function).bind(*args, **kwargs).arguments
            service = bound["self"]
            user = service.current_user
            event = {
                "action": action,
                "username": user.username if user else None,
                "currency_code": bound.get("currency_code"),
                "amount": bound.get("amount"),
                "base": "USD",
                "rate": None,
            }
            if isinstance(event["amount"], float) and not math.isfinite(
                event["amount"]
            ):
                event["amount"] = str(event["amount"])
            logger = logging.getLogger("valutatrade.actions")
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                event.update(
                    result="ERROR",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                logger.info("Domain operation", extra={"event": event})
                raise
            event.update(
                result="OK",
                currency_code=result["currency_code"],
                amount=result["amount"],
                rate=result["rate"],
            )
            if verbose:
                event.update(before=result["before"], after=result["after"])
            logger.info("Domain operation", extra={"event": event})
            return result

        return wrapped

    return decorate


def confirm_action(function):
    """Ask in the CLI layer; the --yes flag supplies explicit confirmation."""

    @wraps(function)
    def wrapped(*args, approved=False, **kwargs):
        if not approved and input("Подтвердить сделку? [y/N]: ").strip().lower() != "y":
            print("Сделка отменена")
            return None
        return function(*args, **kwargs)

    return wrapped
