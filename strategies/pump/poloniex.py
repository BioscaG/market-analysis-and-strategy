"""Poloniex pump anticipation trader."""

import os
import ccxt
from dotenv import load_dotenv
from strategies.base_pump_trader import BasePumpTrader

load_dotenv()


class PoloniexPumpTrader(BasePumpTrader):
    """Pump trader for Poloniex exchange.

    Uses cost-based market buy orders (passes USD amount directly).
    """

    @property
    def _market_buy_uses_cost(self) -> bool:
        return True

    def _create_exchange(self) -> ccxt.Exchange:
        exchange = ccxt.poloniex({
            "apiKey": os.getenv("POLONIEX_API_KEY"),
            "secret": os.getenv("POLONIEX_API_SECRET"),
        })
        exchange.options["createMarketBuyOrderRequiresPrice"] = False
        return exchange
