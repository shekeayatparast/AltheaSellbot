#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# manage.sh – All-in-one Telegram Bot manager (install, update, service control)
# ---------------------------------------------------------------------------
set -euo pipefail

# --- Colors ---
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

# --- Paths & defaults ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SCRIPT_DIR}"                # assume script is at repo root
CONFIG_FILE="${PROJECT_DIR}/.setup-config"
VENV_DIR="${PROJECT_DIR}/venv"
ENV_FILE="${PROJECT_DIR}/.env"
LOG_DIR="${PROJECT_DIR}/logs"

# Default values (overridden by config or prompts)
SERVICE_NAME="${SERVICE_NAME:-telegram-bot}"
USER_SERVICE="${USER_SERVICE:-false}"
BOT_SCRIPT="${BOT_SCRIPT:-bot.py}"
RUN_USER="${RUN_USER:-${USER:-$(whoami)}}"
RESTART="${RESTART:-always}"

# Load saved configuration if exists
if [[ -f "$CONFIG_FILE" ]]; then
    source "$CONFIG_FILE"
fi

# Determine systemctl command prefix
if [[ "$USER_SERVICE" == "true" ]]; then
    SYSTEMCTL="systemctl --user"
    SERVICE_DIR="${HOME}/.config/systemd/user"
    INSTALL_TARGET="default.target"
else
    SYSTEMCTL="sudo systemctl"
    SERVICE_DIR="/etc/systemd/system"
    INSTALL_TARGET="multi-user.target"
fi
SERVICE_FILE="${SERVICE_DIR}/${SERVICE_NAME}.service"

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
press_enter() {
    echo ""
    read -rp "Press Enter to continue..."
}

check_systemd() {
    if ! pidof systemd &>/dev/null; then
        echo -e "${RED}systemd not running. Service management unavailable.${NC}"
        return 1
    fi
    return 0
}

save_config() {
    cat > "$CONFIG_FILE" <<EOF
SERVICE_NAME="${SERVICE_NAME}"
USER_SERVICE="${USER_SERVICE}"
BOT_SCRIPT="${BOT_SCRIPT}"
RUN_USER="${RUN_USER}"
RESTART="${RESTART}"
EOF
    echo -e "${GREEN}Configuration saved to ${CONFIG_FILE}${NC}"
}

# ---------------------------------------------------------------------------
# Action functions
# ---------------------------------------------------------------------------

do_install() {
    echo -e "${CYAN}${BOLD}🚀 Full installation & service setup${NC}\n"

    # --- 1. Pre-flight checks ---
    if ! command -v python3 &>/dev/null; then
        echo -e "${RED}❌ python3 not found. Install Python 3.9+ first.${NC}"
        return 1
    fi
    if [ "$(python3 -c 'import sys; print(sys.version_info >= (3,9))')" != "True" ]; then
        echo -e "${RED}❌ Python 3.9+ required.${NC}"
        return 1
    fi
    echo -e "${GREEN}✅ Python OK${NC}"

    if ! python3 -m pip --version &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq python3-pip
    fi
    if ! python3 -m venv --help &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv
    fi

    # --- 2. Gather configuration ---
    if [ -t 0 ]; then
        echo -e "\n${CYAN}📝 Bot Configuration${NC}"
        read -rsp "Bot Token (from @BotFather): " BOT_TOKEN
        echo ""
        read -rp "Admin IDs (comma-separated): " ADMIN_IDS
        ADMIN_IDS=$(echo "${ADMIN_IDS:-}" | tr -d ' ' | sed 's/,,/,/g')

        echo -e "\n${CYAN}⚙️  Other settings (press Enter for default)${NC}"
        read -rp "Database path [bot.db]: " DB_PATH; DB_PATH="${DB_PATH:-bot.db}"
        read -rp "Default language [fa]: " LANG; LANG="${LANG:-fa}"
        read -rp "Default currency [toman]: " CURRENCY; CURRENCY="${CURRENCY:-toman}"
        read -rp "Expiry reminder days [3,1]: " EXPIRY; EXPIRY="${EXPIRY:-3,1}"
        read -rp "Traffic threshold 1 (%) [80]: " T1; T1="${T1:-80}"
        read -rp "Traffic threshold 2 (%) [95]: " T2; T2="${T2:-95}"
    else
        # Non-interactive: token/admin must be provided via environment or arguments
        echo -e "${RED}Non-interactive install not yet fully supported.${NC}"
        return 1
    fi

    if [ -z "${BOT_TOKEN:-}" ]; then
        echo -e "${RED}❌ Bot token required.${NC}"
        return 1
    fi

    # --- 3. Write .env ---
    if [ -f "$ENV_FILE" ]; then
        cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%s)"
        echo -e "${YELLOW}⚠️  Existing .env backed up${NC}"
    fi
    cat > "$ENV_FILE" <<EOF
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}
DATABASE_PATH=${DB_PATH:-bot.db}
DEFAULT_LANGUAGE=${LANG:-fa}
DEFAULT_CURRENCY=${CURRENCY:-toman}
EXPIRY_REMINDER_DAYS=${EXPIRY:-3,1}
TRAFFIC_ALERT_THRESHOLD_1=${T1:-80}
TRAFFIC_ALERT_THRESHOLD_2=${T2:-95}
EOF
    echo -e "${GREEN}✅ .env created${NC}"

    # --- 4. Virtual environment ---
    echo -e "\n${CYAN}🐍 Creating virtual environment...${NC}"
    python3 -m venv "$VENV_DIR"
    PIP="${VENV_DIR}/bin/pip"
    "$PIP" install --upgrade pip setuptools wheel -q
    echo -e "${CYAN}📦 Installing core dependencies...${NC}"
    "$PIP" install -q aiogram aiosqlite httpx python-dotenv
    if "$PIP" install -q "qrcode[pil]" 2>/dev/null; then
        echo -e "${GREEN}✅ QR-code support installed${NC}"
    else
        echo -e "${YELLOW}⚠️  qrcode not available${NC}"
    fi
    echo -e "${GREEN}✅ Dependencies installed${NC}"

    # --- 5. Service setup ---
    if ! check_systemd; then
        CREATE_SERVICE=false
    else
        CREATE_SERVICE=true
        echo -e "\n${CYAN}🔧 systemd service configuration${NC}"
        read -rp "Service name [${SERVICE_NAME}]: " input; SERVICE_NAME="${input:-$SERVICE_NAME}"
        BOT_SCRIPT_ABS="${PROJECT_DIR}/${BOT_SCRIPT}"
        read -rp "Bot script path [${BOT_SCRIPT_ABS}]: " input
        [ -n "$input" ] && BOT_SCRIPT_ABS="$input"
        # ensure absolute
        [[ "$BOT_SCRIPT_ABS" != /* ]] && BOT_SCRIPT_ABS="${PROJECT_DIR}/${BOT_SCRIPT_ABS}"
        read -rp "Run as user [${RUN_USER}]: " input; RUN_USER="${input:-$RUN_USER}"
        read -rp "Restart policy [${RESTART}]: " input; RESTART="${input:-$RESTART}"
        read -rp "Use user service (no sudo)? [y/N]: " choice
        if [[ "$choice" =~ ^[Yy]$ ]]; then
            USER_SERVICE=true
            SYSTEMCTL="systemctl --user"
            SERVICE_DIR="${HOME}/.config/systemd/user"
            INSTALL_TARGET="default.target"
        else
            USER_SERVICE=false
            SYSTEMCTL="sudo systemctl"
            SERVICE_DIR="/etc/systemd/system"
            INSTALL_TARGET="multi-user.target"
        fi
        SERVICE_FILE="${SERVICE_DIR}/${SERVICE_NAME}.service"
        PYTHON_VENV_ABS="${VENV_DIR}/bin/python"
        WORKDIR="$(dirname "$BOT_SCRIPT_ABS")"

        mkdir -p "$LOG_DIR"

        cat > "/tmp/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Telegram Bot - ${SERVICE_NAME}
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${WORKDIR}
EnvironmentFile=${WORKDIR}/.env
ExecStart=${PYTHON_VENV_ABS} ${BOT_SCRIPT_ABS}
Restart=${RESTART}
RestartSec=10
StandardOutput=append:${LOG_DIR}/bot.log
StandardError=append:${LOG_DIR}/bot_error.log

[Install]
WantedBy=${INSTALL_TARGET}
EOF

        if [ "$USER_SERVICE" = true ]; then
            mkdir -p "$SERVICE_DIR"
            mv "/tmp/${SERVICE_NAME}.service" "$SERVICE_FILE"
            $SYSTEMCTL daemon-reload
            $SYSTEMCTL enable "${SERVICE_NAME}.service"
            $SYSTEMCTL start "${SERVICE_NAME}.service"
        else
            sudo mv "/tmp/${SERVICE_NAME}.service" "$SERVICE_FILE"
            $SYSTEMCTL daemon-reload
            $SYSTEMCTL enable "${SERVICE_NAME}.service"
            $SYSTEMCTL start "${SERVICE_NAME}.service"
        fi
        echo -e "${GREEN}✅ Service '${SERVICE_NAME}' installed and started${NC}"
    fi

    # Save configuration for future management
    save_config

    echo -e "\n${GREEN}╔══════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║      🎉 Setup completed successfully!   ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
    if [ "$CREATE_SERVICE" != true ]; then
        echo -e "Run manually: source ${VENV_DIR}/bin/activate && python ${BOT_SCRIPT}"
    fi
    press_enter
}

do_update() {
    echo -e "${CYAN}⬇️  Pulling latest code from git...${NC}"
    cd "$PROJECT_DIR"
    git pull
    echo -e "${GREEN}✅ Code updated.${NC}"
    echo -e "${YELLOW}⚠️  If dependencies changed, use 'Update Dependencies'.${NC}"
    echo -e "${YELLOW}⚠️  Use 'Restart Service' to apply changes.${NC}"
    press_enter
}

do_update_deps() {
    if [ ! -d "$VENV_DIR" ]; then
        echo -e "${RED}❌ Virtual environment not found. Run 'Install/Setup' first.${NC}"
        press_enter
        return 1
    fi
    echo -e "${CYAN}📦 Updating Python packages...${NC}"
    source "${VENV_DIR}/bin/activate"
    if [ -f "${PROJECT_DIR}/requirements.txt" ]; then
        pip install --upgrade -r "${PROJECT_DIR}/requirements.txt"
    else
        pip install --upgrade aiogram aiosqlite httpx python-dotenv
    fi
    deactivate
    echo -e "${GREEN}✅ Dependencies updated.${NC}"
    press_enter
}

do_restart() {
    if [ ! -f "$SERVICE_FILE" ]; then
        echo -e "${RED}❌ Service file not found. Run 'Install/Setup' first.${NC}"
        press_enter
        return 1
    fi
    check_systemd || return 1
    echo -e "${CYAN}🔄 Restarting service...${NC}"
    $SYSTEMCTL restart "${SERVICE_NAME}"
    echo -e "${GREEN}✅ Service restarted.${NC}"
    press_enter
}

do_stop() {
    if [ ! -f "$SERVICE_FILE" ]; then
        echo -e "${RED}❌ Service file not found.${NC}"
        press_enter
        return 1
    fi
    check_systemd || return 1
    echo -e "${CYAN}⏹️  Stopping service...${NC}"
    $SYSTEMCTL stop "${SERVICE_NAME}"
    echo -e "${GREEN}✅ Service stopped.${NC}"
    press_enter
}

do_status() {
    if [ ! -f "$SERVICE_FILE" ]; then
        echo -e "${RED}❌ Service file not found.${NC}"
        press_enter
        return 1
    fi
    check_systemd || return 1
    $SYSTEMCTL status "${SERVICE_NAME}" || true
    press_enter
}

do_logs() {
    if [ ! -f "$SERVICE_FILE" ]; then
        echo -e "${RED}❌ Service file not found.${NC}"
        press_enter
        return 1
    fi
    check_systemd || return 1
    echo -e "${CYAN}📋 Following logs (Ctrl+C to exit)...${NC}"
    if [ "$USER_SERVICE" = true ]; then
        journalctl --user -u "${SERVICE_NAME}" -f
    else
        journalctl -u "${SERVICE_NAME}" -f
    fi
}

do_reconfigure() {
    if [ ! -f "$ENV_FILE" ]; then
        echo -e "${RED}❌ .env not found. Run 'Install/Setup' first.${NC}"
        press_enter
        return 1
    fi
    echo -e "${CYAN}📝 Editing .env file with nano...${NC}"
    nano "$ENV_FILE"
    echo -e "${YELLOW}⚠️  Restart the service to apply changes.${NC}"
    press_enter
}

do_remove_service() {
    if [ ! -f "$SERVICE_FILE" ]; then
        echo -e "${YELLOW}⚠️  Service file not found, nothing to remove.${NC}"
        press_enter
        return 0
    fi
    check_systemd || return 1
    echo -e "${CYAN}🗑️  Removing service '${SERVICE_NAME}'...${NC}"
    $SYSTEMCTL stop "${SERVICE_NAME}" 2>/dev/null || true
    $SYSTEMCTL disable "${SERVICE_NAME}" 2>/dev/null || true
    if [ "$USER_SERVICE" = true ]; then
        rm -f "$SERVICE_FILE"
        $SYSTEMCTL daemon-reload
    else
        sudo rm -f "$SERVICE_FILE"
        $SYSTEMCTL daemon-reload
    fi
    echo -e "${GREEN}✅ Service removed.${NC}"
    rm -f "$CONFIG_FILE"
    press_enter
}

do_exit() {
    echo -e "${GREEN}👋 Goodbye!${NC}"
    exit 0
}

# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------
show_menu() {
    clear
    echo -e "${BOLD}${CYAN}========================================${NC}"
    echo -e "${BOLD}${CYAN}       Telegram Bot Manager           ${NC}"
    echo -e "${BOLD}${CYAN}========================================${NC}"
    echo -e " Project: ${PROJECT_DIR}"
    if [ -f "$CONFIG_FILE" ]; then
        echo -e " Service: ${GREEN}${SERVICE_NAME}${NC} (${USER_SERVICE:+user}${USER_SERVICE:-system})"
    fi
    echo ""
    echo -e " 1) ${GREEN}Install / Setup${NC} (first time)"
    echo -e " 2) Update Code (git pull)"
    echo -e " 3) Update Dependencies"
    echo -e " 4) Restart Service"
    echo -e " 5) Stop Service"
    echo -e " 6) Service Status"
    echo -e " 7) View Logs (live)"
    echo -e " 8) Reconfigure .env (edit)"
    echo -e " 9) Remove Service"
    echo -e " 0) Exit"
    echo ""
    read -rp "Choose an option [0-9]: " choice

    case "$choice" in
        1) do_install ;;
        2) do_update ;;
        3) do_update_deps ;;
        4) do_restart ;;
        5) do_stop ;;
        6) do_status ;;
        7) do_logs ;;
        8) do_reconfigure ;;
        9) do_remove_service ;;
        0) do_exit ;;
        *) echo -e "${RED}Invalid option${NC}"; press_enter ;;
    esac
}

# ---------------------------------------------------------------------------
# CLI argument handling
# ---------------------------------------------------------------------------
if [[ $# -gt 0 ]]; then
    case "$1" in
        --install) do_install ;;
        --update) do_update ;;
        --update-deps) do_update_deps ;;
        --restart) do_restart ;;
        --stop) do_stop ;;
        --status) do_status ;;
        --logs) do_logs ;;
        --reconfigure) do_reconfigure ;;
        --remove) do_remove_service ;;
        -h|--help)
            echo "Usage: $0 [OPTION]"
            echo "Options: --install, --update, --update-deps, --restart, --stop, --status, --logs, --reconfigure, --remove"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    exit 0
fi

# No arguments -> interactive loop
while true; do
    show_menu
done
