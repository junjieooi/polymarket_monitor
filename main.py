#!/usr/bin/env python3
"""
Polymarket Real-Time Monitor Bot with Telegram Notifications
Detects suspicious activity on Polymarket using the public Gamma API.
Sends alerts via Telegram bot.
"""

import requests
import time
import json
import os
import html
import signal
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Any, Set, Tuple
import asyncio
from telegram import Bot
from telegram.error import TelegramError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ============================================================================
# LOGGING SETUP
# ============================================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

# Telegram Configuration (loaded from environment variables)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "true").lower() == "true"

# Suspicious activity thresholds
LARGE_BET_THRESHOLD = float(os.getenv("LARGE_BET_THRESHOLD", "10000"))  # USD
FRESH_WALLET_MAX_TRADES = int(os.getenv("FRESH_WALLET_MAX_TRADES", "0"))  # 0 = completely new wallet (first trade ever)
POLITICAL_REPEAT_THRESHOLD = int(os.getenv("POLITICAL_REPEAT_THRESHOLD", "3"))  # Repeated entries
MAX_BUY_PRICE = float(os.getenv("MAX_BUY_PRICE", "0.70"))  # Max price to track (filters out 99% bots)

# Political keywords for market detection
POLITICAL_KEYWORDS = ['election', 'president', 'trump', 'biden', 'congress',
                      'senate', 'governor', 'vote', 'primary', 'democrat',
                      'republican', 'political', 'politician', 'poll']

# Sports keywords to filter out (sports markets are not monitored)
SPORTS_KEYWORDS = [
    'nba', 'nfl', 'mlb', 'nhl', 'mls', 'pga', 'atp', 'wta', 'ufc', 'f1',
    'super bowl', 'world series', 'stanley cup', 'world cup', 'olympics',
    'soccer', 'football', 'basketball', 'baseball', 'hockey', 'tennis',
    'golf', 'mma', 'boxing', 'formula 1', 'nascar',
    'playoff', 'playoffs', 'finals', 'championship', 'tournament',
    'win the 202', 'beat the', 'defeat',
]

# Monitoring interval
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "120"))

# API endpoints
EVENTS_API_URL = "https://gamma-api.polymarket.com/events"
DATA_API_URL = "https://data-api.polymarket.com"

# Persistence
STATE_FILE = os.getenv("STATE_FILE", "polymarket_state.json")

# Request timeout and retry settings
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # Exponential backoff delays in seconds

# Telegram rate limiting
TELEGRAM_MESSAGE_DELAY = 0.1  # Seconds between messages

# ============================================================================
# TELEGRAM BOT SETUP
# ============================================================================

telegram_bot = None
event_loop = None
last_telegram_message_time = 0


def get_event_loop():
    """Get or create a reusable event loop."""
    global event_loop
    if event_loop is None or event_loop.is_closed():
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
    return event_loop


def run_async(coro):
    """Run an async coroutine using the shared event loop."""
    loop = get_event_loop()
    return loop.run_until_complete(coro)


def escape_html(text: str) -> str:
    """Escape HTML special characters to prevent injection."""
    if text is None:
        return ""
    return html.escape(str(text))


def initialize_telegram():
    """Initialize Telegram bot."""
    global telegram_bot

    if not ENABLE_TELEGRAM:
        logger.info("Telegram notifications disabled")
        return False

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured. Notifications disabled.")
        logger.info("To enable Telegram:")
        logger.info("  1. Create a bot with @BotFather on Telegram")
        logger.info("  2. Get your CHAT_ID by messaging your bot, then visit:")
        logger.info("     https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates")
        logger.info("  3. Create a .env file with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return False

    try:
        telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)
        run_async(telegram_bot.get_me())
        logger.info("Telegram bot initialized successfully")
        send_test_message()
        return True
    except TelegramError as e:
        logger.error(f"Failed to initialize Telegram bot: {e}")
        logger.info("Continuing without Telegram notifications")
        return False


def send_test_message():
    """Send a test message to confirm Telegram connection."""
    if not telegram_bot:
        return

    try:
        test_message = (
            f"✅ <b>Polymarket Suspicious Activity Monitor</b>\n\n"
            f"Telegram bot connected successfully!\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        run_async(telegram_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=test_message,
            parse_mode='HTML'
        ))
        logger.info("Test message sent to Telegram successfully")
    except TelegramError as e:
        logger.error(f"Failed to send test message: {e}")


def send_telegram_message(message: str, parse_mode: str = 'HTML'):
    """Send a message via Telegram with rate limiting."""
    global last_telegram_message_time

    if not telegram_bot:
        return

    # Rate limiting - ensure minimum delay between messages
    elapsed = time.time() - last_telegram_message_time
    if elapsed < TELEGRAM_MESSAGE_DELAY:
        time.sleep(TELEGRAM_MESSAGE_DELAY - elapsed)

    try:
        run_async(telegram_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode=parse_mode,
            disable_web_page_preview=False
        ))
        last_telegram_message_time = time.time()
    except TelegramError as e:
        logger.error(f"Failed to send Telegram message: {e}")


# ============================================================================
# STATE MANAGEMENT
# ============================================================================

class MonitorState:
    """Manages the monitoring state for suspicious activity detection."""

    def __init__(self, state_file: Optional[str] = None):
        self.state_file = state_file
        self.seen_suspicious: Set[str] = set()  # Track seen suspicious activity
        self.first_run: bool = True
        self._dirty: bool = False  # Track if state needs saving
        self.load_state()

    def load_state(self):
        """Load state from file if it exists."""
        if self.state_file:
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.seen_suspicious = set(data.get('seen_suspicious', []))
                    self.first_run = False  # Not first run if we loaded state
                    logger.info(f"Loaded state from {self.state_file}")
            except FileNotFoundError:
                logger.info("No existing state file found. Starting fresh (initialization mode).")
                self.first_run = True
            except Exception as e:
                logger.error(f"Failed to load state: {e}")
                self.first_run = True

    def save_state(self):
        """Save state to file (only if dirty)."""
        if not self._dirty or not self.state_file:
            return

        try:
            with open(self.state_file, 'w') as f:
                # Keep only recent seen_suspicious entries (last 1000)
                recent_suspicious = list(self.seen_suspicious)[-1000:]
                json.dump({
                    'seen_suspicious': recent_suspicious
                }, f, indent=2)
            self._dirty = False
            logger.debug("State saved to file")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def mark_dirty(self):
        """Mark state as needing to be saved."""
        self._dirty = True

    def complete_first_run(self):
        """Mark first run as complete."""
        self.first_run = False
        self.mark_dirty()


# ============================================================================
# API FUNCTIONS
# ============================================================================

def fetch_with_retry(url: str, params: Dict[str, Any]) -> Optional[requests.Response]:
    """Fetch URL with exponential backoff retry logic."""
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.warning(f"Request failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}. Retrying in {delay}s...")
                time.sleep(delay)
            else:
                logger.error(f"Request failed after {MAX_RETRIES} attempts: {e}")
                return None
    return None


def is_sports_market(title: str, slug: str = '') -> bool:
    """Check if a market is sports-related based on keywords."""
    combined = f"{title} {slug}".lower()
    return any(kw in combined for kw in SPORTS_KEYWORDS)


def fetch_recent_trades(limit: int = 100, min_usd: float = None) -> List[Dict]:
    """
    Fetch recent trades from the Data API.

    Args:
        limit: Maximum trades to fetch
        min_usd: Minimum USD value filter (uses CASH filterType)
    """
    params = {
        'limit': limit,
        'offset': 0,
        'takerOnly': 'true'
    }

    if min_usd:
        params['filterType'] = 'CASH'
        params['filterAmount'] = min_usd

    url = f"{DATA_API_URL}/trades"
    response = fetch_with_retry(url, params)

    if response is None:
        logger.error("Failed to fetch recent trades")
        return []

    try:
        return response.json()
    except Exception as e:
        logger.error(f"Error parsing trades response: {e}")
        return []


def fetch_wallet_activity(wallet: str, limit: int = 100) -> List[Dict]:
    """
    Fetch activity history for a specific wallet.

    Args:
        wallet: Wallet address (0x prefixed)
        limit: Maximum activities to fetch
    """
    params = {
        'user': wallet,
        'limit': limit,
        'offset': 0,
        'sortBy': 'TIMESTAMP',
        'sortDirection': 'DESC'
    }

    url = f"{DATA_API_URL}/activity"
    response = fetch_with_retry(url, params)

    if response is None:
        logger.warning(f"Failed to fetch activity for wallet {wallet[:10]}...")
        return []

    try:
        return response.json()
    except Exception as e:
        logger.error(f"Error parsing activity response: {e}")
        return []


def fetch_political_events() -> List[int]:
    """
    Fetch event IDs for political markets.
    Returns list of event IDs that match political keywords.
    """
    params = {
        'limit': 100,
        'active': 'true',
        'order': 'volume24hr',
        'ascending': 'false'
    }

    response = fetch_with_retry(EVENTS_API_URL, params)
    if response is None:
        return []

    political_event_ids = []
    try:
        events = response.json()
        for event in events:
            title = (event.get('title', '') or '').lower()
            description = (event.get('description', '') or '').lower()
            slug = (event.get('slug', '') or '').lower()

            combined_text = f"{title} {description} {slug}"
            if any(kw in combined_text for kw in POLITICAL_KEYWORDS):
                event_id = event.get('id')
                if event_id:
                    political_event_ids.append(int(event_id))
    except Exception as e:
        logger.error(f"Error parsing political events: {e}")

    return political_event_ids


# ============================================================================
# SUSPICIOUS ACTIVITY DETECTION
# ============================================================================

@dataclass
class SuspiciousActivity:
    """Represents a flagged suspicious activity."""
    wallet: str
    trade_size_usd: float
    price: float  # The price/odds of the trade
    market_title: str
    market_slug: str
    event_slug: str
    side: str
    outcome: str  # The specific outcome being bet on (e.g., "Yes", "No", or option name)
    timestamp: int
    flags: List[str]  # List of triggered flags
    wallet_total_trades: int
    political_trade_count: int = 0


def analyze_wallet_freshness(wallet: str, cached_wallet_data: Dict[str, Dict]) -> Tuple[bool, int]:
    """
    Check if a wallet is fresh (has limited trading history).

    Returns:
        Tuple of (is_fresh, total_trade_count)
    """
    # Use cached data if available
    if wallet in cached_wallet_data:
        total_trades = cached_wallet_data[wallet].get('total_trades', 0)
        return total_trades <= FRESH_WALLET_MAX_TRADES, total_trades

    # Fetch wallet activity
    activity = fetch_wallet_activity(wallet, limit=FRESH_WALLET_MAX_TRADES + 1)
    total_trades = len([a for a in activity if a.get('type') == 'TRADE'])

    # Cache the result
    cached_wallet_data[wallet] = {'total_trades': total_trades}

    return total_trades <= FRESH_WALLET_MAX_TRADES, total_trades


def detect_suspicious_activity(
    trades: List[Dict],
    political_event_ids: List[int],
    state: MonitorState
) -> List[SuspiciousActivity]:
    """
    Analyze trades for suspicious patterns:
    1. Fresh wallets with no history making trades
    2. Abnormally large bets
    3. Repeated entries into political markets

    Args:
        trades: List of recent trades
        political_event_ids: List of political event IDs
        state: Monitor state for tracking seen alerts

    Returns:
        List of SuspiciousActivity objects
    """
    suspicious = []
    cached_wallet_data: Dict[str, Dict] = {}  # Cache wallet lookups
    wallet_political_trades: Dict[str, int] = {}  # Track political trades per wallet

    for trade in trades:
        wallet = trade.get('proxyWallet', '')
        if not wallet:
            continue

        # Get trade details
        try:
            trade_size = float(trade.get('size', 0))
            price = float(trade.get('price', 0))
            usd_size = trade_size * price  # Approximate USD value
        except (ValueError, TypeError):
            continue

        market_title = trade.get('title', 'Unknown')
        market_slug = trade.get('slug', '')
        event_slug = trade.get('eventSlug', market_slug)

        # Skip sports markets (not monitored)
        if is_sports_market(market_title, market_slug):
            logger.debug(f"Skipping sports market: {market_title}")
            continue

        side = trade.get('side', 'UNKNOWN')
        # Get the specific outcome being bet on
        outcome = trade.get('outcome', '') or trade.get('outcomeName', '') or trade.get('asset', '')
        timestamp = trade.get('timestamp', 0)
        condition_id = trade.get('conditionId', '')

        # Skip buys at high prices (bots buying near-certain outcomes for small wins)
        if side == 'BUY' and price > MAX_BUY_PRICE:
            continue

        # Create unique key for dedup
        trade_key = f"{wallet}_{condition_id}_{timestamp}"
        if trade_key in state.seen_suspicious:
            continue

        flags = []

        # Check 1: Is this a large bet?
        is_large_bet = usd_size >= LARGE_BET_THRESHOLD
        if is_large_bet:
            flags.append(f"LARGE_BET (${usd_size:,.0f})")

        # Check 2: Is this a fresh wallet (first trade ever)?
        is_fresh, total_trades = analyze_wallet_freshness(wallet, cached_wallet_data)
        if is_fresh:
            flags.append(f"FRESH_WALLET ({total_trades} history)")

        # Check 3: Is this a political market?
        is_political = False
        try:
            # Try to match by event slug or condition
            for pid in political_event_ids:
                if str(pid) in condition_id or str(pid) in event_slug:
                    is_political = True
                    break
        except Exception:
            pass

        # Also check by market title keywords
        if any(kw in market_title.lower() for kw in POLITICAL_KEYWORDS):
            is_political = True

        if is_political:
            wallet_political_trades[wallet] = wallet_political_trades.get(wallet, 0) + 1
            if wallet_political_trades[wallet] >= POLITICAL_REPEAT_THRESHOLD:
                flags.append(f"POLITICAL_REPEAT ({wallet_political_trades[wallet]}x)")

        # Flag if: (fresh wallet + BUY + political) OR (large bet + another pattern)
        is_fresh_political_buy = is_fresh and side == 'BUY' and is_political
        if is_fresh_political_buy:
            flags.append("POLITICAL_BUY")

        should_flag = is_fresh_political_buy or (is_large_bet and len(flags) >= 2)
        if should_flag:
            activity = SuspiciousActivity(
                wallet=wallet,
                trade_size_usd=usd_size,
                price=price,
                market_title=market_title,
                market_slug=market_slug,
                event_slug=event_slug,
                side=side,
                outcome=outcome,
                timestamp=timestamp,
                flags=flags,
                wallet_total_trades=total_trades,
                political_trade_count=wallet_political_trades.get(wallet, 0)
            )
            suspicious.append(activity)

            # Mark as seen
            state.seen_suspicious.add(trade_key)

    return suspicious


# ============================================================================
# ALERT FUNCTIONS
# ============================================================================


def alert_suspicious_activity(activity: SuspiciousActivity):
    """Print alert for suspicious activity and send Telegram notification."""
    # Construct URL
    url = f"https://polymarket.com/event/{activity.event_slug}" if activity.event_slug else "URL not available"

    # Format timestamp
    trade_time = datetime.fromtimestamp(activity.timestamp).strftime('%Y-%m-%d %H:%M:%S') if activity.timestamp else 'Unknown'

    # Format position (side + outcome)
    position = f"{activity.side} {activity.outcome}" if activity.outcome else activity.side

    # Console output
    logger.info("=" * 60)
    logger.info("SUSPICIOUS ACTIVITY DETECTED")
    logger.info(f"Wallet: {activity.wallet}")
    logger.info(f"Flags: {', '.join(activity.flags)}")
    logger.info(f"Trade Size: ${activity.trade_size_usd:,.2f}")
    logger.info(f"Odds: {activity.price*100:.0f}%")
    logger.info(f"Position: {position}")
    logger.info(f"Market: {activity.market_title}")
    logger.info(f"Wallet Total Trades: {activity.wallet_total_trades}")
    logger.info(f"Time: {trade_time}")
    logger.info(f"URL: {url}")
    logger.info("=" * 60)

    # Telegram notification
    if telegram_bot:
        safe_title = escape_html(activity.market_title)
        safe_outcome = escape_html(activity.outcome) if activity.outcome else ''
        flags_str = ' | '.join(activity.flags)

        # Determine emoji based on severity
        flag_count = len(activity.flags)
        if flag_count >= 3:
            emoji = "🚨"  # High severity
        elif flag_count >= 2:
            emoji = "⚠️"  # Medium severity
        else:
            emoji = "🔍"  # Low severity

        # Format position for telegram
        position_display = f"{activity.side} <b>{safe_outcome}</b>" if safe_outcome else activity.side

        telegram_message = (
            f"{emoji} <b>SUSPICIOUS ACTIVITY</b>\n\n"
            f"<b>Flags:</b> {flags_str}\n\n"
            f"<b>Wallet:</b>\n<code>{activity.wallet}</code>\n\n"
            f"<b>Trade Size:</b> ${activity.trade_size_usd:,.2f}\n"
            f"<b>Odds:</b> {activity.price*100:.0f}%\n"
            f"<b>Position:</b> {position_display}\n"
            f"<b>Market:</b> {safe_title}\n"
            f"<b>Wallet History:</b> {activity.wallet_total_trades} total trades\n"
            f"<b>Time:</b> {trade_time}\n\n"
            f"<a href='{url}'>View on Polymarket</a>\n"
            f"<a href='https://polygonscan.com/address/{activity.wallet}'>View Wallet</a>"
        )
        send_telegram_message(telegram_message)


# ============================================================================
# MAIN MONITORING LOOP
# ============================================================================

# Global state for graceful shutdown
shutdown_requested = False
global_state = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    signal_name = signal.Signals(signum).name
    logger.info(f"Received {signal_name}, initiating graceful shutdown...")
    shutdown_requested = True


def monitor_polymarket():
    """Main monitoring function."""
    global global_state

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Initialize Telegram
    telegram_enabled = initialize_telegram()

    state = MonitorState(STATE_FILE)
    global_state = state  # Store reference for shutdown handler

    logger.info("=" * 60)
    logger.info("POLYMARKET MONITOR BOT STARTED")
    logger.info("=" * 60)
    logger.info(f"Suspicious Activity: Large bets >= ${LARGE_BET_THRESHOLD:,.0f}, Fresh wallets <= {FRESH_WALLET_MAX_TRADES} trades")
    logger.info(f"Check Interval: {CHECK_INTERVAL_SECONDS} seconds")
    logger.info(f"State File: {STATE_FILE if STATE_FILE else 'Memory only'}")
    logger.info(f"Telegram: {'Enabled' if telegram_enabled else 'Disabled'}")
    logger.info(f"First Run Mode: {state.first_run}")
    logger.info("=" * 60)

    # Fetch political events once at startup (refresh every 100 iterations)
    political_event_ids = fetch_political_events()
    logger.info(f"Tracking {len(political_event_ids)} political events for repeat detection")

    # Send startup notification (only if not first run)
    if telegram_bot and not state.first_run:
        startup_message = (
            f"🤖 <b>Polymarket Monitor Started</b>\n\n"
            f"<b>Suspicious Activity Detection:</b>\n"
            f"  • Large bets: >= ${LARGE_BET_THRESHOLD:,.0f}\n"
            f"  • Fresh wallets: <= {FRESH_WALLET_MAX_TRADES} trades\n"
            f"  • Political repeats: >= {POLITICAL_REPEAT_THRESHOLD}x\n\n"
            f"<b>Check Interval:</b> {CHECK_INTERVAL_SECONDS} seconds\n"
            f"<b>Tracking:</b> {len(political_event_ids)} political events\n\n"
            f"Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        send_telegram_message(startup_message)

    iteration = 0

    while not shutdown_requested:
        iteration += 1
        logger.info(f"Starting check #{iteration}...")

        try:
            # On first run, populate state without sending alerts
            skip_alerts = state.first_run

            if skip_alerts:
                logger.info("First run - populating baseline state (no alerts will be sent)")

            # Detect suspicious activity (skip on first run)
            if not skip_alerts:
                logger.info("Scanning for suspicious activity...")
                # Fetch recent large trades
                recent_trades = fetch_recent_trades(limit=100, min_usd=LARGE_BET_THRESHOLD)
                logger.info(f"Fetched {len(recent_trades)} recent trades for analysis")

                # Refresh political events periodically
                if iteration % 100 == 0:
                    political_event_ids = fetch_political_events()
                    logger.info(f"Refreshed political events: {len(political_event_ids)} found")

                # Detect suspicious patterns
                suspicious = detect_suspicious_activity(recent_trades, political_event_ids, state)
                if suspicious:
                    logger.info(f"Found {len(suspicious)} suspicious activit{'y' if len(suspicious) == 1 else 'ies'}!")
                    for activity in suspicious:
                        alert_suspicious_activity(activity)
                else:
                    logger.info("No suspicious activity detected")

            # Complete first run if applicable
            if state.first_run:
                state.complete_first_run()
                logger.info(f"Initialization complete. Now tracking {len(political_event_ids)} political events.")
                if telegram_bot:
                    send_telegram_message(
                        f"🤖 <b>Polymarket Monitor Initialized</b>\n\n"
                        f"Baseline established. Now tracking:\n"
                        f"  • {len(political_event_ids)} political events\n\n"
                        f"Alerts will be sent for:\n"
                        f"  • Suspicious activity (fresh wallets, large bets, political repeats)"
                    )

            # Batch save state at end of cycle (instead of per-update)
            state.save_state()

            logger.info(f"Check #{iteration} complete. Sleeping for {CHECK_INTERVAL_SECONDS}s...")

        except Exception as e:
            logger.error(f"Unexpected error during monitoring: {e}")
            logger.info(f"Will retry in {CHECK_INTERVAL_SECONDS} seconds...")

        # Sleep until next check (interrupt
        for _ in range(CHECK_INTERVAL_SECONDS):
            if shutdown_requested:
                break
            time.sleep(1)

    # Graceful shutdown
    logger.info("Shutting down...")
    if global_state:
        global_state.save_state()
        logger.info("State saved.")
    if telegram_bot:
        send_telegram_message(
            f"🛑 <b>Polymarket Monitor Bot Stopped</b>\n\n"
            f"Monitoring ended at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    try:
        monitor_polymarket()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        if telegram_bot:
            send_telegram_message(
                f"❌ <b>Polymarket Monitor Bot Crashed</b>\n\n"
                f"Error: {escape_html(str(e))}\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        raise
    finally:
        logger.info("Monitor exited.")