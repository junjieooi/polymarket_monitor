# Polymarket Monitor - VPS Deployment Guide

## Prerequisites

- VPS with Ubuntu/Debian (or similar Linux)
- Python 3.9+
- SSH access to your VPS

---

## 1. Upload Files to VPS

From your local machine:

```bash
# Create directory on VPS
ssh user@your-vps "mkdir -p ~/polymarket_monitor"

# Upload files
scp main.py fetch_chatID.py .env requirements.txt user@your-vps:~/polymarket_monitor/
```

Or clone from git if you've pushed to a repo:

```bash
ssh user@your-vps
git clone https://github.com/yourusername/polymarket_monitor.git
cd polymarket_monitor
```

---

## 2. Install Dependencies

SSH into your VPS:

```bash
ssh user@your-vps
cd ~/polymarket_monitor

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install python-telegram-bot python-dotenv requests
```

Or if you have a requirements.txt:

```bash
pip install -r requirements.txt
```

---

## 3. Configure Environment

```bash
# Edit your .env file
nano .env
```

Ensure these are set:

```
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
ENABLE_TELEGRAM=true
LOG_LEVEL=INFO
```

---

## 4. Deployment Options

### Option A: systemd (Recommended)

Create a systemd service file:

```bash
sudo nano /etc/systemd/system/polymarket-monitor.service
```

Paste this content (adjust paths and username):

```ini
[Unit]
Description=Polymarket Monitor Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/home/your_username/polymarket_monitor
Environment=PATH=/home/your_username/polymarket_monitor/venv/bin
ExecStart=/home/your_username/polymarket_monitor/venv/bin/python main.py
Restart=always
RestartSec=10

# Logging
StandardOutput=append:/var/log/polymarket-monitor.log
StandardError=append:/var/log/polymarket-monitor.log

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable on boot
sudo systemctl enable polymarket-monitor

# Start the service
sudo systemctl start polymarket-monitor

# Check status
sudo systemctl status polymarket-monitor
```

**Useful commands:**

```bash
# View logs
sudo journalctl -u polymarket-monitor -f

# Or check log file
tail -f /var/log/polymarket-monitor.log

# Restart
sudo systemctl restart polymarket-monitor

# Stop
sudo systemctl stop polymarket-monitor
```

---

### Option B: PM2 (If you're already using it for other bots)

```bash
# Install PM2 if not already installed
npm install -g pm2

cd ~/polymarket_monitor

# Start with PM2
pm2 start main.py --name polymarket-monitor --interpreter ./venv/bin/python

# Save PM2 process list
pm2 save

# Set PM2 to start on boot (if not already done)
pm2 startup
```

**Useful commands:**

```bash
# View logs
pm2 logs polymarket-monitor

# Restart
pm2 restart polymarket-monitor

# Stop
pm2 stop polymarket-monitor

# List all processes
pm2 list
```

---

### Option C: Docker

Create a `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY .env .

CMD ["python", "main.py"]
```

Create `requirements.txt` if not exists:

```
python-telegram-bot>=20.0
python-dotenv>=1.0.0
requests>=2.28.0
```

Build and run:

```bash
# Build image
docker build -t polymarket-monitor .

# Run container
docker run -d \
  --name polymarket-monitor \
  --restart unless-stopped \
  -v $(pwd)/polymarket_state.json:/app/polymarket_state.json \
  polymarket-monitor
```

**Useful commands:**

```bash
# View logs
docker logs -f polymarket-monitor

# Restart
docker restart polymarket-monitor

# Stop
docker stop polymarket-monitor
```

---

### Option D: Docker Compose (If running multiple containers)

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  polymarket-monitor:
    build: .
    container_name: polymarket-monitor
    restart: unless-stopped
    volumes:
      - ./polymarket_state.json:/app/polymarket_state.json
    environment:
      - TZ=UTC
```

Run:

```bash
docker-compose up -d
```

---

## 5. Running Alongside Other Bots

Since you have another bot running, here's how to manage multiple bots:

### If using systemd:
Each bot gets its own service file. They run independently.

```bash
# List all your services
systemctl list-units --type=service | grep -E "(polymarket|your-other-bot)"
```

### If using PM2:
PM2 handles multiple processes natively.

```bash
# View all running processes
pm2 list

# Expected output:
# ┌─────────────────────┬────┬─────────┬──────┐
# │ Name                │ id │ status  │ cpu  │
# ├─────────────────────┼────┼─────────┼──────┤
# │ your-other-bot      │ 0  │ online  │ 0.1% │
# │ polymarket-monitor  │ 1  │ online  │ 0.2% │
# └─────────────────────┴────┴─────────┴──────┘
```

### If using Docker:
Each container runs independently.

```bash
# View all containers
docker ps
```

---

## 6. Monitoring & Maintenance

### Check if running:

```bash
# systemd
sudo systemctl is-active polymarket-monitor

# PM2
pm2 status polymarket-monitor

# Docker
docker ps | grep polymarket
```

### View resource usage:

```bash
# systemd - check with htop or:
ps aux | grep main.py

# PM2
pm2 monit

# Docker
docker stats polymarket-monitor
```

### Update the bot:

```bash
# Stop the service
sudo systemctl stop polymarket-monitor  # or pm2 stop / docker stop

# Upload new files or git pull
git pull  # if using git

# Restart
sudo systemctl start polymarket-monitor  # or pm2 restart / docker restart
```

---

## 7. Troubleshooting

### Bot not starting?

```bash
# Check logs
sudo journalctl -u polymarket-monitor -n 50 --no-pager

# Test manually
cd ~/polymarket_monitor
source venv/bin/activate
python main.py
```

### Telegram not working?

```bash
# Test bot token
curl "https://api.telegram.org/bot<YOUR_TOKEN>/getMe"

# Check .env is loaded
cat .env
```

### State file permission issues?

```bash
# Ensure correct ownership
sudo chown your_username:your_username polymarket_state.json
chmod 644 polymarket_state.json
```

---

## Quick Reference

| Action | systemd | PM2 | Docker |
|--------|---------|-----|--------|
| Start | `systemctl start polymarket-monitor` | `pm2 start polymarket-monitor` | `docker start polymarket-monitor` |
| Stop | `systemctl stop polymarket-monitor` | `pm2 stop polymarket-monitor` | `docker stop polymarket-monitor` |
| Restart | `systemctl restart polymarket-monitor` | `pm2 restart polymarket-monitor` | `docker restart polymarket-monitor` |
| Logs | `journalctl -u polymarket-monitor -f` | `pm2 logs polymarket-monitor` | `docker logs -f polymarket-monitor` |
| Status | `systemctl status polymarket-monitor` | `pm2 status` | `docker ps` |
