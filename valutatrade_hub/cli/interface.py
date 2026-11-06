"""Parse commands, render tables and translate expected errors for the user."""

import argparse
import os
import sys

from prettytable import PrettyTable

from valutatrade_hub.core.currencies import REGISTRY
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    AppError,
    CurrencyNotFoundError,
)
from valutatrade_hub.core.usecases import TradingService
from valutatrade_hub.core.utils import split_command
from valutatrade_hub.decorators import confirm_action
from valutatrade_hub.logging_config import setup_logging
from valutatrade_hub.parser_service.updater import make_updater


class CommandParser(argparse.ArgumentParser):
    """Keep argument errors inside the interactive session."""

    def error(self, message):
        """Raise a printable validation error instead of exiting Python."""
        raise ValueError(message)


def build_parser():
    """Declare commands and arguments without implementing business rules."""
    parser = CommandParser(prog="valutatrade", allow_abbrev=False)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("register", "login"):
        sub = subs.add_parser(name)
        sub.add_argument("--username", required=True)
        sub.add_argument("--password", required=True)
    sub = subs.add_parser("show-portfolio")
    sub.add_argument("--base", default=None)
    for name in ("buy", "sell"):
        sub = subs.add_parser(name)
        sub.add_argument("--currency", required=True)
        sub.add_argument("--amount", required=True, type=float)
        sub.add_argument("--yes", action="store_true")
    sub = subs.add_parser("deposit")
    sub.add_argument("--amount", required=True, type=float)
    sub = subs.add_parser("get-rate")
    sub.add_argument("--from", dest="from_code", required=True)
    sub.add_argument("--to", dest="to_code", required=True)
    sub = subs.add_parser("update-rates")
    sub.add_argument("--source", choices=("coingecko", "exchangerate"))
    sub = subs.add_parser("show-rates")
    sub.add_argument("--currency")
    sub.add_argument("--top", type=int)
    sub.add_argument("--base", default="USD")
    for name in ("logout", "show-history", "currencies", "exit"):
        subs.add_parser(name)
    return parser


def print_table(headers, rows):
    """Render a uniform terminal table."""
    table = PrettyTable(headers)
    for row in rows:
        table.add_row(row)
    print(table)


@confirm_action
def execute_trade(service, args):
    """Call the selected usecase after CLI-level confirmation."""
    result = getattr(service, args.command)(args.currency, args.amount)
    print(
        f"{result['action']}: {result['amount']:.8g} {result['currency_code']}, "
        f"курс {result['rate']:.8g} USD, сумма {result['total']:.2f} USD"
    )
    print(f"Было: {result['before']}; стало: {result['after']}")


def dispatch(service, args):
    """Translate parsed arguments into one application action."""
    command = args.command
    if command in ("register", "login"):
        result = getattr(service, command)(args.username, args.password)
        if command == "register":
            print(
                f"Пользователь '{result['username']}' зарегистрирован "
                f"(id={result['user_id']}). Войдите через login."
            )
        else:
            print(f"Вы вошли как '{result['username']}'")
    elif command == "logout":
        service.logout()
        print("Вы вышли из учётной записи")
    elif command == "deposit":
        print(f"Учебное пополнение. Баланс USD: {service.deposit(args.amount):.2f}")
    elif command in ("buy", "sell"):
        execute_trade(service, args, approved=args.yes)
    elif command == "show-portfolio":
        result = service.show_portfolio(args.base)
        print(f"Портфель '{result['username']}', база {result['base']}")
        if not result["rows"]:
            print("Кошельков пока нет. Для учебных USD: deposit --amount 10000")
        else:
            print_table(
                ["Валюта", "Баланс", f"Стоимость {result['base']}"],
                [
                    (code, f"{balance:.8g}", f"{value:.2f}")
                    for code, balance, value in result["rows"]
                ],
            )
        print(f"ИТОГО: {result['total']:,.2f} {result['base']}")
    elif command == "get-rate":
        rate, timestamp = service.get_rate(args.from_code, args.to_code)
        print(f"Курс {args.from_code.upper()}→{args.to_code.upper()}: {rate:.10g}")
        print(f"Обратный курс: {1 / rate:.10g}; обновлено: {timestamp}")
    elif command == "update-rates":
        result = service.updater.run_update(args.source)
        print(f"Обновлено курсов: {result['updated']}; время: {result['last_refresh']}")
        if result["errors"]:
            print("Обновление частичное; подробности в parser.log:")
            print("\n".join(result["errors"]))
    elif command == "show-rates":
        print_table(
            ["Пара", "Курс", "Получено UTC", "Статус"],
            [
                (pair, f"{rate:.10g}", date, status)
                for pair, rate, date, status in service.show_rates(
                    args.currency, args.top, args.base
                )
            ],
        )
    elif command == "show-history":
        print_table(
            ["Время UTC", "Действие", "Валюта", "Количество", "Курс USD"],
            [
                (
                    r["timestamp"],
                    r["action"],
                    r["currency_code"],
                    r["amount"],
                    r["rate"],
                )
                for r in service.show_history()
            ],
        )
    elif command == "currencies":
        for currency in REGISTRY.values():
            print(currency.get_display_info())


def run_tokens(service, parser, tokens):
    """Handle a single command and leave the interactive loop alive."""
    if not tokens:
        return True
    if tokens[0] == "help":
        tokens = tokens[1:] + ["--help"]
    try:
        args = parser.parse_args(tokens)
        if args.command == "exit":
            return False
        dispatch(service, args)
    except SystemExit as exc:
        if exc.code:
            print("Проверьте аргументы; справка: help")
    except CurrencyNotFoundError as exc:
        print(f"{exc}. Поддерживаются: {', '.join(REGISTRY)}. Справка: help get-rate")
    except ApiRequestError as exc:
        print(f"{exc}. Проверьте ключи и сеть, затем повторите update-rates.")
    except (AppError, ValueError, TypeError, KeyError) as exc:
        print(f"Ошибка: {exc}")
    return True


def main():
    """Start a persistent interactive session or a public one-shot command."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    boot = CommandParser(add_help=False, allow_abbrev=False)
    boot.add_argument("--demo", action="store_true")
    boot.add_argument("--data-dir")
    options, tokens = boot.parse_known_args()
    if options.demo:
        os.environ["VTH_DEMO"] = "1"
        os.environ["VTH_DATA_DIR"] = options.data_dir or "data_demo"
        os.environ["VTH_LOG_DIR"] = "logs_demo"
        print("УЧЕБНЫЙ РЕЖИМ: фиксированные вымышленные курсы, без запросов к API")
    elif options.data_dir:
        os.environ["VTH_DATA_DIR"] = options.data_dir
    try:
        setup_logging()
        service = TradingService(make_updater())
        parser = build_parser()
        if tokens:
            run_tokens(service, parser, tokens)
            return
        print("ValutaTrade Hub. Справка: help. Завершение: exit.")
        while True:
            try:
                tokens = split_command(input("vth> "))
                if not run_tokens(service, parser, tokens):
                    break
            except ValueError as exc:
                print(f"Ошибка: {exc}")
            except (EOFError, KeyboardInterrupt):
                print("\nДо свидания")
                break
    except (AppError, OSError) as exc:
        print(f"Не удалось запустить приложение: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
