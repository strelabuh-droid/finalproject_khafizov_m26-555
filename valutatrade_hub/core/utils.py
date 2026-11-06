"""Validation, UTC time, command splitting and a closure cache."""

import math
import re
import time
from copy import deepcopy
from datetime import UTC, datetime


def normalize_code(value):
    """Return an uppercase alphabetic currency code of 2 to 5 letters."""
    if not isinstance(value, str):
        raise ValueError("Код валюты должен быть строкой")
    code = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{2,5}", code):
        raise ValueError("Код валюты: от 2 до 5 латинских букв без пробелов")
    return code


def number(value, *, positive=False):
    """Validate a finite number; reject booleans and numeric strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Ожидается число, а не строка или bool")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(
            "Сумма должна быть конечным положительным числом"
            if positive
            else "Баланс должен быть конечным и неотрицательным"
        )
    return result


def utc_now():
    """Return the current aware UTC datetime."""
    return datetime.now(UTC)


def iso_time(value=None):
    """Serialize an aware datetime as ISO UTC, with microseconds."""
    return (value or utc_now()).astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value):
    """Parse an ISO timestamp and refuse ambiguous naive dates."""
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("У даты должен быть часовой пояс UTC")
    return result.astimezone(UTC)


def create_cached_reader(loader, ttl=1.0):
    """Return read and clear closures sharing a private in-memory cache."""
    cache = {}

    def read(path):
        stamp = path.stat()
        key = (stamp.st_mtime_ns, stamp.st_size)
        old = cache.get(path)
        if old and old[0] == key and time.monotonic() - old[1] < ttl:
            return deepcopy(old[2])
        data = loader(path)
        cache[path] = (key, time.monotonic(), deepcopy(data))
        return data

    def clear():
        cache.clear()

    return read, clear


def split_command(line):
    """Split our small CLI language, preserving backslashes on Windows."""
    tokens, current = [], []
    quote = None
    started = False
    for char in line:
        if quote:
            if char == quote:
                quote = None
            else:
                current.append(char)
        elif char in "\"'":
            quote = char
            started = True
        elif char.isspace():
            if started:
                tokens.append("".join(current))
                current, started = [], False
        else:
            current.append(char)
            started = True
    if quote:
        raise ValueError("Не закрыта кавычка в команде")
    if started:
        tokens.append("".join(current))
    return tokens
