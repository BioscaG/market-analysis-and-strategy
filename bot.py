"""Telegram bot controller for the crypto trading system.

Provides a remote control interface via Telegram to:
- Start/stop real-time pump alert monitoring
- Execute manual and automated trades
- Configure trading parameters on the fly
- Switch between exchanges and strategies
"""

import os
import time
import asyncio
from collections import deque
from datetime import datetime
from multiprocessing import Process, Queue, Value

import ccxt
import pandas as pd
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackContext,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from strategies.pump.latoken import LatokenPumpTrader
from strategies.pump.poloniex import PoloniexPumpTrader
from strategies.pump.mexc import MexcPumpTrader
from strategies.pump.digifinex import DigifinexPumpTrader
from strategies.orderbook.poloniex import PoloniexOrderBookTrader
from strategies.orderbook.latoken import LatokenOrderBookTrader
from strategies.orderbook.digifinex import DigifinexOrderBookTrader

load_dotenv()

# --- Conversation states ---
USD, BENEFIT_PARTIAL, BENEFIT_TOTAL, TIME_LIMIT_PARTIAL, TIME_LIMIT_TOTAL, MIN_UP_START_TIME = range(6)
THRESHOLD_QUOTE, DIF_TIME, THRESHOLD_PERCENTAGE, NAME_TRADER = range(4)

# --- VIP coin filter ---
with open("data/coins.txt", "r") as f:
    vip_filter = [line.strip() + "/USDT" for line in f]

filter_enabled = False

# --- Global state ---
info = None
trader = None
name_trader = None
alert_queue = Queue()
authorized_chat_id = int(os.getenv("TELEGRAM_ADMIN_ID", "0"))
alert_process = None
alert_job = None
buy_next = False
buy_next_except = None
time_buy_next = None
pause_alerts = Value("b", False)
bookorder_trader = None

# --- Alert detection thresholds ---
threshold_quote = 1.1
dif_time = 60 * 28
threshold_percentage = 2

# --- Trade parameters ---
usd = 0.01
benefit_partial = 0.4
benefit_total = 1
time_limit_partial = 20
time_limit_total = 10
min_up_start_time = 0.5
slippage = 0.5

# --- Strategy parameters ---
do_strategy = False
time_strategy = 70
time_limit_strategy = 210


def set_trader(trader_name: str) -> None:
    """Initialize the exchange trader and info objects for the selected exchange."""
    global info, trader, name_trader, bookorder_trader
    name_trader = trader_name

    exchanges = {
        "latoken": (ccxt.latoken, LatokenPumpTrader, LatokenOrderBookTrader),
        "poloniex": (ccxt.poloniex, PoloniexPumpTrader, PoloniexOrderBookTrader),
        "mexc": (ccxt.mexc, MexcPumpTrader, None),
        "digifinex": (ccxt.digifinex, DigifinexPumpTrader, DigifinexOrderBookTrader),
    }

    if trader_name in exchanges:
        exchange_cls, pump_cls, book_cls = exchanges[trader_name]
        info = exchange_cls()
        trader = pump_cls()
        bookorder_trader = book_cls() if book_cls else None


def save_order_book_to_csv(pair: str) -> None:
    """Record order book snapshots to CSV for later analysis."""
    os.makedirs("book_order", exist_ok=True)
    filename = f"book_order/{pair[:-5]}.csv"
    time_start = time.time()

    while time.time() - time_start < 200:
        try:
            order_book = info.fetch_order_book(pair)
            bids = order_book["bids"][:5]
            asks = order_book["asks"][:5]

            row = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            for i, (price, volume) in enumerate(bids):
                row[f"bid_price_{i + 1}"] = price
                row[f"bid_vol_{i + 1}"] = volume
            for i, (price, volume) in enumerate(asks):
                row[f"ask_price_{i + 1}"] = price
                row[f"ask_vol_{i + 1}"] = volume

            df = pd.DataFrame([row])
            header = not os.path.exists(filename)
            df.to_csv(filename, mode="a", header=header, index=False)
        except Exception as e:
            print(f"Error collecting data: {e}")

        time.sleep(1)


def alerts(alert_queue: Queue, pause_alerts) -> None:
    """Continuously monitor tickers and push alerts when pump conditions are detected.

    Runs as a separate process. Checks for volume spikes and price increases
    that exceed the configured thresholds.
    """
    min_quote_volume: dict[str, float] = {}
    min_price: dict[str, float] = {}

    while True:
        try:
            if pause_alerts.value:
                time.sleep(2.5)

            tickers = info.fetch_tickers()
            for symbol, value in tickers.items():
                if not symbol.endswith("USDT"):
                    continue
                if filter_enabled and symbol not in vip_filter:
                    continue

                quote_volume = value["quoteVolume"]
                if quote_volume is None:
                    continue

                # Track price changes
                percentage = None
                current_price = value["last"]

                if symbol in min_price:
                    prev_min = min_price[symbol]
                    if current_price is not None and prev_min is not None:
                        percentage = round((current_price - prev_min) / prev_min * 100, 2)
                    if prev_min is None or (current_price is not None and current_price < prev_min):
                        min_price[symbol] = current_price
                else:
                    min_price[symbol] = current_price

                meets_price_threshold = percentage is not None and percentage >= threshold_percentage

                # Track volume changes and trigger alerts
                if symbol in min_quote_volume:
                    prev_volume = min_quote_volume[symbol]
                    if prev_volume == 0:
                        if quote_volume >= 5 and meets_price_threshold:
                            alert_queue.put({
                                "crypto": symbol,
                                "vol_act": quote_volume,
                                "vol_ant": prev_volume,
                                "percentage": percentage,
                            })
                            min_quote_volume[symbol] = quote_volume
                    elif quote_volume / prev_volume >= threshold_quote:
                        if quote_volume >= 5 and meets_price_threshold:
                            alert_queue.put({
                                "crypto": symbol,
                                "vol_act": quote_volume,
                                "vol_ant": prev_volume,
                                "percentage": percentage,
                            })
                            min_quote_volume[symbol] = quote_volume
                else:
                    min_quote_volume[symbol] = quote_volume

            time.sleep(0.2)
        except Exception as e:
            print(f"Alert error: {e}")
            time.sleep(1)


async def start_strategy(symbol: str) -> None:
    """Launch the order book strategy after a delay."""
    await asyncio.sleep(time_strategy)
    strategy_process = Process(
        target=bookorder_trader.strategy,
        args=(symbol, 4, 30, time_limit_strategy),
    )
    strategy_process.start()


async def buy_crypto(crypto_name: str) -> None:
    """Execute a buy trade for the given crypto pair."""
    print(f"Buying {crypto_name}")
    symbol = crypto_name[:-5]
    pause_alerts.value = True

    buy_process = Process(
        target=trader.buy_and_sell,
        args=(symbol, usd, benefit_partial, benefit_total,
              time_limit_partial, time_limit_total, min_up_start_time, slippage),
    )
    buy_process.start()
    await asyncio.sleep(2)

    pause_alerts.value = False
    if do_strategy:
        asyncio.create_task(start_strategy(symbol))


# --- Telegram command handlers ---

async def help_command(update: Update, context: CallbackContext) -> None:
    """Show available commands."""
    commands = (
        "/startalerts - Start automatic alerts\n"
        "/stopalerts - Stop automatic alerts\n"
        "/benefitsettings - Configure profit parameters\n"
        "/datasettings - Configure detection parameters\n"
        "/showsettings - Show current configuration\n"
        "/filter - Toggle VIP coin filter\n"
        "/logs <lines> - Show recent log lines\n"
        "/buynext - Toggle auto-buy on next alert\n"
        "/slippage <value> - Set slippage\n"
        "/buy <symbol> - Manual buy\n"
        "/timerbuynext <timestamp> - Auto-buy at timestamp\n"
        "/strategy <time> - Toggle order book strategy\n"
        "/buystrategy <symbol> - Manual strategy buy\n"
        "/timelimitstrategy <value> - Set strategy time limit"
    )
    await update.message.reply_text(f"Available commands:\n{commands}")


async def toggle_filter(update: Update, context: CallbackContext) -> None:
    """Toggle the VIP coin filter on/off."""
    global filter_enabled
    filter_enabled = not filter_enabled
    status = "enabled" if filter_enabled else "disabled"
    await update.message.reply_text(f"VIP coin filter {status}.")


# --- Benefit settings conversation ---

async def parameters(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Enter USD amount:")
    return USD

async def set_usd(update: Update, context: CallbackContext) -> int:
    global usd
    usd = float(update.message.text)
    await update.message.reply_text("Enter Benefit Partial:")
    return BENEFIT_PARTIAL

async def set_benefit_partial(update: Update, context: CallbackContext) -> int:
    global benefit_partial
    benefit_partial = float(update.message.text)
    await update.message.reply_text("Enter Benefit Total:")
    return BENEFIT_TOTAL

async def set_benefit_total(update: Update, context: CallbackContext) -> int:
    global benefit_total
    benefit_total = float(update.message.text)
    await update.message.reply_text("Enter Time Limit Partial (seconds):")
    return TIME_LIMIT_PARTIAL

async def set_time_limit_partial(update: Update, context: CallbackContext) -> int:
    global time_limit_partial
    time_limit_partial = float(update.message.text)
    await update.message.reply_text("Enter Time Limit Total (seconds):")
    return TIME_LIMIT_TOTAL

async def set_time_limit_total(update: Update, context: CallbackContext) -> int:
    global time_limit_total
    time_limit_total = float(update.message.text)
    await update.message.reply_text("Enter Min Up Start Time:")
    return MIN_UP_START_TIME

async def set_min_up_start_time(update: Update, context: CallbackContext) -> int:
    global min_up_start_time
    min_up_start_time = float(update.message.text)
    await update.message.reply_text(
        f"Parameters set: USD={usd}, Partial={benefit_partial}, "
        f"Total={benefit_total}, TLP={time_limit_partial}, "
        f"TLT={time_limit_total}, MinUp={min_up_start_time}"
    )
    return ConversationHandler.END


# --- Data settings conversation ---

async def parameters_threshold(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Enter Threshold Quote:")
    return THRESHOLD_QUOTE

async def set_threshold_quote(update: Update, context: CallbackContext) -> int:
    global threshold_quote
    threshold_quote = float(update.message.text)
    await update.message.reply_text("Enter Dif Time (minutes):")
    return DIF_TIME

async def set_dif_time(update: Update, context: CallbackContext) -> int:
    global dif_time
    dif_time = int(update.message.text) * 60
    await update.message.reply_text("Enter Threshold Percentage:")
    return THRESHOLD_PERCENTAGE

async def set_threshold_percentage(update: Update, context: CallbackContext) -> int:
    global threshold_percentage
    threshold_percentage = float(update.message.text)
    await update.message.reply_text("Enter trader name (latoken, poloniex, mexc, digifinex):")
    return NAME_TRADER

async def set_name_trader(update: Update, context: CallbackContext) -> int:
    name = update.message.text
    set_trader(name)
    await update.message.reply_text(
        f"Parameters set: Quote={threshold_quote}, DifTime={dif_time}s, "
        f"Percentage={threshold_percentage}%, Trader={name}"
    )
    return ConversationHandler.END


# --- Utility commands ---

async def show_settings(update: Update, context: CallbackContext) -> None:
    """Display all current configuration parameters."""
    settings = (
        f"USD: {usd}\n"
        f"Benefit Partial: {benefit_partial}\n"
        f"Benefit Total: {benefit_total}\n"
        f"Time Limit Partial: {time_limit_partial}s\n"
        f"Time Limit Total: {time_limit_total}s\n"
        f"Min Up Start Time: {min_up_start_time}\n"
        f"Threshold Quote: {threshold_quote}\n"
        f"Dif Time: {dif_time}s\n"
        f"Threshold Percentage: {threshold_percentage}%\n"
        f"Trader: {name_trader}\n"
        f"Filter: {filter_enabled}\n"
        f"Buy Next: {buy_next}\n"
        f"Slippage: {slippage}\n"
        f"Timer Buy Next: {time_buy_next}\n"
        f"Strategy: {do_strategy} (delay={time_strategy}s)\n"
        f"Strategy Time Limit: {time_limit_strategy}s"
    )
    await update.message.reply_text(settings)


async def set_slippage_cmd(update: Update, context: CallbackContext) -> None:
    global slippage
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /slippage <value>")
        return
    slippage = float(args[0])
    await update.message.reply_text(f"Slippage set to {slippage}")


async def set_time_limit_strategy_cmd(update: Update, context: CallbackContext) -> None:
    global time_limit_strategy
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /timelimitstrategy <value>")
        return
    time_limit_strategy = int(args[0])
    await update.message.reply_text(f"Strategy time limit set to {time_limit_strategy}s")


async def showlog(update: Update, context: CallbackContext) -> None:
    """Show the last N lines from the log file."""
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /logs <number_of_lines>")
        return
    lines = int(args[0])
    try:
        with open("log.out", "r") as file:
            log = "".join(deque(file, maxlen=lines))
        if not log:
            await update.message.reply_text("No logs found.")
        else:
            await update.message.reply_text(log)
    except FileNotFoundError:
        await update.message.reply_text("Log file not found.")


# --- Trading commands ---

async def button_callback(update: Update, context: CallbackContext) -> None:
    """Handle buy button presses from alert messages."""
    global time_buy_next
    query = update.callback_query
    await query.answer()
    crypto_name = query.data
    try:
        if time_buy_next is not None:
            time_buy_next = None
        await buy_crypto(crypto_name)
        await query.edit_message_text(f"Buy executed for {crypto_name}")
    except Exception as e:
        await query.edit_message_text(f"Buy error: {e}")


async def send_alerts(context: CallbackContext) -> None:
    """Process alert queue and send Telegram notifications with buy buttons."""
    global buy_next, buy_next_except, time_buy_next
    job = context.job
    chat_id = job.data

    while not alert_queue.empty():
        alert = alert_queue.get()
        crypto = alert["crypto"]

        # Auto-buy logic
        if buy_next and (buy_next_except is None or buy_next_except != crypto):
            buy_next = False
            buy_next_except = None
            await buy_crypto(crypto)
        elif (time_buy_next is not None
              and time.time() >= time_buy_next
              and time.time() < (time_buy_next + 300)):
            time_buy_next = None
            await buy_crypto(crypto)

        vol_act = round(alert["vol_act"], 4)
        vol_ant = round(alert["vol_ant"], 4)
        percentage = alert["percentage"]

        buttons = [[InlineKeyboardButton("Buy", callback_data=crypto)]]
        reply_markup = InlineKeyboardMarkup(buttons)

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Crypto: {crypto}\n"
                f"Volume Now: {vol_act}\n"
                f"Volume Before: {vol_ant}\n"
                f"Change: {percentage}%"
            ),
            reply_markup=reply_markup,
        )


async def toggle_strategy(update: Update, context: CallbackContext) -> None:
    """Toggle the order book strategy on/off."""
    global do_strategy, time_strategy
    args = context.args
    do_strategy = not do_strategy
    if do_strategy:
        if len(args) == 1:
            time_strategy = int(args[0])
        await update.message.reply_text(f"Strategy enabled (delay: {time_strategy}s)")
    else:
        await update.message.reply_text("Strategy disabled.")


async def toggle_buynext(update: Update, context: CallbackContext) -> None:
    """Toggle auto-buy on next alert."""
    global buy_next, buy_next_except
    args = context.args
    buy_next = not buy_next
    if buy_next:
        if len(args) == 1:
            buy_next_except = args[0] + "/USDT"
            await update.message.reply_text(f"Auto-buy enabled (except {buy_next_except})")
        else:
            await update.message.reply_text("Auto-buy enabled.")
    else:
        buy_next_except = None
        await update.message.reply_text("Auto-buy disabled.")


async def timer_buynext(update: Update, context: CallbackContext) -> None:
    """Set a timestamp for timed auto-buy."""
    global time_buy_next
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /timerbuynext <timestamp>")
        return
    time_buy_next = float(args[0])
    formatted = datetime.fromtimestamp(time_buy_next).strftime("%Y-%m-%d %H:%M:%S")
    await update.message.reply_text(f"Auto-buy scheduled at:\n{formatted}")


async def manual_buy(update: Update, context: CallbackContext) -> None:
    """Execute a manual buy for a specific crypto."""
    chat_id = update.message.chat_id
    if chat_id != authorized_chat_id:
        await update.message.reply_text("Unauthorized.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /buy <symbol>")
        return
    crypto_name = args[0] + "/USDT"
    try:
        await buy_crypto(crypto_name)
        await update.message.reply_text(f"Buy executed for {crypto_name}")
    except Exception as e:
        await update.message.reply_text(f"Buy error: {e}")


async def manual_strategy_buy(update: Update, context: CallbackContext) -> None:
    """Execute a manual order book strategy buy."""
    chat_id = update.message.chat_id
    if chat_id != authorized_chat_id:
        await update.message.reply_text("Unauthorized.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /buystrategy <symbol>")
        return
    try:
        await start_strategy(args[0])
        await update.message.reply_text(f"Strategy launched for {args[0]}")
    except Exception as e:
        await update.message.reply_text(f"Strategy error: {e}")


async def stop_alerts(update: Update, context: CallbackContext) -> None:
    """Stop the alert monitoring system."""
    global alert_job, alert_process
    active = True

    if alert_job:
        alert_job.schedule_removal()
        alert_job = None
    else:
        active = False
        await update.message.reply_text("Alert job not running.")

    if alert_process:
        alert_process.kill()
        alert_process.join()
        alert_process = None
    else:
        active = False
        await update.message.reply_text("Alert process not running.")

    if active:
        await update.message.reply_text("Alerts stopped.")


def start_alerts() -> None:
    """Launch the alert monitoring process."""
    global alert_process
    alert_process = Process(target=alerts, args=(alert_queue, pause_alerts))
    alert_process.start()


async def alerts_command(update: Update, context: CallbackContext) -> None:
    """Start alert monitoring (authorized users only)."""
    global alert_job
    chat_id = update.message.chat_id
    if chat_id != authorized_chat_id:
        await update.message.reply_text("Unauthorized.")
        return
    start_alerts()
    alert_job = context.job_queue.run_repeating(
        send_alerts, interval=0.2, first=0, data=chat_id
    )
    await update.message.reply_text("Alerts started.")


# --- Conversation handlers ---

conv_handler = ConversationHandler(
    entry_points=[CommandHandler("benefitsettings", parameters)],
    states={
        USD: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_usd)],
        BENEFIT_PARTIAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_benefit_partial)],
        BENEFIT_TOTAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_benefit_total)],
        TIME_LIMIT_PARTIAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_time_limit_partial)],
        TIME_LIMIT_TOTAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_time_limit_total)],
        MIN_UP_START_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_min_up_start_time)],
    },
    fallbacks=[],
)

conv_handler_threshold = ConversationHandler(
    entry_points=[CommandHandler("datasettings", parameters_threshold)],
    states={
        THRESHOLD_QUOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_threshold_quote)],
        DIF_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_dif_time)],
        THRESHOLD_PERCENTAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_threshold_percentage)],
        NAME_TRADER: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_name_trader)],
    },
    fallbacks=[],
)

# --- Application setup ---

application = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("startalerts", alerts_command))
application.add_handler(CallbackQueryHandler(button_callback))
application.add_handler(conv_handler)
application.add_handler(conv_handler_threshold)
application.add_handler(CommandHandler("showsettings", show_settings))
application.add_handler(CommandHandler("stopalerts", stop_alerts))
application.add_handler(CommandHandler("filter", toggle_filter))
application.add_handler(CommandHandler("logs", showlog))
application.add_handler(CommandHandler("buynext", toggle_buynext))
application.add_handler(CommandHandler("slippage", set_slippage_cmd))
application.add_handler(CommandHandler("buy", manual_buy))
application.add_handler(CommandHandler("timerbuynext", timer_buynext))
application.add_handler(CommandHandler("strategy", toggle_strategy))
application.add_handler(CommandHandler("buystrategy", manual_strategy_buy))
application.add_handler(CommandHandler("timelimitstrategy", set_time_limit_strategy_cmd))

application.run_polling()
