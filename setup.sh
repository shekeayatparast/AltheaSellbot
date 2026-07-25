#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup.sh – Advanced Telegram Bot Setup (Ubuntu)
# Supports systemd service creation, full .env customization, CLI arguments.
# ---------------------------------------------------------------------------
set -euo pipefail

# --- colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# --- defaults ---
VENV_DIR="venv"
DEFAULT_SERVICE_NAME="telegram-bot"
DEFAULT_BOT_SCRIPT="bot.py"
DEFAULT_USER="${USER:-$(whoami)}"
DEFAULT_RESTART="always"
DEFAULT_WORKDIR="$(pwd)"
LOG_DIR="logs"
DEFAULT_ENV_VARS=(
  "DATABASE_PATH=bot.db"
  "DEFAULT_LANGUAGE=fa"
  "DEFAULT_CURRENCY=toman"
  "EXPIRY_REMINDER_DAYS=3,1"
  "TRAFFIC_ALERT_THRESHOLD_1=80"
  "TRAFFIC_ALERT_THRESHOLD_2=95"
)

# --- usage ---
usage() {
  cat <<EOF
Usage: $0 [OPTIONS]

Professional setup script for Telegram Bot on Ubuntu.

Options:
  --token TOKEN                Telegram Bot Token
  --admins ID1,ID2,...         Admin user IDs (comma-separated)
  --db-path PATH               Database path (default: bot.db)
  --lang CODE                  Default language (default: fa)
  --currency CURRENCY          Default currency (default: toman)
  --expiry-reminder DAYS       Reminder days e.g. "3,1" (default: 3,1)
  --traffic-threshold1 PCT     First traffic alert % (default: 80)
  --traffic-threshold2 PCT     Second traffic alert % (default: 95)
  --service-name NAME          systemd service name (default: telegram-bot)
  --bot-script PATH            Path to bot Python script (default: bot.py)
  --run-user USER              User to run service (default: current user)
  --restart POLICY             Restart policy: always|on-failure|no (default: always)
  --user-service               Create a user systemd service (no root needed)
  --no-service                 Do not create a systemd service
  -h, --help                   Show this help

If no token/admin provided interactively, prompts appear.
EOF
}

# --- argument parsing ---
ARGS=$(getopt -o h -l "token:,admins:,db-path:,lang:,currency:,expiry-reminder:,traffic-threshold1:,traffic-threshold2:,service-name:,bot-script:,run-user:,restart:,user-service,no-service,help" -n "$0" -- "$@")
eval set -- "$ARGS"

BOT_TOKEN=""
ADMIN_IDS=""
DB_PATH=""
LANG=""
CURRENCY=""
EXPIRY_REMINDER=""
TRAFFIC_THRESH1=""
TRAFFIC_THRESH2=""
SERVICE_NAME="$DEFAULT_SERVICE_NAME"
BOT_SCRIPT="$DEFAULT_BOT_SCRIPT"
RUN_USER="$DEFAULT_USER"
RESTART="$DEFAULT_RESTART"
CREATE_SERVICE=true
USER_SERVICE=false

while true; do
  case "$1" in
    --token) BOT_TOKEN="$2"; shift 2 ;;
    --admins) ADMIN_IDS="$2"; shift 2 ;;
    --db-path) DB_PATH="$2"; shift 2 ;;
    --lang) LANG="$2"; shift 2 ;;
    --currency) CURRENCY="$2"; shift 2 ;;
    --expiry-reminder) EXPIRY_REMINDER="$2"; shift 2 ;;
    --traffic-threshold1) TRAFFIC_THRESH1="$2"; shift 2 ;;
    --traffic-threshold2) TRAFFIC_THRESH2="$2"; shift 2 ;;
    --service-name) SERVICE_NAME="$2"; shift 2 ;;
    --bot-script) BOT_SCRIPT="$2"; shift 2 ;;
    --run-user) RUN_USER="$2"; shift 2 ;;
    --restart) RESTART="$2"; shift 2 ;;
    --user-service) USER_SERVICE=true; shift ;;
    --no-service) CREATE_SERVICE=false; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    *) echo -e "${RED}Invalid option${NC}"; usage; exit 1 ;;
  esac
done

# --- helper functions ---
ask_if_interactive() {
  # $1: prompt message, $2: variable name (reference)
  if [ -t 0 ]; then
    read -rp "$1" "$2"
  fi
}

# --- pre-flight checks ---
echo -e "${CYAN}🔍 Checking system requirements...${NC}"

if ! command -v python3 &>/dev/null; then
    echo -e "${RED}❌ python3 is not installed. Please install Python 3.9+${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [ "$(python3 -c 'import sys; print(sys.version_info >= (3,9))')" != "True" ]; then
    echo -e "${RED}❌ Python 3.9+ required. Found: ${PYTHON_VERSION}${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Python ${PYTHON_VERSION} found${NC}"

if ! python3 -m pip --version &>/dev/null; then
    echo -e "${YELLOW}📦 Installing python3-pip...${NC}"
    sudo apt-get update -qq && sudo apt-get install -y -qq python3-pip
fi

if ! python3 -m venv --help &>/dev/null; then
    echo -e "${YELLOW}📦 Installing python3-venv...${NC}"
    sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv
fi

echo -e "${GREEN}✅ pip and venv are available${NC}"

# --- interactive configuration (if terminal) ---
if [ -t 0 ] && [ -z "$BOT_TOKEN" ]; then
    echo ""
    echo -e "${CYAN}📝 Telegram Bot Configuration${NC}"
    read -rsp "Enter Bot Token (from @BotFather): " BOT_TOKEN
    echo ""
fi

if [ -z "$BOT_TOKEN" ]; then
    echo -e "${RED}❌ Bot token is required. Use --token or interactive mode.${NC}"
    exit 1
fi

if [ -t 0 ] && [ -z "$ADMIN_IDS" ]; then
    read -rp "Enter Admin ID(s), comma-separated (e.g., 123456789,987654321): " ADMIN_IDS
fi
ADMIN_IDS=$(echo "${ADMIN_IDS:-}" | tr -d ' ' | sed 's/,,/,/g')
if [ -z "$ADMIN_IDS" ]; then
    echo -e "${YELLOW}⚠️  No admin IDs provided. Add them later in .env${NC}"
fi

# --- other .env customization ---
if [ -t 0 ]; then
    echo ""
    echo -e "${CYAN}⚙️  Customize other settings? Press Enter to use defaults.${NC}"
    read -rp "Database path [bot.db]: " input; DB_PATH="${input:-$DB_PATH}"
    if [ -z "$DB_PATH" ]; then DB_PATH="bot.db"; fi

    read -rp "Default language [fa]: " input; LANG="${input:-$LANG}"
    if [ -z "$LANG" ]; then LANG="fa"; fi

    read -rp "Default currency [toman]: " input; CURRENCY="${input:-$CURRENCY}"
    if [ -z "$CURRENCY" ]; then CURRENCY="toman"; fi

    read -rp "Expiry reminder days (comma) [3,1]: " input; EXPIRY_REMINDER="${input:-$EXPIRY_REMINDER}"
    if [ -z "$EXPIRY_REMINDER" ]; then EXPIRY_REMINDER="3,1"; fi

    read -rp "Traffic alert threshold 1 (%) [80]: " input; TRAFFIC_THRESH1="${input:-$TRAFFIC_THRESH1}"
    if [ -z "$TRAFFIC_THRESH1" ]; then TRAFFIC_THRESH1="80"; fi

    read -rp "Traffic alert threshold 2 (%) [95]: " input; TRAFFIC_THRESH2="${input:-$TRAFFIC_THRESH2}"
    if [ -z "$TRAFFIC_THRESH2" ]; then TRAFFIC_THRESH2="95"; fi
else
    # Use defaults if not set via args
    : ${DB_PATH:="bot.db"}
    : ${LANG:="fa"}
    : ${CURRENCY:="toman"}
    : ${EXPIRY_REMINDER:="3,1"}
    : ${TRAFFIC_THRESH1:="80"}
    : ${TRAFFIC_THRESH2:="95"}
fi

# --- write .env ---
ENV_FILE=".env"
if [ -f "$ENV_FILE" ]; then
    echo -e "${YELLOW}⚠️  $ENV_FILE exists, backing up...${NC}"
    cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%s)"
fi

cat > "$ENV_FILE" <<EOF
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}
DATABASE_PATH=${DB_PATH}
DEFAULT_LANGUAGE=${LANG}
DEFAULT_CURRENCY=${CURRENCY}
EXPIRY_REMINDER_DAYS=${EXPIRY_REMINDER}
TRAFFIC_ALERT_THRESHOLD_1=${TRAFFIC_THRESH1}
TRAFFIC_ALERT_THRESHOLD_2=${TRAFFIC_THRESH2}
EOF

echo -e "${GREEN}✅ .env file written${NC}"

# --- virtual environment ---
echo ""
echo -e "${CYAN}🐍 Creating Python virtual environment...${NC}"
python3 -m venv "$VENV_DIR"
PIP="${VENV_DIR}/bin/pip"
PYTHON_VENV="${VENV_DIR}/bin/python"

"$PIP" install --upgrade pip setuptools wheel -q
echo -e "${CYAN}📦 Installing dependencies...${NC}"
"$PIP" install -q aiogram aiosqlite httpx python-dotenv
if "$PIP" install -q "qrcode[pil]" 2>/dev/null; then
    echo -e "${GREEN}✅ QR-code support installed${NC}"
else
    echo -e "${YELLOW}⚠️  qrcode installation failed, QR feature disabled${NC}"
fi

echo -e "${GREEN}✅ Dependencies installed${NC}"

# --- systemd service setup ---
if [ "$CREATE_SERVICE" = true ]; then
    # Check systemd
    if pidof systemd &>/dev/null; then
        echo ""
        echo -e "${CYAN}🔧 Systemd service configuration${NC}"
    else
        echo -e "${YELLOW}⚠️  systemd not detected, skipping service creation${NC}"
        CREATE_SERVICE=false
    fi
fi

if [ "$CREATE_SERVICE" = true ]; then
    # Bot script absolute path
    if [ ! -f "$BOT_SCRIPT" ]; then
        echo -e "${YELLOW}⚠️  Bot script '${BOT_SCRIPT}' not found, but will continue${NC}"
    fi
    BOT_SCRIPT_ABS="$(realpath "$BOT_SCRIPT" 2>/dev/null || echo "$DEFAULT_WORKDIR/$BOT_SCRIPT")"

    if [ -t 0 ]; then
        read -rp "Service name [${SERVICE_NAME}]: " input
        SERVICE_NAME="${input:-$SERVICE_NAME}"
        read -rp "Bot script path (absolute or relative) [${BOT_SCRIPT_ABS}]: " input
        if [ -n "$input" ]; then
            BOT_SCRIPT_ABS="$input"
        fi
        read -rp "Run as user [${RUN_USER}]: " input
        RUN_USER="${input:-$RUN_USER}"
        read -rp "Restart policy (always/on-failure/no) [${RESTART}]: " input
        RESTART="${input:-$RESTART}"
        if [ "$USER_SERVICE" != true ]; then
            read -rp "Use user service (no sudo needed)? [y/N]: " choice
            if [[ "$choice" =~ ^[Yy]$ ]]; then
                USER_SERVICE=true
            fi
        fi
    fi

    # Ensure BOT_SCRIPT_ABS is absolute
    if [[ "$BOT_SCRIPT_ABS" != /* ]]; then
        BOT_SCRIPT_ABS="$(pwd)/$BOT_SCRIPT_ABS"
    fi

    WORKDIR="$(dirname "$BOT_SCRIPT_ABS")"

    # Create log directory
    mkdir -p "$WORKDIR/$LOG_DIR"

    # Build service file content
    if [ "$USER_SERVICE" = true ]; then
        SERVICE_DIR="${HOME}/.config/systemd/user"
        mkdir -p "$SERVICE_DIR"
        SERVICE_FILE="${SERVICE_DIR}/${SERVICE_NAME}.service"
        SYSTEMCTL="systemctl --user"
        SUDO=""
    else
        SERVICE_DIR="/etc/systemd/system"
        SERVICE_FILE="${SERVICE_DIR}/${SERVICE_NAME}.service"
        SYSTEMCTL="sudo systemctl"
        SUDO="sudo"
    fi

    # Unit file
    cat <<EOF > "/tmp/${SERVICE_NAME}.service"
[Unit]
Description=Telegram Bot - ${SERVICE_NAME}
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${WORKDIR}
EnvironmentFile=${WORKDIR}/.env
ExecStart=${PYTHON_VENV} ${BOT_SCRIPT_ABS}
Restart=${RESTART}
RestartSec=10
StandardOutput=append:${WORKDIR}/${LOG_DIR}/bot.log
StandardError=append:${WORKDIR}/${LOG_DIR}/bot_error.log

[Install]
WantedBy=multi-user.target
EOF

    if [ "$USER_SERVICE" = true ]; then
        # For user service, no sudo needed
        mv "/tmp/${SERVICE_NAME}.service" "$SERVICE_FILE"
        $SYSTEMCTL daemon-reload
        $SYSTEMCTL enable "${SERVICE_NAME}.service"
        $SYSTEMCTL start "${SERVICE_NAME}.service"
        echo -e "${GREEN}✅ User service '${SERVICE_NAME}' created and started${NC}"
    else
        # System service
        $SUDO mv "/tmp/${SERVICE_NAME}.service" "$SERVICE_FILE"
        $SYSTEMCTL daemon-reload
        $SYSTEMCTL enable "${SERVICE_NAME}.service"
        $SYSTEMCTL start "${SERVICE_NAME}.service"
        echo -e "${GREEN}✅ System service '${SERVICE_NAME}' installed and started${NC}"
    fi

    echo ""
    echo -e "${CYAN}Useful commands:${NC}"
    echo -e "  Status:   ${SYSTEMCTL} status ${SERVICE_NAME}"
    echo -e "  Logs:     ${SYSTEMCTL} status ${SERVICE_NAME}"
    echo -e "  Restart:  ${SYSTEMCTL} restart ${SERVICE_NAME}"
    echo -e "  Stop:     ${SYSTEMCTL} stop ${SERVICE_NAME}"
    echo ""
fi

# --- final message ---
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║      🎉 Setup completed successfully!   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
if [ "$CREATE_SERVICE" != true ]; then
    echo -e "To run manually:"
    echo -e "  source ${VENV_DIR}/bin/activate"
    echo -e "  python ${BOT_SCRIPT:-your_bot.py}"
fi
