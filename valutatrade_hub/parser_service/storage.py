"""Merge the latest cache and append deduplicated historical measurements."""

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.utils import number, parse_time


class RatesStorage:
    """Persist each JSON file atomically through DatabaseManager."""

    def __init__(self, database):
        self.database = database

    def save(self, measurements):
        """Keep newer cache entries, historical IDs and previous-price context."""
        with self.database.lock:
            cache = self.database.read("rates")
            history = self.database.read("exchange_rates")
            known_ids = {row["id"] for row in history}
            pairs = cache["pairs"]
            for row in measurements:
                get_currency(row["from_currency"])
                get_currency(row["to_currency"])
                number(row["rate"], positive=True)
                timestamp = parse_time(row["timestamp"])
                pair = f"{row['from_currency']}_{row['to_currency']}"
                row = {**row, "meta": dict(row.get("meta", {}))}
                previous = pairs.get(pair)
                if previous:
                    row["meta"]["previous_rate"] = previous["rate"]
                    row["meta"]["change_percent"] = (
                        row["rate"] / previous["rate"] - 1
                    ) * 100
                if row["id"] not in known_ids:
                    history.append(row)
                    known_ids.add(row["id"])
                if previous is None or timestamp > parse_time(previous["updated_at"]):
                    pairs[pair] = {
                        "rate": row["rate"],
                        "updated_at": row["timestamp"],
                        "source": row["source"],
                    }
            if pairs:
                cache["last_refresh"] = max(
                    item["updated_at"] for item in pairs.values()
                )
                cache["source"] = "ParserService"
            # Retry after a partial write is safe because historical IDs are unique.
            self.database.write("exchange_rates", history)
            self.database.write("rates", cache)
