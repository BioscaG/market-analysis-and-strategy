"""Base class for pump anticipation trading strategy.

Implements the core buy-and-sell logic used across all exchanges:
1. Detect a pump signal (handled externally by the alert system)
2. Execute a market buy order
3. Place a limit sell at a target profit
4. Manage partial exits and timeouts
5. Fall back to track_sell for optimal exit pricing
"""

from abc import ABC, abstractmethod
import time
import math
import ccxt
from colorama import Fore, Style


class BasePumpTrader(ABC):
    """Abstract base for exchange-specific pump traders.

    Subclasses must implement `_create_exchange()` to return a configured
    ccxt exchange instance. Override `_market_buy_uses_cost` to indicate
    whether the exchange's market buy accepts a USD cost (True) or a
    token amount (False).
    """

    def __init__(self) -> None:
        self.exchange: ccxt.Exchange = self._create_exchange()
        self._warmup_connection()

    @abstractmethod
    def _create_exchange(self) -> ccxt.Exchange:
        """Return a configured ccxt exchange instance with API credentials."""
        ...

    @property
    def _market_buy_uses_cost(self) -> bool:
        """If True, create_market_buy_order receives USD cost instead of token amount."""
        return False

    def _warmup_connection(self) -> None:
        """Pre-warm the HTTP connection to reduce latency on the first real trade."""
        try:
            self.exchange.fetch_ticker("BTC/USDT")
        except Exception:
            pass

    def get_price(self, pair: str) -> float:
        """Fetch the last traded price for a given pair."""
        ticker = self.exchange.fetch_ticker(pair)
        return ticker["last"]

    def get_available_coins(self, symbol: str) -> float:
        """Return the free balance for a given symbol."""
        while True:
            try:
                balance = self.exchange.fetch_balance()
                break
            except Exception:
                time.sleep(0.2)
        if symbol not in balance:
            return 0
        return balance[symbol]["free"]

    def buy_and_sell(
        self,
        symbol: str,
        usd: float,
        benefit_partial: float,
        benefit_total: float,
        time_limit_partial: float,
        time_limit_total: float,
        min_up_start_time: float,
        slippage: float,
    ) -> None:
        """Execute a full pump trade cycle: buy, set take-profit, manage exit.

        Args:
            symbol: Token symbol without quote (e.g. "DOGE").
            usd: Amount in USDT to invest.
            benefit_partial: Partial profit target as a fraction (e.g. 0.004 = 0.4%).
            benefit_total: Full profit target as a fraction (e.g. 0.01 = 1%).
            time_limit_partial: Seconds before switching to partial take-profit.
            time_limit_total: Seconds before forced exit via track_sell.
            min_up_start_time: Minimum price increase to start partial timer.
            slippage: Maximum acceptable slippage (currently used for logging).
        """
        pair = f"{symbol}/USDT"

        # --- Phase 1: Market Buy ---
        order_book = self._fetch_order_book_safe(pair)
        if order_book is None:
            return

        best_ask = order_book["asks"][:5]
        best_bid = order_book["bids"][:5]
        print(f"ASK: {best_ask}")
        print(f"BID: {best_bid}")

        order = self._execute_market_buy(pair, usd, order_book)
        if order is None:
            return

        start_time = time.time()
        print("Buy order executed")

        # --- Phase 2: Wait for fill ---
        order_info = self._wait_for_fill(order, pair)
        buy_price = order_info["average"]

        # --- Phase 3: Place take-profit sell ---
        available_coins = self.get_available_coins(symbol)
        sell_price = buy_price * (1 + benefit_total)

        sell_order = self._place_limit_sell(pair, available_coins, sell_price)
        sell_order_id = sell_order["id"]
        print("Sell order placed")

        # --- Phase 4: Monitor and manage exit ---
        partial_sell = False
        start_partial_time = -1

        while True:
            try:
                order_status = self.exchange.fetch_order(sell_order_id, pair)
            except Exception:
                time.sleep(0.2)
                continue

            if order_status["status"] == "closed":
                profit = usd * (benefit_partial if partial_sell else benefit_total)
                print(f"{Fore.GREEN} Profit: {profit:.4f} $ {Style.RESET_ALL}")
                break

            elapsed_total = time.time() - start_time

            # Check if price has risen enough to start partial timer
            if start_partial_time == -1:
                try:
                    current_price = self.get_price(pair)
                    price_change = (current_price / buy_price) - 1
                    if price_change > min_up_start_time:
                        start_partial_time = time.time()
                except Exception:
                    pass

            elapsed_partial = (
                time.time() - start_partial_time if start_partial_time != -1 else -1
            )

            # Switch to partial take-profit
            if not partial_sell and elapsed_partial > time_limit_partial:
                self._cancel_order_safe(sell_order_id, pair)
                sell_price = buy_price * (1 + benefit_partial)
                sell_order = self._place_limit_sell(pair, available_coins, sell_price)
                sell_order_id = sell_order["id"]
                partial_sell = True

            # Total timeout: fall back to track_sell
            if elapsed_total > time_limit_total:
                self._cancel_order_safe(sell_order_id, pair)
                self.track_sell(symbol)
                break

            time.sleep(0.3)

    def track_sell(self, symbol: str) -> None:
        """Continuously adjust a limit sell to undercut the best ask.

        Places a sell order just below the best ask price and monitors
        the order book, re-placing the order when outbid or when the
        spread becomes too wide. Runs for a maximum of 1 hour.
        """
        pair = f"{symbol}/USDT"

        market = self.exchange.markets[pair]
        price_precision = market["precision"]["price"]
        amount_precision = market["precision"]["amount"]
        min_increment = price_precision

        sell_order = None
        sell_order_price = None
        start_time = time.time()

        while (time.time() - start_time) < 3600:
            try:
                order_book = self.exchange.fetch_order_book(pair)
            except Exception:
                continue

            best_ask = order_book["asks"][0][0]
            second_best_ask = order_book["asks"][1][0]

            available_coins = self.get_available_coins(symbol)

            if sell_order is not None:
                order_info = self._fetch_order_safe(sell_order["id"], pair)
                spread_ratio = (second_best_ask - best_ask) / best_ask

                if order_info["status"] == "closed":
                    print(f"[TRACK] Sell FILLED at {sell_order_price}")
                    sell_order = None
                    sell_order_price = None

                elif best_ask < sell_order_price or available_coins > amount_precision:
                    print(f"[TRACK] Outbid or new coins available, re-placing order...")
                    self._cancel_order_safe(sell_order["id"], pair)
                    sell_order = None
                    sell_order_price = None
                    available_coins = self.get_available_coins(symbol)

                elif spread_ratio > 0.3:
                    print(f"[TRACK] Spread too wide ({spread_ratio:.2%}), re-placing...")
                    self._cancel_order_safe(sell_order["id"], pair)
                    sell_order = None
                    sell_order_price = None
                    best_ask = second_best_ask
                    available_coins = self.get_available_coins(symbol)

            if available_coins > amount_precision and sell_order is None:
                price = best_ask - min_increment
                sell_order_price = price
                sell_order = self._place_limit_sell(pair, available_coins, price)
                print(f"[TRACK] Sell PLACED at {sell_order_price}")

    # --- Internal helpers ---

    def _fetch_order_book_safe(self, pair: str, max_retries: int = 10) -> dict | None:
        """Fetch order book with retries."""
        for attempt in range(max_retries):
            try:
                return self.exchange.fetch_order_book(pair)
            except Exception:
                time.sleep(0.2)
        return None

    def _execute_market_buy(self, pair: str, usd: float, order_book: dict) -> dict | None:
        """Execute a market buy with up to 10 retries, reducing size on failure."""
        buy_arg = usd  # cost or amount depending on exchange

        if not self._market_buy_uses_cost:
            ask_price = order_book["asks"][0][0]
            buy_arg = math.floor((usd / ask_price) * 1e6) / 1e6

        for attempt in range(10):
            try:
                return self.exchange.create_market_buy_order(pair, buy_arg)
            except Exception as e:
                print(f"Buy attempt {attempt + 1} failed: {e}")
                buy_arg = (
                    round(buy_arg * 0.7, 2)
                    if self._market_buy_uses_cost
                    else math.floor((buy_arg * 0.7) * 1e6) / 1e6
                )
                time.sleep(0.2)
        return None

    def _wait_for_fill(self, order: dict, pair: str, timeout: float = 2.0) -> dict:
        """Wait for a buy order to be filled, with timeout."""
        start = time.time()
        order_info = order
        while True:
            if time.time() - start > timeout:
                break
            try:
                order_info = self.exchange.fetch_order(order["id"], pair)
                if order_info["status"] == "closed":
                    break
                if order_info["status"] == "ORDER_STATUS_REJECTED":
                    order = self.exchange.create_market_buy_order(
                        pair, order["amount"]
                    )
            except Exception:
                time.sleep(0.2)
        return order_info

    def _place_limit_sell(self, pair: str, amount: float, price: float) -> dict:
        """Place a limit sell order with infinite retries."""
        while True:
            try:
                return self.exchange.create_limit_sell_order(pair, amount, price)
            except Exception:
                time.sleep(0.2)

    def _cancel_order_safe(self, order_id: str, pair: str, max_retries: int = 10) -> None:
        """Cancel an order with retries."""
        for _ in range(max_retries):
            try:
                self.exchange.cancel_order(order_id, pair)
                return
            except Exception:
                time.sleep(0.2)

    def _fetch_order_safe(self, order_id: str, pair: str, max_retries: int = 10) -> dict:
        """Fetch order info with retries."""
        for _ in range(max_retries):
            try:
                return self.exchange.fetch_order(order_id, pair)
            except Exception:
                time.sleep(0.2)
        return {}
