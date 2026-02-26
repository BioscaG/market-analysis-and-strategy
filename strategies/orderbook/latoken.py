"""LAToken order book spread exploitation trader."""

import os
import ccxt
from dotenv import load_dotenv
from strategies.base_orderbook_trader import BaseOrderBookTrader

load_dotenv()


class LatokenOrderBookTrader(BaseOrderBookTrader):
    """Order book trader for LAToken exchange."""

    def _create_exchange(self) -> ccxt.Exchange:
        return ccxt.latoken({
            "apiKey": os.getenv("LATOKEN_API_KEY"),
            "secret": os.getenv("LATOKEN_API_SECRET"),
        })
