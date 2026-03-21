"""Digifinex order book spread exploitation trader."""

import os
import ccxt
from dotenv import load_dotenv
from strategies.base_orderbook_trader import BaseOrderBookTrader

load_dotenv()


class DigifinexOrderBookTrader(BaseOrderBookTrader):
    """Order book trader for Digifinex exchange."""

    def _create_exchange(self) -> ccxt.Exchange:
        return ccxt.digifinex({
            "apiKey": os.getenv("DIGIFINEX_API_KEY"),
            "secret": os.getenv("DIGIFINEX_API_SECRET"),
        })
