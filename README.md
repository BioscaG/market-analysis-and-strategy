# Market Analysis and Strategy

Automated cryptocurrency trading bot that detects and exploits pump events across multiple exchanges, controlled remotely via Telegram.

Built as a personal research project to explore **market microstructure**, **real-time data processing**, and **automated trade execution** in low-liquidity altcoin markets.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![CCXT](https://img.shields.io/badge/CCXT-Multi--Exchange-green)
![Telegram](https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?logo=telegram&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## The Problem

Telegram groups with thousands of members coordinate pump events on low-cap altcoins — announcing a specific time when everyone should buy. These events create predictable patterns:
- **Sharp volume spikes** seconds before the announcement
- **Wide bid-ask spreads** during the volatility window
- **Rapid price movements** that decay within minutes

The challenge: can you detect these signals fast enough to act before the crowd?

## The Solution

This bot implements two independent strategies that exploit different aspects of pump events:

### Strategy 1: Pump Anticipation

Continuously monitors ticker data across all USDT pairs on the selected exchange. When it detects:
- **Volume increase** exceeding a configurable threshold (default: 1.1x)
- **Price increase** exceeding a percentage threshold (default: 2%)

It sends a Telegram alert with a one-tap buy button and can auto-execute trades based on timing rules.

**Trade lifecycle:**
1. Market buy on signal detection
2. Limit sell at target profit (configurable)
3. Partial profit-taking if price stalls after initial rise
4. Adaptive exit via order book tracking if time limit is exceeded

### Strategy 2: Order Book Spread Exploitation

Targets the wide spreads that form during pump/dump volatility. When the bid-ask spread exceeds a threshold (default: 4%):

1. Places a limit buy just above the best bid
2. When filled, places a limit sell just below the best ask
3. Continuously adjusts orders as the order book moves
4. Captures the spread as profit

Both strategies handle edge cases like rejected orders, partial fills, and market moves against the position.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Telegram Bot                       │
│              (asyncio event loop)                    │
│   Commands: /startalerts /buy /strategy /settings    │
└──────────────┬──────────────────┬────────────────────┘
               │                  │
    ┌──────────▼──────┐  ┌───────▼────────┐
    │  Alert Process  │  │  Buy Process   │
    │ (multiprocessing)│  │(multiprocessing)│
    │                  │  │                 │
    │ fetch_tickers()  │  │ buy_and_sell() │
    │ detect volume ↑  │  │ track_sell()   │
    │ detect price ↑   │  │                 │
    └──────────────────┘  └────────┬────────┘
                                   │
                          ┌────────▼────────┐
                          │Strategy Process │
                          │(multiprocessing)│
                          │                 │
                          │ order book      │
                          │ spread capture  │
                          └─────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │      CCXT Exchange Layer     │
                    │  LAToken │ Poloniex │ MEXC   │
                    │       Digifinex              │
                    └──────────────────────────────┘
```

**Key design decisions:**
- **Multiprocessing** over threading: trading processes run in isolated memory spaces, so a crash in one strategy doesn't affect others
- **Async Telegram loop**: non-blocking command handling ensures the bot stays responsive while trades execute
- **Queue-based alerts**: the alert detector pushes to a queue consumed by the Telegram event loop, decoupling detection from notification
- **Abstract base classes**: `BasePumpTrader` and `BaseOrderBookTrader` encapsulate all shared logic — adding a new exchange requires only ~15 lines

---

## Project Structure

```
├── bot.py                          # Telegram bot controller & alert engine
├── strategies/
│   ├── base_pump_trader.py         # Abstract base: pump anticipation logic
│   ├── base_orderbook_trader.py    # Abstract base: spread exploitation logic
│   ├── pump/
│   │   ├── latoken.py              # LAToken pump trader
│   │   ├── poloniex.py             # Poloniex pump trader
│   │   ├── mexc.py                 # MEXC pump trader
│   │   └── digifinex.py            # Digifinex pump trader
│   └── orderbook/
│       ├── latoken.py              # LAToken order book trader
│       ├── poloniex.py             # Poloniex order book trader
│       └── digifinex.py            # Digifinex order book trader
├── data/
│   └── coins.txt                   # VIP coin filter list
├── .env.example                    # Environment variable template
├── requirements.txt                # Python dependencies
└── start_bot.sh                    # Deployment startup script
```

---

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Language | Python 3.10+ | Core runtime |
| Exchange connectivity | CCXT | Unified API for 4 exchanges |
| Bot interface | python-telegram-bot | Remote control via Telegram |
| Concurrency | multiprocessing + asyncio | Isolated trade execution + non-blocking I/O |
| IPC | Queue + shared Value | Cross-process alert pipeline |
| Configuration | python-dotenv | Secure credential management |

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/your-username/cryptocurrency-bot.git
cd cryptocurrency-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your API keys and Telegram bot token
```

### 3. Run

```bash
python bot.py

# Or in background:
bash start_bot.sh
```

### 4. Control via Telegram

Send `/help` to your bot to see all available commands.

---

## Adding a New Exchange

Create a new file inheriting from the base trader:

```python
# strategies/pump/binance.py
from strategies.base_pump_trader import BasePumpTrader

class BinancePumpTrader(BasePumpTrader):
    def _create_exchange(self):
        return ccxt.binance({
            "apiKey": os.getenv("BINANCE_API_KEY"),
            "secret": os.getenv("BINANCE_API_SECRET"),
        })
```

Register it in `bot.py`'s `set_trader()` function. All trading logic is inherited automatically.

---

## Disclaimer

This project was built for educational and research purposes. Cryptocurrency trading involves significant financial risk. This bot is not financial advice and should not be used with funds you cannot afford to lose.

---

## Contact

**Guido Biosca Lasa**
- Email: guido.biosca0@gmail.com
- LinkedIn: [linkedin.com/in/guido-biosca](https://linkedin.com/in/guido-biosca)
