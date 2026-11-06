"""Users, individual wallets and a user's complete portfolio."""

import hashlib
import hmac
import secrets
from datetime import datetime

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.exceptions import InsufficientFundsError
from valutatrade_hub.core.utils import iso_time, number, parse_time, utc_now


class User:
    """Store identity and a salted one-way password hash."""

    def __init__(self, user_id, username, hashed_password, salt, registration_date):
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
            raise ValueError("user_id должен быть положительным целым числом")
        self._user_id = user_id
        self.username = username
        if not hashed_password or not salt:
            raise ValueError("Хеш и соль не могут быть пустыми")
        self._hashed_password, self._salt = hashed_password, salt
        if not isinstance(registration_date, datetime):
            raise ValueError("registration_date должен быть datetime")
        if registration_date.tzinfo is None:
            raise ValueError("Дата регистрации должна содержать часовой пояс")
        self._registration_date = registration_date

    @property
    def user_id(self):
        """Return the immutable identity."""
        return self._user_id

    @property
    def username(self):
        """Return the public user name."""
        return self._username

    @username.setter
    def username(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Имя пользователя не может быть пустым")
        self._username = value.strip()

    @property
    def hashed_password(self):
        """Return the stored hash for serialization."""
        return self._hashed_password

    @property
    def salt(self):
        """Return the salt for serialization."""
        return self._salt

    @property
    def registration_date(self):
        """Return the immutable registration date."""
        return self._registration_date

    def change_password(self, new_password):
        """Validate a password, generate salt and calculate SHA-256."""
        if not isinstance(new_password, str) or len(new_password) < 4:
            raise ValueError("Пароль должен быть не короче 4 символов")
        self._salt = secrets.token_hex(16)
        self._hashed_password = hashlib.sha256(
            (new_password + self._salt).encode("utf-8")
        ).hexdigest()

    def verify_password(self, password):
        """Compare hashes without storing the entered password."""
        candidate = hashlib.sha256((password + self._salt).encode()).hexdigest()
        return hmac.compare_digest(candidate, self._hashed_password)

    def get_user_info(self):
        """Return public identity without password hash or salt."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "registration_date": iso_time(self.registration_date),
        }

    def to_dict(self):
        """Serialize private credentials only for internal storage."""
        return {
            **self.get_user_info(),
            "hashed_password": self.hashed_password,
            "salt": self.salt,
        }

    @classmethod
    def create(cls, user_id, username, password):
        """Construct a user from a new plaintext password."""
        user = cls(user_id, username, "pending", "pending", utc_now())
        user.change_password(password)
        return user

    @classmethod
    def from_dict(cls, data):
        """Restore a stored user without hashing the hash again."""
        return cls(
            data["user_id"],
            data["username"],
            data["hashed_password"],
            data["salt"],
            parse_time(data["registration_date"]),
        )


class Wallet:
    """Keep a nonnegative balance in a single registered currency."""

    def __init__(self, currency_code, balance=0.0):
        self.currency_code = get_currency(currency_code).code
        self.balance = balance

    @property
    def balance(self):
        """Return the current amount."""
        return self._balance

    @balance.setter
    def balance(self, value):
        self._balance = number(value)

    def deposit(self, amount):
        """Add a strictly positive finite amount."""
        self.balance += number(amount, positive=True)

    def withdraw(self, amount):
        """Subtract only an amount covered by the balance."""
        amount = number(amount, positive=True)
        if amount > self.balance:
            raise InsufficientFundsError(self.balance, amount, self.currency_code)
        self.balance -= amount

    def get_balance_info(self):
        """Return a readable balance."""
        return f"{self.currency_code}: {self.balance:.8g}"


class Portfolio:
    """Aggregate wallets and value them through an injected rate provider."""

    def __init__(self, user_id, wallets, user, rate_provider, transactions=None):
        if user.user_id != user_id:
            raise ValueError("Портфель должен принадлежать этому пользователю")
        self._user_id, self._user = user_id, user
        self._wallets = dict(wallets)
        self._rate_provider = rate_provider
        self._transactions = list(transactions or [])

    @property
    def user(self):
        """Return the owner without allowing reassignment."""
        return self._user

    @property
    def wallets(self):
        """Return a shallow dictionary copy; wallet objects remain shared."""
        return self._wallets.copy()

    def add_currency(self, currency_code):
        """Create a unique wallet; refuse accidental duplicate creation."""
        code = get_currency(currency_code).code
        if code in self._wallets:
            raise ValueError(f"Кошелёк {code} уже существует")
        self._wallets[code] = Wallet(code)
        return self._wallets[code]

    def get_wallet(self, currency_code):
        """Return a wallet, or None when the user has none in that currency."""
        return self._wallets.get(get_currency(currency_code).code)

    def get_total_value(self, base_currency="USD"):
        """Convert every nonzero wallet to a validated base currency."""
        base = get_currency(base_currency).code
        return sum(
            wallet.balance * self._rate_provider(code, base)[0]
            for code, wallet in self._wallets.items()
            if wallet.balance
        )

    @property
    def transactions(self):
        """Return independent dictionaries for the transaction history."""
        return [dict(row) for row in self._transactions]

    def record_transaction(self, action, currency_code, amount, rate):
        """Append a successful simulated operation to persistent history."""
        self._transactions.append(
            {
                "action": action,
                "currency_code": currency_code,
                "amount": amount,
                "rate": rate,
                "base": "USD",
                "timestamp": iso_time(),
            }
        )

    def to_dict(self):
        """Serialize only persistent state, not callbacks or user objects."""
        return {
            "user_id": self._user_id,
            "transactions": self.transactions,
            "wallets": {
                code: {"balance": wallet.balance}
                for code, wallet in self._wallets.items()
            },
        }
