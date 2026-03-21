"""Digifinex pump anticipation trader."""

import os
import ccxt
from dotenv import load_dotenv
from strategies.base_pump_trader import BasePumpTrader

load_dotenv()


class DigifinexPumpTrader(BasePumpTrader):
    """Pump trader for Digifinex exchange.

    Uses cost-based market buy orders (passes USD amount directly).
    """

    @property
    def _market_buy_uses_cost(self) -> bool:
        return True

    def _create_exchange(self) -> ccxt.Exchange:
        exchange = ccxt.digifinex({
            "apiKey": os.getenv("DIGIFINEX_API_KEY"),
            "secret": os.getenv("DIGIFINEX_API_SECRET"),
        })
        exchange.options["createMarketBuyOrderRequiresPrice"] = False
        return exchange
