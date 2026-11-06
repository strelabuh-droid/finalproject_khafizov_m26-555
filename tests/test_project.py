"""Behavioral checks in isolated temporary data directories, without live APIs."""

import contextlib
import io
import json
import logging
import math
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from valutatrade_hub.cli.interface import build_parser, run_tokens
from valutatrade_hub.core.currencies import Currency, get_currency
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    AppError,
    CurrencyNotFoundError,
    InsufficientFundsError,
    StorageError,
)
from valutatrade_hub.core.models import User, Wallet
from valutatrade_hub.core.usecases import TradingService
from valutatrade_hub.core.utils import iso_time, split_command, utc_now
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.logging_config import setup_logging
from valutatrade_hub.parser_service.api_clients import (
    CoinGeckoClient,
    ExchangeRateApiClient,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage
from valutatrade_hub.parser_service.updater import RatesUpdater, make_updater


class ProjectTests(unittest.TestCase):
    """Verify invariants, persistence, rates, failures and command handling."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(
            os.environ,
            {
                "VTH_DATA_DIR": str(self.root / "data"),
                "VTH_LOG_DIR": str(self.root / "logs"),
                "VTH_RATES_TTL_SECONDS": "300",
                "VTH_DEMO": "1",
            },
        )
        self.environment.start()
        SettingsLoader._instance = DatabaseManager._instance = None
        setup_logging()
        self.updater = make_updater()
        self.service = TradingService(self.updater)
        self.database = DatabaseManager()

    def tearDown(self):
        for name in ("actions", "parser"):
            logger = logging.getLogger(f"valutatrade.{name}")
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)
        self.environment.stop()
        SettingsLoader._instance = DatabaseManager._instance = None
        self.temp.cleanup()

    def ready(self):
        self.service.register("alice", "1234")
        self.service.login("alice", "1234")
        self.service.deposit(10000.0)
        self.updater.run_update()

    def test_user_hash_salt_change_and_public_info(self):
        user = User.create(1, "alice", "1234")
        other = User.create(2, "bob", "1234")
        self.assertNotEqual(user.hashed_password, other.hashed_password)
        self.assertTrue(user.verify_password("1234"))
        self.assertFalse(user.verify_password("wrong"))
        self.assertNotIn("hashed_password", user.get_user_info())
        self.assertNotIn("salt", user.get_user_info())
        user.change_password("5678")
        self.assertTrue(user.verify_password("5678"))
        self.assertFalse(user.verify_password("1234"))
        restored = User.from_dict(user.to_dict())
        self.assertTrue(restored.verify_password("5678"))

    def test_empty_name_and_short_password_rejected(self):
        for name, password in ((" ", "1234"), ("alice", "123")):
            with self.assertRaises(ValueError):
                self.service.register(name, password)
        self.assertEqual(self.database.read("users"), [])

    def test_wallet_invalid_numbers_do_not_change_balance(self):
        wallet = Wallet("USD", 50.0)
        for value in (-1, 0, True, "10", math.nan, math.inf):
            with self.assertRaises(ValueError):
                wallet.deposit(value)
            self.assertEqual(wallet.balance, 50.0)
        with self.assertRaises(ValueError):
            wallet.balance = -1
        with self.assertRaises(InsufficientFundsError):
            wallet.withdraw(51.0)
        self.assertEqual(wallet.balance, 50.0)

    def test_registration_empty_portfolio_and_duplicate(self):
        self.service.register("alice", "1234")
        self.assertEqual(self.database.read("portfolios")[0]["wallets"], {})
        with self.assertRaises(AppError):
            self.service.register("alice", "5678")
        self.assertEqual(len(self.database.read("users")), 1)

    def test_login_errors_and_session_not_persisted(self):
        self.service.register("alice", "1234")
        for username, password in (("bob", "1234"), ("alice", "wrong")):
            with self.assertRaises(AppError):
                self.service.login(username, password)
        self.service.login("alice", "1234")
        self.assertIsNone(TradingService(self.updater).current_user)

    def test_trades_require_login(self):
        for action in (self.service.buy, self.service.sell):
            with self.assertRaises(AppError):
                action("BTC", 1.0)

    def test_full_trade_cycle_and_total(self):
        self.ready()
        result = self.service.buy("btc", 0.05)
        self.assertEqual(result["after"], {"USD": 7000.0, "BTC": 0.05})
        self.service.sell("BTC", 0.01)
        report = self.service.show_portfolio()
        balances = {r[0]: r[1] for r in report["rows"]}
        self.assertAlmostEqual(balances["USD"], 7600.0)
        self.assertAlmostEqual(balances["BTC"], 0.04)
        self.assertAlmostEqual(report["total"], 10000.0)
        self.assertEqual(len(self.service.show_history()), 3)

    def test_failed_buy_and_sell_leave_file_unchanged(self):
        self.ready()
        before = self.database.path("portfolios").read_bytes()
        with self.assertRaises(InsufficientFundsError):
            self.service.buy("BTC", 1.0)
        self.assertEqual(before, self.database.path("portfolios").read_bytes())
        self.service.buy("BTC", 0.05)
        before = self.database.path("portfolios").read_bytes()
        with self.assertRaises(InsufficientFundsError):
            self.service.sell("BTC", 0.06)
        self.assertEqual(before, self.database.path("portfolios").read_bytes())

    def test_data_survive_new_service_and_login(self):
        self.ready()
        self.service.buy("BTC", 0.05)
        new = TradingService(make_updater())
        new.login("alice", "1234")
        self.assertAlmostEqual(new.show_portfolio()["total"], 10000.0)

    def test_portfolio_copy_and_read_only_user(self):
        self.ready()
        portfolio = self.service._portfolio()
        copied = portfolio.wallets
        copied.clear()
        self.assertIsNotNone(portfolio.get_wallet("USD"))
        with self.assertRaises(AttributeError):
            portfolio.user = User.create(2, "bob", "1234")
        with self.assertRaises(ValueError):
            portfolio.add_currency("USD")

    def test_currency_factory_and_abstraction(self):
        with self.assertRaises(TypeError):
            Currency("Name", "ABC")
        with self.assertRaises(CurrencyNotFoundError):
            get_currency("ABC")
        with self.assertRaises(ValueError):
            get_currency("A B")
        self.assertIn("[FIAT]", get_currency("USD").get_display_info())
        self.assertIn("[CRYPTO]", get_currency("BTC").get_display_info())

    def test_inverse_cross_and_same_currency(self):
        self.updater.run_update()
        self.assertAlmostEqual(self.service.get_rate("USD", "BTC")[0], 1 / 60000)
        self.assertAlmostEqual(self.service.get_rate("BTC", "EUR")[0], 60000 / 1.1)
        self.assertEqual(self.service.get_rate("EUR", "EUR")[0], 1.0)

    def test_stale_pair_triggers_refresh(self):
        self.updater.run_update()
        data = self.database.read("rates")
        data["pairs"]["BTC_USD"]["updated_at"] = iso_time(utc_now() - timedelta(days=1))
        self.database.write("rates", data)
        with patch.object(
            self.updater, "run_update", wraps=self.updater.run_update
        ) as run:
            self.assertEqual(self.service.get_rate("BTC", "USD")[0], 60000.0)
            self.assertEqual(run.call_count, 1)

    def test_stale_pair_after_partial_refresh_is_refused(self):
        self.updater.run_update()
        data = self.database.read("rates")
        data["pairs"]["BTC_USD"]["updated_at"] = iso_time(utc_now() - timedelta(days=1))
        self.database.write("rates", data)
        with patch.object(self.updater, "run_update", return_value={"updated": 3}):
            with self.assertRaises(ApiRequestError):
                self.service.get_rate("BTC", "EUR")

    def test_partial_failure_preserves_previous_rates(self):
        self.updater.run_update()
        old = self.database.read("rates")["pairs"]["BTC_USD"]
        bad = Mock(name="broken")
        bad.name, bad.source = "coingecko", "CoinGecko"
        bad.fetch_rates.side_effect = ApiRequestError("offline")
        updater = RatesUpdater(
            [bad, self.updater.clients[1]], RatesStorage(self.database)
        )
        result = updater.run_update()
        self.assertEqual(result["updated"], 3)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(self.database.read("rates")["pairs"]["BTC_USD"], old)
        with self.assertRaises(ApiRequestError):
            RatesUpdater([bad], RatesStorage(self.database)).run_update()

    def test_history_deduplicated_and_older_price_does_not_win(self):
        self.updater.run_update()
        history = self.database.read("exchange_rates")
        storage = RatesStorage(self.database)
        storage.save(history)
        self.assertEqual(len(self.database.read("exchange_rates")), 6)
        row = dict(history[0])
        row["timestamp"] = "2000-01-01T00:00:00.000000Z"
        row["id"], row["rate"] = "BTC_USD_2000-01-01T00:00:00.000000Z", 1.0
        storage.save([row])
        self.assertEqual(self.database.read("rates")["pairs"]["BTC_USD"]["rate"], 60000)

    def test_damaged_json_is_not_erased(self):
        path = self.database.path("users")
        path.write_text("broken JSON", encoding="utf-8")
        with self.assertRaises(StorageError):
            self.database.read("users")
        self.assertEqual(path.read_text(encoding="utf-8"), "broken JSON")

    def test_cache_copies_and_invalidation(self):
        self.database.write("users", [{"user_id": 1}])
        data = self.database.read("users")
        data[0]["user_id"] = 99
        self.assertEqual(self.database.read("users")[0]["user_id"], 1)
        self.database.write("users", [{"user_id": 2}])
        self.assertEqual(self.database.read("users")[0]["user_id"], 2)

    def test_singletons(self):
        self.assertIs(SettingsLoader(), SettingsLoader())
        self.assertIs(DatabaseManager(), self.database)

    def test_cli_quotes_windows_slashes_and_argument_errors(self):
        self.assertEqual(
            split_command('login --username "alice smith" --password a\\b'),
            ["login", "--username", "alice smith", "--password", "a\\b"],
        )
        with self.assertRaises(ValueError):
            split_command('login "alice')
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertTrue(run_tokens(self.service, build_parser(), ["buy"]))
        self.assertIn("Ошибка", stream.getvalue())
        self.assertFalse(run_tokens(self.service, build_parser(), ["exit"]))

    def test_top_contains_only_crypto(self):
        self.updater.run_update()
        rows = self.service.show_rates(top=2)
        self.assertEqual([row[0] for row in rows], ["BTC_USD", "ETH_USD"])
        self.assertEqual(self.service.show_rates(currency="RUB")[0][0], "RUB_USD")

    def response(self, data, status=200):
        response = Mock()
        response.status_code, response.headers = status, {}
        response.json.return_value = data
        return response

    def test_fiat_client_uses_conversion_rates_and_inverts(self):
        config = ParserConfig(EXCHANGERATE_API_KEY="test-secret")
        data = {
            "result": "success",
            "base_code": "USD",
            "conversion_rates": {"USD": 1, "EUR": 0.8, "GBP": 0.5, "RUB": 100},
        }
        with patch("requests.get", return_value=self.response(data)):
            rates = ExchangeRateApiClient(config).fetch_rates()
        self.assertEqual(rates, {"EUR_USD": 1.25, "GBP_USD": 2.0, "RUB_USD": 0.01})

    def test_crypto_client_and_bad_response(self):
        client = CoinGeckoClient(ParserConfig())
        data = {
            "bitcoin": {"usd": 60000},
            "ethereum": {"usd": 3000},
            "solana": {"usd": 150},
        }
        with patch("requests.get", return_value=self.response(data)) as request:
            self.assertEqual(client.fetch_rates()["BTC_USD"], 60000)
            self.assertEqual(request.call_args.kwargs["timeout"], 10)
        with patch(
            "requests.get", return_value=self.response({"bitcoin": {"usd": True}})
        ):
            with self.assertRaises(ApiRequestError):
                client.fetch_rates()

    def test_network_and_http_errors_do_not_leak_api_key(self):
        client = ExchangeRateApiClient(ParserConfig(EXCHANGERATE_API_KEY="test-secret"))
        for status in (401, 429, 500):
            with patch("requests.get", return_value=self.response({}, status)):
                with self.assertRaises(ApiRequestError) as caught:
                    client.fetch_rates()
                self.assertNotIn("test-secret", str(caught.exception))
        with patch(
            "requests.get", side_effect=requests.exceptions.Timeout("test-secret")
        ):
            with self.assertRaises(ApiRequestError) as caught:
                client.fetch_rates()
            self.assertNotIn("test-secret", str(caught.exception))

    def test_logs_have_context_and_exception_is_propagated(self):
        self.ready()
        self.service.buy("BTC", 0.05)
        with self.assertRaises(InsufficientFundsError):
            self.service.sell("BTC", 1.0)
        with self.assertRaises(ValueError):
            self.service.buy("BTC", math.nan)
        lines = (
            (self.root / "logs" / "actions.log")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        events = [json.loads(line) for line in lines]
        self.assertEqual(events[0]["result"], "OK")
        self.assertEqual(events[1]["error_type"], "InsufficientFundsError")
        self.assertIn("before", events[0])
        self.assertNotIn("1234", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
