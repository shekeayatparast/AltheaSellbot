# 3X-UI VPN Sales Bot - Setup Guide

## 📋 Prerequisites

- Python 3.11 or higher
- Telegram Bot Token (from @BotFather)
- 3X-UI Panel with API access
- Server/VPS for hosting the bot

## 🚀 Installation Steps

### 1. Clone/Download the Bot

```bash
cd /path/to/your/bot
```

### 2. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install aiogram>=3.0.0 aiosqlite aiohttp
```

Or use the requirements file:

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create a `.env` file in the same directory as `bot.py`:

```bash
# Telegram Bot Configuration
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz

# Admin Telegram IDs (comma-separated)
ADMIN_IDS=123456789,987654321

# Primary 3X-UI Panel (fallback if no panels in DB)
PANEL_URL=https://your-panel.example.com
API_TOKEN=your-api-token-here

# Database Path (optional, defaults to bot_database.db)
DATABASE_PATH=bot_database.db

# Webhook URL (optional, leave empty for polling mode)
WEBHOOK_URL=
```

### 5. Get Your Telegram User ID

To set yourself as admin:

1. Message @userinfobot on Telegram
2. It will reply with your numeric ID
3. Add this ID to `ADMIN_IDS` in `.env`

### 6. Get 3X-UI Panel API Token

1. Log in to your 3X-UI panel
2. Go to **Settings → Security → API Token**
3. Click **Create Token**
4. Give it a name (e.g., "Sales Bot")
5. **Copy the token immediately** (shown only once!)
6. Add it to `API_TOKEN` in `.env`

### 7. Run the Bot

```bash
python bot.py
```

You should see:
```
INFO | Bot is starting up...
INFO | Database connected: bot_database.db
INFO | Registered 1 admin(s)
INFO | Background tasks started
INFO | Webhook deleted (polling mode)
INFO | Bot running in polling mode...
```

## 🎯 First-Time Setup

### Add Your Panel via Bot (Admin Only)

If you want to configure multiple panels or change settings:

1. Start the bot on Telegram: `/start`
2. As an admin, use: `/addpanel`
3. Send panel details in format:
   ```
   Panel Name|https://panel-url.com|api-token
   ```

### Create Promo Codes (Admin)

From the admin panel inside Telegram:
1. Click **Admin Panel → Promo Codes**
2. Click **Create Promo**
3. Follow the prompts

### Customize Plans

The bot comes with default plans. To modify:

**Option 1:** Via database directly
```bash
sqlite3 bot_database.db
UPDATE plans SET price=9.99 WHERE name='Standard';
```

**Option 2:** Add new plans via admin panel (feature can be added)

## 🔧 Advanced Configuration

### Webhook Mode (for production)

For better performance on production:

1. Set up a reverse proxy (nginx)
2. Configure SSL certificate
3. Set `WEBHOOK_URL` in `.env`:
   ```
   WEBHOOK_URL=https://your-domain.com/webhook
   ```
4. Run the bot (it will start a web server on port 8080)

### Multiple Panels (Load Balancing)

The bot supports multiple 3X-UI panels:

1. Add first panel as primary via `/addpanel`
2. Add more panels via admin panel
3. The bot will automatically balance new clients across healthy panels

### Customizing Welcome Message

Edit the bot code or update via settings:
```sql
UPDATE settings SET value='Your custom welcome message' WHERE key='welcome_message';
```

## 🛡️ Security Best Practices

1. **Protect your `.env` file:**
   ```bash
   chmod 600 .env
   ```

2. **Use environment variables in production:**
   ```bash
   export BOT_TOKEN="..."
   export ADMIN_IDS="..."
   python bot.py
   ```

3. **Regular backups:**
   - Use the **Backup** feature in admin panel
   - Schedule automatic database backups

4. **Limit admin access:**
   - Only add trusted users to `ADMIN_IDS`
   - Use `/setadmin` command carefully

## 📊 Monitoring & Maintenance

### Check Bot Status

```bash
# If running in background
ps aux | grep bot.py

# View logs (if redirected)
tail -f bot.log
```

### Background Tasks

The bot automatically runs:
- ✅ Expiry checks (every hour)
- ✅ Panel health monitoring (every 5 minutes)
- ✅ Notification processing (every minute)

### Manual Cleanup

To delete depleted clients:
1. Go to **Admin Panel → Clients → Depleted**
2. Review and take action

## 🆘 Troubleshooting

### Bot doesn't respond

1. Check if bot is running: `ps aux | grep bot.py`
2. Verify `BOT_TOKEN` is correct
3. Check bot is not blocked by Telegram

### API connection errors

1. Verify panel URL is accessible
2. Check API token is valid
3. Ensure panel has API enabled

### Database errors

1. Check file permissions on database files
2. Ensure disk space is available
3. Try deleting `bot_fsm.db` (will reset active conversations)

### Can't add panel

1. Make sure you're an admin (in `ADMIN_IDS`)
2. Verify panel URL includes `https://`
3. Test API token manually with curl

## 📈 Scaling Tips

### For High Traffic

1. Use webhook mode instead of polling
2. Deploy behind nginx with SSL
3. Use systemd service for auto-restart
4. Consider Redis for FSM storage instead of SQLite

### Systemd Service Example

Create `/etc/systemd/system/vpn-bot.service`:

```ini
[Unit]
Description=3X-UI VPN Sales Bot
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/bot
Environment="PATH=/path/to/venv/bin"
ExecStart=/path/to/venv/bin/python bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable vpn-bot
sudo systemctl start vpn-bot
sudo systemctl status vpn-bot
```

## 🎉 You're Ready!

Your bot is now running! Key features:

- **Users can:** Buy plans, view stats, get support, refer friends
- **Admins can:** Manage everything from Telegram
- **Automation:** Expiry alerts, health monitoring, notifications

For questions or support, open a ticket via the bot's support menu!

---

**Version:** 1.0.0  
**License:** Proprietary  
**Support:** Use the bot's built-in ticket system
