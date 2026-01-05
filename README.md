# Polymarket Monitor Bot

A real-time monitoring bot that tracks Polymarket prediction markets and sends Telegram alerts for:
- **New markets** - Get notified when new prediction markets are created
- **Volume spikes** - Detect unusual trading activity on existing markets

## Features

- Real-time monitoring of Polymarket via the Gamma API
- Telegram notifications with direct links to markets
- Configurable volume spike thresholds (percentage and absolute)
- Persistent state - survives restarts without duplicate alerts
- Graceful shutdown handling
- Auto-cleanup of stale market data

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/junjieooi/polymarket_monitor.git
cd polymarket_monitor
```

### 2. Set Up Python Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Copy the example file and edit it:

```bash
cp .env.example .env
nano .env
```

Required settings:
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

### 4. Run the Bot

```bash
python main.py
```

## Getting Telegram Credentials

### Bot Token
1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the bot token provided

### Chat ID
1. Start a chat with your new bot (send any message)
2. Run the helper script:
   ```bash
   python fetch_chatID.py
   ```
3. Copy the chat ID from the output

## Configuration

All settings are configured via the `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | - | Your Telegram bot token (required) |
| `TELEGRAM_CHAT_ID` | - | Your Telegram chat ID (required) |
| `ENABLE_TELEGRAM` | `true` | Enable/disable Telegram notifications |
| `VOLUME_PERCENT_THRESHOLD` | `300` | Alert when volume increases by this % |
| `VOLUME_ABSOLUTE_THRESHOLD` | `50000` | Alert when volume increases by this USD |
| `CHECK_INTERVAL_SECONDS` | `120` | How often to check for updates |
| `STATE_FILE` | `polymarket_state.json` | File to persist state between restarts |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |

## How It Works

1. **First Run**: The bot fetches all current markets and establishes a baseline (no alerts sent)
2. **Subsequent Runs**: Compares current state to baseline and sends alerts for:
   - New events with IDs higher than previously seen
   - Markets where 24h volume increased beyond thresholds

### Volume Spike Detection

A volume spike alert is triggered when EITHER condition is met:
- Volume increased by >= `VOLUME_PERCENT_THRESHOLD`% (default 300%)
- Volume increased by >= `VOLUME_ABSOLUTE_THRESHOLD` USD (default $50,000)

## Deployment

For 24/7 operation on a VPS, see [deployment.md](deployment.md).

**Quick systemd setup:**
```bash
sudo nano /etc/systemd/system/polymarket-monitor.service
```

```ini
[Unit]
Description=Polymarket Monitor Bot
After=network.target

[Service]
Type=simple
User=trader
WorkingDirectory=/home/trader/polymarket_monitor
ExecStart=/home/trader/polymarket_monitor/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable polymarket-monitor
sudo systemctl start polymarket-monitor
```

## Useful Commands

| Command | Description |
|---------|-------------|
| `sudo systemctl status polymarket-monitor` | Check if bot is running |
| `sudo systemctl restart polymarket-monitor` | Restart the bot |
| `sudo systemctl stop polymarket-monitor` | Stop the bot |
| `journalctl -u polymarket-monitor -f` | View live logs |

## Updating

```bash
cd ~/polymarket_monitor
git pull
sudo systemctl restart polymarket-monitor
```

## Project Structure

```
polymarket_monitor/
├── main.py              # Main bot script
├── fetch_chatID.py      # Helper to get Telegram chat ID
├── requirements.txt     # Python dependencies
├── .env.example         # Example environment configuration
├── .env                 # Your configuration (not in git)
├── .gitignore           # Git ignore rules
├── deployment.md        # Detailed deployment guide
└── README.md            # This file
```

## License

MIT License
