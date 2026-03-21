"""Poloniex order book spread exploitation trader."""

import os
import ccxt
from dotenv import load_dotenv
from strategies.base_orderbook_trader import BaseOrderBookTrader

load_dotenv()


class PoloniexOrderBookTrader(BaseOrderBookTrader):
    """Order book trader for Poloniex exchange."""

    def _create_exchange(self) -> ccxt.Exchange:
        exchange = ccxt.poloniex({
            "apiKey": os.getenv("POLONIEX_API_KEY"),
            "secret": os.getenv("POLONIEX_API_SECRET"),
        })
        exchange.options["createMarketBuyOrderRequiresPrice"] = False
        return exchange
