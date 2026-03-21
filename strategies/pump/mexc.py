"""MEXC pump anticipation trader."""

import os
import ccxt
from dotenv import load_dotenv
from strategies.base_pump_trader import BasePumpTrader

load_dotenv()


class MexcPumpTrader(BasePumpTrader):
    """Pump trader for MEXC exchange.

    Uses amount-based market buy orders (calculates token amount from USD).
    """

    def _create_exchange(self) -> ccxt.Exchange:
        return ccxt.mexc({
            "apiKey": os.getenv("MEXC_API_KEY"),
            "secret": os.getenv("MEXC_API_SECRET"),
        })
