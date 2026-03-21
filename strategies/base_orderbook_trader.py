"""Base class for order book spread exploitation strategy.

This strategy profits from wide bid-ask spreads that occur during
high-volatility events (pumps/dumps). It continuously:
1. Monitors the spread between best bid and best ask
2. When spread exceeds a threshold, places limit buys just above best bid
3. When coins are acquired, places limit sells just below best ask
4. Captures the spread as profit
"""

from abc import ABC, abstractmethod
import time
import math
import ccxt


class BaseOrderBookTrader(ABC):
    """Abstract base for exchange-specific order book traders.

    Subclasses only need to implement `_create_exchange()` to provide
    a configured ccxt exchange instance. All trading logic is shared.
    """

    def __init__(self) -> None:
        self.exchange: ccxt.Exchange = self._create_exchange()
        self._load_markets()
        self._warmup_connection()

    @abstractmethod
    def _create_exchange(self) -> ccxt.Exchange:
        """Return a configured ccxt exchange instance with API credentials."""
        ...

    def _load_markets(self) -> None:
        """Pre-load exchange market data for precision info."""
        try:
            self.exchange.load_markets()
        except Exception as e:
            print(f"Error loading markets: {e}")

    def _warmup_connection(self) -> None:
        """Pre-warm the HTTP connection to reduce first-trade latency."""
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

    def strategy(
        self,
        symbol: str,
        usd: float,
        dif_activate: float,
        time_limit: float,
    ) -> None:
        """Run the order book spread exploitation strategy.

        Continuously monitors the bid-ask spread and places limit orders
        on both sides to capture the spread as profit.

        Args:
            symbol: Token symbol without quote (e.g. "DOGE").
            usd: Amount in USDT per buy order.
            dif_activate: Minimum spread percentage to activate buying.
            time_limit: Seconds after which new buy orders stop being placed.
        """
        pair = f"{symbol}/USDT"

        market = self.exchange.markets[pair]
        price_precision = market["precision"]["price"]
        amount_precision = market["precision"]["amount"]
        min_increment = price_precision

        buy_order = None
        buy_order_price = None
        sell_order = None
        sell_order_price = None

        start_time = time.time()

        while (time.time() - start_time) < 3600:
            # --- Fetch order book ---
            try:
                order_book = self.exchange.fetch_order_book(pair)
            except Exception:
                continue

            best_bid = order_book["bids"][0][0]
            second_best_bid = order_book["bids"][1][0]
            best_ask = order_book["asks"][0][0]
            second_best_ask = order_book["asks"][1][0]

            # --- BUY SIDE: place limit buy when spread is wide ---
            spread_pct = (best_ask - best_bid) / best_bid * 100

            if spread_pct > dif_activate:
                if buy_order is not None:
                    order_info = self._fetch_order_safe(buy_order["id"], pair)
                    bid_gap = (best_bid - second_best_bid) / second_best_bid

                    if order_info["status"] == "closed":
                        print(f"[BOOK] Buy FILLED at {buy_order_price}")
                        buy_order = None
                        buy_order_price = None

                    elif best_bid > buy_order_price:
                        print("[BOOK] Buy outbid, re-placing...")
                        self._cancel_order_safe(buy_order["id"], pair)
                        buy_order = None
                        buy_order_price = None

                    elif bid_gap > 0.2:
                        print(f"[BOOK] Buy gap too wide ({bid_gap:.2%}), re-placing...")
                        self._cancel_order_safe(buy_order["id"], pair)
                        buy_order = None
                        buy_order_price = None
                        best_bid = second_best_bid

                if buy_order is None and (time.time() - start_time) < time_limit:
                    price = best_bid + min_increment
                    buy_order_price = price
                    amount = math.floor((usd / price) * 1e6) / 1e6
                    buy_order = self._place_limit_buy_safe(pair, amount, price)
                    if buy_order:
                        print(f"[BOOK] Buy PLACED at {buy_order_price}")

            # --- SELL SIDE: place limit sell when coins are available ---
            available_coins = self.get_available_coins(symbol)

            if sell_order is not None:
                order_info = self._fetch_order_safe(sell_order["id"], pair)
                ask_gap = (second_best_ask - best_ask) / best_ask

                if order_info["status"] == "closed":
                    print(f"[BOOK] Sell FILLED at {sell_order_price}")
                    sell_order = None
                    sell_order_price = None

                elif best_ask < sell_order_price or available_coins > amount_precision:
                    print("[BOOK] Sell outbid or new coins, re-placing...")
                    self._cancel_order_safe(sell_order["id"], pair)
                    sell_order = None
                    sell_order_price = None
                    available_coins = self.get_available_coins(symbol)

                elif ask_gap > 0.3:
                    print(f"[BOOK] Sell gap too wide ({ask_gap:.2%}), re-placing...")
                    self._cancel_order_safe(sell_order["id"], pair)
                    sell_order = None
                    sell_order_price = None
                    best_ask = second_best_ask
                    available_coins = self.get_available_coins(symbol)

            if available_coins > amount_precision and sell_order is None:
                price = best_ask - min_increment
                sell_order_price = price
                sell_order = self._place_limit_sell_safe(pair, available_coins, price)
                print(f"[BOOK] Sell PLACED at {sell_order_price}")

    # --- Internal helpers ---

    def _fetch_order_safe(self, order_id: str, pair: str) -> dict:
        """Fetch order info with retries."""
        for _ in range(10):
            try:
                return self.exchange.fetch_order(order_id, pair)
            except Exception:
                time.sleep(0.2)
        return {}

    def _cancel_order_safe(self, order_id: str, pair: str) -> None:
        """Cancel an order with retries."""
        for attempt in range(10):
            try:
                self.exchange.cancel_order(order_id, pair)
                return
            except Exception:
                time.sleep(0.2)

    def _place_limit_buy_safe(self, pair: str, amount: float, price: float) -> dict | None:
        """Place a limit buy order with limited retries."""
        for _ in range(3):
            try:
                return self.exchange.create_limit_buy_order(pair, amount, price)
            except Exception:
                time.sleep(0.2)
        return None

    def _place_limit_sell_safe(self, pair: str, amount: float, price: float) -> dict:
        """Place a limit sell order with infinite retries."""
        while True:
            try:
                return self.exchange.create_limit_sell_order(pair, amount, price)
            except Exception:
                time.sleep(0.2)
