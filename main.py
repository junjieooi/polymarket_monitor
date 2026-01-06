#!/usr/bin/env python3
"""
Polymarket Real-Time Monitor Bot with Telegram Notifications
Detects new markets and volume spikes on Polymarket using the public Gamma API.
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
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, Set
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

# Volume spike thresholds (configurable via env vars)
VOLUME_PERCENT_THRESHOLD = int(os.getenv("VOLUME_PERCENT_THRESHOLD", "300"))
VOLUME_ABSOLUTE_THRESHOLD = int(os.getenv("VOLUME_ABSOLUTE_THRESHOLD", "50000"))

# Suspicious activity thresholds
LARGE_BET_THRESHOLD = float(os.getenv("LARGE_BET_THRESHOLD", "10000"))  # USD
FRESH_WALLET_MAX_TRADES = int(os.getenv("FRESH_WALLET_MAX_TRADES", "5"))  # Max trades to be "fresh"
POLITICAL_REPEAT_THRESHOLD = int(os.getenv("POLITICAL_REPEAT_THRESHOLD", "3"))  # Repeated entries
MAX_BUY_PRICE = float(os.getenv("MAX_BUY_PRICE", "0.70"))  # Max price to track (filters out 99% bots)

# Monitoring interval
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "120"))

# API endpoints
EVENTS_API_URL = "https://gamma-api.polymarket.com/events"
MARKETS_API_URL = "https://gamma-api.polymarket.com/markets"
DATA_API_URL = "https://data-api.polymarket.com"

# Persistence
STATE_FILE = os.getenv("STATE_FILE", "polymarket_state.json")

# Request timeout and retry settings
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # Exponential backoff delays in seconds

# Stale market cleanup (days)
STALE_MARKET_DAYS = 7

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
            f"✅ <b>Polymarket Volume Tracking</b>\n\n"
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
    """Manages the monitoring state (max event ID and market volumes)."""

    def __init__(self, state_file: Optional[str] = None):
        self.state_file = state_file
        self.max_event_id: int = 0
        self.market_volumes: Dict[str, float] = {}
        self.market_last_seen: Dict[str, str] = {}  # Track when markets were last seen
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
                    self.max_event_id = data.get('max_event_id', 0)
                    self.market_volumes = data.get('market_volumes', {})
                    self.market_last_seen = data.get('market_last_seen', {})
                    self.seen_suspicious = set(data.get('seen_suspicious', []))
                    self.first_run = False  # Not first run if we loaded state
                    logger.info(f"Loaded state from {self.state_file}")
                    logger.info(f"Max event ID: {self.max_event_id}, Tracking {len(self.market_volumes)} markets")
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
                    'max_event_id': self.max_event_id,
                    'market_volumes': self.market_volumes,
                    'market_last_seen': self.market_last_seen,
                    'seen_suspicious': recent_suspicious
                }, f, indent=2)
            self._dirty = False
            logger.debug("State saved to file")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def mark_dirty(self):
        """Mark state as needing to be saved."""
        self._dirty = True

    def update_max_event_id(self, event_id: int):
        """Update the maximum event ID seen (without immediate save)."""
        if event_id > self.max_event_id:
            self.max_event_id = event_id
            self.mark_dirty()

    def update_market_volume(self, market_id: str, volume: float):
        """Update the stored volume for a market (without immediate save)."""
        self.market_volumes[market_id] = volume
        self.market_last_seen[market_id] = datetime.now().isoformat()
        self.mark_dirty()

    def get_previous_volume(self, market_id: str) -> Optional[float]:
        """Get the previously stored volume for a market."""
        return self.market_volumes.get(market_id)

    def cleanup_stale_markets(self, days: int = STALE_MARKET_DAYS):
        """Remove markets not seen in the specified number of days."""
        cutoff = datetime.now() - timedelta(days=days)
        stale_markets = []

        for market_id, last_seen_str in list(self.market_last_seen.items()):
            try:
                last_seen = datetime.fromisoformat(last_seen_str)
                if last_seen < cutoff:
                    stale_markets.append(market_id)
            except (ValueError, TypeError):
                # Invalid date, remove it
                stale_markets.append(market_id)

        for market_id in stale_markets:
            self.market_volumes.pop(market_id, None)
            self.market_last_seen.pop(market_id, None)

        if stale_markets:
            logger.info(f"Cleaned up {len(stale_markets)} stale markets (not seen in {days} days)")
            self.mark_dirty()

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


def fetch_all_events(limit_per_page: int = 100, max_events: int = 500) -> List[Dict]:
    """
    Fetch active/trending events from Polymarket.
    Only fetches open events sorted by volume (most active first).
    Limited to prevent OOM on low-memory servers.

    Args:
        limit_per_page: Number of events per API call
        max_events: Maximum total events to fetch (default 500)
    """
    all_events = []
    offset = 0

    while len(all_events) < max_events:
        params = {
            'limit': limit_per_page,
            'offset': offset,
            'active': 'true',  # Only active/open events
            'order': 'volume24hr',  # Sort by 24h volume (trending)
            'ascending': 'false'  # Highest volume first
        }

        response = fetch_with_retry(EVENTS_API_URL, params)
        if response is None:
            logger.error(f"Failed to fetch events at offset {offset}")
            break

        events = response.json()

        if not events:
            break

        all_events.extend(events)

        if len(events) < limit_per_page:
            break

        offset += limit_per_page

    # Trim to max_events if we got more
    all_events = all_events[:max_events]
    return all_events


def fetch_all_markets(limit_per_page: int = 100, max_markets: int = 1000) -> List[Dict]:
    """
    Fetch active markets from Polymarket, sorted by volume (trending).
    Limited to prevent OOM on low-memory servers.

    Args:
        limit_per_page: Number of markets per API call
        max_markets: Maximum total markets to fetch (default 1000)
    """
    all_markets = []
    offset = 0

    while len(all_markets) < max_markets:
        params = {
            'limit': limit_per_page,
            'offset': offset,
            'active': 'true',
            'order': 'volume24hr',  # Sort by 24h volume (trending)
            'ascending': 'false'  # Highest volume first
        }

        response = fetch_with_retry(MARKETS_API_URL, params)
        if response is None:
            logger.error(f"Failed to fetch markets at offset {offset}")
            break

        markets = response.json()

        if not markets:
            break

        all_markets.extend(markets)

        if len(markets) < limit_per_page:
            break

        offset += limit_per_page

    # Trim to max_markets if we got more
    all_markets = all_markets[:max_markets]
    return all_markets


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
    political_keywords = ['election', 'president', 'trump', 'biden', 'congress',
                          'senate', 'governor', 'vote', 'primary', 'democrat',
                          'republican', 'political', 'politician', 'poll']

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
            if any(kw in combined_text for kw in political_keywords):
                event_id = event.get('id')
                if event_id:
                    political_event_ids.append(int(event_id))
    except Exception as e:
        logger.error(f"Error parsing political events: {e}")

    return political_event_ids


# ============================================================================
# DETECTION FUNCTIONS
# ============================================================================

def get_volume_from_market(market: Dict) -> float:
    """
    Extract volume from market data, checking multiple possible fields.
    Uses proper None checks instead of truthy checks to handle 0 values correctly.
    """
    # Check fields in order of preference
    for field in ['volume24hr', 'volume24hrClob', 'volumeNum']:
        value = market.get(field)
        if value is not None:
            try:
                return float(value)
            except (ValueError, TypeError):
                continue
    return 0.0


def detect_new_events(events: List[Dict], state: MonitorState, skip_alerts: bool = False) -> List[Dict]:
    """
    Detect new events that have appeared since last check.
    Returns list of new events.

    Args:
        events: List of event dictionaries
        state: Monitor state object
        skip_alerts: If True, updates state but returns empty list (for first run)
    """
    new_events = []

    for event in events:
        try:
            event_id = int(event.get('id', 0))

            # If this event ID is higher than our max, it's new
            if event_id > state.max_event_id:
                if not skip_alerts:
                    new_events.append(event)
                state.update_max_event_id(event_id)
        except (ValueError, TypeError):
            continue

    return new_events


def detect_volume_spikes(markets: List[Dict], state: MonitorState, skip_alerts: bool = False) -> List[Tuple[Dict, float, float, float]]:
    """
    Detect volume spikes on existing markets.
    Returns list of tuples: (market, current_volume, previous_volume, percent_increase)

    Args:
        markets: List of market dictionaries
        state: Monitor state object
        skip_alerts: If True, updates state but returns empty list (for first run)
    """
    spikes = []

    for market in markets:
        try:
            market_id = market.get('id')
            if not market_id:
                continue

            # Get volume using proper None checks
            current_volume = get_volume_from_market(market)

            # Skip if no volume data
            if current_volume == 0:
                state.update_market_volume(market_id, current_volume)
                continue

            # Get previous volume
            previous_volume = state.get_previous_volume(market_id)

            # If this is the first time seeing this market, just store it
            if previous_volume is None:
                state.update_market_volume(market_id, current_volume)
                continue

            # Calculate changes
            volume_increase = current_volume - previous_volume

            # Skip if volume decreased or stayed the same
            if volume_increase <= 0:
                state.update_market_volume(market_id, current_volume)
                continue

            # Calculate percent increase
            percent_increase = (volume_increase / previous_volume * 100) if previous_volume > 0 else 0

            # Check if spike thresholds are met (only if not skipping alerts)
            if not skip_alerts and (percent_increase >= VOLUME_PERCENT_THRESHOLD or
                    volume_increase >= VOLUME_ABSOLUTE_THRESHOLD):
                spikes.append((market, current_volume, previous_volume, percent_increase))

            # Update stored volume
            state.update_market_volume(market_id, current_volume)

        except (ValueError, TypeError, ZeroDivisionError) as e:
            logger.error(f"Error processing market {market.get('id')}: {e}")
            continue

    return spikes


# ============================================================================
# SUSPICIOUS ACTIVITY DETECTION
# ============================================================================

@dataclass
class SuspiciousActivity:
    """Represents a flagged suspicious activity."""
    wallet: str
    trade_size_usd: float
    market_title: str
    market_slug: str
    event_slug: str
    side: str
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
        side = trade.get('side', 'UNKNOWN')
        timestamp = trade.get('timestamp', 0)
        condition_id = trade.get('conditionId', '')

        # Skip buys at high prices (bots buying near-certain outcomes for small wins)
        if side == 'BUY' and price > MAX_BUY_PRICE:
            continue

        # Create unique key for dedup
        trade_key = f"{wallet}_{condition_id}_{timestamp}"
        if hasattr(state, 'seen_suspicious') and trade_key in state.seen_suspicious:
            continue

        flags = []

        # Check 1: Is this a large bet?
        is_large_bet = usd_size >= LARGE_BET_THRESHOLD
        if is_large_bet:
            flags.append(f"LARGE_BET (${usd_size:,.0f})")

        # Check 2: Is this a fresh wallet?
        is_fresh, total_trades = analyze_wallet_freshness(wallet, cached_wallet_data)
        if is_fresh:
            flags.append(f"FRESH_WALLET ({total_trades} trades)")

        # Check 3: Is this a political market with repeated entries?
        # Check if this trade's event is in political events
        is_political = False
        try:
            # Try to match by event slug or condition
            for pid in political_event_ids:
                if str(pid) in condition_id or str(pid) in event_slug:
                    is_political = True
                    break
        except:
            pass

        # Also check by market title keywords
        political_keywords = ['election', 'president', 'trump', 'biden', 'congress',
                              'senate', 'governor', 'vote', 'primary', 'democrat',
                              'republican', 'political']
        if any(kw in market_title.lower() for kw in political_keywords):
            is_political = True

        if is_political:
            wallet_political_trades[wallet] = wallet_political_trades.get(wallet, 0) + 1
            if wallet_political_trades[wallet] >= POLITICAL_REPEAT_THRESHOLD:
                flags.append(f"POLITICAL_REPEAT ({wallet_political_trades[wallet]}x)")

        # Only flag if LARGE BET + at least one other suspicious pattern
        if is_large_bet and len(flags) >= 2:
            activity = SuspiciousActivity(
                wallet=wallet,
                trade_size_usd=usd_size,
                market_title=market_title,
                market_slug=market_slug,
                event_slug=event_slug,
                side=side,
                timestamp=timestamp,
                flags=flags,
                wallet_total_trades=total_trades,
                political_trade_count=wallet_political_trades.get(wallet, 0)
            )
            suspicious.append(activity)

            # Mark as seen
            if not hasattr(state, 'seen_suspicious'):
                state.seen_suspicious = set()
            state.seen_suspicious.add(trade_key)

    return suspicious


# ============================================================================
# ALERT FUNCTIONS
# ============================================================================

def alert_new_event(event: Dict):
    """Print alert for a new event/market and send Telegram notification."""
    event_id = event.get('id', 'N/A')
    title = event.get('title', 'Unknown Event')
    slug = event.get('slug', '')
    description = event.get('description', 'No description')

    # Construct URL
    url = f"https://polymarket.com/event/{slug}" if slug else "URL not available"

    # Console output
    logger.info("=" * 60)
    logger.info("NEW MARKET DETECTED")
    logger.info(f"Event ID: {event_id}")
    logger.info(f"Title: {title}")
    logger.info(f"URL: {url}")
    logger.info(f"Description: {description[:150]}...")
    logger.info("=" * 60)

    # Telegram notification (with HTML escaping for user content)
    if telegram_bot:
        safe_title = escape_html(title)
        safe_description = escape_html(description[:200])
        telegram_message = (
            f"🆕 <b>NEW MARKET DETECTED</b>\n\n"
            f"<b>Event ID:</b> {event_id}\n"
            f"<b>Title:</b> {safe_title}\n"
            f"<b>Description:</b> {safe_description}{'...' if len(description) > 200 else ''}\n\n"
            f"<a href='{url}'>View on Polymarket</a>"
        )
        send_telegram_message(telegram_message)


def alert_volume_spike(market: Dict, current_volume: float, previous_volume: float, percent_increase: float):
    """Print alert for a volume spike and send Telegram notification."""
    market_id = market.get('id', 'N/A')
    question = market.get('question', 'Unknown Market')
    slug = market.get('slug', '')

    # Try to get event slug if available
    event_slug = market.get('eventSlug', slug)
    url = f"https://polymarket.com/event/{event_slug}" if event_slug else "URL not available"

    volume_increase = current_volume - previous_volume

    # Console output
    logger.info("=" * 60)
    logger.info("VOLUME SPIKE DETECTED")
    logger.info(f"Market ID: {market_id}")
    logger.info(f"Question: {question}")
    logger.info(f"URL: {url}")
    logger.info(f"Previous 24h Vol: ${previous_volume:,.2f}")
    logger.info(f"Current 24h Vol: ${current_volume:,.2f}")
    logger.info(f"Volume Increase: ${volume_increase:,.2f} (+{percent_increase:.1f}%)")
    logger.info("=" * 60)

    # Telegram notification (with HTML escaping for user content)
    if telegram_bot:
        safe_question = escape_html(question)
        telegram_message = (
            f"📈 <b>VOLUME SPIKE DETECTED</b>\n\n"
            f"<b>Market ID:</b> {market_id}\n"
            f"<b>Question:</b> {safe_question}\n\n"
            f"<b>Previous 24h Volume:</b> ${previous_volume:,.2f}\n"
            f"<b>Current 24h Volume:</b> ${current_volume:,.2f}\n"
            f"<b>Volume Increase:</b> ${volume_increase:,.2f} (+{percent_increase:.1f}%)\n\n"
            f"<a href='{url}'>View on Polymarket</a>"
        )
        send_telegram_message(telegram_message)


def alert_suspicious_activity(activity: SuspiciousActivity):
    """Print alert for suspicious activity and send Telegram notification."""
    wallet_short = f"{activity.wallet[:6]}...{activity.wallet[-4:]}"

    # Construct URL
    url = f"https://polymarket.com/event/{activity.event_slug}" if activity.event_slug else "URL not available"

    # Format timestamp
    trade_time = datetime.fromtimestamp(activity.timestamp).strftime('%Y-%m-%d %H:%M:%S') if activity.timestamp else 'Unknown'

    # Console output
    logger.info("=" * 60)
    logger.info("SUSPICIOUS ACTIVITY DETECTED")
    logger.info(f"Wallet: {wallet_short}")
    logger.info(f"Flags: {', '.join(activity.flags)}")
    logger.info(f"Trade Size: ${activity.trade_size_usd:,.2f}")
    logger.info(f"Side: {activity.side}")
    logger.info(f"Market: {activity.market_title}")
    logger.info(f"Wallet Total Trades: {activity.wallet_total_trades}")
    logger.info(f"Time: {trade_time}")
    logger.info(f"URL: {url}")
    logger.info("=" * 60)

    # Telegram notification
    if telegram_bot:
        safe_title = escape_html(activity.market_title)
        flags_str = ' | '.join(activity.flags)

        # Determine emoji based on severity
        flag_count = len(activity.flags)
        if flag_count >= 3:
            emoji = "🚨"  # High severity
        elif flag_count >= 2:
            emoji = "⚠️"  # Medium severity
        else:
            emoji = "🔍"  # Low severity

        telegram_message = (
            f"{emoji} <b>SUSPICIOUS ACTIVITY</b>\n\n"
            f"<b>Flags:</b> {flags_str}\n\n"
            f"<b>Wallet:</b> <code>{wallet_short}</code>\n"
            f"<b>Trade Size:</b> ${activity.trade_size_usd:,.2f}\n"
            f"<b>Side:</b> {activity.side}\n"
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
    logger.info(f"Volume Spike Thresholds: {VOLUME_PERCENT_THRESHOLD}% or ${VOLUME_ABSOLUTE_THRESHOLD:,}")
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
            f"<b>Volume Spike Thresholds:</b>\n"
            f"  • Increase: {VOLUME_PERCENT_THRESHOLD}% or ${VOLUME_ABSOLUTE_THRESHOLD:,}\n\n"
            f"<b>Suspicious Activity Detection:</b>\n"
            f"  • Large bets: >= ${LARGE_BET_THRESHOLD:,.0f}\n"
            f"  • Fresh wallets: <= {FRESH_WALLET_MAX_TRADES} trades\n"
            f"  • Political repeats: >= {POLITICAL_REPEAT_THRESHOLD}x\n\n"
            f"<b>Check Interval:</b> {CHECK_INTERVAL_SECONDS} seconds\n"
            f"<b>Tracking:</b> {len(state.market_volumes)} markets, {len(political_event_ids)} political events\n\n"
            f"Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        send_telegram_message(startup_message)

    iteration = 0

    while not shutdown_requested:
        iteration += 1
        logger.info(f"Starting check #{iteration}...")

        try:
            # Fetch markets (sorted by volume - trending)
            logger.info("Fetching markets...")
            markets = fetch_all_markets()
            logger.info(f"Fetched {len(markets)} active markets")

            # On first run, populate state without sending alerts
            skip_alerts = state.first_run

            if skip_alerts:
                logger.info("First run - populating baseline state (no alerts will be sent)")

            # Detect volume spikes
            spikes = detect_volume_spikes(markets, state, skip_alerts=skip_alerts)
            if spikes:
                logger.info(f"Found {len(spikes)} volume spike(s)!")
                for market, curr_vol, prev_vol, pct_inc in spikes:
                    alert_volume_spike(market, curr_vol, prev_vol, pct_inc)
            else:
                logger.info("No volume spikes detected")

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
                logger.info(f"Initialization complete. Now tracking {len(state.market_volumes)} markets.")
                if telegram_bot:
                    send_telegram_message(
                        f"🤖 <b>Polymarket Monitor Initialized</b>\n\n"
                        f"Baseline established. Now tracking:\n"
                        f"  • {len(state.market_volumes)} markets (by volume)\n"
                        f"  • {len(political_event_ids)} political events\n\n"
                        f"Alerts will be sent for:\n"
                        f"  • Volume spikes\n"
                        f"  • Suspicious activity (fresh wallets, large bets, political repeats)"
                    )

            # Cleanup stale markets periodically (every 10 iterations)
            if iteration % 10 == 0:
                state.cleanup_stale_markets()

            # Batch save state at end of cycle (instead of per-update)
            state.save_state()

            logger.info(f"Check #{iteration} complete. Sleeping for {CHECK_INTERVAL_SECONDS}s...")

        except Exception as e:
            logger.error(f"Unexpected error during monitoring: {e}")
            logger.info(f"Will retry in {CHECK_INTERVAL_SECONDS} seconds...")

        # Sleep until next check (interruptible)
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