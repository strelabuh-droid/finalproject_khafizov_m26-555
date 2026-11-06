"""Application actions; no input(), print() or CLI parsing belongs here."""

from valutatrade_hub.core.currencies import CryptoCurrency, get_currency
from valutatrade_hub.core.exceptions import ApiRequestError, AppError, StorageError
from valutatrade_hub.core.models import Portfolio, User, Wallet
from valutatrade_hub.core.utils import iso_time, number, parse_time, utc_now
from valutatrade_hub.decorators import log_action
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


class TradingService:
    """Hold the current login and coordinate models, storage and rate updates."""

    def __init__(self, updater):
        self.database = DatabaseManager()
        self.settings = SettingsLoader()
        self.updater = updater
        self.current_user = None

    def _require_login(self):
        if self.current_user is None:
            raise AppError("Сначала выполните login")
        return self.current_user

    def register(self, username, password):
        """Create an identity and an initially empty portfolio."""
        if not isinstance(username, str) or not username.strip():
            raise ValueError("Имя пользователя не может быть пустым")
        username = username.strip()
        with self.database.lock:
            users = self.database.read("users")
            if any(row["username"] == username for row in users):
                raise AppError(f"Имя пользователя '{username}' уже занято")
            user_id = max((row["user_id"] for row in users), default=0) + 1
            user = User.create(user_id, username, password)
            portfolios = self.database.read("portfolios")
            # Write the portfolio first; a failed second write is rolled back.
            self.database.write(
                "portfolios",
                portfolios + [{"user_id": user_id, "wallets": {}, "transactions": []}],
            )
            try:
                self.database.write("users", users + [user.to_dict()])
            except StorageError:
                self.database.write("portfolios", portfolios)
                raise
        return user.get_user_info()

    def login(self, username, password):
        """Change the current session only after successful authentication."""
        for row in self.database.read("users"):
            if row["username"] == username.strip():
                user = User.from_dict(row)
                if not user.verify_password(password):
                    raise AppError("Неверный пароль")
                self.current_user = user
                return user.get_user_info()
        raise AppError(f"Пользователь '{username}' не найден")

    def logout(self):
        """Forget the in-memory login without deleting saved data."""
        self.current_user = None

    def _portfolio(self):
        user = self._require_login()
        for row in self.database.read("portfolios"):
            if row["user_id"] == user.user_id:
                wallets = {
                    code: Wallet(code, data["balance"])
                    for code, data in row["wallets"].items()
                }
                return Portfolio(
                    user.user_id,
                    wallets,
                    user,
                    self.get_rate,
                    row.get("transactions", []),
                )
        raise StorageError("Не найден портфель пользователя")

    def _save_portfolio(self, portfolio):
        rows = self.database.read("portfolios")
        replacement = portfolio.to_dict()
        for index, row in enumerate(rows):
            if row["user_id"] == replacement["user_id"]:
                rows[index] = replacement
                self.database.write("portfolios", rows)
                return
        raise StorageError("Не найден портфель для сохранения")

    def deposit(self, amount):
        """Issue simulated USD to the logged-in user's wallet."""
        self._require_login()
        amount = number(amount, positive=True)
        with self.database.lock:
            portfolio = self._portfolio()
            wallet = portfolio.get_wallet("USD") or portfolio.add_currency("USD")
            wallet.deposit(amount)
            portfolio.record_transaction("DEPOSIT", "USD", amount, 1.0)
            self._save_portfolio(portfolio)
            return wallet.balance

    def _quote(self, pairs, from_code, to_code, fresh=True):
        def leg(code):
            if code == "USD":
                return 1.0, None
            row = pairs.get(f"{code}_USD")
            if row is None:
                raise ApiRequestError(f"нет курса {code}→USD; выполните update-rates")
            rate = number(row["rate"], positive=True)
            timestamp = parse_time(row["updated_at"])
            age = (utc_now() - timestamp).total_seconds()
            if fresh and (age < -60 or age > self.settings.get("rates_ttl_seconds")):
                raise ApiRequestError(
                    f"курс {code}→USD устарел; выполните update-rates"
                )
            return rate, row["updated_at"]

        left, left_time = leg(from_code)
        right, right_time = leg(to_code)
        times = [value for value in (left_time, right_time) if value]
        timestamp = min(times, key=parse_time) if times else iso_time()
        return number(left / right, positive=True), timestamp

    def get_rate(self, from_code, to_code):
        """Return rate and oldest leg timestamp; attempt one refresh if needed."""
        left, right = get_currency(from_code).code, get_currency(to_code).code
        if left == right:
            return 1.0, iso_time()
        try:
            return self._quote(self.database.read("rates")["pairs"], left, right)
        except ApiRequestError:
            self.updater.run_update()
            # Partial update is insufficient if the requested leg remains stale.
            return self._quote(self.database.read("rates")["pairs"], left, right)

    def _trade(self, action, currency_code, amount):
        self._require_login()
        code = get_currency(currency_code).code
        amount = number(amount, positive=True)
        if code == "USD":
            raise ValueError(
                "USD — расчётная валюта; используйте deposit для пополнения"
            )
        rate, updated_at = self.get_rate(code, "USD")
        total = number(amount * rate, positive=True)
        with self.database.lock:
            portfolio = self._portfolio()
            wallet = portfolio.get_wallet(code)
            usd = portfolio.get_wallet("USD")
            before = {
                "USD": usd.balance if usd else 0.0,
                code: wallet.balance if wallet else 0.0,
            }
            if action == "BUY":
                usd = usd or portfolio.add_currency("USD")
                usd.withdraw(total)
                wallet = wallet or portfolio.add_currency(code)
                wallet.deposit(amount)
            else:
                if wallet is None:
                    raise AppError(
                        f"У вас нет кошелька '{code}'. Он создаётся при первой покупке."
                    )
                wallet.withdraw(amount)
                usd = usd or portfolio.add_currency("USD")
                usd.deposit(total)
            portfolio.record_transaction(action, code, amount, rate)
            self._save_portfolio(portfolio)
        return {
            "action": action,
            "currency_code": code,
            "amount": amount,
            "rate": rate,
            "total": total,
            "updated_at": updated_at,
            "before": before,
            "after": {"USD": usd.balance, code: wallet.balance},
        }

    @log_action("BUY", verbose=True)
    def buy(self, currency_code, amount):
        """Pay USD and increase the purchased currency in one saved portfolio."""
        return self._trade("BUY", currency_code, amount)

    @log_action("SELL", verbose=True)
    def sell(self, currency_code, amount):
        """Decrease a currency and credit the corresponding USD proceeds."""
        return self._trade("SELL", currency_code, amount)

    def show_portfolio(self, base=None):
        """Return rows and a total in a supported base currency."""
        self._require_login()
        base = get_currency(base or self.settings.get("default_base_currency")).code
        portfolio = self._portfolio()
        rows = []
        for code, wallet in sorted(portfolio.wallets.items()):
            rate = self.get_rate(code, base)[0] if wallet.balance else 0.0
            rows.append((code, wallet.balance, wallet.balance * rate))
        return {
            "username": portfolio.user.username,
            "base": base,
            "rows": rows,
            "total": portfolio.get_total_value(base),
        }

    def show_rates(self, currency=None, top=None, base="USD"):
        """Inspect the local cache without silently refreshing stale data."""
        base = get_currency(base).code
        code = get_currency(currency).code if currency else None
        if top is not None and (isinstance(top, bool) or top < 1):
            raise ValueError("--top должен быть положительным целым числом")
        pairs = self.database.read("rates")["pairs"]
        if not pairs:
            raise AppError("Локальный кеш курсов пуст. Выполните 'update-rates'.")
        rows = []
        codes = sorted({pair.split("_")[0] for pair in pairs})
        for candidate in codes:
            if code and code != candidate:
                continue
            if top is not None and not isinstance(
                get_currency(candidate), CryptoCurrency
            ):
                continue
            rate, timestamp = self._quote(pairs, candidate, base, fresh=False)
            age = (utc_now() - parse_time(timestamp)).total_seconds()
            status = (
                "устарел" if age > self.settings.get("rates_ttl_seconds") else "свежий"
            )
            rows.append((f"{candidate}_{base}", rate, timestamp, status))
        if not rows:
            raise AppError(f"Курс для '{code or 'криптовалют'}' не найден в кеше")
        return sorted(rows, key=lambda row: row[1], reverse=True)[:top] if top else rows

    def show_history(self):
        """Return the logged-in user's saved simulated transactions."""
        return self._portfolio().transactions
