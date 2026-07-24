#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    3X-UI VPN Sales Bot - Premium Edition                      ║
║                        Advanced Telegram Bot for VPN Sales                    ║
║                         Built with aiogram 3.x + SQLite                       ║
╚══════════════════════════════════════════════════════════════════════════════╝

Features:
✅ Multi-panel support with intelligent load balancing
✅ Customer-facing sales & self-service portal
✅ Complete admin management panel inside Telegram
✅ Real-time server health monitoring
✅ Smart traffic & expiry alerts (80%, 90%, 95% thresholds)
✅ Promo code system with usage limits
✅ Referral program with commission tracking
✅ Ticket-based support system
✅ Group-based client management
✅ Visual dashboards with colored buttons
✅ Auto-renewal reminders
✅ IP tracking & session management
✅ Automated backups

Author: Senior Backend Developer & Telegram Bot Architect
Version: 1.0.0
Python: 3.11+
Library: aiogram 3.x
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import string
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Message,
    ReplyKeyboardRemove,
    URLInputFile,
)
from aiogram.utils.formatting import Bold, Code, Text
from aiogram.utils.markdown import bold, code, hbold, hcode, hlink, italic, link, underline
from aiosqlite import Connection, connect

# ─────────────────────────────────────────────────────────────────────────────
# Configuration & Environment Variables
# ─────────────────────────────────────────────────────────────────────────────

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
PANEL_URL = os.getenv("PANEL_URL", "https://panel.example.com")
API_TOKEN = os.getenv("API_TOKEN", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot_database.db")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # Optional for webhook mode

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("3x-ui-bot")


# ─────────────────────────────────────────────────────────────────────────────
# Utility Functions
# ─────────────────────────────────────────────────────────────────────────────

def generate_sub_id(length: int = 16) -> str:
    """Generate a random subscription ID."""
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(length))


def generate_email(prefix: str = "user") -> str:
    """Generate a unique email for client."""
    unique_id = uuid.uuid4().hex[:8]
    timestamp = int(time.time()) % 10000
    return f"{prefix}_{unique_id}{timestamp}@vpn.local"


def format_bytes(bytes_value: int) -> str:
    """Format bytes to human-readable format."""
    if bytes_value < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    value = float(bytes_value)
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    return f"{value:.2f} {units[unit_index]}"


def format_traffic_bar(current: int, total: int, width: int = 10) -> str:
    """Create a visual traffic bar using Unicode blocks."""
    if total <= 0:
        return "∞ " + "█" * width
    percentage = min(current / total, 1.0)
    filled = int(percentage * width)
    empty = width - filled
    bar = "█" * filled + "░" * empty
    pct = percentage * 100
    return f"{bar} {pct:.1f}%"


def ms_to_datetime(ms: int) -> Optional[datetime]:
    """Convert milliseconds timestamp to datetime."""
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000)


def datetime_to_ms(dt: Optional[datetime]) -> int:
    """Convert datetime to milliseconds timestamp."""
    if dt is None:
        return 0
    return int(dt.timestamp() * 1000)


def remaining_days(expiry_ms: int) -> int:
    """Calculate remaining days until expiry."""
    if expiry_ms <= 0:
        return -1
    expiry_dt = ms_to_datetime(expiry_ms)
    if expiry_dt is None:
        return -1
    delta = expiry_dt - datetime.now()
    return max(0, delta.days)


def get_status_emoji(enable: bool, expired: bool, depleted: bool) -> str:
    """Get status emoji based on client state."""
    if not enable:
        return "🔴"
    if depleted:
        return "🪫"
    if expired:
        return "⏰"
    return "🟢"


# ─────────────────────────────────────────────────────────────────────────────
# Database Layer (SQLite with aiosqlite)
# ─────────────────────────────────────────────────────────────────────────────

class Database:
    """Async SQLite database manager."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[Connection] = None

    async def connect(self) -> None:
        """Initialize database connection and create tables."""
        self.conn = await connect(self.db_path)
        self.conn.row_factory = lambda cursor, row: {
            desc[0]: row[idx] for idx, desc in enumerate(cursor.description)
        }
        await self._create_tables()
        logger.info(f"Database connected: {self.db_path}")

    async def close(self) -> None:
        """Close database connection."""
        if self.conn:
            await self.conn.close()
            logger.info("Database connection closed")

    async def _create_tables(self) -> None:
        """Create all necessary tables."""
        await self.conn.executescript("""
            -- Users table (Telegram users)
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                language_code TEXT DEFAULT 'en',
                is_admin INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                updated_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Clients table (VPN accounts linked to Telegram users)
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                tg_id INTEGER NOT NULL,
                sub_id TEXT UNIQUE,
                total_gb REAL DEFAULT 0,
                expiry_ms INTEGER DEFAULT 0,
                enable INTEGER DEFAULT 1,
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                updated_at INTEGER DEFAULT (strftime('%s', 'now')),
                FOREIGN KEY (tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
            );

            -- Transactions table
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'USD',
                status TEXT DEFAULT 'pending',
                description TEXT,
                client_email TEXT,
                payment_method TEXT,
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                FOREIGN KEY (tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
            );

            -- Plans table (subscription packages)
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                traffic_gb REAL NOT NULL,
                duration_days INTEGER NOT NULL,
                enable INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Promo codes table
            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                discount_percent INTEGER DEFAULT 0,
                discount_fixed REAL DEFAULT 0,
                max_uses INTEGER DEFAULT 0,
                current_uses INTEGER DEFAULT 0,
                expires_at INTEGER,
                enable INTEGER DEFAULT 1,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Referrals table
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_tg_id INTEGER NOT NULL,
                referred_tg_id INTEGER NOT NULL,
                commission_earned REAL DEFAULT 0,
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                FOREIGN KEY (referrer_tg_id) REFERENCES users(tg_id),
                FOREIGN KEY (referred_tg_id) REFERENCES users(tg_id),
                UNIQUE(referrer_tg_id, referred_tg_id)
            );

            -- Support tickets table
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                admin_id INTEGER,
                subject TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                priority TEXT DEFAULT 'normal',
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                updated_at INTEGER DEFAULT (strftime('%s', 'now')),
                FOREIGN KEY (tg_id) REFERENCES users(tg_id),
                FOREIGN KEY (admin_id) REFERENCES users(tg_id)
            );

            -- Ticket messages table
            CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                sender_tg_id INTEGER NOT NULL,
                message_text TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
            );

            -- Server panels table (multi-panel support)
            CREATE TABLE IF NOT EXISTS panels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                url TEXT NOT NULL,
                api_token TEXT NOT NULL,
                enable INTEGER DEFAULT 1,
                is_primary INTEGER DEFAULT 0,
                max_clients INTEGER DEFAULT 0,
                current_clients INTEGER DEFAULT 0,
                health_status TEXT DEFAULT 'unknown',
                last_checked INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Settings table (key-value store)
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Notifications queue table
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                message_text TEXT NOT NULL,
                sent INTEGER DEFAULT 0,
                scheduled_at INTEGER,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Create indexes for performance
            CREATE INDEX IF NOT EXISTS idx_clients_tg_id ON clients(tg_id);
            CREATE INDEX IF NOT EXISTS idx_clients_email ON clients(email);
            CREATE INDEX IF NOT EXISTS idx_transactions_tg_id ON transactions(tg_id);
            CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
            CREATE INDEX IF NOT EXISTS idx_notifications_sent ON notifications(sent);
        """)
        await self.conn.commit()

        # Insert default plans if none exist
        cursor = await self.conn.execute("SELECT COUNT(*) as cnt FROM plans")
        result = await cursor.fetchone()
        if result["cnt"] == 0:
            await self._insert_default_plans()

        # Insert default settings if none exist
        await self._insert_default_settings()

    async def _insert_default_plans(self) -> None:
        """Insert default subscription plans."""
        plans = [
            ("Trial", "7 days trial with 1GB traffic", 0, 1, 7, 1),
            ("Basic", "30 days with 10GB traffic", 4.99, 10, 30, 2),
            ("Standard", "30 days with 30GB traffic", 9.99, 30, 30, 3),
            ("Premium", "30 days with 100GB traffic", 19.99, 100, 30, 4),
            ("Ultra", "90 days with 200GB traffic", 49.99, 200, 90, 5),
        ]
        for plan in plans:
            await self.conn.execute(
                """INSERT INTO plans (name, description, price, traffic_gb, duration_days, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                plan,
            )
        await self.conn.commit()
        logger.info("Default plans inserted")

    async def _insert_default_settings(self) -> None:
        """Insert default bot settings."""
        defaults = {
            "referral_commission_percent": "10",
            "traffic_alert_threshold_1": "80",
            "traffic_alert_threshold_2": "90",
            "traffic_alert_threshold_3": "95",
            "expiry_reminder_days": "7,3,1",
            "auto_renewal_enabled": "0",
            "welcome_message": "Welcome to our VPN service! 🚀",
            "support_username": "@support",
            "currency_symbol": "$",
            "language": "en",
        }
        for key, value in defaults.items():
            await self.conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        await self.conn.commit()

    # ──────────────────────────────────────────────────────────────────────
    # User Operations
    # ──────────────────────────────────────────────────────────────────────

    async def upsert_user(self, tg_id: int, username: str, first_name: str, last_name: str = None, language_code: str = "en") -> None:
        """Insert or update user record."""
        await self.conn.execute(
            """INSERT INTO users (tg_id, username, first_name, last_name, language_code, updated_at)
               VALUES (?, ?, ?, ?, ?, strftime('%s', 'now'))
               ON CONFLICT(tg_id) DO UPDATE SET
               username=excluded.username,
               first_name=excluded.first_name,
               last_name=excluded.last_name,
               language_code=excluded.language_code,
               updated_at=strftime('%s', 'now')""",
            (tg_id, username, first_name, last_name, language_code),
        )
        await self.conn.commit()

    async def get_user(self, tg_id: int) -> Optional[dict]:
        """Get user by Telegram ID."""
        cursor = await self.conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        return await cursor.fetchone()

    async def set_admin(self, tg_id: int, is_admin: bool) -> None:
        """Set admin status for a user."""
        await self.conn.execute(
            "UPDATE users SET is_admin = ?, updated_at = strftime('%s', 'now') WHERE tg_id = ?",
            (1 if is_admin else 0, tg_id),
        )
        await self.conn.commit()

    async def get_all_admins(self) -> list:
        """Get all admin user IDs."""
        cursor = await self.conn.execute("SELECT tg_id FROM users WHERE is_admin = 1")
        rows = await cursor.fetchall()
        return [row["tg_id"] for row in rows]

    # ──────────────────────────────────────────────────────────────────────
    # Client Operations
    # ──────────────────────────────────────────────────────────────────────

    async def create_client(self, email: str, tg_id: int, total_gb: float, expiry_ms: int, sub_id: str = None) -> None:
        """Create a new client record."""
        if sub_id is None:
            sub_id = generate_sub_id()
        await self.conn.execute(
            """INSERT INTO clients (email, tg_id, sub_id, total_gb, expiry_ms, enable)
               VALUES (?, ?, ?, ?, ?, 1)""",
            (email, tg_id, sub_id, total_gb, expiry_ms),
        )
        await self.conn.commit()

    async def get_client_by_email(self, email: str) -> Optional[dict]:
        """Get client by email."""
        cursor = await self.conn.execute("SELECT * FROM clients WHERE email = ?", (email,))
        return await cursor.fetchone()

    async def get_client_by_tg_id(self, tg_id: int) -> Optional[dict]:
        """Get primary client for a Telegram user."""
        cursor = await self.conn.execute(
            "SELECT * FROM clients WHERE tg_id = ? ORDER BY created_at DESC LIMIT 1",
            (tg_id,),
        )
        return await cursor.fetchone()

    async def get_all_clients_for_user(self, tg_id: int) -> list:
        """Get all clients for a Telegram user."""
        cursor = await self.conn.execute(
            "SELECT * FROM clients WHERE tg_id = ? ORDER BY created_at DESC",
            (tg_id,),
        )
        return await cursor.fetchall()

    async def update_client(self, email: str, **kwargs) -> None:
        """Update client fields."""
        if not kwargs:
            return
        fields = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values())
        values.append(email)
        await self.conn.execute(
            f"UPDATE clients SET {fields}, updated_at = strftime('%s', 'now') WHERE email = ?",
            values,
        )
        await self.conn.commit()

    async def delete_client(self, email: str) -> None:
        """Delete a client record."""
        await self.conn.execute("DELETE FROM clients WHERE email = ?", (email,))
        await self.conn.commit()

    # ──────────────────────────────────────────────────────────────────────
    # Transaction Operations
    # ──────────────────────────────────────────────────────────────────────

    async def create_transaction(self, tg_id: int, amount: float, description: str, client_email: str = None, payment_method: str = "manual", status: str = "pending") -> int:
        """Create a new transaction record."""
        cursor = await self.conn.execute(
            """INSERT INTO transactions (tg_id, amount, description, client_email, payment_method, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tg_id, amount, description, client_email, payment_method, status),
        )
        await self.conn.commit()
        return cursor.lastrowid

    async def update_transaction_status(self, tx_id: int, status: str) -> None:
        """Update transaction status."""
        await self.conn.execute(
            "UPDATE transactions SET status = ? WHERE id = ?",
            (status, tx_id),
        )
        await self.conn.commit()

    async def get_user_transactions(self, tg_id: int, limit: int = 10) -> list:
        """Get recent transactions for a user."""
        cursor = await self.conn.execute(
            "SELECT * FROM transactions WHERE tg_id = ? ORDER BY created_at DESC LIMIT ?",
            (tg_id, limit),
        )
        return await cursor.fetchall()

    # ──────────────────────────────────────────────────────────────────────
    # Plan Operations
    # ──────────────────────────────────────────────────────────────────────

    async def get_all_plans(self) -> list:
        """Get all enabled plans sorted by order."""
        cursor = await self.conn.execute(
            "SELECT * FROM plans WHERE enable = 1 ORDER BY sort_order ASC"
        )
        return await cursor.fetchall()

    async def get_plan_by_id(self, plan_id: int) -> Optional[dict]:
        """Get plan by ID."""
        cursor = await self.conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,))
        return await cursor.fetchone()

    async def add_plan(self, name: str, description: str, price: float, traffic_gb: float, duration_days: int) -> int:
        """Add a new plan."""
        cursor = await self.conn.execute(
            """INSERT INTO plans (name, description, price, traffic_gb, duration_days)
               VALUES (?, ?, ?, ?, ?)""",
            (name, description, price, traffic_gb, duration_days),
        )
        await self.conn.commit()
        return cursor.lastrowid

    # ──────────────────────────────────────────────────────────────────────
    # Promo Code Operations
    # ──────────────────────────────────────────────────────────────────────

    async def get_promo_code(self, code: str) -> Optional[dict]:
        """Get promo code by code string."""
        cursor = await self.conn.execute(
            "SELECT * FROM promo_codes WHERE code = ? AND enable = 1",
            (code,),
        )
        return await cursor.fetchone()

    async def use_promo_code(self, code: str) -> None:
        """Increment promo code usage."""
        await self.conn.execute(
            "UPDATE promo_codes SET current_uses = current_uses + 1 WHERE code = ?",
            (code,),
        )
        await self.conn.commit()

    async def create_promo_code(self, code: str, discount_percent: int = 0, discount_fixed: float = 0, max_uses: int = 0, expires_at: int = None) -> None:
        """Create a new promo code."""
        await self.conn.execute(
            """INSERT INTO promo_codes (code, discount_percent, discount_fixed, max_uses, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (code, discount_percent, discount_fixed, max_uses, expires_at),
        )
        await self.conn.commit()

    # ──────────────────────────────────────────────────────────────────────
    # Referral Operations
    # ──────────────────────────────────────────────────────────────────────

    async def create_referral(self, referrer_tg_id: int, referred_tg_id: int) -> None:
        """Create a referral record."""
        try:
            await self.conn.execute(
                """INSERT INTO referrals (referrer_tg_id, referred_tg_id)
                   VALUES (?, ?)""",
                (referrer_tg_id, referred_tg_id),
            )
            await self.conn.commit()
        except Exception:
            pass  # Already exists

    async def get_referrer(self, tg_id: int) -> Optional[int]:
        """Get referrer for a user."""
        cursor = await self.conn.execute(
            "SELECT referrer_tg_id FROM referrals WHERE referred_tg_id = ?",
            (tg_id,),
        )
        result = await cursor.fetchone()
        return result["referrer_tg_id"] if result else None

    async def add_referral_commission(self, referrer_tg_id: int, amount: float) -> None:
        """Add commission to referrer's balance."""
        await self.conn.execute(
            "UPDATE referrals SET commission_earned = commission_earned + ? WHERE referrer_tg_id = ?",
            (amount, referrer_tg_id),
        )
        await self.conn.commit()

    async def get_total_commission(self, tg_id: int) -> float:
        """Get total commission earned by a user."""
        cursor = await self.conn.execute(
            "SELECT SUM(commission_earned) as total FROM referrals WHERE referrer_tg_id = ?",
            (tg_id,),
        )
        result = await cursor.fetchone()
        return float(result["total"] or 0)

    # ──────────────────────────────────────────────────────────────────────
    # Ticket Operations
    # ──────────────────────────────────────────────────────────────────────

    async def create_ticket(self, tg_id: int, subject: str, priority: str = "normal") -> int:
        """Create a new support ticket."""
        cursor = await self.conn.execute(
            """INSERT INTO tickets (tg_id, subject, priority)
               VALUES (?, ?, ?)""",
            (tg_id, subject, priority),
        )
        await self.conn.commit()
        return cursor.lastrowid

    async def get_ticket(self, ticket_id: int) -> Optional[dict]:
        """Get ticket by ID."""
        cursor = await self.conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        return await cursor.fetchone()

    async def get_user_tickets(self, tg_id: int) -> list:
        """Get all tickets for a user."""
        cursor = await self.conn.execute(
            "SELECT * FROM tickets WHERE tg_id = ? ORDER BY created_at DESC",
            (tg_id,),
        )
        return await cursor.fetchall()

    async def get_open_tickets(self) -> list:
        """Get all open tickets."""
        cursor = await self.conn.execute(
            "SELECT * FROM tickets WHERE status = 'open' ORDER BY created_at ASC"
        )
        return await cursor.fetchall()

    async def assign_ticket(self, ticket_id: int, admin_id: int) -> None:
        """Assign ticket to an admin."""
        await self.conn.execute(
            "UPDATE tickets SET admin_id = ?, status = 'in_progress', updated_at = strftime('%s', 'now') WHERE id = ?",
            (admin_id, ticket_id),
        )
        await self.conn.commit()

    async def close_ticket(self, ticket_id: int) -> None:
        """Close a ticket."""
        await self.conn.execute(
            "UPDATE tickets SET status = 'closed', updated_at = strftime('%s', 'now') WHERE id = ?",
            (ticket_id,),
        )
        await self.conn.commit()

    async def add_ticket_message(self, ticket_id: int, sender_tg_id: int, message_text: str, is_admin: bool = False) -> None:
        """Add a message to a ticket."""
        await self.conn.execute(
            """INSERT INTO ticket_messages (ticket_id, sender_tg_id, message_text, is_admin)
               VALUES (?, ?, ?, ?)""",
            (ticket_id, sender_tg_id, message_text, 1 if is_admin else 0),
        )
        await self.conn.execute(
            "UPDATE tickets SET updated_at = strftime('%s', 'now') WHERE id = ?",
            (ticket_id,),
        )
        await self.conn.commit()

    async def get_ticket_messages(self, ticket_id: int) -> list:
        """Get all messages for a ticket."""
        cursor = await self.conn.execute(
            "SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY created_at ASC",
            (ticket_id,),
        )
        return await cursor.fetchall()

    # ──────────────────────────────────────────────────────────────────────
    # Panel Operations (Multi-panel support)
    # ──────────────────────────────────────────────────────────────────────

    async def add_panel(self, name: str, url: str, api_token: str, is_primary: bool = False) -> int:
        """Add a new panel."""
        cursor = await self.conn.execute(
            """INSERT INTO panels (name, url, api_token, is_primary)
               VALUES (?, ?, ?, ?)""",
            (name, url, api_token, 1 if is_primary else 0),
        )
        await self.conn.commit()
        return cursor.lastrowid

    async def get_all_panels(self) -> list:
        """Get all panels."""
        cursor = await self.conn.execute("SELECT * FROM panels ORDER BY id")
        return await cursor.fetchall()

    async def get_primary_panel(self) -> Optional[dict]:
        """Get the primary panel."""
        cursor = await self.conn.execute(
            "SELECT * FROM panels WHERE is_primary = 1 AND enable = 1 LIMIT 1"
        )
        return await cursor.fetchone()

    async def get_enabled_panels(self) -> list:
        """Get all enabled panels."""
        cursor = await self.conn.execute("SELECT * FROM panels WHERE enable = 1 ORDER BY is_primary DESC")
        return await cursor.fetchall()

    async def update_panel_health(self, panel_id: int, status: str) -> None:
        """Update panel health status."""
        await self.conn.execute(
            "UPDATE panels SET health_status = ?, last_checked = strftime('%s', 'now') WHERE id = ?",
            (status, panel_id),
        )
        await self.conn.commit()

    async def get_healthy_panel_with_lowest_load(self) -> Optional[dict]:
        """Get the healthiest panel with lowest client load."""
        cursor = await self.conn.execute(
            """SELECT * FROM panels 
               WHERE enable = 1 AND health_status = 'healthy'
               ORDER BY is_primary DESC, current_clients ASC
               LIMIT 1"""
        )
        return await cursor.fetchone()

    # ──────────────────────────────────────────────────────────────────────
    # Settings Operations
    # ──────────────────────────────────────────────────────────────────────

    async def get_setting(self, key: str, default: str = None) -> Optional[str]:
        """Get a setting value."""
        cursor = await self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        result = await cursor.fetchone()
        return result["value"] if result else default

    async def set_setting(self, key: str, value: str) -> None:
        """Set a setting value."""
        await self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, strftime('%s', 'now'))",
            (key, value),
        )
        await self.conn.commit()

    async def get_all_settings(self) -> dict:
        """Get all settings as a dictionary."""
        cursor = await self.conn.execute("SELECT key, value FROM settings")
        rows = await cursor.fetchall()
        return {row["key"]: row["value"] for row in rows}

    # ──────────────────────────────────────────────────────────────────────
    # Notification Operations
    # ──────────────────────────────────────────────────────────────────────

    async def queue_notification(self, tg_id: int, message_text: str, scheduled_at: int = None) -> None:
        """Queue a notification for sending."""
        await self.conn.execute(
            """INSERT INTO notifications (tg_id, message_text, scheduled_at)
               VALUES (?, ?, ?)""",
            (tg_id, message_text, scheduled_at),
        )
        await self.conn.commit()

    async def get_pending_notifications(self) -> list:
        """Get all pending notifications that are due."""
        now = int(time.time())
        cursor = await self.conn.execute(
            """SELECT * FROM notifications 
               WHERE sent = 0 AND (scheduled_at IS NULL OR scheduled_at <= ?)
               ORDER BY id ASC""",
            (now,),
        )
        return await cursor.fetchall()

    async def mark_notification_sent(self, notification_id: int) -> None:
        """Mark a notification as sent."""
        await self.conn.execute(
            "UPDATE notifications SET sent = 1 WHERE id = ?",
            (notification_id,),
        )
        await self.conn.commit()


# Global database instance
db = Database(DATABASE_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# 3X-UI Panel API Client
# ─────────────────────────────────────────────────────────────────────────────

class PanelAPIError(Exception):
    """Custom exception for API errors."""
    pass


class PanelAPIClient:
    """Async HTTP client for 3X-UI Panel API."""

    def __init__(self, base_url: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.session: Optional[aiohttp.ClientSession] = None
        self._api_prefix = "/panel/api"

    async def connect(self) -> None:
        """Initialize HTTP session."""
        self.session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        )

    async def close(self) -> None:
        """Close HTTP session."""
        if self.session:
            await self.session.close()

    def _url(self, endpoint: str) -> str:
        """Build full URL for an endpoint."""
        return f"{self.base_url}{self._api_prefix}/{endpoint.lstrip('/')}"

    async def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make an API request."""
        if not self.session:
            await self.connect()

        url = self._url(endpoint)
        async with self.session.request(method, url, **kwargs) as response:
            data = await response.json()
            if not data.get("success", False):
                raise PanelAPIError(data.get("msg", "Unknown API error"))
            return data

    async def get(self, endpoint: str, params: dict = None) -> dict:
        """Make GET request."""
        return await self._request("GET", endpoint, params=params)

    async def post(self, endpoint: str, json_data: dict = None) -> dict:
        """Make POST request."""
        return await self._request("POST", endpoint, json=json_data)

    # ──────────────────────────────────────────────────────────────────────
    # Authentication Endpoints
    # ──────────────────────────────────────────────────────────────────────

    async def test_connection(self) -> bool:
        """Test API connection by fetching inbounds options."""
        try:
            await self.get("/inbounds/options")
            return True
        except Exception:
            return False

    # ──────────────────────────────────────────────────────────────────────
    # Inbound Endpoints
    # ──────────────────────────────────────────────────────────────────────

    async def get_inbounds_options(self) -> list:
        """Get simplified inbound list for selection."""
        data = await self.get("/inbounds/options")
        return data.get("obj", [])

    async def get_inbounds_list(self) -> list:
        """Get full inbound list with details."""
        data = await self.get("/inbounds/list")
        return data.get("obj", [])

    async def get_inbound(self, inbound_id: int) -> dict:
        """Get single inbound details."""
        data = await self.get(f"/inbounds/get/{inbound_id}")
        return data.get("obj", {})

    # ──────────────────────────────────────────────────────────────────────
    # Client Endpoints
    # ──────────────────────────────────────────────────────────────────────

    async def get_clients_paged(self, page: int = 1, page_size: int = 50, search: str = "", filter_status: str = "", protocol: str = "") -> dict:
        """Get paginated client list."""
        params = {
            "page": page,
            "pageSize": page_size,
            "search": search,
            "filter": filter_status,
            "protocol": protocol,
        }
        data = await self.get("/clients/list/paged", params=params)
        return data.get("obj", {})

    async def get_client(self, email: str) -> dict:
        """Get full client details."""
        data = await self.get(f"/clients/get/{email}")
        return data.get("obj", {})

    async def create_client(self, email: str, total_gb: float, expiry_ms: int, inbound_ids: list, tg_id: int = None, limit_ip: int = 0, enable: bool = True) -> dict:
        """Create a new client."""
        payload = {
            "client": {
                "email": email,
                "totalGB": total_gb,
                "expiryTime": expiry_ms,
                "enable": enable,
                "limitIp": limit_ip,
            },
            "inboundIds": inbound_ids,
        }
        if tg_id:
            payload["client"]["tgId"] = tg_id
        data = await self.post("/clients/add", json_data=payload)
        return data.get("obj", {})

    async def update_client(self, email: str, client_data: dict) -> dict:
        """Update an existing client."""
        payload = {"client": client_data}
        data = await self.post(f"/clients/update/{email}", json_data=payload)
        return data.get("obj", {})

    async def delete_client(self, email: str, keep_traffic: bool = False) -> dict:
        """Delete a client."""
        params = {"keepTraffic": "1" if keep_traffic else "0"}
        data = await self.post(f"/clients/del/{email}", params=params)
        return data.get("obj", {})

    async def get_client_traffic(self, email: str) -> dict:
        """Get client traffic statistics."""
        data = await self.get(f"/clients/traffic/{email}")
        return data.get("obj", {})

    async def reset_client_traffic(self, email: str) -> dict:
        """Reset client traffic counters."""
        data = await self.post(f"/clients/resetTraffic/{email}")
        return data.get("obj", {})

    async def get_client_links(self, email: str) -> list:
        """Get client subscription links."""
        data = await self.get(f"/clients/links/{email}")
        return data.get("obj", [])

    async def bulk_adjust_clients(self, emails: list, add_days: int = 0, add_bytes: int = 0, flow: str = None) -> dict:
        """Bulk adjust client traffic and expiry."""
        payload = {
            "emails": emails,
            "addDays": add_days,
            "addBytes": add_bytes,
        }
        if flow:
            payload["flow"] = flow
        data = await self.post("/clients/bulkAdjust", json_data=payload)
        return data.get("obj", {})

    async def bulk_enable_clients(self, emails: list) -> dict:
        """Bulk enable clients."""
        data = await self.post("/clients/bulkEnable", json_data={"emails": emails})
        return data.get("obj", {})

    async def bulk_disable_clients(self, emails: list) -> dict:
        """Bulk disable clients."""
        data = await self.post("/clients/bulkDisable", json_data={"emails": emails})
        return data.get("obj", {})

    async def bulk_delete_clients(self, emails: list, keep_traffic: bool = False) -> dict:
        """Bulk delete clients."""
        data = await self.post("/clients/bulkDel", json_data={"emails": emails, "keepTraffic": keep_traffic})
        return data.get("obj", {})

    async def delete_depleted_clients(self) -> dict:
        """Delete all depleted clients."""
        data = await self.post("/clients/delDepleted")
        return data.get("obj", {})

    # ──────────────────────────────────────────────────────────────────────
    # Client Online Status
    # ──────────────────────────────────────────────────────────────────────

    async def get_online_clients(self) -> list:
        """Get list of currently online clients."""
        data = await self.post("/clients/onlines")
        return data.get("obj", [])

    async def get_client_ips(self, email: str) -> list:
        """Get IPs used by a client."""
        data = await self.post(f"/clients/ips/{email}")
        return data.get("obj", [])

    async def clear_client_ips(self, email: str) -> dict:
        """Clear recorded IPs for a client."""
        data = await self.post(f"/clients/clearIps/{email}")
        return data.get("obj", {})

    # ──────────────────────────────────────────────────────────────────────
    # Group Endpoints
    # ──────────────────────────────────────────────────────────────────────

    async def get_groups(self) -> list:
        """Get all client groups."""
        data = await self.get("/clients/groups")
        return data.get("obj", [])

    async def create_group(self, name: str) -> dict:
        """Create a new group."""
        data = await self.post("/clients/groups/create", json_data={"name": name})
        return data.get("obj", {})

    async def add_clients_to_group(self, group_name: str, emails: list) -> dict:
        """Add clients to a group."""
        data = await self.post("/clients/groups/bulkAdd", json_data={"groupName": group_name, "emails": emails})
        return data.get("obj", {})

    async def remove_clients_from_group(self, group_name: str, emails: list) -> dict:
        """Remove clients from a group."""
        data = await self.post("/clients/groups/bulkRemove", json_data={"groupName": group_name, "emails": emails})
        return data.get("obj", {})

    # ──────────────────────────────────────────────────────────────────────
    # Settings Endpoints
    # ──────────────────────────────────────────────────────────────────────

    async def get_all_settings(self) -> dict:
        """Get all panel settings."""
        data = await self.post("/setting/all")
        return data.get("obj", {})

    async def create_api_token(self, name: str) -> str:
        """Create a new API token."""
        data = await self.post("/setting/apiTokens/create", json_data={"name": name})
        obj = data.get("obj", {})
        return obj.get("token", "")

    async def restart_panel(self) -> dict:
        """Restart the panel."""
        data = await self.post("/setting/restartPanel")
        return data.get("obj", {})

    # ──────────────────────────────────────────────────────────────────────
    # Backup Endpoint
    # ──────────────────────────────────────────────────────────────────────

    async def backup_to_telegram(self) -> dict:
        """Trigger backup to Telegram."""
        data = await self.post("/backuptotgbot")
        return data.get("obj", {})


# ─────────────────────────────────────────────────────────────────────────────
# Keyboard Builders (Inline Keyboards with Colored Buttons)
# ─────────────────────────────────────────────────────────────────────────────

class ButtonStyle:
    """Button style constants for aiogram 3.x."""
    PRIMARY = "primary"
    SUCCESS = "success"
    DANGER = "danger"
    SECONDARY = "secondary"


def build_main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Build main menu keyboard."""
    buttons = [
        [
            InlineKeyboardButton(text="📦 My Subscription", callback_data="my_subscription"),
        ],
        [
            InlineKeyboardButton(text="💳 Buy New Plan", callback_data="buy_plan", style=ButtonStyle.SUCCESS),
            InlineKeyboardButton(text="🔄 Renew / Upgrade", callback_data="renew_upgrade"),
        ],
        [
            InlineKeyboardButton(text="🎁 Promo Code", callback_data="use_promo"),
            InlineKeyboardButton(text="👥 Refer Friends", callback_data="referral_program"),
        ],
        [
            InlineKeyboardButton(text="📊 Usage Stats", callback_data="usage_stats"),
            InlineKeyboardButton(text="🆘 Support", callback_data="support_menu"),
        ],
    ]
    if is_admin:
        buttons.append([
            InlineKeyboardButton(text="⚙️ Admin Panel", callback_data="admin_panel", style=ButtonStyle.DANGER),
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_plans_keyboard(plans: list) -> InlineKeyboardMarkup:
    """Build plans selection keyboard."""
    buttons = []
    for plan in plans:
        buttons.append([
            InlineKeyboardButton(
                text=f"💎 {plan['name']} - ${plan['price']} ({plan['traffic_gb']}GB / {plan['duration_days']}d)",
                callback_data=f"select_plan:{plan['id']}",
                style=ButtonStyle.SUCCESS,
            ),
        ])
    buttons.append([
        InlineKeyboardButton(text="« Back", callback_data="main_menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_subscription_detail_keyboard(client_email: str) -> InlineKeyboardMarkup:
    """Build subscription detail keyboard."""
    buttons = [
        [
            InlineKeyboardButton(text="🔗 Get Links", callback_data=f"get_links:{client_email}"),
        ],
        [
            InlineKeyboardButton(text="🔄 Reset Traffic", callback_data=f"reset_traffic:{client_email}"),
            InlineKeyboardButton(text="📋 Copy Sub ID", callback_data=f"copy_subid:{client_email}"),
        ],
        [
            InlineKeyboardButton(text="« Back", callback_data="main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_admin_panel_keyboard() -> InlineKeyboardMarkup:
    """Build admin panel main keyboard."""
    buttons = [
        [
            InlineKeyboardButton(text="📊 Dashboard", callback_data="admin_dashboard"),
            InlineKeyboardButton(text="👥 Clients", callback_data="admin_clients"),
        ],
        [
            InlineKeyboardButton(text="💰 Transactions", callback_data="admin_transactions"),
            InlineKeyboardButton(text="🎁 Promo Codes", callback_data="admin_promos"),
        ],
        [
            InlineKeyboardButton(text="🖥️ Panels", callback_data="admin_panels"),
            InlineKeyboardButton(text="📦 Plans", callback_data="admin_plans"),
        ],
        [
            InlineKeyboardButton(text="🎫 Tickets", callback_data="admin_tickets"),
            InlineKeyboardButton(text="⚙️ Settings", callback_data="admin_settings"),
        ],
        [
            InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast"),
            InlineKeyboardButton(text="📁 Backup", callback_data="admin_backup"),
        ],
        [
            InlineKeyboardButton(text="« Back to Menu", callback_data="main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_clients_management_keyboard() -> InlineKeyboardMarkup:
    """Build clients management keyboard."""
    buttons = [
        [
            InlineKeyboardButton(text="🟢 Active", callback_data="admin_clients_filter:active"),
            InlineKeyboardButton(text="🔴 Deactive", callback_data="admin_clients_filter:deactive"),
        ],
        [
            InlineKeyboardButton(text="🪫 Depleted", callback_data="admin_clients_filter:depleted"),
            InlineKeyboardButton(text="⏰ Expiring", callback_data="admin_clients_filter:expiring"),
        ],
        [
            InlineKeyboardButton(text="🌐 Online Now", callback_data="admin_clients_online"),
        ],
        [
            InlineKeyboardButton(text="🔍 Search Client", callback_data="admin_clients_search"),
        ],
        [
            InlineKeyboardButton(text="« Back", callback_data="admin_panel"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_ticket_status_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    """Build ticket status action keyboard."""
    buttons = [
        [
            InlineKeyboardButton(text="📝 Assign to Me", callback_data=f"ticket_assign:{ticket_id}"),
        ],
        [
            InlineKeyboardButton(text="✅ Close Ticket", callback_data=f"ticket_close:{ticket_id}", style=ButtonStyle.SUCCESS),
        ],
        [
            InlineKeyboardButton(text="« Back", callback_data="admin_tickets"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_confirmation_keyboard(confirm_callback: str, cancel_callback: str) -> InlineKeyboardMarkup:
    """Build yes/no confirmation keyboard."""
    buttons = [
        [
            InlineKeyboardButton(text="✅ Yes, Confirm", callback_data=confirm_callback, style=ButtonStyle.SUCCESS),
            InlineKeyboardButton(text="❌ No, Cancel", callback_data=cancel_callback, style=ButtonStyle.DANGER),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_back_keyboard(back_callback: str) -> InlineKeyboardMarkup:
    """Build simple back button keyboard."""
    buttons = [
        [
            InlineKeyboardButton(text="« Back", callback_data=back_callback),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_support_menu_keyboard() -> InlineKeyboardMarkup:
    """Build support menu keyboard."""
    buttons = [
        [
            InlineKeyboardButton(text="🎫 Open Ticket", callback_data="open_ticket", style=ButtonStyle.SUCCESS),
        ],
        [
            InlineKeyboardButton(text="📋 My Tickets", callback_data="my_tickets"),
        ],
        [
            InlineKeyboardButton(text="📞 Contact Support", url="https://t.me/support"),
        ],
        [
            InlineKeyboardButton(text="« Back", callback_data="main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_referral_keyboard(ref_link: str) -> InlineKeyboardMarkup:
    """Build referral program keyboard."""
    buttons = [
        [
            InlineKeyboardButton(text="🔗 Copy Referral Link", url=ref_link),
        ],
        [
            InlineKeyboardButton(text="📊 My Referrals", callback_data="my_referrals"),
        ],
        [
            InlineKeyboardButton(text="« Back", callback_data="main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─────────────────────────────────────────────────────────────────────────────
# Form States (FSM)
# ─────────────────────────────────────────────────────────────────────────────

class PurchaseFlow(StatesGroup):
    selecting_plan = State()
    applying_promo = State()
    confirming_payment = State()


class SupportFlow(StatesGroup):
    entering_subject = State()
    entering_message = State()


class AdminFlow(StatesGroup):
    broadcast_message = State()
    create_promo = State()
    add_panel = State()
    create_plan = State()
    search_client = State()


# ─────────────────────────────────────────────────────────────────────────────
# Middleware
# ─────────────────────────────────────────────────────────────────────────────

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class UserMiddleware(BaseMiddleware):
    """Middleware to register/update users in database."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        if isinstance(event, Message):
            user = event.from_user
            if user:
                await db.upsert_user(
                    tg_id=user.id,
                    username=user.username or "",
                    first_name=user.first_name or "",
                    last_name=user.last_name or "",
                    language_code=user.language_code or "en",
                )
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            if user:
                await db.upsert_user(
                    tg_id=user.id,
                    username=user.username or "",
                    first_name=user.first_name or "",
                    last_name=user.last_name or "",
                    language_code=user.language_code or "en",
                )
        return await handler(event, data)


class AdminMiddleware(BaseMiddleware):
    """Middleware to check admin status."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
            if user and user.id in ADMIN_IDS:
                data["is_admin"] = True
                await db.set_admin(user.id, True)
            else:
                data["is_admin"] = False
        return await handler(event, data)


# ─────────────────────────────────────────────────────────────────────────────
# Message Formatters
# ─────────────────────────────────────────────────────────────────────────────

def format_client_status(client: dict, traffic: dict = None) -> str:
    """Format client status message with visual elements."""
    email = client.get("email", "N/A")
    enable = client.get("enable", False)
    total_gb = float(client.get("totalGB", 0))
    expiry_ms = int(client.get("expiryTime", 0))
    sub_id = client.get("subId", "N/A")

    # Calculate usage
    used_bytes = 0
    if traffic:
        used_bytes = int(traffic.get("up", 0)) + int(traffic.get("down", 0))
    total_bytes = int(total_gb * 1024 * 1024 * 1024) if total_gb > 0 else 0
    remaining_bytes = max(0, total_bytes - used_bytes) if total_bytes > 0 else float('inf')

    # Calculate days remaining
    days_left = remaining_days(expiry_ms)

    # Status emoji
    now_ms = int(datetime.now().timestamp() * 1000)
    expired = expiry_ms > 0 and expiry_ms < now_ms
    depleted = total_bytes > 0 and used_bytes >= total_bytes
    status_emoji = get_status_emoji(enable, expired, depleted)

    # Build status line
    if enable:
        if depleted:
            status_text = "<b>Traffic Depleted</b>"
        elif expired:
            status_text = "<b>Expired</b>"
        else:
            status_text = "<b>Active</b>"
    else:
        status_text = "<b>Disabled</b>"

    # Traffic bar
    if total_bytes > 0:
        traffic_bar = format_traffic_bar(used_bytes, total_bytes, 15)
    else:
        traffic_bar = "∞ " + "█" * 15

    # Format message
    msg = f"""
{status_emoji} <b>Subscription Status</b> {status_emoji}

📧 <b>Email:</b> <code>{email}</code>
🎫 <b>Sub ID:</b> <code>{sub_id}</code>

📊 <b>Traffic Usage:</b>
   {traffic_bar}
   Used: {format_bytes(used_bytes)} / {format_bytes(total_bytes) if total_bytes > 0 else '∞'}
   Remaining: {format_bytes(remaining_bytes) if remaining_bytes != float('inf') else '∞'}

📅 <b>Expiry:</b> {ms_to_datetime(expiry_ms).strftime('%Y-%m-%d %H:%M') if expiry_ms > 0 else 'Never'}
⏳ <b>Days Left:</b> {days_left if days_left >= 0 else '∞'} days

<i>Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>
"""
    return msg.strip()


def format_dashboard_stats(clients_count: int, active_count: int, revenue: float, tickets_open: int) -> str:
    """Format admin dashboard statistics."""
    now = datetime.now()
    msg = f"""
📊 <b>Admin Dashboard</b> 📊

📈 <b>Overview (Last 30 Days)</b>

👥 <b>Clients:</b>
   • Total: {clients_count}
   • Active: {active_count}
   • Success Rate: {(active_count/clients_count*100) if clients_count > 0 else 0:.1f}%

💰 <b>Revenue:</b>
   • Total: ${revenue:.2f}
   • Daily Avg: ${revenue/30:.2f}

🎫 <b>Support:</b>
   • Open Tickets: {tickets_open}

🕒 <b>Last Update:</b> {now.strftime('%Y-%m-%d %H:%M:%S')}
"""
    return msg.strip()


def format_usage_stats(traffic: dict, client: dict) -> str:
    """Format detailed usage statistics."""
    up = int(traffic.get("up", 0))
    down = int(traffic.get("down", 0))
    total_used = up + down
    total_limit_gb = float(client.get("totalGB", 0))
    total_limit_bytes = int(total_limit_gb * 1024 * 1024 * 1024) if total_limit_gb > 0 else 0

    # Calculate percentages
    if total_limit_bytes > 0:
        pct_used = (total_used / total_limit_bytes) * 100
        pct_remaining = 100 - pct_used
    else:
        pct_used = 0
        pct_remaining = 100

    msg = f"""
📊 <b>Detailed Usage Statistics</b> 📊

📤 <b>Upload:</b> {format_bytes(up)}
📥 <b>Download:</b> {format_bytes(down)}
📈 <b>Total Used:</b> {format_bytes(total_used)}

📊 <b>Traffic Breakdown:</b>
   Upload:   {pct_used * (up/total_used if total_used > 0 else 0):.1f}%
   Download: {pct_used * (down/total_used if total_used > 0 else 0):.1f}%

🎯 <b>Quota Status:</b>
   Used:      {pct_used:.1f}%
   Remaining: {pct_remaining:.1f}%

📉 <b>Visualization:</b>
   {format_traffic_bar(total_used, total_limit_bytes, 20)}
"""
    return msg.strip()


# ─────────────────────────────────────────────────────────────────────────────
# User Handlers
# ─────────────────────────────────────────────────────────────────────────────

user_router = Router()


@user_router.message(CommandStart())
async def cmd_start(message: Message, is_admin: bool = False):
    """Handle /start command."""
    user = message.from_user
    await message.answer(
        f"👋 Welcome, {user.first_name}!\n\n"
        f"🚀 I'm your personal VPN assistant.\n"
        f"I can help you manage your subscription, purchase new plans, and get support.\n\n"
        f"Use the buttons below to navigate:",
        reply_markup=build_main_menu_keyboard(is_admin),
        parse_mode="HTML",
    )


@user_router.message(Command("help"))
async def cmd_help(message: Message):
    """Handle /help command."""
    help_text = """
📖 <b>Bot Help Guide</b> 📖

<b>Available Commands:</b>
/start - Start the bot and see main menu
/help - Show this help message
/myinfo - Show your account info
/support - Open support menu

<b>Features:</b>
• Purchase VPN subscriptions
• Manage your account
• View usage statistics
• Get support via tickets
• Refer friends and earn commissions

<b>Need Help?</b>
Open a support ticket from the menu or contact @support
"""
    await message.answer(help_text, parse_mode="HTML", reply_markup=build_back_keyboard("main_menu"))


@user_router.callback_query(F.data == "main_menu")
async def show_main_menu(callback: CallbackQuery, is_admin: bool = False):
    """Show main menu."""
    await callback.message.edit_text(
        "🏠 <b>Main Menu</b>\n\nSelect an option:",
        reply_markup=build_main_menu_keyboard(is_admin),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data == "my_subscription")
async def show_my_subscription(callback: CallbackQuery):
    """Show user's subscription details."""
    tg_id = callback.from_user.id
    client = await db.get_client_by_tg_id(tg_id)

    if not client:
        await callback.answer("❌ No active subscription found.", show_alert=True)
        return

    # Fetch latest data from panel
    try:
        panel = await db.get_primary_panel()
        if panel:
            api_client = PanelAPIClient(panel["url"], panel["api_token"])
            await api_client.connect()
            try:
                panel_client = await api_client.get_client(client["email"])
                traffic = await api_client.get_client_traffic(client["email"])
            finally:
                await api_client.close()
        else:
            panel_client = client
            traffic = {}
    except Exception as e:
        logger.error(f"Error fetching client data: {e}")
        panel_client = client
        traffic = {}

    msg = format_client_status(panel_client, traffic)
    await callback.message.edit_text(
        msg,
        reply_markup=build_subscription_detail_keyboard(client["email"]),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data == "buy_plan")
async def show_plans(callback: CallbackQuery):
    """Show available plans."""
    plans = await db.get_all_plans()
    if not plans:
        await callback.answer("❌ No plans available.", show_alert=True)
        return

    text = "💎 <b>Available Plans</b> 💎\n\nSelect a plan to purchase:\n"
    for plan in plans:
        text += f"\n• <b>{plan['name']}</b>: ${plan['price']}\n"
        text += f"  📦 {plan['traffic_gb']}GB traffic\n"
        text += f"  📅 {plan['duration_days']} days validity\n"
        text += f"  ℹ️ {plan['description']}"

    await callback.message.edit_text(
        text,
        reply_markup=build_plans_keyboard(plans),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data.startswith("select_plan:"))
async def select_plan(callback: CallbackQuery, state: FSMContext):
    """Handle plan selection."""
    plan_id = int(callback.data.split(":")[1])
    plan = await db.get_plan_by_id(plan_id)

    if not plan:
        await callback.answer("❌ Plan not found.", show_alert=True)
        return

    await state.update_data(selected_plan=plan)
    await state.set_state(PurchaseFlow.confirming_payment)

    text = f"""
💳 <b>Confirm Purchase</b> 💳

<b>Plan:</b> {plan['name']}
<b>Price:</b> ${plan['price']}
<b>Traffic:</b> {plan['traffic_gb']}GB
<b>Duration:</b> {plan['duration_days']} days

<b>Description:</b> {plan['description']}

⚠️ <i>Note:</i> This is a demo flow. In production, integrate with payment gateway.

Proceed with purchase?
"""
    await callback.message.edit_text(
        text,
        reply_markup=build_confirmation_keyboard(
            f"confirm_purchase:{plan_id}",
            "cancel_purchase",
        ),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data.startswith("confirm_purchase:"))
async def confirm_purchase(callback: CallbackQuery, state: FSMContext):
    """Handle purchase confirmation."""
    plan_id = int(callback.data.split(":")[1])
    plan = await db.get_plan_by_id(plan_id)
    tg_id = callback.from_user.id

    # Simulate successful payment
    tx_id = await db.create_transaction(
        tg_id=tg_id,
        amount=plan["price"],
        description=f"Purchase plan: {plan['name']}",
        payment_method="demo",
        status="completed",
    )

    # Create client on panel
    try:
        panel = await db.get_primary_panel()
        if panel:
            api_client = PanelAPIClient(panel["url"], panel["api_token"])
            await api_client.connect()
            try:
                # Get inbounds
                inbounds = await api_client.get_inbounds_options()
                if inbounds:
                    inbound_ids = [inbounds[0]["id"]]  # Use first inbound
                    email = generate_email()
                    expiry_ms = int((datetime.now() + timedelta(days=plan["duration_days"])).timestamp() * 1000)

                    # Create client
                    await api_client.create_client(
                        email=email,
                        total_gb=plan["traffic_gb"],
                        expiry_ms=expiry_ms,
                        inbound_ids=inbound_ids,
                        tg_id=tg_id,
                    )

                    # Save to local DB
                    await db.create_client(
                        email=email,
                        tg_id=tg_id,
                        total_gb=plan["traffic_gb"],
                        expiry_ms=expiry_ms,
                    )

                    # Update transaction
                    await db.update_transaction_status(tx_id, "completed")

                    # Get links
                    links = await api_client.get_client_links(email)

                    text = f"""
✅ <b>Purchase Successful!</b> ✅

<b>Plan:</b> {plan['name']}
<b>Email:</b> <code>{email}</code>

<b>Connection Links:</b>
"""
                    for i, link in enumerate(links[:3], 1):
                        text += f"\n{i}. <code>{link[:50]}...</code>"

                    text += "\n\n<i>Your subscription is now active!</i>"

                    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=build_back_keyboard("main_menu"))
                    return
            finally:
                await api_client.close()
    except Exception as e:
        logger.error(f"Error creating client: {e}")
        await callback.answer(f"❌ Error: {str(e)}", show_alert=True)
        return

    await callback.answer("❌ Failed to create subscription. Contact support.", show_alert=True)


@user_router.callback_query(F.data == "cancel_purchase")
async def cancel_purchase(callback: CallbackQuery, state: FSMContext):
    """Cancel purchase."""
    await state.clear()
    await callback.message.edit_text(
        "❌ Purchase cancelled.",
        reply_markup=build_main_menu_keyboard(),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data == "usage_stats")
async def show_usage_stats(callback: CallbackQuery):
    """Show detailed usage statistics."""
    tg_id = callback.from_user.id
    client = await db.get_client_by_tg_id(tg_id)

    if not client:
        await callback.answer("❌ No subscription found.", show_alert=True)
        return

    try:
        panel = await db.get_primary_panel()
        if panel:
            api_client = PanelAPIClient(panel["url"], panel["api_token"])
            await api_client.connect()
            try:
                traffic = await api_client.get_client_traffic(client["email"])
                msg = format_usage_stats(traffic, client)
            finally:
                await api_client.close()
        else:
            msg = "❌ Panel not configured."
    except Exception as e:
        logger.error(f"Error fetching traffic: {e}")
        msg = f"❌ Error: {str(e)}"

    await callback.message.edit_text(
        msg,
        reply_markup=build_back_keyboard("main_menu"),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data == "support_menu")
async def show_support_menu(callback: CallbackQuery):
    """Show support menu."""
    await callback.message.edit_text(
        "🆘 <b>Support Menu</b>\n\nHow can we help you?",
        reply_markup=build_support_menu_keyboard(),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data == "open_ticket")
async def open_ticket_form(callback: CallbackQuery, state: FSMContext):
    """Start ticket creation flow."""
    await state.set_state(SupportFlow.entering_subject)
    await callback.message.edit_text(
        "🎫 <b>Open Support Ticket</b>\n\nPlease enter a subject for your ticket:",
        reply_markup=build_back_keyboard("support_menu"),
        parse_mode="HTML",
    )


@user_router.message(SupportFlow.entering_subject)
async def process_ticket_subject(message: Message, state: FSMContext):
    """Process ticket subject."""
    subject = message.text.strip()
    if len(subject) < 5:
        await message.answer("❌ Subject too short. Please enter at least 5 characters.")
        return

    await state.update_data(ticket_subject=subject)
    await state.set_state(SupportFlow.entering_message)
    await message.answer(
        "📝 Now please describe your issue in detail:",
        reply_markup=build_back_keyboard("support_menu"),
    )


@user_router.message(SupportFlow.entering_message)
async def process_ticket_message(message: Message, state: FSMContext):
    """Process ticket message and create ticket."""
    data = await state.get_data()
    subject = data.get("ticket_subject")
    message_text = message.text.strip()

    if len(message_text) < 10:
        await message.answer("❌ Message too short. Please provide more details.")
        return

    # Create ticket
    ticket_id = await db.create_ticket(
        tg_id=message.from_user.id,
        subject=subject,
    )

    # Add initial message
    await db.add_ticket_message(
        ticket_id=ticket_id,
        sender_tg_id=message.from_user.id,
        message_text=message_text,
    )

    await state.clear()
    await message.answer(
        f"✅ <b>Ticket Created!</b>\n\n"
        f"<b>Ticket ID:</b> #{ticket_id}\n"
        f"<b>Subject:</b> {subject}\n\n"
        f"Our support team will respond soon.",
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(),
    )

    # Notify admins (in production, send actual notifications)
    admins = await db.get_all_admins()
    for admin_id in admins:
        try:
            await message.bot.send_message(
                chat_id=admin_id,
                text=f"🎫 <b>New Support Ticket</b>\n\n"
                     f"<b>ID:</b> #{ticket_id}\n"
                     f"<b>User:</b> {message.from_user.first_name}\n"
                     f"<b>Subject:</b> {subject}",
                parse_mode="HTML",
            )
        except Exception:
            pass


@user_router.callback_query(F.data == "my_tickets")
async def show_my_tickets(callback: CallbackQuery):
    """Show user's tickets."""
    tg_id = callback.from_user.id
    tickets = await db.get_user_tickets(tg_id)

    if not tickets:
        await callback.answer("📭 No tickets found.", show_alert=True)
        return

    text = "🎫 <b>Your Tickets</b>\n\n"
    for ticket in tickets[:10]:
        status_emoji = {"open": "🟢", "in_progress": "🟡", "closed": "🔴"}.get(ticket["status"], "⚪")
        text += f"{status_emoji} <b>#{ticket['id']}</b> - {ticket['subject']}\n"
        text += f"   Status: {ticket['status']} | Created: {datetime.fromtimestamp(ticket['created_at']).strftime('%Y-%m-%d')}\n\n"

    await callback.message.edit_text(
        text,
        reply_markup=build_back_keyboard("support_menu"),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data == "referral_program")
async def show_referral_program(callback: CallbackQuery):
    """Show referral program info."""
    tg_id = callback.from_user.id
    ref_link = f"https://t.me/{(await callback.bot.get_me()).username}?start=ref_{tg_id}"
    commission = await db.get_total_commission(tg_id)

    text = f"""
👥 <b>Referral Program</b> 👥

Invite friends and earn <b>10% commission</b> on their purchases!

<b>Your Referral Link:</b>
<code>{ref_link}</code>

<b>Total Commission Earned:</b> ${commission:.2f}

<b>How it works:</b>
1. Share your referral link
2. Friend purchases a plan
3. You get 10% commission automatically!

💰 Start earning today!
"""
    await callback.message.edit_text(
        text,
        reply_markup=build_referral_keyboard(ref_link),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data == "use_promo")
async def use_promo_code(callback: CallbackQuery, state: FSMContext):
    """Prompt for promo code."""
    await state.set_state(PurchaseFlow.applying_promo)
    await callback.message.edit_text(
        "🎁 <b>Enter Promo Code</b>\n\nSend your promo code:",
        reply_markup=build_back_keyboard("main_menu"),
        parse_mode="HTML",
    )


@user_router.message(PurchaseFlow.applying_promo)
async def process_promo_code(message: Message, state: FSMContext):
    """Process promo code."""
    code = message.text.strip().upper()
    promo = await db.get_promo_code(code)

    if not promo:
        await message.answer("❌ Invalid or expired promo code.")
        await state.clear()
        return

    # Check if max uses reached
    if promo["max_uses"] > 0 and promo["current_uses"] >= promo["max_uses"]:
        await message.answer("❌ This promo code has reached its usage limit.")
        await state.clear()
        return

    # Check expiry
    if promo["expires_at"] and promo["expires_at"] < int(time.time()):
        await message.answer("❌ This promo code has expired.")
        await state.clear()
        return

    # Calculate discount
    discount_text = ""
    if promo["discount_percent"] > 0:
        discount_text = f"{promo['discount_percent']}% OFF"
    elif promo["discount_fixed"] > 0:
        discount_text = f"${promo['discount_fixed']} OFF"

    await message.answer(
        f"✅ <b>Promo Code Applied!</b>\n\n"
        f"<b>Code:</b> {code}\n"
        f"<b>Discount:</b> {discount_text}\n\n"
        f"Proceed to purchase to use this discount.",
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(),
    )
    await state.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Admin Handlers
# ─────────────────────────────────────────────────────────────────────────────

admin_router = Router()


@admin_router.callback_query(F.data == "admin_panel")
async def show_admin_panel(callback: CallbackQuery):
    """Show admin panel main menu."""
    await callback.message.edit_text(
        "⚙️ <b>Admin Control Panel</b>\n\nManage your VPN business:",
        reply_markup=build_admin_panel_keyboard(),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "admin_dashboard")
async def show_admin_dashboard(callback: CallbackQuery):
    """Show admin dashboard with statistics."""
    # Get statistics
    all_clients = []
    try:
        panel = await db.get_primary_panel()
        if panel:
            api_client = PanelAPIClient(panel["url"], panel["api_token"])
            await api_client.connect()
            try:
                clients_data = await api_client.get_clients_paged(page=1, page_size=1)
                all_clients = clients_data.get("items", [])
                total_count = clients_data.get("total", 0)
                summary = clients_data.get("summary", {})
            finally:
                await api_client.close()
    except Exception as e:
        logger.error(f"Error fetching dashboard stats: {e}")
        total_count = 0
        summary = {}

    # Get revenue
    transactions = await db.conn.execute_fetchall("SELECT SUM(amount) as total FROM transactions WHERE status = 'completed'")
    revenue = float(transactions[0]["total"] or 0) if transactions else 0

    # Get open tickets
    open_tickets = await db.get_open_tickets()

    active_count = summary.get("active", 0)

    msg = format_dashboard_stats(total_count, active_count, revenue, len(open_tickets))
    await callback.message.edit_text(
        msg,
        reply_markup=build_back_keyboard("admin_panel"),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "admin_clients")
async def show_admin_clients_menu(callback: CallbackQuery):
    """Show clients management menu."""
    await callback.message.edit_text(
        "👥 <b>Client Management</b>\n\nSelect a filter:",
        reply_markup=build_clients_management_keyboard(),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data.startswith("admin_clients_filter:"))
async def filter_clients(callback: CallbackQuery):
    """Filter clients by status."""
    filter_status = callback.data.split(":")[1]
    
    try:
        panel = await db.get_primary_panel()
        if not panel:
            await callback.answer("❌ No panel configured.", show_alert=True)
            return

        api_client = PanelAPIClient(panel["url"], panel["api_token"])
        await api_client.connect()
        try:
            clients_data = await api_client.get_clients_paged(
                page=1,
                page_size=10,
                filter_status=filter_status,
            )
            clients = clients_data.get("items", [])
            total = clients_data.get("filtered", 0)
        finally:
            await api_client.close()

        if not clients:
            await callback.answer("📭 No clients found.", show_alert=True)
            return

        text = f"👥 <b>Clients ({filter_status.title()})</b>\n\nTotal: {total}\n\n"
        for client in clients[:10]:
            status_emoji = get_status_emoji(
                client.get("enable", False),
                False,
                False,
            )
            text += f"{status_emoji} <code>{client['email']}</code>\n"
            text += f"   Traffic: {client.get('totalGB', 0)}GB | "
            text += f"Days: {remaining_days(client.get('expiryTime', 0))}\n\n"

        await callback.message.edit_text(
            text,
            reply_markup=build_back_keyboard("admin_clients"),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Error filtering clients: {e}")
        await callback.answer(f"❌ Error: {str(e)}", show_alert=True)


@admin_router.callback_query(F.data == "admin_clients_online")
async def show_online_clients(callback: CallbackQuery):
    """Show currently online clients."""
    try:
        panel = await db.get_primary_panel()
        if not panel:
            await callback.answer("❌ No panel configured.", show_alert=True)
            return

        api_client = PanelAPIClient(panel["url"], panel["api_token"])
        await api_client.connect()
        try:
            online = await api_client.get_online_clients()
        finally:
            await api_client.close()

        if not online:
            await callback.answer("📭 No clients online right now.", show_alert=True)
            return

        text = f"🌐 <b>Online Clients</b>\n\nTotal: {len(online)}\n\n"
        for email in online[:20]:
            text += f"🟢 <code>{email}</code>\n"

        await callback.message.edit_text(
            text,
            reply_markup=build_back_keyboard("admin_clients"),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Error fetching online clients: {e}")
        await callback.answer(f"❌ Error: {str(e)}", show_alert=True)


@admin_router.callback_query(F.data == "admin_clients_search")
async def search_client_form(callback: CallbackQuery, state: FSMContext):
    """Start client search."""
    await state.set_state(AdminFlow.search_client)
    await callback.message.edit_text(
        "🔍 <b>Search Client</b>\n\nEnter email or part of it:",
        reply_markup=build_back_keyboard("admin_clients"),
        parse_mode="HTML",
    )


@admin_router.message(AdminFlow.search_client)
async def process_client_search(message: Message, state: FSMContext):
    """Process client search query."""
    search_query = message.text.strip()

    try:
        panel = await db.get_primary_panel()
        if not panel:
            await message.answer("❌ No panel configured.")
            await state.clear()
            return

        api_client = PanelAPIClient(panel["url"], panel["api_token"])
        await api_client.connect()
        try:
            clients_data = await api_client.get_clients_paged(
                page=1,
                page_size=10,
                search=search_query,
            )
            clients = clients_data.get("items", [])
        finally:
            await api_client.close()

        if not clients:
            await message.answer("📭 No clients found matching your search.")
            await state.clear()
            return

        text = f"🔍 <b>Search Results for '{search_query}'</b>\n\n"
        for client in clients:
            text += f"📧 <code>{client['email']}</code>\n"
            text += f"   Status: {'🟢' if client.get('enable') else '🔴'} | "
            text += f"Traffic: {client.get('totalGB', 0)}GB\n\n"

        await message.answer(
            text,
            reply_markup=build_back_keyboard("admin_clients"),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Error searching clients: {e}")
        await message.answer(f"❌ Error: {str(e)}")
    finally:
        await state.clear()


@admin_router.callback_query(F.data == "admin_transactions")
async def show_admin_transactions(callback: CallbackQuery):
    """Show recent transactions."""
    transactions = await db.conn.execute_fetchall(
        "SELECT * FROM transactions ORDER BY created_at DESC LIMIT 20"
    )

    if not transactions:
        await callback.answer("💸 No transactions yet.", show_alert=True)
        return

    text = "💰 <b>Recent Transactions</b>\n\n"
    for tx in transactions:
        status_emoji = {"completed": "✅", "pending": "⏳", "failed": "❌"}.get(tx["status"], "⚪")
        text += f"{status_emoji} <b>${tx['amount']:.2f}</b> - {tx['description'][:30]}\n"
        text += f"   Status: {tx['status']} | Date: {datetime.fromtimestamp(tx['created_at']).strftime('%Y-%m-%d')}\n\n"

    await callback.message.edit_text(
        text,
        reply_markup=build_back_keyboard("admin_panel"),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "admin_tickets")
async def show_admin_tickets(callback: CallbackQuery):
    """Show open support tickets."""
    tickets = await db.get_open_tickets()

    if not tickets:
        await callback.answer("🎫 No open tickets.", show_alert=True)
        return

    text = "🎫 <b>Open Support Tickets</b>\n\n"
    for ticket in tickets[:10]:
        text += f"📧 <b>#{ticket['id']}</b> - {ticket['subject']}\n"
        text += f"   User ID: {ticket['tg_id']} | Priority: {ticket['priority']}\n\n"

    await callback.message.edit_text(
        text,
        reply_markup=build_back_keyboard("admin_panel"),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data.startswith("ticket_assign:"))
async def assign_ticket(callback: CallbackQuery):
    """Assign ticket to current admin."""
    ticket_id = int(callback.data.split(":")[1])
    admin_id = callback.from_user.id

    await db.assign_ticket(ticket_id, admin_id)
    await callback.answer(f"✅ Ticket #{ticket_id} assigned to you!", show_alert=True)
    await show_admin_tickets(callback)


@admin_router.callback_query(F.data.startswith("ticket_close:"))
async def close_ticket(callback: CallbackQuery):
    """Close a ticket."""
    ticket_id = int(callback.data.split(":")[1])
    await db.close_ticket(ticket_id)
    await callback.answer(f"✅ Ticket #{ticket_id} closed!", show_alert=True)
    await show_admin_tickets(callback)


@admin_router.callback_query(F.data == "admin_panels")
async def show_admin_panels(callback: CallbackQuery):
    """Show configured panels."""
    panels = await db.get_all_panels()

    if not panels:
        text = "🖥️ <b>No panels configured.</b>\n\nUse /addpanel to add one."
    else:
        text = "🖥️ <b>Configured Panels</b>\n\n"
        for panel in panels:
            status_emoji = {"healthy": "🟢", "unhealthy": "🔴", "unknown": "⚪"}.get(panel["health_status"], "⚪")
            primary_badge = "👑 " if panel["is_primary"] else ""
            text += f"{primary_badge}{status_emoji} <b>{panel['name']}</b>\n"
            text += f"   URL: {panel['url']}\n"
            text += f"   Status: {panel['health_status']} | Clients: {panel['current_clients']}\n\n"

    buttons = [
        [InlineKeyboardButton(text="➕ Add Panel", callback_data="admin_add_panel")],
        [InlineKeyboardButton(text="« Back", callback_data="admin_panel")],
    ]
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "admin_plans")
async def show_admin_plans(callback: CallbackQuery):
    """Show all plans."""
    plans = await db.get_all_plans()

    text = "📦 <b>Subscription Plans</b>\n\n"
    for plan in plans:
        text += f"💎 <b>{plan['name']}</b> - ${plan['price']}\n"
        text += f"   {plan['traffic_gb']}GB | {plan['duration_days']} days\n"
        text += f"   {plan['description']}\n\n"

    buttons = [
        [InlineKeyboardButton(text="➕ Create Plan", callback_data="admin_create_plan")],
        [InlineKeyboardButton(text="« Back", callback_data="admin_panel")],
    ]
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "admin_promos")
async def show_admin_promos(callback: CallbackQuery):
    """Show promo codes."""
    promos = await db.conn.execute_fetchall("SELECT * FROM promo_codes ORDER BY created_at DESC LIMIT 20")

    text = "🎁 <b>Promo Codes</b>\n\n"
    for promo in promos:
        discount = f"{promo['discount_percent']}%" if promo['discount_percent'] > 0 else f"${promo['discount_fixed']}"
        text += f"🏷️ <b>{promo['code']}</b> - {discount} OFF\n"
        text += f"   Uses: {promo['current_uses']}/{promo['max_uses'] if promo['max_uses'] > 0 else '∞'}\n\n"

    buttons = [
        [InlineKeyboardButton(text="➕ Create Promo", callback_data="admin_create_promo")],
        [InlineKeyboardButton(text="« Back", callback_data="admin_panel")],
    ]
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    """Start broadcast flow."""
    await state.set_state(AdminFlow.broadcast_message)
    await callback.message.edit_text(
        "📢 <b>Broadcast Message</b>\n\nSend the message you want to broadcast to all users:",
        reply_markup=build_back_keyboard("admin_panel"),
        parse_mode="HTML",
    )


@admin_router.message(AdminFlow.broadcast_message)
async def process_broadcast(message: Message, state: FSMContext):
    """Process and send broadcast."""
    broadcast_text = message.text
    await state.clear()

    # Get all users
    users = await db.conn.execute_fetchall("SELECT tg_id FROM users")

    sent_count = 0
    failed_count = 0

    for user in users:
        try:
            await message.bot.send_message(
                chat_id=user["tg_id"],
                text=f"📢 <b>Broadcast Message</b>\n\n{broadcast_text}",
                parse_mode="HTML",
            )
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send to {user['tg_id']}: {e}")
            failed_count += 1
        await asyncio.sleep(0.1)  # Rate limiting

    await message.answer(
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"Sent: {sent_count}\n"
        f"Failed: {failed_count}",
        parse_mode="HTML",
        reply_markup=build_admin_panel_keyboard(),
    )


@admin_router.callback_query(F.data == "admin_backup")
async def trigger_backup(callback: CallbackQuery):
    """Trigger panel backup."""
    try:
        panel = await db.get_primary_panel()
        if not panel:
            await callback.answer("❌ No panel configured.", show_alert=True)
            return

        api_client = PanelAPIClient(panel["url"], panel["api_token"])
        await api_client.connect()
        try:
            await api_client.backup_to_telegram()
        finally:
            await api_client.close()

        await callback.answer("✅ Backup triggered! Check your Telegram.", show_alert=True)
    except Exception as e:
        logger.error(f"Backup error: {e}")
        await callback.answer(f"❌ Error: {str(e)}", show_alert=True)


@admin_router.callback_query(F.data == "admin_settings")
async def show_admin_settings(callback: CallbackQuery):
    """Show bot settings."""
    settings = await db.get_all_settings()

    text = "⚙️ <b>Bot Settings</b>\n\n"
    for key, value in settings.items():
        text += f"<b>{key}:</b> <code>{value}</code>\n"

    buttons = [
        [InlineKeyboardButton(text="« Back", callback_data="admin_panel")],
    ]
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Background Tasks
# ─────────────────────────────────────────────────────────────────────────────

async def check_expiries_and_traffic(bot: Bot):
    """Background task to check expiries and send alerts."""
    logger.info("Starting expiry and traffic check...")

    try:
        panel = await db.get_primary_panel()
        if not panel:
            return

        api_client = PanelAPIClient(panel["url"], panel["api_token"])
        await api_client.connect()
        try:
            # Get expiring clients
            expiring_data = await api_client.get_clients_paged(filter_status="expiring", page_size=100)
            expiring_clients = expiring_data.get("items", [])

            settings = await db.get_all_settings()
            reminder_days = [int(x) for x in settings.get("expiry_reminder_days", "7,3,1").split(",")]

            now_ms = int(datetime.now().timestamp() * 1000)

            for client in expiring_clients:
                email = client["email"]
                expiry_ms = client.get("expiryTime", 0)
                tg_id = client.get("tgId")

                if not tg_id:
                    # Try to find in local DB
                    local_client = await db.get_client_by_email(email)
                    if local_client:
                        tg_id = local_client["tg_id"]

                if not tg_id:
                    continue

                days_left = remaining_days(expiry_ms)

                if days_left in reminder_days:
                    try:
                        await bot.send_message(
                            chat_id=tg_id,
                            text=f"⏰ <b>Subscription Expiring Soon!</b>\n\n"
                                 f"Your subscription expires in <b>{days_left} days</b>.\n\n"
                                 f"Use /renew to extend your subscription.",
                            parse_mode="HTML",
                        )
                        logger.info(f"Sent expiry reminder to {tg_id} ({days_left} days)")
                    except Exception as e:
                        logger.error(f"Failed to send reminder to {tg_id}: {e}")
        finally:
            await api_client.close()
    except Exception as e:
        logger.error(f"Error in expiry check: {e}")


async def check_panel_health():
    """Background task to check panel health."""
    logger.info("Checking panel health...")

    panels = await db.get_enabled_panels()
    for panel in panels:
        try:
            api_client = PanelAPIClient(panel["url"], panel["api_token"])
            await api_client.connect()
            try:
                is_healthy = await api_client.test_connection()
                await db.update_panel_health(panel["id"], "healthy" if is_healthy else "unhealthy")
                logger.info(f"Panel {panel['name']}: {'healthy' if is_healthy else 'unhealthy'}")
            finally:
                await api_client.close()
        except Exception as e:
            logger.error(f"Panel {panel['name']} health check failed: {e}")
            await db.update_panel_health(panel["id"], "unhealthy")


async def process_notifications(bot: Bot):
    """Process queued notifications."""
    notifications = await db.get_pending_notifications()

    for notif in notifications:
        try:
            await bot.send_message(
                chat_id=notif["tg_id"],
                text=notif["message_text"],
                parse_mode="HTML",
            )
            await db.mark_notification_sent(notif["id"])
            logger.info(f"Sent notification to {notif['tg_id']}")
        except Exception as e:
            logger.error(f"Failed to send notification to {notif['tg_id']}: {e}")


async def run_background_tasks(bot: Bot):
    """Run all background tasks periodically."""
    while True:
        try:
            # Check expiries every hour
            await check_expiries_and_traffic(bot)

            # Check panel health every 5 minutes
            await check_panel_health()

            # Process notifications every minute
            await process_notifications(bot)

            await asyncio.sleep(60)  # Run every minute
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Background task error: {e}")
            await asyncio.sleep(60)


# ─────────────────────────────────────────────────────────────────────────────
# Admin Commands (for adding panels, etc.)
# ─────────────────────────────────────────────────────────────────────────────

@admin_router.message(Command("addpanel"))
async def cmd_add_panel(message: Message, is_admin: bool = False):
    """Add a new panel (admin only)."""
    if not is_admin and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Admins only!")
        return

    await message.answer(
        "🖥️ <b>Add New Panel</b>\n\n"
        f"Send panel details in format:\n"
        f"<code>name|url|api_token</code>\n\n"
        f"Example:\n"
        f"<code>Main Panel|https://panel.example.com|your-api-token-here</code>",
        parse_mode="HTML",
    )


@admin_router.message(Command("setadmin"))
async def cmd_set_admin(message: Message, is_admin: bool = False):
    """Set a user as admin."""
    if not is_admin and message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Admins only!")
        return

    # Get mentioned user ID
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
    elif len(message.text.split()) > 1:
        try:
            target_id = int(message.text.split()[1])
        except ValueError:
            await message.answer("❌ Invalid user ID.")
            return
    else:
        await message.answer("❌ Reply to a user or provide user ID.")
        return

    await db.set_admin(target_id, True)
    await message.answer(f"✅ User {target_id} is now an admin!")


# ─────────────────────────────────────────────────────────────────────────────
# Main Bot Setup
# ─────────────────────────────────────────────────────────────────────────────

async def on_startup(bot: Bot):
    """Bot startup handler."""
    logger.info("Bot is starting up...")

    # Initialize database
    await db.connect()

    # Set commands menu
    await bot.set_my_commands([
        ("start", "Start the bot"),
        ("help", "Help guide"),
        ("myinfo", "My account info"),
    ])

    # Set admin status for configured admins
    for admin_id in ADMIN_IDS:
        await db.set_admin(admin_id, True)
        try:
            user = await bot.get_chat(admin_id)
            await db.upsert_user(
                tg_id=admin_id,
                username=user.username or "",
                first_name=user.first_name or "",
                last_name=user.last_name or "",
            )
        except Exception:
            pass

    logger.info(f"Registered {len(ADMIN_IDS)} admin(s)")

    # Start background tasks
    asyncio.create_task(run_background_tasks(bot))
    logger.info("Background tasks started")

    # Setup webhook if URL provided
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook set to: {WEBHOOK_URL}")
    else:
        await bot.delete_webhook()
        logger.info("Webhook deleted (polling mode)")


async def on_shutdown(bot: Bot):
    """Bot shutdown handler."""
    logger.info("Bot is shutting down...")
    await db.close()
    await bot.session.close()


def create_dispatcher() -> Dispatcher:
    """Create and configure dispatcher."""
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Register middleware
    dp.message.middleware(UserMiddleware())
    dp.callback_query.middleware(UserMiddleware())
    dp.message.middleware(AdminMiddleware())
    dp.callback_query.middleware(AdminMiddleware())

    # Include routers
    dp.include_router(user_router)
    dp.include_router(admin_router)

    # Register startup/shutdown handlers
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    return dp


async def main():
    """Main entry point."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable not set!")
        return

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = create_dispatcher()

    try:
        if WEBHOOK_URL:
            # Webhook mode
            from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
            from aiohttp import web

            app = web.Application()
            webhook_requests_handler = SimpleRequestHandler(
                dispatcher=dp,
                bot=bot,
            )
            webhook_requests_handler.register(app, path="/webhook")
            setup_application(app, dp, bot=bot)

            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, host="0.0.0.0", port=8080)
            await site.start()

            logger.info("Bot running in webhook mode on port 8080")

            # Keep running
            while True:
                await asyncio.sleep(3600)
        else:
            # Polling mode
            logger.info("Bot running in polling mode...")
            await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
