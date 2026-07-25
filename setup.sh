#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup.sh - Initial project setup for the Telegram bot
# ---------------------------------------------------------------------------
set -euo pipefail

# ---------------------------- helper functions ------------------------------
info()  { echo -e "\e[32m[INFO]\e[0m  $*"; }
warn()  { echo -e "\e[33m[WARN]\e[0m  $*"; }
err()   { echo -e "\e[31m[ERR]\e[0m   $*" >&2; }
ask()   { read -r -p "$(echo -e "\e[34m[INPUT]\e[0m $* ")" "$@"; }
bail_out() { err "$@"; exit 1; }

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------
command -v python3 >/dev/null 2>&1 || bail_out "python3 is required but not found."

# ---------------------------------------------------------------------------
# 2. Collect secrets
# ---------------------------------------------------------------------------
echo ""
info "We need a few details to configure the bot."

# Bot token – must be non-empty
while true; do
    ask "Enter your Bot Token (from @BotFather):"
    if [[ -n "$REPLY" ]]; then
        BOT_TOKEN="$REPLY"
        break
    fi
    warn "Bot token cannot be empty."
done

# Admin IDs – comma-separated, optional
ask "Enter Admin ID(s), comma-separated (leave empty if none):"
ADMIN_IDS="$REPLY"

# ---------------------------------------------------------------------------
# 3. Virtual environment
# ---------------------------------------------------------------------------
VENV_DIR="venv"
if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating Python virtual environment in '$VENV_DIR' ..."
    python3 -m venv "$VENV_DIR"
else
    warn "Virtual environment already exists, using it."
fi

info "Installing dependencies inside the virtual environment ..."
# Use the venv's pip directly – no need to activate the whole script
"$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel -q
"$VENV_DIR/bin/pip" install aiogram aiosqlite httpx python-dotenv "qrcode[pil]" -q

# ---------------------------------------------------------------------------
# 4. Create .env file (with sensible defaults for the remaining variables)
# ---------------------------------------------------------------------------
ENV_FILE=".env"
if [[ -f "$ENV_FILE" ]]; then
    ask ".env already exists. Overwrite? (y/N)"
    if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
        info "Leaving existing .env untouched."
        exit 0
    fi
fi

info "Writing configuration to $ENV_FILE ..."
cat > "$ENV_FILE" <<EOF
# Telegram Bot configuration
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}

# Database path (default: bot.db)
DATABASE_PATH=bot.db

# Default language (fa = Persian)
DEFAULT_LANGUAGE=fa

# Default currency (toman or usd)
DEFAULT_CURRENCY=toman

# Days before expiry to send reminders (comma-separated)
EXPIRY_REMINDER_DAYS=3,1

# Traffic alert thresholds (percentage)
TRAFFIC_ALERT_THRESHOLD_1=80
TRAFFIC_ALERT_THRESHOLD_2=95
EOF

# ---------------------------------------------------------------------------
# 5. Done
# ---------------------------------------------------------------------------
echo ""
info "Setup completed successfully!"
info "→ Virtual environment: '$VENV_DIR'"
info "→ Configuration file: '$ENV_FILE'"
echo ""
info "To run the bot:"
echo "    source $VENV_DIR/bin/activate"
echo "    python your_bot_file.py"
