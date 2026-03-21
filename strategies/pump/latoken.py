"""LAToken pump anticipation trader."""

import os
import ccxt
from dotenv import load_dotenv
from strategies.base_pump_trader import BasePumpTrader

load_dotenv()


class LatokenPumpTrader(BasePumpTrader):
    """Pump trader for LAToken exchange.

    Uses amount-based market buy orders (calculates token amount from USD).
    """

    def _create_exchange(self) -> ccxt.Exchange:
        return ccxt.latoken({
            "apiKey": os.getenv("LATOKEN_API_KEY"),
            "secret": os.getenv("LATOKEN_API_SECRET"),
        })
