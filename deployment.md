# Polymarket Monitor - VPS Deployment Guide

This guide walks you through deploying the Polymarket Monitor bot on a VPS for 24/7 operation.

## Prerequisites

- VPS with Ubuntu/Debian (DigitalOcean, Linode, AWS, etc.)
- Python 3.9+
- SSH access to your VPS
- Telegram bot token and chat ID

---

## Step 1: Clone Repository to VPS

SSH into your server:
```bash
ssh user@your-server-ip
```

Clone the repository:
```bash
git clone https://github.com/yourusername/polymarket_monitor.git
cd polymarket_monitor
```

> **Tip**: For private repos, use a personal access token:
> ```bash
> git clone https://username:token@github.com/username/polymarket_monitor.git
> ```

---

## Step 2: Set Up Python Environment

Install Python if not already installed:
```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv
```

Create and activate virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:
```bash
pip install -r requirements.txt
```

---

## Step 3: Configure Environment Variables

Create the `.env` file:
```bash
nano .env
```

Add your configuration:
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
ENABLE_TELEGRAM=true
VOLUME_PERCENT_THRESHOLD=300
VOLUME_ABSOLUTE_THRESHOLD=50000
CHECK_INTERVAL_SECONDS=120
STATE_FILE=polymarket_state.json
LOG_LEVEL=INFO
```

Save and exit: `Ctrl+X` → `Y` → `Enter`

---

## Step 4: Test the Bot

```bash
source venv/bin/activate
python main.py
```

You should receive a Telegram message. Press `Ctrl+C` to stop.

---

## Step 5: Set Up systemd Service (Recommended)

Create the service file:
```bash
sudo nano /etc/systemd/system/polymarket-monitor.service
```

Paste this content (adjust `User` and paths if needed):
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

Save and exit.

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable polymarket-monitor
sudo systemctl start polymarket-monitor
```

Verify it's running:
```bash
sudo systemctl status polymarket-monitor
```

You should see `active (running)` in green.

---

## Managing the Service

| Action | Command |
|--------|---------|
| Check status | `sudo systemctl status polymarket-monitor` |
| Start | `sudo systemctl start polymarket-monitor` |
| Stop | `sudo systemctl stop polymarket-monitor` |
| Restart | `sudo systemctl restart polymarket-monitor` |
| View live logs | `journalctl -u polymarket-monitor -f` |
| View recent logs | `journalctl -u polymarket-monitor --since "1 hour ago"` |
| Check if running | `sudo systemctl is-active polymarket-monitor` |

---

## Updating the Bot

When you push updates to GitHub:

```bash
cd ~/polymarket_monitor
git pull
sudo systemctl restart polymarket-monitor
```

---

## Running Multiple Bots

Each bot gets its own:
1. Directory (e.g., `/home/trader/bot1`, `/home/trader/bot2`)
2. Virtual environment (`venv` folder inside each)
3. systemd service file (different names)

They run independently without conflicts.

List all your services:
```bash
systemctl list-units --type=service | grep your-keyword
```

---

## Troubleshooting

### Bot not starting?

Check logs for errors:
```bash
sudo journalctl -u polymarket-monitor -n 50 --no-pager
```

Test manually:
```bash
cd ~/polymarket_monitor
source venv/bin/activate
python main.py
```

### Telegram not working?

Verify bot token:
```bash
curl "https://api.telegram.org/bot<YOUR_TOKEN>/getMe"
```

Check `.env` is configured:
```bash
cat .env
```

### Permission issues?

Ensure correct ownership:
```bash
sudo chown -R trader:trader ~/polymarket_monitor
```

---

## Alternative: PM2

If you prefer PM2 (Node.js process manager):

```bash
npm install -g pm2
cd ~/polymarket_monitor
pm2 start main.py --name polymarket-monitor --interpreter ./venv/bin/python
pm2 save
pm2 startup
```

| Action | Command |
|--------|---------|
| Status | `pm2 status` |
| Logs | `pm2 logs polymarket-monitor` |
| Restart | `pm2 restart polymarket-monitor` |
| Stop | `pm2 stop polymarket-monitor` |

---

## Alternative: Docker

Create `Dockerfile`:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .env ./
CMD ["python", "main.py"]
```

Build and run:
```bash
docker build -t polymarket-monitor .
docker run -d --name polymarket-monitor --restart unless-stopped polymarket-monitor
```

| Action | Command |
|--------|---------|
| Status | `docker ps` |
| Logs | `docker logs -f polymarket-monitor` |
| Restart | `docker restart polymarket-monitor` |
| Stop | `docker stop polymarket-monitor` |
