#!/usr/bin/env python3.11
"""
3X-UI Telegram Sales Bot
========================
A production-ready Telegram bot for selling and managing 3X-UI VPN subscriptions.

Features:
- Multi-panel architecture with smart load balancing
- Free trial system with conversion funnel
- Referral program with bonus rewards
- Proactive traffic & expiry alerts
- In-bot support tickets
- Live financial dashboard
- Targeted broadcasts
- Full admin management (zero panel web UI needed)
- Colored-button UI throughout (aiogram 3.x)
- Plan builder, promo codes, gift codes
- Server health monitoring

Author: Senior Backend Developer
License: MIT
"""

# ============================================================================
# SECTION 0: IMPORTS & ENVIRONMENT
# ============================================================================

import os
import sys
import asyncio
import logging
import json
import time
import hashlib
import secrets
import string
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Dict, List, Tuple, Callable
from contextlib import asynccontextmanager

import httpx
import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    User as TgUser, Chat, LabeledPrice, ReplyKeyboardRemove,
)
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot.db")
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "fa")
TRIAL_ENABLED = os.getenv("TRIAL_ENABLED", "true").lower() == "true"
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "3"))
TRIAL_GB = int(os.getenv("TRIAL_GB", "5"))
REFERRAL_BONUS_DAYS = int(os.getenv("REFERRAL_BONUS_DAYS", "5"))
REFERRAL_BONUS_GB = int(os.getenv("REFERRAL_BONUS_GB", "2"))
CURRENCY = os.getenv("CURRENCY", "IRT")  # Iranian Toman
EXPIRY_REMINDER_DAYS = [int(x) for x in os.getenv("EXPIRY_REMINDER_DAYS", "3,1").split(",")]
TRAFFIC_ALERT_THRESHOLD_1 = int(os.getenv("TRAFFIC_ALERT_THRESHOLD_1", "80"))
TRAFFIC_ALERT_THRESHOLD_2 = int(os.getenv("TRAFFIC_ALERT_THRESHOLD_2", "95"))

# Validation
if not BOT_TOKEN:
    print("FATAL: BOT_TOKEN is not set. Please configure .env file.")
    sys.exit(1)
if not ADMIN_IDS:
    print("FATAL: ADMIN_IDS is not set. Please configure .env file.")
    sys.exit(1)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("vpnbot")
logging.getLogger("aiogram").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Constants
GB = 1073741824  # 1 GB in bytes
MS_PER_DAY = 86400000  # milliseconds per day

# ============================================================================
# SECTION 1: DATABASE LAYER
# ============================================================================

class Database:
    """Async SQLite database wrapper for bot state."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
    
    async def connect(self):
        """Initialize database connection and create tables."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()
        await self._db.commit()
        logger.info("Database initialized successfully")
    
    async def disconnect(self):
        if self._db:
            await self._db.close()
            logger.info("Database connection closed")
    
    async def _create_tables(self):
        """Create all required tables."""
        await self._db.executescript("""
            -- Users table: Telegram users of the bot
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                language TEXT DEFAULT 'en',
                balance REAL DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                referred_by INTEGER,
                referral_code TEXT UNIQUE,
                created_at TEXT DEFAULT (datetime('now')),
                last_activity TEXT,
                total_spent REAL DEFAULT 0,
                total_orders INTEGER DEFAULT 0
            );
            
            -- Servers table: 3X-UI panel instances
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alias TEXT NOT NULL,
                panel_url TEXT NOT NULL,
                api_token TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                is_healthy INTEGER DEFAULT 1,
                last_check TEXT,
                last_error TEXT,
                total_clients INTEGER DEFAULT 0,
                total_traffic INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            
            -- Inbounds table: cached inbound configs from panels
            CREATE TABLE IF NOT EXISTS inbounds (
                id INTEGER PRIMARY KEY,
                server_id INTEGER NOT NULL,
                inbound_id INTEGER NOT NULL,
                remark TEXT,
                protocol TEXT,
                port INTEGER,
                enable INTEGER DEFAULT 1,
                tag TEXT,
                is_reality INTEGER DEFAULT 0,
                is_tls INTEGER DEFAULT 0,
                UNIQUE(server_id, inbound_id),
                FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE
            );
            
            -- Plans table: subscription plans
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                traffic_gb INTEGER NOT NULL,
                duration_days INTEGER NOT NULL,
                price REAL NOT NULL,
                limit_ip INTEGER DEFAULT 0,
                inbound_group TEXT DEFAULT 'default',
                is_active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            
            -- Accounts table: VPN accounts linked to users
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_tg_id INTEGER NOT NULL,
                server_id INTEGER NOT NULL,
                email TEXT NOT NULL UNIQUE,
                sub_id TEXT,
                plan_id INTEGER,
                traffic_gb INTEGER,
                expiry_time INTEGER,
                limit_ip INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                is_trial INTEGER DEFAULT 0,
                inbound_ids TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                renewed_at TEXT,
                FOREIGN KEY (user_tg_id) REFERENCES users(tg_id),
                FOREIGN KEY (server_id) REFERENCES servers(id),
                FOREIGN KEY (plan_id) REFERENCES plans(id)
            );
            
            -- Transactions table: payment records
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_tg_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                type TEXT NOT NULL,
                description TEXT,
                account_email TEXT,
                plan_id INTEGER,
                status TEXT DEFAULT 'completed',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_tg_id) REFERENCES users(tg_id)
            );
            
            -- Promo codes table
            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                discount_percent INTEGER DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                max_uses INTEGER DEFAULT 0,
                used_count INTEGER DEFAULT 0,
                expires_at TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
            
            -- Gift codes table: redeem for account or balance
            CREATE TABLE IF NOT EXISTS gift_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                type TEXT NOT NULL,
                value TEXT NOT NULL,
                plan_id INTEGER,
                traffic_gb INTEGER,
                duration_days INTEGER,
                is_used INTEGER DEFAULT 0,
                used_by INTEGER,
                used_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                expires_at TEXT
            );
            
            -- Support tickets
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_tg_id INTEGER NOT NULL,
                subject TEXT,
                status TEXT DEFAULT 'open',
                priority TEXT DEFAULT 'normal',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT,
                closed_at TEXT,
                FOREIGN KEY (user_tg_id) REFERENCES users(tg_id)
            );
            
            -- Ticket messages
            CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                sender TEXT NOT NULL,
                message TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
            );
            
            -- Broadcasts
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                message TEXT,
                target TEXT DEFAULT 'all',
                total_sent INTEGER DEFAULT 0,
                total_failed INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                scheduled_at TEXT
            );
            
            -- Settings: key-value store for bot configuration
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            
            -- Traffic alerts tracking (to avoid duplicate alerts)
            CREATE TABLE IF NOT EXISTS traffic_alerts (
                account_email TEXT,
                threshold INTEGER,
                alerted_at TEXT DEFAULT (datetime('now')),
                UNIQUE(account_email, threshold)
            );
            
            -- Expiry reminders tracking
            CREATE TABLE IF NOT EXISTS expiry_reminders (
                account_email TEXT,
                days_before INTEGER,
                reminded_at TEXT DEFAULT (datetime('now')),
                UNIQUE(account_email, days_before)
            );
            
            -- Referral rewards tracking
            CREATE TABLE IF NOT EXISTS referral_rewards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_tg_id INTEGER,
                referred_tg_id INTEGER,
                account_email TEXT,
                bonus_days INTEGER,
                bonus_gb INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            );
            
            -- Indexes for performance
            CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(user_tg_id);
            CREATE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email);
            CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_tg_id);
            CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_tg_id);
            CREATE INDEX IF NOT EXISTS idx_users_referral ON users(referral_code);
        """)
    
    # --- User operations ---
    
    async def get_or_create_user(self, tg_id: int, username: str = "", 
                                  first_name: str = "", ref_code: str = "") -> dict:
        """Get user or create if not exists. Returns user dict."""
        async with self._db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
        
        if row:
            await self._db.execute(
                "UPDATE users SET last_activity = ?, username = ?, first_name = ? WHERE tg_id = ?",
                (datetime.now().isoformat(), username, first_name, tg_id)
            )
            await self._db.commit()
            return dict(row)
        
        # Generate referral code
        ref_code_generated = self._gen_referral_code(tg_id)
        
        referred_by = None
        if ref_code and ref_code != ref_code_generated:
            async with self._db.execute(
                "SELECT tg_id FROM users WHERE referral_code = ?", (ref_code,)
            ) as cur:
                ref_row = await cur.fetchone()
                if ref_row and ref_row["tg_id"] != tg_id:
                    referred_by = ref_row["tg_id"]
        
        await self._db.execute(
            """INSERT INTO users (tg_id, username, first_name, language, referred_by, referral_code, last_activity)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tg_id, username, first_name, DEFAULT_LANGUAGE, referred_by, ref_code_generated, datetime.now().isoformat())
        )
        await self._db.commit()
        
        async with self._db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            return dict(await cur.fetchone())
    
    def _gen_referral_code(self, tg_id: int) -> str:
        """Generate a unique referral code for a user."""
        chars = string.ascii_uppercase + string.digits
        code = ''.join(random.choices(chars, k=6))
        return f"REF{code}"
    
    async def get_user(self, tg_id: int) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
    
    async def update_user_balance(self, tg_id: int, amount: float, add: bool = True):
        if add:
            await self._db.execute(
                "UPDATE users SET balance = balance + ? WHERE tg_id = ?", (amount, tg_id)
            )
        else:
            await self._db.execute(
                "UPDATE users SET balance = balance - ? WHERE tg_id = ?", (amount, tg_id)
            )
        await self._db.commit()
    
    async def ban_user(self, tg_id: int, banned: bool = True):
        await self._db.execute(
            "UPDATE users SET is_banned = ? WHERE tg_id = ?", (1 if banned else 0, tg_id)
        )
        await self._db.commit()
    
    async def get_all_users(self) -> List[dict]:
        async with self._db.execute("SELECT * FROM users ORDER BY created_at DESC") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    async def count_users(self) -> int:
        async with self._db.execute("SELECT COUNT(*) as cnt FROM users") as cur:
            row = await cur.fetchone()
            return row["cnt"]
    
    async def get_users_by_filter(self, filter_type: str) -> List[int]:
        """Get user tg_ids by filter: all, active, expired, trial, banned."""
        if filter_type == "all":
            async with self._db.execute("SELECT tg_id FROM users WHERE is_banned = 0") as cur:
                rows = await cur.fetchall()
        elif filter_type == "active":
            async with self._db.execute(
                """SELECT DISTINCT u.tg_id FROM users u 
                   JOIN accounts a ON u.tg_id = a.user_tg_id 
                   WHERE a.is_active = 1 AND u.is_banned = 0""") as cur:
                rows = await cur.fetchall()
        elif filter_type == "expired":
            async with self._db.execute(
                """SELECT DISTINCT u.tg_id FROM users u 
                   JOIN accounts a ON u.tg_id = a.user_tg_id 
                   WHERE a.is_active = 0 AND u.is_banned = 0""") as cur:
                rows = await cur.fetchall()
        elif filter_type == "trial":
            async with self._db.execute(
                """SELECT DISTINCT u.tg_id FROM users u 
                   JOIN accounts a ON u.tg_id = a.user_tg_id 
                   WHERE a.is_trial = 1 AND u.is_banned = 0""") as cur:
                rows = await cur.fetchall()
        elif filter_type == "banned":
            async with self._db.execute("SELECT tg_id FROM users WHERE is_banned = 1") as cur:
                rows = await cur.fetchall()
        else:
            rows = []
        return [r["tg_id"] for r in rows]
    
    # --- Server operations ---
    
    async def add_server(self, alias: str, panel_url: str, api_token: str) -> int:
        cur = await self._db.execute(
            "INSERT INTO servers (alias, panel_url, api_token) VALUES (?, ?, ?)",
            (alias, panel_url.rstrip('/'), api_token)
        )
        await self._db.commit()
        return cur.lastrowid
    
    async def get_servers(self, active_only: bool = False) -> List[dict]:
        q = "SELECT * FROM servers"
        if active_only:
            q += " WHERE is_active = 1"
        q += " ORDER BY id"
        async with self._db.execute(q) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    async def get_server(self, server_id: int) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM servers WHERE id = ?", (server_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
    
    async def update_server_health(self, server_id: int, healthy: bool, 
                                    error: str = "", total_clients: int = 0, 
                                    total_traffic: int = 0):
        await self._db.execute(
            """UPDATE servers SET is_healthy = ?, last_check = ?, last_error = ?, 
               total_clients = ?, total_traffic = ? WHERE id = ?""",
            (1 if healthy else 0, datetime.now().isoformat(), error, 
             total_clients, total_traffic, server_id)
        )
        await self._db.commit()
    
    async def toggle_server(self, server_id: int, active: bool):
        await self._db.execute(
            "UPDATE servers SET is_active = ? WHERE id = ?",
            (1 if active else 0, server_id)
        )
        await self._db.commit()
    
    async def delete_server(self, server_id: int):
        await self._db.execute("DELETE FROM servers WHERE id = ?", (server_id,))
        await self._db.commit()
    
    # --- Inbound operations ---
    
    async def sync_inbounds(self, server_id: int, inbounds: List[dict]):
        await self._db.execute("DELETE FROM inbounds WHERE server_id = ?", (server_id,))
        for ib in inbounds:
            await self._db.execute(
                """INSERT INTO inbounds (server_id, inbound_id, remark, protocol, port, enable, tag)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (server_id, ib.get("id", 0), ib.get("remark", ""), 
                 ib.get("protocol", ""), ib.get("port", 0),
                 1 if ib.get("enable", True) else 0, ib.get("tag", ""))
            )
        await self._db.commit()
    
    async def get_inbounds(self, server_id: int) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM inbounds WHERE server_id = ? AND enable = 1 ORDER BY inbound_id",
            (server_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    async def get_all_inbounds_by_group(self, group: str = "default") -> List[Tuple[dict, dict]]:
        """Get (server, inbound) pairs for a given group."""
        servers = await self.get_servers(active_only=True)
        result = []
        for srv in servers:
            inbounds = await self.get_inbounds(srv["id"])
            for ib in inbounds:
                result.append((srv, ib))
        return result
    
    # --- Plan operations ---
    
    async def add_plan(self, name: str, description: str, traffic_gb: int, 
                       duration_days: int, price: float, limit_ip: int = 0,
                       inbound_group: str = "default") -> int:
        cur = await self._db.execute(
            """INSERT INTO plans (name, description, traffic_gb, duration_days, price, limit_ip, inbound_group)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, description, traffic_gb, duration_days, price, limit_ip, inbound_group)
        )
        await self._db.commit()
        return cur.lastrowid
    
    async def get_plans(self, active_only: bool = True) -> List[dict]:
        q = "SELECT * FROM plans"
        if active_only:
            q += " WHERE is_active = 1"
        q += " ORDER BY sort_order, price"
        async with self._db.execute(q) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    async def get_plan(self, plan_id: int) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
    
    async def toggle_plan(self, plan_id: int, active: bool):
        await self._db.execute("UPDATE plans SET is_active = ? WHERE id = ?", (1 if active else 0, plan_id))
        await self._db.commit()
    
    async def delete_plan(self, plan_id: int):
        await self._db.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        await self._db.commit()
    
    async def update_plan(self, plan_id: int, **kwargs):
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [plan_id]
        await self._db.execute(f"UPDATE plans SET {sets} WHERE id = ?", vals)
        await self._db.commit()
    
    # --- Account operations ---
    
    async def add_account(self, user_tg_id: int, server_id: int, email: str, 
                          sub_id: str, plan_id: int, traffic_gb: int, 
                          expiry_time: int, limit_ip: int, inbound_ids: str,
                          is_trial: bool = False) -> int:
        cur = await self._db.execute(
            """INSERT INTO accounts 
               (user_tg_id, server_id, email, sub_id, plan_id, traffic_gb, expiry_time, limit_ip, inbound_ids, is_trial)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_tg_id, server_id, email, sub_id, plan_id, traffic_gb, 
             expiry_time, limit_ip, inbound_ids, 1 if is_trial else 0)
        )
        await self._db.commit()
        return cur.lastrowid
    
    async def get_account(self, email: str) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM accounts WHERE email = ?", (email,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
    
    async def get_account_by_id(self, account_id: int) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
    
    async def get_user_accounts(self, tg_id: int) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM accounts WHERE user_tg_id = ? ORDER BY created_at DESC",
            (tg_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    async def update_account(self, email: str, **kwargs):
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [email]
        await self._db.execute(f"UPDATE accounts SET {sets} WHERE email = ?", vals)
        await self._db.commit()
    
    async def delete_account(self, email: str):
        await self._db.execute("DELETE FROM accounts WHERE email = ?", (email,))
        await self._db.commit()
    
    async def get_all_active_accounts(self) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM accounts WHERE is_active = 1"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    async def get_expiring_accounts(self, days: int) -> List[dict]:
        """Get accounts expiring within X days."""
        future_ts = int((datetime.now() + timedelta(days=days)).timestamp() * 1000)
        now_ts = int(datetime.now().timestamp() * 1000)
        async with self._db.execute(
            """SELECT * FROM accounts WHERE is_active = 1 
               AND expiry_time > 0 AND expiry_time <= ? AND expiry_time > ?""",
            (future_ts, now_ts)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    async def has_used_trial(self, tg_id: int) -> bool:
        async with self._db.execute(
            "SELECT COUNT(*) as cnt FROM accounts WHERE user_tg_id = ? AND is_trial = 1",
            (tg_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] > 0
    
    # --- Transaction operations ---
    
    async def add_transaction(self, user_tg_id: int, amount: float, 
                               type: str, description: str = "",
                               account_email: str = "", plan_id: int = None) -> int:
        cur = await self._db.execute(
            """INSERT INTO transactions (user_tg_id, amount, type, description, account_email, plan_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_tg_id, amount, type, description, account_email, plan_id)
        )
        if type == "purchase":
            await self._db.execute(
                "UPDATE users SET total_spent = total_spent + ?, total_orders = total_orders + 1 WHERE tg_id = ?",
                (amount, user_tg_id)
            )
        await self._db.commit()
        return cur.lastrowid
    
    async def get_user_transactions(self, tg_id: int, limit: int = 10) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM transactions WHERE user_tg_id = ? ORDER BY created_at DESC LIMIT ?",
            (tg_id, limit)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    async def get_revenue_stats(self, days: int = 30) -> dict:
        """Get revenue statistics for the last N days."""
        since = (datetime.now() - timedelta(days=days)).isoformat()
        async with self._db.execute(
            "SELECT SUM(amount) as total, COUNT(*) as count FROM transactions WHERE type = 'purchase' AND created_at >= ?",
            (since,)
        ) as cur:
            row = await cur.fetchone()
        
        # Today's revenue
        today = datetime.now().strftime("%Y-%m-%d")
        async with self._db.execute(
            "SELECT SUM(amount) as total FROM transactions WHERE type = 'purchase' AND created_at LIKE ?",
            (f"{today}%",)
        ) as cur:
            today_row = await cur.fetchone()
        
        # Top plan
        async with self._db.execute(
            """SELECT p.name, COUNT(*) as cnt, SUM(t.amount) as revenue 
               FROM transactions t JOIN plans p ON t.plan_id = p.id 
               WHERE t.type = 'purchase' AND t.created_at >= ?
               GROUP BY p.id ORDER BY revenue DESC LIMIT 5""",
            (since,)
        ) as cur:
            top_plans = [dict(r) for r in await cur.fetchall()]
        
        return {
            "total_revenue": row["total"] or 0,
            "transaction_count": row["count"] or 0,
            "today_revenue": today_row["total"] or 0,
            "top_plans": top_plans,
        }
    
    # --- Promo code operations ---
    
    async def add_promo_code(self, code: str, discount_percent: int = 0, 
                              discount_amount: float = 0, max_uses: int = 0,
                              expires_at: str = None) -> int:
        cur = await self._db.execute(
            """INSERT INTO promo_codes (code, discount_percent, discount_amount, max_uses, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (code.upper(), discount_percent, discount_amount, max_uses, expires_at)
        )
        await self._db.commit()
        return cur.lastrowid
    
    async def validate_promo_code(self, code: str) -> Optional[dict]:
        async with self._db.execute(
            "SELECT * FROM promo_codes WHERE code = ? AND is_active = 1",
            (code.upper(),)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            if row["max_uses"] > 0 and row["used_count"] >= row["max_uses"]:
                return None
            if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) < datetime.now():
                return None
            return dict(row)
    
    async def use_promo_code(self, code: str):
        await self._db.execute(
            "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?",
            (code.upper(),)
        )
        await self._db.commit()
    
    async def get_promo_codes(self) -> List[dict]:
        async with self._db.execute("SELECT * FROM promo_codes ORDER BY created_at DESC") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    async def delete_promo_code(self, code_id: int):
        await self._db.execute("DELETE FROM promo_codes WHERE id = ?", (code_id,))
        await self._db.commit()
    
    # --- Gift code operations ---
    
    async def create_gift_code(self, code: str, type: str, value: str,
                                plan_id: int = None, traffic_gb: int = 0,
                                duration_days: int = 0, expires_at: str = None) -> int:
        cur = await self._db.execute(
            """INSERT INTO gift_codes (code, type, value, plan_id, traffic_gb, duration_days, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (code.upper(), type, value, plan_id, traffic_gb, duration_days, expires_at)
        )
        await self._db.commit()
        return cur.lastrowid
    
    async def get_gift_code(self, code: str) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM gift_codes WHERE code = ?", (code.upper(),)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
    
    async def use_gift_code(self, code: str, user_tg_id: int):
        await self._db.execute(
            "UPDATE gift_codes SET is_used = 1, used_by = ?, used_at = ? WHERE code = ?",
            (user_tg_id, datetime.now().isoformat(), code.upper())
        )
        await self._db.commit()
    
    async def get_gift_codes(self, unused_only: bool = False) -> List[dict]:
        q = "SELECT * FROM gift_codes"
        if unused_only:
            q += " WHERE is_used = 0"
        q += " ORDER BY created_at DESC"
        async with self._db.execute(q) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    # --- Ticket operations ---
    
    async def create_ticket(self, user_tg_id: int, subject: str) -> int:
        cur = await self._db.execute(
            "INSERT INTO tickets (user_tg_id, subject) VALUES (?, ?)",
            (user_tg_id, subject)
        )
        await self._db.commit()
        return cur.lastrowid
    
    async def add_ticket_message(self, ticket_id: int, sender: str, message: str):
        await self._db.execute(
            "INSERT INTO ticket_messages (ticket_id, sender, message) VALUES (?, ?, ?)",
            (ticket_id, sender, message)
        )
        await self._db.execute(
            "UPDATE tickets SET updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), ticket_id)
        )
        await self._db.commit()
    
    async def get_ticket(self, ticket_id: int) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
    
    async def get_ticket_messages(self, ticket_id: int) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY created_at",
            (ticket_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    async def get_user_tickets(self, tg_id: int) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM tickets WHERE user_tg_id = ? ORDER BY created_at DESC",
            (tg_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    async def get_open_tickets(self) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM tickets WHERE status = 'open' ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    async def close_ticket(self, ticket_id: int):
        await self._db.execute(
            "UPDATE tickets SET status = 'closed', closed_at = ? WHERE id = ?",
            (datetime.now().isoformat(), ticket_id)
        )
        await self._db.commit()
    
    async def count_open_tickets(self) -> int:
        async with self._db.execute("SELECT COUNT(*) as cnt FROM tickets WHERE status = 'open'") as cur:
            row = await cur.fetchone()
            return row["cnt"]
    
    # --- Broadcast operations ---
    
    async def create_broadcast(self, admin_id: int, message: str, target: str = "all") -> int:
        cur = await self._db.execute(
            "INSERT INTO broadcasts (admin_id, message, target) VALUES (?, ?, ?)",
            (admin_id, message, target)
        )
        await self._db.commit()
        return cur.lastrowid
    
    async def update_broadcast_stats(self, broadcast_id: int, sent: int, failed: int, status: str = "completed"):
        await self._db.execute(
            "UPDATE broadcasts SET total_sent = ?, total_failed = ?, status = ? WHERE id = ?",
            (sent, failed, status, broadcast_id)
        )
        await self._db.commit()
    
    async def get_broadcasts(self, limit: int = 10) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM broadcasts ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    
    # --- Alert tracking ---
    
    async def has_traffic_alert(self, email: str, threshold: int) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM traffic_alerts WHERE account_email = ? AND threshold = ?",
            (email, threshold)
        ) as cur:
            return await cur.fetchone() is not None
    
    async def add_traffic_alert(self, email: str, threshold: int):
        await self._db.execute(
            "INSERT OR IGNORE INTO traffic_alerts (account_email, threshold) VALUES (?, ?)",
            (email, threshold)
        )
        await self._db.commit()
    
    async def clear_traffic_alerts(self, email: str):
        """Clear alerts when account is renewed."""
        await self._db.execute("DELETE FROM traffic_alerts WHERE account_email = ?", (email,))
        await self._db.commit()
    
    async def has_expiry_reminder(self, email: str, days: int) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM expiry_reminders WHERE account_email = ? AND days_before = ?",
            (email, days)
        ) as cur:
            return await cur.fetchone() is not None
    
    async def add_expiry_reminder(self, email: str, days: int):
        await self._db.execute(
            "INSERT OR IGNORE INTO expiry_reminders (account_email, days_before) VALUES (?, ?)",
            (email, days)
        )
        await self._db.commit()
    
    async def clear_expiry_reminders(self, email: str):
        await self._db.execute("DELETE FROM expiry_reminders WHERE account_email = ?", (email,))
        await self._db.commit()
    
    # --- Referral operations ---
    
    async def add_referral_reward(self, referrer_tg_id: int, referred_tg_id: int,
                                   account_email: str, bonus_days: int, bonus_gb: int):
        await self._db.execute(
            """INSERT INTO referral_rewards 
               (referrer_tg_id, referred_tg_id, account_email, bonus_days, bonus_gb)
               VALUES (?, ?, ?, ?, ?)""",
            (referrer_tg_id, referred_tg_id, account_email, bonus_days, bonus_gb)
        )
        await self._db.commit()
    
    async def get_referral_stats(self, tg_id: int) -> dict:
        async with self._db.execute(
            "SELECT COUNT(*) as cnt FROM referral_rewards WHERE referrer_tg_id = ?",
            (tg_id,)
        ) as cur:
            row = await cur.fetchone()
        async with self._db.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE referred_by = ?",
            (tg_id,)
        ) as cur:
            ref_row = await cur.fetchone()
        return {
            "total_referrals": ref_row["cnt"],
            "completed_referrals": row["cnt"],
        }
    
    # --- Settings ---
    
    async def get_setting(self, key: str, default: str = None) -> Optional[str]:
        async with self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row["value"] if row else default
    
    async def set_setting(self, key: str, value: str):
        await self._db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        await self._db.commit()
    
    async def save_setting(self, key: str, value: str):
        """Alias for set_setting for convenience."""
        await self.set_setting(key, value)
    
    # --- Misc ---
    
    async def search_user(self, query: str) -> List[dict]:
        """Search users by tg_id, username, or email."""
        results = []
        # By tg_id
        if query.isdigit():
            async with self._db.execute("SELECT * FROM users WHERE tg_id = ?", (int(query),)) as cur:
                rows = await cur.fetchall()
                results.extend(dict(r) for r in rows)
        # By username
        async with self._db.execute(
            "SELECT * FROM users WHERE username LIKE ? OR first_name LIKE ?",
            (f"%{query}%", f"%{query}%")
        ) as cur:
            rows = await cur.fetchall()
            results.extend(dict(r) for r in rows)
        # By email (account)
        async with self._db.execute(
            "SELECT u.* FROM users u JOIN accounts a ON u.tg_id = a.user_tg_id WHERE a.email LIKE ?",
            (f"%{query}%",)
        ) as cur:
            rows = await cur.fetchall()
            for r in rows:
                if r not in results:
                    results.append(dict(r))
        return results


# ============================================================================
# SECTION 2: 3X-UI PANEL API CLIENT
# ============================================================================

class PanelAPI:
    """Async client for 3X-UI panel API with multi-panel support."""
    
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            verify=False,  # panels often use self-signed certs
            follow_redirects=True,
        )
    
    async def close(self):
        await self.client.aclose()
    
    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    
    def _url(self, panel_url: str, path: str) -> str:
        return f"{panel_url}{path}"
    
    async def _request(self, method: str, panel_url: str, token: str, 
                       path: str, **kwargs) -> dict:
        """Execute API request and return parsed JSON."""
        url = self._url(panel_url, path)
        headers = self._headers(token)
        try:
            resp = await self.client.request(method, url, headers=headers, **kwargs)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success", False):
                error_msg = data.get("msg", "Unknown API error")
                logger.warning(f"API error from {panel_url}: {error_msg}")
                return {"success": False, "msg": error_msg, "obj": None}
            return data
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP {e.response.status_code} from {panel_url}{path}: {e}")
            return {"success": False, "msg": f"HTTP {e.response.status_code}", "obj": None}
        except httpx.RequestError as e:
            logger.error(f"Request error to {panel_url}{path}: {e}")
            return {"success": False, "msg": f"Connection error: {str(e)[:100]}", "obj": None}
        except Exception as e:
            logger.error(f"Unexpected error to {panel_url}{path}: {e}")
            return {"success": False, "msg": f"Unexpected error: {str(e)[:100]}", "obj": None}
    
    # --- Inbounds ---
    
    async def get_inbounds(self, panel_url: str, token: str) -> List[dict]:
        """GET /panel/api/inbounds/options — lightweight list for pickers."""
        result = await self._request("GET", panel_url, token, "/panel/api/inbounds/options")
        return result.get("obj", []) if result.get("success") else []
    
    async def get_inbound(self, panel_url: str, token: str, inbound_id: int) -> Optional[dict]:
        """GET /panel/api/inbounds/get/{id} — single inbound details."""
        result = await self._request("GET", panel_url, token, f"/panel/api/inbounds/get/{inbound_id}")
        return result.get("obj") if result.get("success") else None
    
    async def get_all_links(self, panel_url: str, token: str) -> str:
        """GET /panel/api/inbounds/allLinks — export all protocol URLs."""
        result = await self._request("GET", panel_url, token, "/panel/api/inbounds/allLinks")
        return result.get("obj", "") if result.get("success") else ""
    
    # --- Clients ---
    
    async def create_client(self, panel_url: str, token: str, 
                            email: str, inbound_ids: List[int],
                            total_gb: int = 0, expiry_time: int = 0,
                            limit_ip: int = 0, tg_id: int = 0,
                            flow: str = "") -> dict:
        """POST /panel/api/clients/add — create a new client."""
        client_data = {
            "email": email,
            "enable": True,
        }
        if total_gb > 0:
            client_data["totalGB"] = total_gb
        if expiry_time > 0:
            client_data["expiryTime"] = expiry_time
        if limit_ip > 0:
            client_data["limitIp"] = limit_ip
        if tg_id > 0:
            client_data["tgId"] = tg_id
        if flow:
            client_data["flow"] = flow
        
        payload = {
            "client": client_data,
            "inboundIds": inbound_ids,
        }
        
        result = await self._request("POST", panel_url, token, "/panel/api/clients/add", json=payload)
        return result
    
    async def get_client(self, panel_url: str, token: str, email: str) -> Optional[dict]:
        """GET /panel/api/clients/get/{email} — full client details."""
        result = await self._request("GET", panel_url, token, f"/panel/api/clients/get/{email}")
        return result.get("obj") if result.get("success") else None
    
    async def get_client_traffic(self, panel_url: str, token: str, email: str) -> Optional[dict]:
        """GET /panel/api/clients/traffic/{email} — traffic counters."""
        result = await self._request("GET", panel_url, token, f"/panel/api/clients/traffic/{email}")
        return result.get("obj") if result.get("success") else None
    
    async def get_client_links(self, panel_url: str, token: str, email: str) -> List[str]:
        """GET /panel/api/clients/links/{email} — protocol URLs."""
        result = await self._request("GET", panel_url, token, f"/panel/api/clients/links/{email}")
        return result.get("obj", []) if result.get("success") else []
    
    async def get_sub_links(self, panel_url: str, token: str, sub_id: str) -> List[str]:
        """GET /panel/api/clients/subLinks/{subId} — subscription links."""
        result = await self._request("GET", panel_url, token, f"/panel/api/clients/subLinks/{sub_id}")
        return result.get("obj", []) if result.get("success") else []
    
    async def update_client(self, panel_url: str, token: str, email: str, 
                            client_data: dict) -> dict:
        """POST /panel/api/clients/update/{email} — update client."""
        result = await self._request("POST", panel_url, token, 
                                      f"/panel/api/clients/update/{email}", json=client_data)
        return result
    
    async def delete_client(self, panel_url: str, token: str, email: str) -> dict:
        """POST /panel/api/clients/del/{email} — delete client."""
        result = await self._request("POST", panel_url, token, f"/panel/api/clients/del/{email}")
        return result
    
    async def reset_client_traffic(self, panel_url: str, token: str, email: str) -> dict:
        """POST /panel/api/clients/resetTraffic/{email} — reset traffic."""
        result = await self._request("POST", panel_url, token, f"/panel/api/clients/resetTraffic/{email}")
        return result
    
    async def enable_client(self, panel_url: str, token: str, email: str) -> dict:
        """Enable a client via update."""
        client = await self.get_client(panel_url, token, email)
        if not client:
            return {"success": False, "msg": "Client not found"}
        client["enable"] = True
        return await self.update_client(panel_url, token, email, client)
    
    async def disable_client(self, panel_url: str, token: str, email: str) -> dict:
        """Disable a client via update."""
        client = await self.get_client(panel_url, token, email)
        if not client:
            return {"success": False, "msg": "Client not found"}
        client["enable"] = False
        return await self.update_client(panel_url, token, email, client)
    
    async def bulk_adjust(self, panel_url: str, token: str, emails: List[str],
                          add_days: int = 0, add_bytes: int = 0) -> dict:
        """POST /panel/api/clients/bulkAdjust — add time/traffic to many users."""
        payload = {"emails": emails}
        if add_days:
            payload["addDays"] = add_days
        if add_bytes:
            payload["addBytes"] = add_bytes
        result = await self._request("POST", panel_url, token, "/panel/api/clients/bulkAdjust", json=payload)
        return result
    
    async def bulk_enable(self, panel_url: str, token: str, emails: List[str]) -> dict:
        result = await self._request("POST", panel_url, token, "/panel/api/clients/bulkEnable", json={"emails": emails})
        return result
    
    async def bulk_disable(self, panel_url: str, token: str, emails: List[str]) -> dict:
        result = await self._request("POST", panel_url, token, "/panel/api/clients/bulkDisable", json={"emails": emails})
        return result
    
    async def bulk_delete(self, panel_url: str, token: str, emails: List[str]) -> dict:
        result = await self._request("POST", panel_url, token, "/panel/api/clients/bulkDel", json={"emails": emails, "keepTraffic": False})
        return result
    
    async def get_online_clients(self, panel_url: str, token: str) -> List[str]:
        """POST /panel/api/clients/onlines — list online client emails."""
        result = await self._request("POST", panel_url, token, "/panel/api/clients/onlines")
        return result.get("obj", []) if result.get("success") else []
    
    async def get_client_ips(self, panel_url: str, token: str, email: str) -> List[str]:
        """POST /panel/api/clients/ips/{email} — list source IPs."""
        result = await self._request("POST", panel_url, token, f"/panel/api/clients/ips/{email}")
        return result.get("obj", []) if result.get("success") else []
    
    async def clear_client_ips(self, panel_url: str, token: str, email: str) -> dict:
        result = await self._request("POST", panel_url, token, f"/panel/api/clients/clearIps/{email}")
        return result
    
    async def get_client_last_online(self, panel_url: str, token: str, emails: List[str]) -> dict:
        """POST /panel/api/clients/lastOnline — map of email → last seen."""
        result = await self._request("POST", panel_url, token, "/panel/api/clients/lastOnline", json={"emails": emails})
        return result.get("obj", {}) if result.get("success") else {}
    
    async def get_clients_paged(self, panel_url: str, token: str, 
                                 page: int = 1, page_size: int = 25,
                                 search: str = "", filter_type: str = "",
                                 sort: str = "expiryTime", order: str = "descend") -> dict:
        """GET /panel/api/clients/list/paged — paginated client list."""
        params = {
            "page": page,
            "pageSize": page_size,
            "sort": sort,
            "order": order,
        }
        if search:
            params["search"] = search
        if filter_type:
            params["filter"] = filter_type
        
        result = await self._request("GET", panel_url, token, "/panel/api/clients/list/paged", params=params)
        return result.get("obj", {}) if result.get("success") else {}
    
    async def attach_client(self, panel_url: str, token: str, email: str, 
                            inbound_ids: List[int]) -> dict:
        """POST /panel/api/clients/{email}/attach — add client to more inbounds."""
        result = await self._request("POST", panel_url, token, 
                                      f"/panel/api/clients/{email}/attach", json={"inboundIds": inbound_ids})
        return result
    
    async def detach_client(self, panel_url: str, token: str, email: str, 
                            inbound_ids: List[int]) -> dict:
        """POST /panel/api/clients/{email}/detach — remove from inbounds."""
        result = await self._request("POST", panel_url, token, 
                                      f"/panel/api/clients/{email}/detach", json={"inboundIds": inbound_ids})
        return result
    
    # --- Groups ---
    
    async def get_groups(self, panel_url: str, token: str) -> List[dict]:
        """GET /panel/api/clients/groups — list all groups."""
        result = await self._request("GET", panel_url, token, "/panel/api/clients/groups")
        return result.get("obj", []) if result.get("success") else []
    
    async def create_group(self, panel_url: str, token: str, name: str) -> dict:
        result = await self._request("POST", panel_url, token, "/panel/api/clients/groups/create", json={"name": name})
        return result
    
    async def delete_group(self, panel_url: str, token: str, name: str) -> dict:
        result = await self._request("POST", panel_url, token, "/panel/api/clients/groups/delete", json={"name": name})
        return result
    
    async def bulk_add_to_group(self, panel_url: str, token: str, 
                                 emails: List[str], group: str) -> dict:
        result = await self._request("POST", panel_url, token, 
                                      "/panel/api/clients/groups/bulkAdd", json={"emails": emails, "group": group})
        return result
    
    async def reset_group_traffic(self, panel_url: str, token: str, group: str) -> dict:
        result = await self._request("POST", panel_url, token, 
                                      "/panel/api/clients/groups/resetTraffic", json={"group": group})
        return result
    
    # --- Backup ---
    
    async def backup_to_telegram(self, panel_url: str, token: str) -> dict:
        """POST /panel/api/backuptotgbot — send DB backup to Telegram."""
        result = await self._request("POST", panel_url, token, "/panel/api/backuptotgbot")
        return result
    
    # --- Settings ---
    
    async def get_panel_settings(self, panel_url: str, token: str) -> dict:
        """POST /panel/api/setting/all — get all panel settings."""
        result = await self._request("POST", panel_url, token, "/panel/api/setting/all")
        return result.get("obj", {}) if result.get("success") else {}
    
    async def restart_panel(self, panel_url: str, token: str) -> dict:
        """POST /panel/api/setting/restartPanel — restart panel."""
        result = await self._request("POST", panel_url, token, "/panel/api/setting/restartPanel")
        return result
    
    async def test_panel_connection(self, panel_url: str, token: str) -> Tuple[bool, str]:
        """Test if panel is reachable and token is valid."""
        result = await self._request("GET", panel_url, token, "/panel/api/inbounds/options")
        if result.get("success"):
            return True, "Connection successful"
        return False, result.get("msg", "Connection failed")
    
    async def get_api_tokens(self, panel_url: str, token: str) -> List[dict]:
        """GET /panel/api/setting/apiTokens — list all API tokens."""
        result = await self._request("GET", panel_url, token, "/panel/api/setting/apiTokens")
        return result.get("obj", []) if result.get("success") else []


# ============================================================================
# SECTION 3: LOAD BALANCER
# ============================================================================

class LoadBalancer:
    """Intelligent server selection based on active client ratio."""
    
    def __init__(self, db: Database, api: PanelAPI):
        self.db = db
        self.api = api
    
    async def select_best_server(self) -> Optional[dict]:
        """Select the server with the least load (fewest active clients)."""
        servers = await self.db.get_servers(active_only=True)
        if not servers:
            return None
        
        healthy_servers = [s for s in servers if s["is_healthy"]]
        if not healthy_servers:
            # Fallback: try all servers
            healthy_servers = servers
        
        best = None
        best_score = float('inf')
        
        for srv in healthy_servers:
            # Get client count from local DB
            local_count = srv.get("total_clients", 0)
            # Also try to get live count from panel
            try:
                online = await self.api.get_online_clients(srv["panel_url"], srv["api_token"])
                online_count = len(online) if isinstance(online, list) else 0
            except:
                online_count = 0
            
            # Score: weighted combination of total and online
            score = local_count * 0.7 + online_count * 10
            
            if score < best_score:
                best_score = score
                best = srv
        
        return best
    
    async def select_inbounds_for_plan(self, server: dict, plan: dict) -> List[int]:
        """Select appropriate inbounds for a plan on a given server."""
        inbounds = await self.db.get_inbounds(server["id"])
        if not inbounds:
            # Fetch from panel if not cached
            panel_inbounds = await self.api.get_inbounds(server["panel_url"], server["api_token"])
            await self.db.sync_inbounds(server["id"], panel_inbounds)
            inbounds = await self.db.get_inbounds(server["id"])
        
        # Return all enabled inbounds (bot admin configures which inbounds to use)
        return [ib["inbound_id"] for ib in inbounds if ib["enable"]]


# ============================================================================
# SECTION 4: FORMATTERS & UTILITIES
# ============================================================================

def fmt_bytes(num_bytes: int) -> str:
    """Format bytes to human-readable string."""
    if num_bytes <= 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} EB"


def fmt_gb(gb: int) -> str:
    """Format GB to human-readable."""
    if gb == 0:
        return "Unlimited"
    if gb >= 1024:
        return f"{gb/1024:.1f} TB"
    return f"{gb} GB"


def fmt_days(days: int) -> str:
    """Format days to human-readable."""
    if days == 0:
        return "Never expires"
    if days >= 365:
        years = days / 365
        return f"{years:.1f} year(s)"
    if days >= 30:
        months = days / 30
        return f"{months:.1f} month(s)"
    return f"{days} day(s)"


def fmt_ts(ts_ms: int) -> str:
    """Format Unix millisecond timestamp to readable date."""
    if ts_ms == 0:
        return "Never"
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def fmt_ts_local(ts_ms: int) -> str:
    """Format Unix millisecond timestamp to local readable date."""
    if ts_ms == 0:
        return "Never"
    dt = datetime.fromtimestamp(ts_ms / 1000)
    return dt.strftime("%Y-%m-%d %H:%M")


def fmt_remaining(expiry_ms: int) -> str:
    """Format time remaining until expiry."""
    if expiry_ms == 0:
        return "∞"
    now_ms = int(datetime.now().timestamp() * 1000)
    diff = expiry_ms - now_ms
    if diff <= 0:
        return "Expired"
    days = diff // MS_PER_DAY
    hours = (diff % MS_PER_DAY) // 3600000
    if days > 0:
        return f"{days}d {hours}h"
    minutes = (diff % 3600000) // 60000
    return f"{hours}h {minutes}m"


def fmt_progress_bar(percentage: float, width: int = 10) -> str:
    """Create a visual progress bar using Unicode blocks."""
    if percentage < 0:
        percentage = 0
    if percentage > 100:
        percentage = 100
    filled = int(width * percentage / 100)
    empty = width - filled
    bar = "█" * filled + "░" * empty
    return f"[{bar}] {percentage:.1f}%"


def gen_email(tg_id: int) -> str:
    """Generate a unique email for a VPN client."""
    timestamp = int(time.time())
    random_part = secrets.token_hex(4)
    return f"tg{tg_id}_{timestamp}_{random_part}"


def gen_gift_code() -> str:
    """Generate a random gift code."""
    chars = string.ascii_uppercase + string.digits
    segments = [''.join(random.choices(chars, k=4)) for _ in range(4)]
    return '-'.join(segments)


def gen_sub_id() -> str:
    """Generate a subscription ID."""
    return secrets.token_hex(8)


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_account_card(account: dict, traffic_data: dict = None, 
                      server_alias: str = "", plan_name: str = "") -> str:
    """Format an account status card as HTML."""
    email = account.get("email", "N/A")
    traffic_gb = account.get("traffic_gb", 0)
    expiry_time = account.get("expiry_time", 0)
    is_active = account.get("is_active", False)
    is_trial = account.get("is_trial", False)
    
    status_emoji = "🟢" if is_active else "🔴"
    trial_badge = " 🎁[TRIAL]" if is_trial else ""
    
    card = f"<b>{status_emoji} VPN Account{trial_badge}</b>\n"
    card += f"<pre>┌──────────────────────────┐\n"
    card += f"│ Email:  {email[:22]:<22} │\n"
    if server_alias:
        card += f"│ Server: {server_alias[:22]:<22} │\n"
    if plan_name:
        card += f"│ Plan:   {plan_name[:22]:<22} │\n"
    card += f"│ Quota:  {fmt_gb(traffic_gb):<22} │\n"
    card += f"│ Exp:    {fmt_remaining(expiry_time):<22} │\n"
    
    if traffic_data:
        up = traffic_data.get("up", 0)
        down = traffic_data.get("down", 0)
        total_bytes = traffic_data.get("total", 0)
        used = up + down
        
        if total_bytes > 0:
            remaining = total_bytes - used
            pct_used = (used / total_bytes) * 100
            pct_remaining = 100 - pct_used
            card += f"│ Used:   {fmt_bytes(used):<22} │\n"
            card += f"│ Left:   {fmt_bytes(remaining):<22} │\n"
            card += f"│ {fmt_progress_bar(pct_used):<24} │\n"
        else:
            card += f"│ Used:   {fmt_bytes(used):<22} │\n"
            card += f"│ Limit:  {'Unlimited':<22} │\n"
    
    card += f"└──────────────────────────┘</pre>"
    return card


def fmt_dashboard_html(stats: dict) -> str:
    """Format admin dashboard as HTML table."""
    today = datetime.now().strftime("%Y-%m-%d")
    
    html = "<b>📊 Admin Dashboard</b>\n"
    html += f"<pre>┌────────────────────────────┐\n"
    html += f"│ Date: {today:<22} │\n"
    html += f"├────────────────────────────┤\n"
    html += f"│ Total Users:    {stats.get('total_users', 0):>10} │\n"
    html += f"│ Active Accounts:{stats.get('active_accounts', 0):>10} │\n"
    html += f"│ Total Accounts: {stats.get('total_accounts', 0):>10} │\n"
    html += f"│ Open Tickets:   {stats.get('open_tickets', 0):>10} │\n"
    html += f"│ Servers Online: {stats.get('servers_online', 0):>10} │\n"
    html += f"├────────────────────────────┤\n"
    html += f"│ Revenue (30d):  ${stats.get('revenue_30d', 0):>9.2f} │\n"
    html += f"│ Revenue (Today):${stats.get('revenue_today', 0):>9.2f} │\n"
    html += f"│ Total Revenue:  ${stats.get('total_revenue', 0):>9.2f} │\n"
    html += f"└────────────────────────────┘</pre>"
    return html


def fmt_server_health(server: dict, online_count: int = 0) -> str:
    """Format server health card."""
    status = "🟢 Healthy" if server["is_healthy"] else "🔴 Unhealthy"
    if not server["is_active"]:
        status = "⚪ Disabled"
    
    html = f"<b>🖥 Server: {escape_html(server['alias'])}</b>\n"
    html += f"<pre>┌────────────────────────────┐\n"
    html += f"│ Status: {status:<21} │\n"
    html += f"│ URL:    {server['panel_url'][:22]:<22} │\n"
    html += f"│ Clients:{server.get('total_clients', 0):>22} │\n"
    html += f"│ Online: {online_count:>22} │\n"
    html += f"│ Traffic:{fmt_bytes(server.get('total_traffic', 0)):>22} │\n"
    if server.get("last_check"):
        dt = datetime.fromisoformat(server["last_check"])
        html += f"│ Check:  {dt.strftime('%Y-%m-%d %H:%M'):<22} │\n"
    if server.get("last_error"):
        html += f"│ Error:  {server['last_error'][:22]:<22} │\n"
    html += f"└────────────────────────────┘</pre>"
    return html


def fmt_plan_card(plan: dict) -> str:
    """Format plan card for display."""
    html = f"<b>📦 {escape_html(plan['name'])}</b>\n"
    html += f"<pre>┌────────────────────────────┐\n"
    html += f"│ Traffic: {fmt_gb(plan['traffic_gb']):<21} │\n"
    html += f"│ Duration:{fmt_days(plan['duration_days']):<21} │\n"
    html += f"│ Price:   ${plan['price']:<22.2f} │\n"
    if plan.get("limit_ip", 0) > 0:
        html += f"│ Max IPs: {plan['limit_ip']:<22} │\n"
    html += f"└────────────────────────────┘</pre>"
    if plan.get("description"):
        html += f"\n<i>{escape_html(plan['description'])}</i>"
    return html


# ============================================================================
# SECTION 5: CALLBACK DATA FACTORIES
# ============================================================================

class MenuCB(CallbackData, prefix="menu"):
    """Main menu navigation."""
    action: str
    data: str = ""


class PlanCB(CallbackData, prefix="plan"):
    """Plan selection."""
    action: str
    plan_id: int


class AccountCB(CallbackData, prefix="acct"):
    """Account operations."""
    action: str
    email: str = ""


class AdminCB(CallbackData, prefix="admin"):
    """Admin panel navigation."""
    action: str
    data: str = ""


class ServerCB(CallbackData, prefix="srv"):
    """Server management."""
    action: str
    server_id: int = 0


class TicketCB(CallbackData, prefix="ticket"):
    """Ticket operations."""
    action: str
    ticket_id: int = 0


class BuyCB(CallbackData, prefix="buy"):
    """Purchase flow."""
    action: str
    plan_id: int = 0
    step: str = ""


class GiftCB(CallbackData, prefix="gift"):
    """Gift code operations."""
    action: str


# ============================================================================
# SECTION 6: KEYBOARDS
# ============================================================================

def kb_main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Main user menu."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🛒 Buy VPN", callback_data=MenuCB(action="buy").pack(), style="primary")
    kb.button(text="📱 My Accounts", callback_data=MenuCB(action="my_accounts").pack())
    kb.button(text="🎁 Free Trial", callback_data=MenuCB(action="trial").pack(), style="success")
    kb.button(text="💳 Balance", callback_data=MenuCB(action="balance").pack())
    kb.button(text="🔗 Referral", callback_data=MenuCB(action="referral").pack())
    kb.button(text="🎫 Gift Code", callback_data=MenuCB(action="gift").pack())
    kb.button(text="💬 Support", callback_data=MenuCB(action="support").pack())
    kb.button(text="📚 Guide", callback_data=MenuCB(action="guide").pack())
    if is_admin:
        kb.button(text="⚙️ Admin Panel", callback_data=AdminCB(action="dashboard").pack(), style="danger")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


def kb_admin_menu() -> InlineKeyboardMarkup:
    """Admin panel main menu."""
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Dashboard", callback_data=AdminCB(action="dashboard").pack(), style="primary")
    kb.button(text="🖥 Servers", callback_data=AdminCB(action="servers").pack())
    kb.button(text="📦 Plans", callback_data=AdminCB(action="plans").pack())
    kb.button(text="👥 Users", callback_data=AdminCB(action="users").pack())
    kb.button(text="💰 Finance", callback_data=AdminCB(action="finance").pack())
    kb.button(text="🎫 Promo Codes", callback_data=AdminCB(action="promos").pack())
    kb.button(text="🎁 Gift Codes", callback_data=AdminCB(action="gift_codes").pack())
    kb.button(text="💬 Tickets", callback_data=AdminCB(action="tickets").pack())
    kb.button(text="📣 Broadcast", callback_data=AdminCB(action="broadcast").pack())
    kb.button(text="⚙️ Settings", callback_data=AdminCB(action="settings").pack())
    kb.button(text="🔙 Back", callback_data=MenuCB(action="main").pack(), style="danger")
    kb.adjust(2, 2, 2, 2, 2, 1)
    return kb.as_markup()


def kb_plans(plans: List[dict]) -> InlineKeyboardMarkup:
    """Plan selection keyboard."""
    kb = InlineKeyboardBuilder()
    for plan in plans:
        kb.button(
            text=f"📦 {plan['name']} — ${plan['price']:.2f}",
            callback_data=PlanCB(action="view", plan_id=plan["id"]).pack(),
            style="primary"
        )
    kb.button(text="🔙 Back", callback_data=MenuCB(action="main").pack(), style="danger")
    kb.adjust(1)
    return kb.as_markup()


def kb_plan_view(plan_id: int) -> InlineKeyboardMarkup:
    """Single plan view with buy button."""
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Buy Now", callback_data=BuyCB(action="start", plan_id=plan_id, step="confirm").pack(), style="success")
    kb.button(text="🔙 Plans", callback_data=MenuCB(action="buy").pack(), style="danger")
    kb.adjust(2)
    return kb.as_markup()


def kb_account_details(email: str, is_active: bool) -> InlineKeyboardMarkup:
    """Account details keyboard."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Renew", callback_data=AccountCB(action="renew", email=email).pack(), style="success")
    kb.button(text="📈 Traffic", callback_data=AccountCB(action="traffic", email=email).pack())
    kb.button(text="🔗 Get Link", callback_data=AccountCB(action="links", email=email).pack(), style="primary")
    if is_active:
        kb.button(text="⛔ Disable", callback_data=AccountCB(action="disable", email=email).pack(), style="danger")
    else:
        kb.button(text="✅ Enable", callback_data=AccountCB(action="enable", email=email).pack(), style="success")
    kb.button(text="📱 QR Code", callback_data=AccountCB(action="qr", email=email).pack())
    kb.button(text="🔙 Back", callback_data=MenuCB(action="my_accounts").pack(), style="danger")
    kb.adjust(2, 2, 2)
    return kb.as_markup()


def kb_accounts_list(accounts: List[dict]) -> InlineKeyboardMarkup:
    """List of user accounts."""
    kb = InlineKeyboardBuilder()
    for acc in accounts:
        status = "🟢" if acc["is_active"] else "🔴"
        email_short = acc["email"][:20]
        kb.button(
            text=f"{status} {email_short}",
            callback_data=AccountCB(action="view", email=acc["email"]).pack()
        )
    kb.button(text="🔙 Back", callback_data=MenuCB(action="main").pack(), style="danger")
    kb.adjust(1)
    return kb.as_markup()


def kb_servers(servers: List[dict]) -> InlineKeyboardMarkup:
    """Server management list."""
    kb = InlineKeyboardBuilder()
    for srv in servers:
        status = "🟢" if srv["is_healthy"] else "🔴"
        if not srv["is_active"]:
            status = "⚪"
        kb.button(
            text=f"{status} {srv['alias']}",
            callback_data=ServerCB(action="view", server_id=srv["id"]).pack()
        )
    kb.button(text="➕ Add Server", callback_data=ServerCB(action="add").pack(), style="success")
    kb.button(text="🔄 Sync All", callback_data=ServerCB(action="sync_all").pack(), style="primary")
    kb.button(text="🔙 Back", callback_data=AdminCB(action="main").pack(), style="danger")
    kb.adjust(1, 2, 1)
    return kb.as_markup()


def kb_server_view(server_id: int) -> InlineKeyboardMarkup:
    """Server detail actions."""
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Stats", callback_data=ServerCB(action="stats", server_id=server_id).pack(), style="primary")
    kb.button(text="🔄 Sync Inbounds", callback_data=ServerCB(action="sync", server_id=server_id).pack())
    kb.button(text="🔄 Restart Panel", callback_data=ServerCB(action="restart", server_id=server_id).pack(), style="danger")
    kb.button(text="💾 Backup", callback_data=ServerCB(action="backup", server_id=server_id).pack())
    kb.button(text="📶 Test Connection", callback_data=ServerCB(action="test", server_id=server_id).pack())
    kb.button(text="🔙 Servers", callback_data=AdminCB(action="servers").pack(), style="danger")
    kb.adjust(2, 2, 2)
    return kb.as_markup()


def kb_admin_plans(plans: List[dict]) -> InlineKeyboardMarkup:
    """Admin plan management."""
    kb = InlineKeyboardBuilder()
    for plan in plans:
        status = "✅" if plan["is_active"] else "❌"
        kb.button(
            text=f"{status} {plan['name']} — ${plan['price']:.2f}",
            callback_data=PlanCB(action="admin_view", plan_id=plan["id"]).pack()
        )
    kb.button(text="➕ Add Plan", callback_data=PlanCB(action="add", plan_id=0).pack(), style="success")
    kb.button(text="🔙 Back", callback_data=AdminCB(action="main").pack(), style="danger")
    kb.adjust(1, 2, 1)
    return kb.as_markup()


def kb_admin_plan_view(plan_id: int) -> InlineKeyboardMarkup:
    """Admin plan detail actions."""
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Edit", callback_data=PlanCB(action="edit", plan_id=plan_id).pack(), style="primary")
    kb.button(text="🗑 Delete", callback_data=PlanCB(action="delete", plan_id=plan_id).pack(), style="danger")
    kb.button(text="🔙 Plans", callback_data=AdminCB(action="plans").pack(), style="danger")
    kb.adjust(2, 1)
    return kb.as_markup()


def kb_tickets(tickets: List[dict]) -> InlineKeyboardMarkup:
    """Ticket list."""
    kb = InlineKeyboardBuilder()
    for t in tickets:
        status = "🟢" if t["status"] == "open" else "🔴"
        kb.button(
            text=f"{status} #{t['id']} - {t['subject'][:20]}",
            callback_data=TicketCB(action="view", ticket_id=t["id"]).pack()
        )
    kb.button(text="🔙 Back", callback_data=AdminCB(action="main").pack(), style="danger")
    kb.adjust(1)
    return kb.as_markup()


def kb_ticket_view(ticket_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    """Ticket detail actions."""
    kb = InlineKeyboardBuilder()
    if is_admin:
        kb.button(text="💬 Reply", callback_data=TicketCB(action="reply", ticket_id=ticket_id).pack(), style="primary")
        kb.button(text="🔒 Close", callback_data=TicketCB(action="close", ticket_id=ticket_id).pack(), style="danger")
    else:
        kb.button(text="💬 Add Message", callback_data=TicketCB(action="reply", ticket_id=ticket_id).pack(), style="primary")
    kb.button(text="🔙 Back", callback_data=TicketCB(action="list", ticket_id=0).pack(), style="danger")
    kb.adjust(2)
    return kb.as_markup()


def kb_cancel() -> InlineKeyboardMarkup:
    """Cancel button for FSM states."""
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Cancel", callback_data=MenuCB(action="cancel").pack(), style="danger")
    return kb.as_markup()


def kb_confirm_purchase(plan_id: int) -> InlineKeyboardMarkup:
    """Purchase confirmation."""
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Confirm & Pay", callback_data=BuyCB(action="confirm", plan_id=plan_id, step="execute").pack(), style="success")
    kb.button(text="🎟 Promo Code", callback_data=BuyCB(action="promo", plan_id=plan_id, step="enter").pack(), style="primary")
    kb.button(text="❌ Cancel", callback_data=MenuCB(action="buy").pack(), style="danger")
    kb.adjust(1, 2)
    return kb.as_markup()


def kb_back_to_menu() -> InlineKeyboardMarkup:
    """Simple back to main menu button."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Main Menu", callback_data=MenuCB(action="main").pack(), style="primary")
    return kb.as_markup()


def kb_broadcast_targets() -> InlineKeyboardMarkup:
    """Broadcast target selection."""
    kb = InlineKeyboardBuilder()
    kb.button(text="👥 All Users", callback_data=AdminCB(action="broadcast_all").pack(), style="primary")
    kb.button(text="🟢 Active", callback_data=AdminCB(action="broadcast_active").pack(), style="success")
    kb.button(text="🔴 Expired", callback_data=AdminCB(action="broadcast_expired").pack(), style="danger")
    kb.button(text="🎁 Trial", callback_data=AdminCB(action="broadcast_trial").pack())
    kb.button(text="🔙 Back", callback_data=AdminCB(action="main").pack(), style="danger")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


# ============================================================================
# SECTION 7: FSM STATES
# ============================================================================

class UserStates(StatesGroup):
    """FSM states for user flows."""
    waiting_for_promo_code = State()
    waiting_for_gift_code = State()
    waiting_for_ticket_subject = State()
    waiting_for_ticket_message = State()
    waiting_for_ticket_reply = State()


class AdminStates(StatesGroup):
    """FSM states for admin flows."""
    # Server management
    waiting_for_server_alias = State()
    waiting_for_server_url = State()
    waiting_for_server_token = State()
    # Plan management
    waiting_for_plan_name = State()
    waiting_for_plan_desc = State()
    waiting_for_plan_traffic = State()
    waiting_for_plan_duration = State()
    waiting_for_plan_price = State()
    waiting_for_plan_limit_ip = State()
    waiting_for_plan_inbounds = State()
    # Broadcast
    waiting_for_broadcast_message = State()
    # User search
    waiting_for_user_search = State()
    # Add balance
    waiting_for_add_balance_amount = State()
    # Ticket reply
    waiting_for_admin_ticket_reply = State()
    # Promo code
    waiting_for_promo_code_str = State()
    waiting_for_promo_discount = State()
    waiting_for_promo_max_uses = State()
    # Trial settings
    waiting_for_trial_days = State()
    waiting_for_trial_gb = State()


# ============================================================================
# SECTION 8: MIDDLEWARE
# ============================================================================

class AuthMiddleware:
    """Middleware for user authentication and ban checking."""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)
        
        # Get or create user
        db_user = await self.db.get_or_create_user(user.id, user.username, user.first_name)
        
        if db_user.get("is_banned"):
            if isinstance(event, Message):
                await event.answer("🚫 You have been banned from using this bot.")
            elif isinstance(event, CallbackQuery):
                await event.answer("🚫 You are banned.", show_alert=True)
            return
        
        data["db_user"] = db_user
        return await handler(event, data)


class AdminMiddleware:
    """Middleware to check if user is admin."""
    
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.id not in ADMIN_IDS:
            if isinstance(event, CallbackQuery):
                await event.answer("⛔ Admin only.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("⛔ This command is for admins only.")
            return
        return await handler(event, data)


# ============================================================================
# SECTION 9: USER HANDLERS
# ============================================================================

def create_user_router(db: Database, api: PanelAPI, lb: LoadBalancer, bot: Bot) -> Router:
    """Create and configure the user router with all user-facing handlers."""
    router = Router()
    
    # --- /start command ---
    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext, db_user: dict):
        await state.clear()
        
        # Check for referral code in deep link
        args = message.text.split(maxsplit=1)
        ref_code = ""
        if len(args) > 1:
            ref_code = args[1].strip()
        
        # Update referral if code provided and user is new
        if ref_code and not db_user.get("referred_by"):
            async with db._db.execute(
                "SELECT tg_id FROM users WHERE referral_code = ?", (ref_code,)
            ) as cur:
                ref_row = await cur.fetchone()
                if ref_row and ref_row["tg_id"] != message.from_user.id:
                    await db._db.execute(
                        "UPDATE users SET referred_by = ? WHERE tg_id = ?",
                        (ref_row["tg_id"], message.from_user.id)
                    )
                    await db._db.commit()
        
        is_admin = message.from_user.id in ADMIN_IDS
        welcome_text = (
            f"👋 <b>Welcome to VPN Bot!</b>\n\n"
            f"🔐 Premium VPN service with instant delivery.\n"
            f"📱 Manage your accounts directly in Telegram.\n\n"
            f"<b>What I can do:</b>\n"
            f"• 🛒 Purchase VPN subscriptions instantly\n"
            f"• 📱 View account status & traffic usage\n"
            f"• 🔄 Renew & extend subscriptions\n"
            f"• 🎁 Get a free trial\n"
            f"• 🔗 Earn rewards via referrals\n"
            f"• 💬 Get support without leaving Telegram\n\n"
            f"Choose an option below 👇"
        )
        await message.answer(welcome_text, reply_markup=kb_main_menu(is_admin))
    
    # --- Main menu navigation ---
    @router.callback_query(MenuCB.filter(F.action == "main"))
    async def cb_main_menu(callback: CallbackQuery, state: FSMContext, db_user: dict):
        await state.clear()
        is_admin = callback.from_user.id in ADMIN_IDS
        await callback.message.edit_text(
            "🏠 <b>Main Menu</b>\n\nWhat would you like to do?",
            reply_markup=kb_main_menu(is_admin)
        )
        await callback.answer()
    
    @router.callback_query(MenuCB.filter(F.action == "cancel"))
    async def cb_cancel(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        is_admin = callback.from_user.id in ADMIN_IDS
        await callback.message.edit_text(
            "❌ Action cancelled.\n\nBack to main menu 👇",
            reply_markup=kb_main_menu(is_admin)
        )
        await callback.answer()
    
    # --- Buy VPN ---
    @router.callback_query(MenuCB.filter(F.action == "buy"))
    async def cb_buy(callback: CallbackQuery):
        plans = await db.get_plans(active_only=True)
        if not plans:
            await callback.message.edit_text(
                "😔 No plans available yet. Please check back later or contact support.",
                reply_markup=kb_back_to_menu()
            )
            await callback.answer()
            return
        
        text = "🛒 <b>Choose a Plan</b>\n\nSelect a subscription plan below:"
        await callback.message.edit_text(text, reply_markup=kb_plans(plans))
        await callback.answer()
    
    @router.callback_query(PlanCB.filter(F.action == "view"))
    async def cb_plan_view(callback: CallbackQuery, callback_data: PlanCB):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer("Plan not found.", show_alert=True)
            return
        
        text = fmt_plan_card(plan)
        await callback.message.edit_text(text, reply_markup=kb_plan_view(plan["id"]))
        await callback.answer()
    
    @router.callback_query(BuyCB.filter(F.action == "start"))
    async def cb_buy_start(callback: CallbackQuery, callback_data: BuyCB, db_user: dict):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer("Plan not found.", show_alert=True)
            return
        
        balance = db_user.get("balance", 0)
        text = fmt_plan_card(plan)
        text += f"\n\n<b>💳 Your Balance:</b> ${balance:.2f}\n"
        
        if balance >= plan["price"]:
            text += "✅ You have sufficient balance to purchase."
        else:
            text += f"⚠️ Insufficient balance. You need ${plan['price'] - balance:.2f} more.\n"
            text += "\n<i>Contact admin to top up your balance, or use a gift code.</i>"
        
        await callback.message.edit_text(text, reply_markup=kb_confirm_purchase(plan["id"]))
        await callback.answer()
    
    @router.callback_query(BuyCB.filter(F.action == "promo"))
    async def cb_buy_promo(callback: CallbackQuery, state: FSMContext, callback_data: BuyCB):
        await state.set_state(UserStates.waiting_for_promo_code)
        await state.update_data(plan_id=callback_data.plan_id)
        await callback.message.edit_text(
            "🎟 <b>Enter Promo Code</b>\n\nSend me your promo code:",
            reply_markup=kb_cancel()
        )
        await callback.answer()
    
    @router.message(UserStates.waiting_for_promo_code)
    async def ms_promo_code(message: Message, state: FSMContext, db_user: dict):
        code = message.text.strip().upper()
        promo = await db.validate_promo_code(code)
        
        if not promo:
            await message.answer(
                "❌ Invalid or expired promo code.\n\nTry again or cancel:",
                reply_markup=kb_cancel()
            )
            return
        
        data = await state.get_data()
        plan_id = data.get("plan_id")
        plan = await db.get_plan(plan_id)
        
        discount = 0
        if promo["discount_percent"] > 0:
            discount = plan["price"] * promo["discount_percent"] / 100
        elif promo["discount_amount"] > 0:
            discount = promo["discount_amount"]
        
        final_price = max(0, plan["price"] - discount)
        
        await state.update_data(promo_code=code, final_price=final_price)
        await state.clear()
        
        text = fmt_plan_card(plan)
        text += f"\n\n🎟 <b>Promo Applied:</b> {code}\n"
        text += f"💰 <b>Discount:</b> ${discount:.2f}\n"
        text += f"💳 <b>Final Price:</b> ${final_price:.2f}\n"
        text += f"💵 <b>Your Balance:</b> ${db_user.get('balance', 0):.2f}"
        
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Confirm & Pay", callback_data=BuyCB(action="confirm", plan_id=plan_id, step="execute_promo").pack(), style="success")
        kb.button(text="❌ Cancel", callback_data=MenuCB(action="buy").pack(), style="danger")
        kb.adjust(1, 1)
        
        await message.answer(text, reply_markup=kb.as_markup())
    
    @router.callback_query(BuyCB.filter(F.action == "confirm"))
    async def cb_buy_confirm(callback: CallbackQuery, callback_data: BuyCB, state: FSMContext, db_user: dict):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer("Plan not found.", show_alert=True)
            return
        
        # Check if promo was applied
        data = await state.get_data()
        final_price = data.get("final_price", plan["price"])
        promo_code = data.get("promo_code")
        await state.clear()
        
        balance = db_user.get("balance", 0)
        if balance < final_price:
            await callback.answer(
                f"Insufficient balance. Need ${final_price - balance:.2f} more.",
                show_alert=True
            )
            return
        
        # Execute purchase
        await callback.message.edit_text("⏳ Creating your VPN account...")
        
        # Select best server
        server = await lb.select_best_server()
        if not server:
            await callback.message.edit_text(
                "❌ No servers available. Please contact support.",
                reply_markup=kb_back_to_menu()
            )
            return
        
        # Select inbounds
        inbound_ids = await lb.select_inbounds_for_plan(server, plan)
        if not inbound_ids:
            await callback.message.edit_text(
                "❌ No inbounds configured on the server. Please contact support.",
                reply_markup=kb_back_to_menu()
            )
            return
        
        # Generate client details
        email = gen_email(callback.from_user.id)
        expiry_time = int((datetime.now() + timedelta(days=plan["duration_days"])).timestamp() * 1000) if plan["duration_days"] > 0 else 0
        
        # Create client on panel
        result = await api.create_client(
            panel_url=server["panel_url"],
            token=server["api_token"],
            email=email,
            inbound_ids=inbound_ids,
            total_gb=plan["traffic_gb"],
            expiry_time=expiry_time,
            limit_ip=plan.get("limit_ip", 0),
            tg_id=callback.from_user.id,
        )
        
        if not result.get("success"):
            await callback.message.edit_text(
                f"❌ Failed to create account: {result.get('msg', 'Unknown error')}\n\nPlease try again or contact support.",
                reply_markup=kb_back_to_menu()
            )
            return
        
        # Get client details for sub_id
        client_data = await api.get_client(server["panel_url"], server["api_token"], email)
        sub_id = client_data.get("subId", "") if client_data else ""
        
        # Save to database
        await db.add_account(
            user_tg_id=callback.from_user.id,
            server_id=server["id"],
            email=email,
            sub_id=sub_id,
            plan_id=plan["id"],
            traffic_gb=plan["traffic_gb"],
            expiry_time=expiry_time,
            limit_ip=plan.get("limit_ip", 0),
            inbound_ids=json.dumps(inbound_ids),
            is_trial=False
        )
        
        # Deduct balance
        await db.update_user_balance(callback.from_user.id, final_price, add=False)
        
        # Use promo code if applied
        if promo_code:
            await db.use_promo_code(promo_code)
        
        # Record transaction
        await db.add_transaction(
            user_tg_id=callback.from_user.id,
            amount=final_price,
            type="purchase",
            description=f"Plan: {plan['name']}",
            account_email=email,
            plan_id=plan["id"]
        )
        
        # Clear any previous alerts for this account (new account)
        await db.clear_traffic_alerts(email)
        await db.clear_expiry_reminders(email)
        
        # Handle referral reward
        if db_user.get("referred_by") and not db_user.get("referral_rewarded", False):
            referrer_id = db_user["referred_by"]
            if REFERRAL_BONUS_DAYS > 0 or REFERRAL_BONUS_GB > 0:
                # Add bonus to referrer's most recent active account
                referrer_accounts = await db.get_user_accounts(referrer_id)
                active_accounts = [a for a in referrer_accounts if a["is_active"]]
                if active_accounts:
                    ref_acc = active_accounts[0]
                    ref_server = await db.get_server(ref_acc["server_id"])
                    if ref_server:
                        bonus_bytes = REFERRAL_BONUS_GB * GB if REFERRAL_BONUS_GB > 0 else 0
                        await api.bulk_adjust(
                            ref_server["panel_url"], ref_server["api_token"],
                            [ref_acc["email"]],
                            add_days=REFERRAL_BONUS_DAYS,
                            add_bytes=bonus_bytes
                        )
                        await db.add_referral_reward(
                            referrer_tg_id=referrer_id,
                            referred_tg_id=callback.from_user.id,
                            account_email=ref_acc["email"],
                            bonus_days=REFERRAL_BONUS_DAYS,
                            bonus_gb=REFERRAL_BONUS_GB
                        )
                        # Notify referrer
                        try:
                            await bot.send_message(
                                referrer_id,
                                f"🎉 <b>Referral Bonus!</b>\n\n"
                                f"Your referral just purchased a VPN plan!\n"
                                f"🎁 You received: {REFERRAL_BONUS_DAYS} days + {REFERRAL_BONUS_GB} GB bonus\n"
                                f"📱 Applied to: <code>{ref_acc['email']}</code>"
                            )
                        except:
                            pass
        
        # Get connection links
        links = await api.get_client_links(server["panel_url"], server["api_token"], email)
        
        # Build delivery message
        delivery_text = (
            f"✅ <b>Account Created Successfully!</b>\n\n"
            f"{fmt_account_card({
                'email': email,
                'traffic_gb': plan['traffic_gb'],
                'expiry_time': expiry_time,
                'is_active': True,
                'is_trial': False
            }, server_alias=server['alias'], plan_name=plan['name'])}\n\n"
        )
        
        if links:
            delivery_text += f"<b>🔗 Connection Links:</b>\n"
            for i, link in enumerate(links[:3], 1):
                delivery_text += f"<code>{escape_html(link)}</code>\n"
            
            if sub_id:
                sub_url = f"{server['panel_url']}/sub/{sub_id}"
                delivery_text += f"\n<b>📡 Subscription URL:</b>\n<code>{escape_html(sub_url)}</code>\n"
        
        delivery_text += (
            f"\n<b>📱 How to use:</b>\n"
            f"1. Download v2rayNG (Android) or Streisand (iOS)\n"
            f"2. Copy the subscription URL above\n"
            f"3. Add subscription in the app\n"
            f"4. Connect and enjoy! 🚀\n\n"
            f"💡 You can manage this account anytime via 'My Accounts'"
        )
        
        kb = InlineKeyboardBuilder()
        kb.button(text="📱 My Accounts", callback_data=MenuCB(action="my_accounts").pack(), style="primary")
        kb.button(text="🏠 Main Menu", callback_data=MenuCB(action="main").pack())
        kb.adjust(2)
        
        await callback.message.edit_text(delivery_text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
        await callback.answer("✅ Purchase successful!")
    
    # --- My Accounts ---
    @router.callback_query(MenuCB.filter(F.action == "my_accounts"))
    async def cb_my_accounts(callback: CallbackQuery, db_user: dict):
        accounts = await db.get_user_accounts(callback.from_user.id)
        if not accounts:
            await callback.message.edit_text(
                "📱 <b>My Accounts</b>\n\nYou don't have any VPN accounts yet.\n\n"
                "🛒 Click below to purchase your first plan!",
                reply_markup=kb_back_to_menu()
            )
            await callback.answer()
            return
        
        text = "📱 <b>My Accounts</b>\n\nSelect an account to view details:"
        await callback.message.edit_text(text, reply_markup=kb_accounts_list(accounts))
        await callback.answer()
    
    @router.callback_query(AccountCB.filter(F.action == "view"))
    async def cb_account_view(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer("Account not found.", show_alert=True)
            return
        
        server = await db.get_server(account["server_id"])
        plan = await db.get_plan(account["plan_id"]) if account.get("plan_id") else None
        
        # Fetch live traffic data
        traffic_data = None
        if server:
            traffic_data = await api.get_client_traffic(server["panel_url"], server["api_token"], account["email"])
        
        text = fmt_account_card(
            account,
            traffic_data,
            server_alias=server["alias"] if server else "Unknown",
            plan_name=plan["name"] if plan else "N/A"
        )
        
        await callback.message.edit_text(text, reply_markup=kb_account_details(account["email"], account["is_active"]))
        await callback.answer()
    
    @router.callback_query(AccountCB.filter(F.action == "links"))
    async def cb_account_links(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer("Account not found.", show_alert=True)
            return
        
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer("Server not found.", show_alert=True)
            return
        
        links = await api.get_client_links(server["panel_url"], server["api_token"], account["email"])
        
        if not links:
            await callback.answer("No links available.", show_alert=True)
            return
        
        text = f"🔗 <b>Connection Links for {escape_html(account['email'])}</b>\n\n"
        for i, link in enumerate(links, 1):
            text += f"<b>Link {i}:</b>\n<code>{escape_html(link)}</code>\n\n"
        
        if account.get("sub_id"):
            sub_url = f"{server['panel_url']}/sub/{account['sub_id']}"
            text += f"<b>📡 Subscription URL:</b>\n<code>{escape_html(sub_url)}</code>\n"
        
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Back", callback_data=AccountCB(action="view", email=account["email"]).pack(), style="primary")
        
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
        await callback.answer()
    
    @router.callback_query(AccountCB.filter(F.action == "traffic"))
    async def cb_account_traffic(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer("Account not found.", show_alert=True)
            return
        
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer("Server not found.", show_alert=True)
            return
        
        traffic = await api.get_client_traffic(server["panel_url"], server["api_token"], account["email"])
        
        if not traffic:
            await callback.answer("Could not fetch traffic data.", show_alert=True)
            return
        
        up = traffic.get("up", 0)
        down = traffic.get("down", 0)
        total = traffic.get("total", 0)
        used = up + down
        
        text = f"📈 <b>Traffic Details</b>\n\n"
        text += f"<pre>┌──────────────────────────┐\n"
        text += f"│ Account: {account['email'][:18]:<18} │\n"
        text += f"│ Upload:  {fmt_bytes(up):<21} │\n"
        text += f"│ Download:{fmt_bytes(down):<21} │\n"
        text += f"│ Total:   {fmt_bytes(used):<21} │\n"
        
        if total > 0:
            remaining = total - used
            pct = (used / total) * 100
            text += f"│ Limit:   {fmt_bytes(total):<21} │\n"
            text += f"│ Remain:  {fmt_bytes(remaining):<21} │\n"
            text += f"│ {fmt_progress_bar(pct):<24} │\n"
        else:
            text += f"│ Limit:   Unlimited{'':<13} │\n"
        
        # Get online status
        online_clients = await api.get_online_clients(server["panel_url"], server["api_token"])
        is_online = account["email"] in online_clients if isinstance(online_clients, list) else False
        text += f"│ Online:  {'Yes' if is_online else 'No':<21} │\n"
        
        # Get IP count
        ips = await api.get_client_ips(server["panel_url"], server["api_token"], account["email"])
        text += f"│ Active IPs: {str(len(ips)):<19} │\n"
        
        text += f"└──────────────────────────┘</pre>"
        
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Back", callback_data=AccountCB(action="view", email=account["email"]).pack(), style="primary")
        
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()
    
    @router.callback_query(AccountCB.filter(F.action == "renew"))
    async def cb_account_renew(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer("Account not found.", show_alert=True)
            return
        
        plan = await db.get_plan(account["plan_id"]) if account.get("plan_id") else None
        if not plan:
            await callback.answer("Original plan not found. Please buy a new plan.", show_alert=True)
            return
        
        balance = db_user.get("balance", 0)
        
        text = fmt_plan_card(plan)
        text += f"\n<b>🔄 Renewing Account:</b> <code>{escape_html(account['email'])}</code>\n"
        text += f"<b>💳 Your Balance:</b> ${balance:.2f}\n"
        
        if balance < plan["price"]:
            text += f"\n⚠️ Insufficient balance. Need ${plan['price'] - balance:.2f} more."
        
        kb = InlineKeyboardBuilder()
        if balance >= plan["price"]:
            kb.button(text="✅ Confirm Renewal", callback_data=AccountCB(action="renew_confirm", email=account["email"]).pack(), style="success")
        kb.button(text="🔙 Back", callback_data=AccountCB(action="view", email=account["email"]).pack(), style="danger")
        kb.adjust(1)
        
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()
    
    @router.callback_query(AccountCB.filter(F.action == "renew_confirm"))
    async def cb_account_renew_confirm(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer("Account not found.", show_alert=True)
            return
        
        plan = await db.get_plan(account["plan_id"])
        if not plan:
            await callback.answer("Plan not found.", show_alert=True)
            return
        
        balance = db_user.get("balance", 0)
        if balance < plan["price"]:
            await callback.answer("Insufficient balance.", show_alert=True)
            return
        
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer("Server not found.", show_alert=True)
            return
        
        # Extend on panel using bulkAdjust
        add_bytes = plan["traffic_gb"] * GB if plan["traffic_gb"] > 0 else 0
        result = await api.bulk_adjust(
            server["panel_url"], server["api_token"],
            [account["email"]],
            add_days=plan["duration_days"],
            add_bytes=add_bytes
        )
        
        if not result.get("success"):
            await callback.answer(f"Failed: {result.get('msg')}", show_alert=True)
            return
        
        # Calculate new expiry
        current_expiry = account["expiry_time"]
        now_ms = int(datetime.now().timestamp() * 1000)
        if current_expiry > now_ms:
            new_expiry = current_expiry + plan["duration_days"] * MS_PER_DAY
        else:
            new_expiry = now_ms + plan["duration_days"] * MS_PER_DAY
        
        # Update DB
        new_traffic = account["traffic_gb"] + plan["traffic_gb"] if account["traffic_gb"] > 0 else 0
        await db.update_account(
            account["email"],
            expiry_time=new_expiry,
            traffic_gb=new_traffic,
            is_active=True,
            renewed_at=datetime.now().isoformat()
        )
        
        # Clear alerts
        await db.clear_traffic_alerts(account["email"])
        await db.clear_expiry_reminders(account["email"])
        
        # Deduct balance
        await db.update_user_balance(callback.from_user.id, plan["price"], add=False)
        
        # Record transaction
        await db.add_transaction(
            user_tg_id=callback.from_user.id,
            amount=plan["price"],
            type="renewal",
            description=f"Renewed: {plan['name']}",
            account_email=account["email"],
            plan_id=plan["id"]
        )
        
        text = (
            f"✅ <b>Account Renewed Successfully!</b>\n\n"
            f"📱 Account: <code>{escape_html(account['email'])}</code>\n"
            f"📦 Plan: {plan['name']}\n"
            f"📅 New Expiry: {fmt_ts_local(new_expiry)}\n"
            f"💾 New Quota: {fmt_gb(new_traffic)}\n"
            f"💳 Charged: ${plan['price']:.2f}\n"
            f"💵 Remaining Balance: ${balance - plan['price']:.2f}"
        )
        
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Account Details", callback_data=AccountCB(action="view", email=account["email"]).pack(), style="primary")
        kb.button(text="🏠 Main Menu", callback_data=MenuCB(action="main").pack())
        kb.adjust(2)
        
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer("✅ Renewal successful!")
    
    @router.callback_query(AccountCB.filter(F.action == "disable"))
    async def cb_account_disable(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer("Account not found.", show_alert=True)
            return
        
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer("Server not found.", show_alert=True)
            return
        
        result = await api.disable_client(server["panel_url"], server["api_token"], account["email"])
        if result.get("success"):
            await db.update_account(account["email"], is_active=False)
            await callback.answer("✅ Account disabled.")
        else:
            await callback.answer(f"Failed: {result.get('msg')}", show_alert=True)
            return
        
        await callback.message.edit_text(
            f"⛔ Account <code>{escape_html(account['email'])}</code> has been disabled.\n\n"
            f"You can re-enable it anytime.",
            reply_markup=kb_account_details(account["email"], False)
        )
    
    @router.callback_query(AccountCB.filter(F.action == "enable"))
    async def cb_account_enable(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer("Account not found.", show_alert=True)
            return
        
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer("Server not found.", show_alert=True)
            return
        
        result = await api.enable_client(server["panel_url"], server["api_token"], account["email"])
        if result.get("success"):
            await db.update_account(account["email"], is_active=True)
            await callback.answer("✅ Account enabled.")
        else:
            await callback.answer(f"Failed: {result.get('msg')}", show_alert=True)
            return
        
        await callback.message.edit_text(
            f"✅ Account <code>{escape_html(account['email'])}</code> has been enabled.\n\n"
            f"You can now connect to the VPN.",
            reply_markup=kb_account_details(account["email"], True)
        )
    
    # --- Free Trial ---
    @router.callback_query(MenuCB.filter(F.action == "trial"))
    async def cb_trial(callback: CallbackQuery, db_user: dict):
        if not TRIAL_ENABLED:
            await callback.message.edit_text(
                "😔 Free trials are currently disabled.\n\nPlease check back later.",
                reply_markup=kb_back_to_menu()
            )
            await callback.answer()
            return
        
        # Check if user already used trial
        has_trial = await db.has_used_trial(callback.from_user.id)
        if has_trial:
            await callback.message.edit_text(
                "🎁 <b>Free Trial</b>\n\n"
                "You have already used your free trial.\n"
                "Each user is limited to one trial.\n\n"
                "🛒 Check out our affordable plans instead!",
                reply_markup=kb_back_to_menu()
            )
            await callback.answer()
            return
        
        # Check if servers are available
        server = await lb.select_best_server()
        if not server:
            await callback.message.edit_text(
                "❌ No servers available for trial. Please try again later.",
                reply_markup=kb_back_to_menu()
            )
            await callback.answer()
            return
        
        text = (
            f"🎁 <b>Free Trial Offer</b>\n\n"
            f"<pre>┌──────────────────────────┐\n"
            f"│ Duration: {fmt_days(TRIAL_DAYS):<21} │\n"
            f"│ Traffic:  {fmt_gb(TRIAL_GB):<21} │\n"
            f"│ Cost:     {'FREE':<21} │\n"
            f"└──────────────────────────┘</pre>\n\n"
            f"✨ Experience our premium VPN service for free!\n"
            f"⏰ Limited to one trial per user.\n"
            f"📈 Upgrade anytime to a paid plan."
        )
        
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Get Free Trial", callback_data=MenuCB(action="trial_activate").pack(), style="success")
        kb.button(text="🔙 Back", callback_data=MenuCB(action="main").pack(), style="danger")
        kb.adjust(1)
        
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()
    
    @router.callback_query(MenuCB.filter(F.action == "trial_activate"))
    async def cb_trial_activate(callback: CallbackQuery, db_user: dict):
        has_trial = await db.has_used_trial(callback.from_user.id)
        if has_trial:
            await callback.answer("You already used your trial.", show_alert=True)
            return
        
        await callback.message.edit_text("⏳ Creating your trial account...")
        
        server = await lb.select_best_server()
        if not server:
            await callback.message.edit_text(
                "❌ No servers available. Please try again later.",
                reply_markup=kb_back_to_menu()
            )
            return
        
        inbound_ids = await lb.select_inbounds_for_plan(server, {})
        if not inbound_ids:
            await callback.message.edit_text(
                "❌ No inbounds available. Please contact support.",
                reply_markup=kb_back_to_menu()
            )
            return
        
        email = gen_email(callback.from_user.id)
        expiry_time = int((datetime.now() + timedelta(days=TRIAL_DAYS)).timestamp() * 1000)
        
        result = await api.create_client(
            panel_url=server["panel_url"],
            token=server["api_token"],
            email=email,
            inbound_ids=inbound_ids,
            total_gb=TRIAL_GB,
            expiry_time=expiry_time,
            tg_id=callback.from_user.id,
        )
        
        if not result.get("success"):
            await callback.message.edit_text(
                f"❌ Failed to create trial: {result.get('msg')}\nPlease try again.",
                reply_markup=kb_back_to_menu()
            )
            return
        
        client_data = await api.get_client(server["panel_url"], server["api_token"], email)
        sub_id = client_data.get("subId", "") if client_data else ""
        
        await db.add_account(
            user_tg_id=callback.from_user.id,
            server_id=server["id"],
            email=email,
            sub_id=sub_id,
            plan_id=None,
            traffic_gb=TRIAL_GB,
            expiry_time=expiry_time,
            limit_ip=1,
            inbound_ids=json.dumps(inbound_ids),
            is_trial=True
        )
        
        await db.add_transaction(
            user_tg_id=callback.from_user.id,
            amount=0,
            type="trial",
            description=f"Free Trial: {TRIAL_DAYS}d/{TRIAL_GB}GB",
            account_email=email
        )
        
        links = await api.get_client_links(server["panel_url"], server["api_token"], email)
        
        text = (
            f"🎉 <b>Trial Account Created!</b>\n\n"
            f"{fmt_account_card({
                'email': email,
                'traffic_gb': TRIAL_GB,
                'expiry_time': expiry_time,
                'is_active': True,
                'is_trial': True
            }, server_alias=server['alias'], plan_name='Trial')}\n\n"
        )
        
        if links:
            text += f"<b>🔗 Connection Links:</b>\n"
            for link in links[:3]:
                text += f"<code>{escape_html(link)}</code>\n"
            
            if sub_id:
                sub_url = f"{server['panel_url']}/sub/{sub_id}"
                text += f"\n<b>📡 Subscription URL:</b>\n<code>{escape_html(sub_url)}</code>\n"
        
        text += (
            f"\n<b>📱 How to connect:</b>\n"
            f"1. Install v2rayNG (Android) or Streisand (iOS)\n"
            f"2. Add the subscription URL\n"
            f"3. Connect and enjoy!\n\n"
            f"⏰ Your trial expires in {TRIAL_DAYS} days.\n"
            f"🛒 Upgrade to a paid plan before it expires for uninterrupted service!"
        )
        
        kb = InlineKeyboardBuilder()
        kb.button(text="🛒 View Plans", callback_data=MenuCB(action="buy").pack(), style="success")
        kb.button(text="🏠 Main Menu", callback_data=MenuCB(action="main").pack())
        kb.adjust(2)
        
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
        await callback.answer("✅ Trial created!")
    
    # --- Balance ---
    @router.callback_query(MenuCB.filter(F.action == "balance"))
    async def cb_balance(callback: CallbackQuery, db_user: dict):
        balance = db_user.get("balance", 0)
        transactions = await db.get_user_transactions(callback.from_user.id, limit=5)
        
        text = (
            f"💳 <b>Your Balance</b>\n\n"
            f"<pre>┌──────────────────────────┐\n"
            f"│ Balance: ${balance:<22.2f} │\n"
            f"│ Orders:  {db_user.get('total_orders', 0):<22} │\n"
            f"│ Spent:   ${db_user.get('total_spent', 0):<21.2f} │\n"
            f"└──────────────────────────┘</pre>\n"
        )
        
        if transactions:
            text += "\n<b>📋 Recent Transactions:</b>\n"
            for t in transactions:
                emoji = "💰" if t["type"] in ("deposit", "purchase") else "💸"
                if t["type"] == "trial":
                    emoji = "🎁"
                date = t["created_at"][:16]
                text += f"{emoji} {date} | ${t['amount']:.2f} | {t['description'][:30]}\n"
        
        text += "\n💡 <i>Contact admin to top up your balance.</i>"
        
        await callback.message.edit_text(text, reply_markup=kb_back_to_menu())
        await callback.answer()
    
    # --- Referral ---
    @router.callback_query(MenuCB.filter(F.action == "referral"))
    async def cb_referral(callback: CallbackQuery, db_user: dict):
        ref_code = db_user.get("referral_code", "")
        stats = await db.get_referral_stats(callback.from_user.id)
        
        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={ref_code}"
        
        text = (
            f"🔗 <b>Referral Program</b>\n\n"
            f"Invite friends and earn rewards!\n\n"
            f"<pre>┌──────────────────────────┐\n"
            f"│ Total Referrals:  {stats['total_referrals']:>8} │\n"
            f"│ Completed:        {stats['completed_referrals']:>8} │\n"
            f"└──────────────────────────┘</pre>\n\n"
            f"🎁 <b>Reward per referral:</b>\n"
            f"• {REFERRAL_BONUS_DAYS} days extension\n"
            f"• {REFERRAL_BONUS_GB} GB bonus traffic\n\n"
            f"📤 <b>Your referral link:</b>\n"
            f"<code>{ref_link}</code>\n\n"
            f"Share this link with friends. When they purchase a plan, you get the bonus automatically!"
        )
        
        await callback.message.edit_text(text, reply_markup=kb_back_to_menu())
        await callback.answer()
    
    # --- Gift Code ---
    @router.callback_query(MenuCB.filter(F.action == "gift"))
    async def cb_gift(callback: CallbackQuery, state: FSMContext):
        await state.set_state(UserStates.waiting_for_gift_code)
        await callback.message.edit_text(
            "🎫 <b>Redeem Gift Code</b>\n\nSend me your gift code:",
            reply_markup=kb_cancel()
        )
        await callback.answer()
    
    @router.message(UserStates.waiting_for_gift_code)
    async def ms_gift_code(message: Message, state: FSMContext, db_user: dict):
        code = message.text.strip().upper()
        gift = await db.get_gift_code(code)
        
        if not gift:
            await message.answer("❌ Invalid gift code. Try again:", reply_markup=kb_cancel())
            return
        
        if gift["is_used"]:
            await message.answer("❌ This code has already been used.", reply_markup=kb_cancel())
            await state.clear()
            return
        
        await state.clear()
        
        if gift["type"] == "balance":
            amount = float(gift["value"])
            await db.update_user_balance(message.from_user.id, amount, add=True)
            await db.add_transaction(
                user_tg_id=message.from_user.id,
                amount=amount,
                type="gift_balance",
                description=f"Gift code: {code}"
            )
            await db.use_gift_code(code, message.from_user.id)
            await message.answer(
                f"✅ <b>Gift Code Redeemed!</b>\n\n"
                f"💰 ${amount:.2f} added to your balance.\n"
                f"💳 New balance: ${db_user.get('balance', 0) + amount:.2f}",
                reply_markup=kb_back_to_menu()
            )
        
        elif gift["type"] == "plan":
            plan_id = int(gift["value"])
            plan = await db.get_plan(plan_id)
            if not plan:
                await message.answer("❌ Gift plan not found. Contact support.", reply_markup=kb_back_to_menu())
                return
            
            # Create account from gift
            server = await lb.select_best_server()
            if not server:
                await message.answer("❌ No servers available.", reply_markup=kb_back_to_menu())
                return
            
            inbound_ids = await lb.select_inbounds_for_plan(server, plan)
            email = gen_email(message.from_user.id)
            expiry_time = int((datetime.now() + timedelta(days=plan["duration_days"])).timestamp() * 1000)
            
            result = await api.create_client(
                panel_url=server["panel_url"],
                token=server["api_token"],
                email=email,
                inbound_ids=inbound_ids,
                total_gb=plan["traffic_gb"],
                expiry_time=expiry_time,
                tg_id=message.from_user.id,
            )
            
            if not result.get("success"):
                await message.answer(f"❌ Failed to create account: {result.get('msg')}", reply_markup=kb_back_to_menu())
                return
            
            client_data = await api.get_client(server["panel_url"], server["api_token"], email)
            sub_id = client_data.get("subId", "") if client_data else ""
            
            await db.add_account(
                user_tg_id=message.from_user.id,
                server_id=server["id"],
                email=email,
                sub_id=sub_id,
                plan_id=plan["id"],
                traffic_gb=plan["traffic_gb"],
                expiry_time=expiry_time,
                limit_ip=plan.get("limit_ip", 0),
                inbound_ids=json.dumps(inbound_ids),
                is_trial=False
            )
            
            await db.add_transaction(
                user_tg_id=message.from_user.id,
                amount=0,
                type="gift_plan",
                description=f"Gift code: {code} -> {plan['name']}",
                account_email=email,
                plan_id=plan["id"]
            )
            
            await db.use_gift_code(code, message.from_user.id)
            
            links = await api.get_client_links(server["panel_url"], server["api_token"], email)
            
            text = (
                f"✅ <b>Gift Code Redeemed!</b>\n\n"
                f"🎁 Plan: {plan['name']}\n\n"
                f"{fmt_account_card({
                    'email': email,
                    'traffic_gb': plan['traffic_gb'],
                    'expiry_time': expiry_time,
                    'is_active': True,
                    'is_trial': False
                }, server_alias=server['alias'], plan_name=plan['name'])}\n\n"
            )
            
            if links:
                text += "<b>🔗 Connection Links:</b>\n"
                for link in links[:3]:
                    text += f"<code>{escape_html(link)}</code>\n"
            
            await message.answer(text, reply_markup=kb_back_to_menu(), disable_web_page_preview=True)
    
    # --- Support ---
    @router.callback_query(MenuCB.filter(F.action == "support"))
    async def cb_support(callback: CallbackQuery):
        await callback.message.edit_text(
            "💬 <b>Support Center</b>\n\n"
            "Need help? Open a support ticket and our team will assist you.\n\n"
            "• 🎫 Create a ticket for any issue\n"
            "• ⏱ We typically respond within a few hours\n"
            "• 🔒 Your conversation is private",
            reply_markup=InlineKeyboardBuilder()
            .button(text="🎫 New Ticket", callback_data=MenuCB(action="new_ticket").pack(), style="success")
            .button(text="📋 My Tickets", callback_data=MenuCB(action="my_tickets").pack())
            .button(text="🔙 Back", callback_data=MenuCB(action="main").pack(), style="danger")
            .adjust(2, 1)
            .as_markup()
        )
        await callback.answer()
    
    @router.callback_query(MenuCB.filter(F.action == "new_ticket"))
    async def cb_new_ticket(callback: CallbackQuery, state: FSMContext):
        await state.set_state(UserStates.waiting_for_ticket_subject)
        await callback.message.edit_text(
            "🎫 <b>New Support Ticket</b>\n\nPlease enter a short subject for your issue:",
            reply_markup=kb_cancel()
        )
        await callback.answer()
    
    @router.message(UserStates.waiting_for_ticket_subject)
    async def ms_ticket_subject(message: Message, state: FSMContext):
        subject = message.text.strip()[:100]
        await state.update_data(subject=subject)
        await state.set_state(UserStates.waiting_for_ticket_message)
        await message.answer(
            f"📝 <b>Subject:</b> {escape_html(subject)}\n\nNow describe your issue in detail:",
            reply_markup=kb_cancel()
        )
    
    @router.message(UserStates.waiting_for_ticket_message)
    async def ms_ticket_message(message: Message, state: FSMContext, db_user: dict):
        msg_text = message.text.strip()[:2000]
        data = await state.get_data()
        subject = data.get("subject", "No subject")
        await state.clear()
        
        ticket_id = await db.create_ticket(message.from_user.id, subject)
        await db.add_ticket_message(ticket_id, "user", msg_text)
        
        # Notify admins
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🎫 <b>New Support Ticket #{ticket_id}</b>\n\n"
                    f"👤 User: {message.from_user.full_name} (<code>{message.from_user.id}</code>)\n"
                    f"📝 Subject: {escape_html(subject)}\n"
                    f"💬 Message: {escape_html(msg_text[:500])}",
                    reply_markup=InlineKeyboardBuilder()
                    .button(text="💬 Reply", callback_data=TicketCB(action="reply", ticket_id=ticket_id).pack(), style="primary")
                    .button(text="🔒 Close", callback_data=TicketCB(action="close", ticket_id=ticket_id).pack(), style="danger")
                    .adjust(2)
                    .as_markup()
                )
            except:
                pass
        
        await message.answer(
            f"✅ <b>Ticket #{ticket_id} Created!</b>\n\n"
            f"📝 Subject: {escape_html(subject)}\n"
            f"⏱ We will respond as soon as possible.\n\n"
            f"You can view your tickets anytime via Support → My Tickets.",
            reply_markup=kb_back_to_menu()
        )
    
    @router.callback_query(MenuCB.filter(F.action == "my_tickets"))
    async def cb_my_tickets(callback: CallbackQuery, db_user: dict):
        tickets = await db.get_user_tickets(callback.from_user.id)
        if not tickets:
            await callback.message.edit_text(
                "📋 <b>My Tickets</b>\n\nYou have no tickets yet.",
                reply_markup=kb_back_to_menu()
            )
            await callback.answer()
            return
        
        kb = InlineKeyboardBuilder()
        for t in tickets:
            status = "🟢" if t["status"] == "open" else "🔴"
            kb.button(
                text=f"{status} #{t['id']} - {t['subject'][:25]}",
                callback_data=TicketCB(action="view", ticket_id=t["id"]).pack()
            )
        kb.button(text="🔙 Back", callback_data=MenuCB(action="support").pack(), style="danger")
        kb.adjust(1)
        
        await callback.message.edit_text("📋 <b>My Tickets</b>\n\nSelect a ticket:", reply_markup=kb.as_markup())
        await callback.answer()
    
    @router.callback_query(TicketCB.filter(F.action == "view"))
    async def cb_ticket_view(callback: CallbackQuery, callback_data: TicketCB, db_user: dict):
        ticket = await db.get_ticket(callback_data.ticket_id)
        if not ticket:
            await callback.answer("Ticket not found.", show_alert=True)
            return
        
        # Check ownership or admin
        is_admin = callback.from_user.id in ADMIN_IDS
        if ticket["user_tg_id"] != callback.from_user.id and not is_admin:
            await callback.answer("Access denied.", show_alert=True)
            return
        
        messages = await db.get_ticket_messages(callback_data.ticket_id)
        
        text = f"🎫 <b>Ticket #{ticket['id']}</b>\n"
        text += f"📝 Subject: {escape_html(ticket['subject'])}\n"
        text += f"📊 Status: {'🟢 Open' if ticket['status'] == 'open' else '🔴 Closed'}\n"
        text += f"📅 Created: {ticket['created_at'][:16]}\n\n"
        text += "<b>💬 Messages:</b>\n"
        
        for msg in messages:
            sender = "👤 User" if msg["sender"] == "user" else "🛡 Admin"
            text += f"\n<b>{sender}</b> ({msg['created_at'][:16]}):\n{escape_html(msg['message'][:500])}\n"
        
        await callback.message.edit_text(text, reply_markup=kb_ticket_view(ticket["id"], is_admin))
        await callback.answer()
    
    @router.callback_query(TicketCB.filter(F.action == "reply"))
    async def cb_ticket_reply(callback: CallbackQuery, callback_data: TicketCB, state: FSMContext):
        await state.set_state(UserStates.waiting_for_ticket_reply)
        await state.update_data(ticket_id=callback_data.ticket_id)
        await callback.message.edit_text(
            "💬 <b>Reply to Ticket</b>\n\nType your message:",
            reply_markup=kb_cancel()
        )
        await callback.answer()
    
    @router.message(UserStates.waiting_for_ticket_reply)
    async def ms_ticket_reply(message: Message, state: FSMContext, db_user: dict):
        msg_text = message.text.strip()[:2000]
        data = await state.get_data()
        ticket_id = data.get("ticket_id")
        await state.clear()
        
        ticket = await db.get_ticket(ticket_id)
        if not ticket:
            await message.answer("❌ Ticket not found.", reply_markup=kb_back_to_menu())
            return
        
        is_admin = message.from_user.id in ADMIN_IDS
        sender = "admin" if is_admin else "user"
        
        await db.add_ticket_message(ticket_id, sender, msg_text)
        
        if is_admin:
            # Notify user
            try:
                await bot.send_message(
                    ticket["user_tg_id"],
                    f"💬 <b>Admin replied to your ticket #{ticket_id}</b>\n\n"
                    f"📝 Subject: {escape_html(ticket['subject'])}\n"
                    f"💬 Message: {escape_html(msg_text[:500])}",
                    reply_markup=InlineKeyboardBuilder()
                    .button(text="💬 Reply", callback_data=TicketCB(action="reply", ticket_id=ticket_id).pack(), style="primary")
                    .button(text="📋 View Ticket", callback_data=TicketCB(action="view", ticket_id=ticket_id).pack())
                    .adjust(2)
                    .as_markup()
                )
            except:
                pass
            await message.answer("✅ Reply sent to user.", reply_markup=kb_back_to_menu())
        else:
            # Notify admins
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"💬 <b>User replied to ticket #{ticket_id}</b>\n\n"
                        f"👤 User: {message.from_user.full_name}\n"
                        f"💬 Message: {escape_html(msg_text[:500])}",
                        reply_markup=kb_ticket_view(ticket_id, True)
                    )
                except:
                    pass
            await message.answer("✅ Reply sent to admin.", reply_markup=kb_back_to_menu())
    
    # --- Guide ---
    @router.callback_query(MenuCB.filter(F.action == "guide"))
    async def cb_guide(callback: CallbackQuery):
        text = (
            "📚 <b>VPN Setup Guide</b>\n\n"
            "<b>🤖 Android (v2rayNG):</b>\n"
            "1. Download v2rayNG from Google Play\n"
            "2. Copy your subscription URL\n"
            "3. Open v2rayNG → Settings → Subscription\n"
            "4. Add subscription, paste URL, update\n"
            "5. Select a server and tap V to connect\n\n"
            "<b>📱 iOS (Streisand / V2Box):</b>\n"
            "1. Download Streisand from App Store\n"
            "2. Go to Settings → Subscriptions\n"
            "3. Add your subscription URL\n"
            "4. Select a server and connect\n\n"
            "<b>💻 Windows (v2rayN):</b>\n"
            "1. Download v2rayN from GitHub\n"
            "2. Subscription → Subscription setting\n"
            "3. Add subscription URL, update\n"
            "4. Select server, right-click → Enable\n\n"
            "<b>🍎 macOS (V2RayU):</b>\n"
            "1. Download V2RayU\n"
            "2. Add subscription URL\n"
            "3. Select server and toggle on\n\n"
            "<b>🌐 Subscription URL:</b>\n"
            "Use the subscription URL to auto-import all servers. "
            "This is the easiest method as it updates automatically.\n\n"
            "<b>❓ Need help?</b> Open a support ticket!"
        )
        await callback.message.edit_text(text, reply_markup=kb_back_to_menu())
        await callback.answer()
    
    return router

# ============================================================================
# SECTION 10: ADMIN HANDLERS
# ============================================================================

def create_admin_router(db: Database, api: PanelAPI, lb: LoadBalancer, bot: Bot) -> Router:
    """Create and configure the admin router."""
    router = Router()
    
    # Apply admin middleware
    @router.message.middleware()
    async def admin_mw(handler, event, data):
        user = data.get("event_from_user")
        if not user or user.id not in ADMIN_IDS:
            return
        return await handler(event, data)
    
    @router.callback_query.middleware()
    async def admin_cb_mw(handler, event, data):
        user = data.get("event_from_user")
        if not user or user.id not in ADMIN_IDS:
            await event.answer("⛔ Admin only.", show_alert=True)
            return
        return await handler(event, data)
    
    # --- /admin command ---
    @router.message(Command("admin"))
    async def cmd_admin(message: Message):
        await message.answer("⚙️ <b>Admin Panel</b>\n\nManage your VPN bot from here:", reply_markup=kb_admin_menu())
    
    # --- Dashboard ---
    @router.callback_query(AdminCB.filter(F.action == "dashboard"))
    async def cb_dashboard(callback: CallbackQuery):
        # Gather stats
        total_users = await db.count_users()
        all_accounts = await db.get_all_active_accounts()
        open_tickets = await db.count_open_tickets()
        servers = await db.get_servers()
        healthy_servers = [s for s in servers if s["is_healthy"] and s["is_active"]]
        revenue = await db.get_revenue_stats(days=30)
        
        stats = {
            "total_users": total_users,
            "active_accounts": len(all_accounts),
            "total_accounts": len(all_accounts),
            "open_tickets": open_tickets,
            "servers_online": len(healthy_servers),
            "revenue_30d": revenue["total_revenue"],
            "revenue_today": revenue["today_revenue"],
            "total_revenue": revenue["total_revenue"],
        }
        
        text = fmt_dashboard_html(stats)
        
        if revenue["top_plans"]:
            text += "\n<b>🏆 Top Plans (30d):</b>\n<pre>"
            for p in revenue["top_plans"]:
                text += f"{p['name'][:15]:<15} | {p['cnt']:>3} sales | ${p['revenue']:.2f}\n"
            text += "</pre>"
        
        await callback.message.edit_text(text, reply_markup=kb_admin_menu())
        await callback.answer()
    
    @router.callback_query(AdminCB.filter(F.action == "main"))
    async def cb_admin_main(callback: CallbackQuery):
        await callback.message.edit_text("⚙️ <b>Admin Panel</b>", reply_markup=kb_admin_menu())
        await callback.answer()
    
    # --- Servers ---
    @router.callback_query(AdminCB.filter(F.action == "servers"))
    async def cb_servers(callback: CallbackQuery):
        servers = await db.get_servers()
        await callback.message.edit_text("🖥 <b>Server Management</b>\n\nSelect a server:", reply_markup=kb_servers(servers))
        await callback.answer()
    
    @router.callback_query(ServerCB.filter(F.action == "add"))
    async def cb_server_add(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_server_alias)
        await callback.message.edit_text(
            "➕ <b>Add New Server</b>\n\nEnter server alias (e.g., 'DE-Frankfurt'):",
            reply_markup=kb_cancel()
        )
        await callback.answer()
    
    @router.message(AdminStates.waiting_for_server_alias)
    async def ms_server_alias(message: Message, state: FSMContext):
        alias = message.text.strip()
        await state.update_data(alias=alias)
        await state.set_state(AdminStates.waiting_for_server_url)
        await message.answer(
            f"✅ Alias: {escape_html(alias)}\n\nEnter panel URL (e.g., https://1.2.3.4:2053):",
            reply_markup=kb_cancel()
        )
    
    @router.message(AdminStates.waiting_for_server_url)
    async def ms_server_url(message: Message, state: FSMContext):
        url = message.text.strip().rstrip('/')
        if not url.startswith("http"):
            await message.answer("❌ URL must start with http:// or https://\n\nTry again:", reply_markup=kb_cancel())
            return
        await state.update_data(panel_url=url)
        await state.set_state(AdminStates.waiting_for_server_token)
        await message.answer("🔐 Enter API token:", reply_markup=kb_cancel())
    
    @router.message(AdminStates.waiting_for_server_token)
    async def ms_server_token(message: Message, state: FSMContext):
        token = message.text.strip()
        data = await state.get_data()
        await state.clear()
        
        # Test connection
        await message.answer("⏳ Testing connection...")
        success, msg = await api.test_panel_connection(data["panel_url"], token)
        
        if not success:
            await message.answer(f"❌ Connection failed: {msg}\n\nServer not added.", reply_markup=kb_back_to_menu())
            return
        
        # Add server
        server_id = await db.add_server(data["alias"], data["panel_url"], token)
        
        # Sync inbounds
        inbounds = await api.get_inbounds(data["panel_url"], token)
        await db.sync_inbounds(server_id, inbounds)
        
        await message.answer(
            f"✅ <b>Server Added!</b>\n\n"
            f"🖥 Alias: {escape_html(data['alias'])}\n"
            f"🔗 URL: <code>{escape_html(data['panel_url'])}</code>\n"
            f"📡 Inbounds synced: {len(inbounds)}",
            reply_markup=kb_back_to_menu()
        )
    
    @router.callback_query(ServerCB.filter(F.action == "view"))
    async def cb_server_view(callback: CallbackQuery, callback_data: ServerCB):
        server = await db.get_server(callback_data.server_id)
        if not server:
            await callback.answer("Server not found.", show_alert=True)
            return
        
        # Get online count
        online = await api.get_online_clients(server["panel_url"], server["api_token"])
        online_count = len(online) if isinstance(online, list) else 0
        
        text = fmt_server_health(server, online_count)
        await callback.message.edit_text(text, reply_markup=kb_server_view(server["id"]))
        await callback.answer()
    
    @router.callback_query(ServerCB.filter(F.action == "sync"))
    async def cb_server_sync(callback: CallbackQuery, callback_data: ServerCB):
        server = await db.get_server(callback_data.server_id)
        if not server:
            await callback.answer("Server not found.", show_alert=True)
            return
        
        inbounds = await api.get_inbounds(server["panel_url"], server["api_token"])
        await db.sync_inbounds(server["id"], inbounds)
        
        await callback.answer(f"✅ Synced {len(inbounds)} inbounds.", show_alert=True)
    
    @router.callback_query(ServerCB.filter(F.action == "sync_all"))
    async def cb_server_sync_all(callback: CallbackQuery):
        servers = await db.get_servers(active_only=True)
        total_synced = 0
        for srv in servers:
            inbounds = await api.get_inbounds(srv["panel_url"], srv["api_token"])
            await db.sync_inbounds(srv["id"], inbounds)
            total_synced += len(inbounds)
        await callback.answer(f"✅ Synced {total_synced} inbounds across {len(servers)} servers.", show_alert=True)
    
    @router.callback_query(ServerCB.filter(F.action == "test"))
    async def cb_server_test(callback: CallbackQuery, callback_data: ServerCB):
        server = await db.get_server(callback_data.server_id)
        if not server:
            await callback.answer("Server not found.", show_alert=True)
            return
        
        success, msg = await api.test_panel_connection(server["panel_url"], server["api_token"])
        await db.update_server_health(server["id"], success, "" if success else msg)
        
        if success:
            await callback.answer("✅ Connection successful!", show_alert=True)
        else:
            await callback.answer(f"❌ Failed: {msg}", show_alert=True)
    
    @router.callback_query(ServerCB.filter(F.action == "restart"))
    async def cb_server_restart(callback: CallbackQuery, callback_data: ServerCB):
        server = await db.get_server(callback_data.server_id)
        if not server:
            await callback.answer("Server not found.", show_alert=True)
            return
        
        result = await api.restart_panel(server["panel_url"], server["api_token"])
        if result.get("success"):
            await callback.answer("✅ Panel restart initiated.", show_alert=True)
        else:
            await callback.answer(f"❌ Failed: {result.get('msg')}", show_alert=True)
    
    @router.callback_query(ServerCB.filter(F.action == "backup"))
    async def cb_server_backup(callback: CallbackQuery, callback_data: ServerCB):
        server = await db.get_server(callback_data.server_id)
        if not server:
            await callback.answer("Server not found.", show_alert=True)
            return
        
        result = await api.backup_to_telegram(server["panel_url"], server["api_token"])
        if result.get("success"):
            await callback.answer("✅ Backup sent to Telegram.", show_alert=True)
        else:
            await callback.answer(f"❌ Failed: {result.get('msg')}", show_alert=True)
    
    @router.callback_query(ServerCB.filter(F.action == "stats"))
    async def cb_server_stats(callback: CallbackQuery, callback_data: ServerCB):
        server = await db.get_server(callback_data.server_id)
        if not server:
            await callback.answer("Server not found.", show_alert=True)
            return
        
        # Get paginated client list
        clients_data = await api.get_clients_paged(server["panel_url"], server["api_token"], page=1, page_size=25)
        summary = clients_data.get("summary", {})
        items = clients_data.get("items", [])
        total = clients_data.get("total", 0)
        filtered = clients_data.get("filtered", 0)
        
        online_clients = await api.get_online_clients(server["panel_url"], server["api_token"])
        online_count = len(online_clients) if isinstance(online_clients, list) else 0
        
        text = f"📊 <b>Server Stats: {escape_html(server['alias'])}</b>\n"
        text += f"<pre>┌────────────────────────────┐\n"
        text += f"│ Total Clients: {total:>12} │\n"
        text += f"│ Active:        {summary.get('active', 0):>12} │\n"
        text += f"│ Online Now:    {online_count:>12} │\n"
        text += f"│ Depleted:      {len(summary.get('depleted', [])):>12} │\n"
        text += f"│ Expiring:      {len(summary.get('expiring', [])):>12} │\n"
        text += f"│ Deactivated:   {len(summary.get('deactive', [])):>12} │\n"
        text += f"└────────────────────────────┘</pre>\n"
        
        if items:
            text += "\n<b>📋 Recent Clients (Page 1):</b>\n<pre>"
            text += f"{'Email':<25} | {'Status':<8} | {'Expiry':<12}\n"
            text += "─" * 50 + "\n"
            for item in items[:10]:
                email = item.get("email", "N/A")[:25]
                status = "Active" if item.get("enable") else "Disabled"
                exp = fmt_ts(item.get("expiryTime", 0))[:12]
                text += f"{email:<25} | {status:<8} | {exp:<12}\n"
            text += "</pre>"
        
        await callback.message.edit_text(text, reply_markup=kb_server_view(server["id"]))
        await callback.answer()
    
    # --- Plans ---
    @router.callback_query(AdminCB.filter(F.action == "plans"))
    async def cb_admin_plans(callback: CallbackQuery):
        plans = await db.get_plans(active_only=False)
        await callback.message.edit_text("📦 <b>Plan Management</b>", reply_markup=kb_admin_plans(plans))
        await callback.answer()
    
    @router.callback_query(PlanCB.filter(F.action == "add"))
    async def cb_plan_add(callback: CallbackQuery, state: FSMContext):
        # Get active servers to show inbounds
        servers = await db.get_servers(active_only=True)
        if not servers:
            await callback.answer("❌ No active server found. Please add a server first.", show_alert=True)
            return
        
        await state.set_state(AdminStates.waiting_for_plan_name)
        await callback.message.edit_text("➕ <b>Add Plan</b>\n\nEnter plan name:", reply_markup=kb_cancel())
        await callback.answer()
    
    @router.message(AdminStates.waiting_for_plan_name)
    async def ms_plan_name(message: Message, state: FSMContext):
        await state.update_data(name=message.text.strip())
        await state.set_state(AdminStates.waiting_for_plan_desc)
        await message.answer("📝 Enter plan description (or '-' for none):", reply_markup=kb_cancel())
    
    @router.message(AdminStates.waiting_for_plan_desc)
    async def ms_plan_desc(message: Message, state: FSMContext):
        desc = message.text.strip()
        if desc == "-":
            desc = ""
        await state.update_data(description=desc)
        await state.set_state(AdminStates.waiting_for_plan_traffic)
        await message.answer("💾 Enter traffic limit in GB (0 = unlimited):", reply_markup=kb_cancel())
    
    @router.message(AdminStates.waiting_for_plan_traffic)
    async def ms_plan_traffic(message: Message, state: FSMContext):
        try:
            gb = int(message.text.strip())
            await state.update_data(traffic_gb=gb)
            await state.set_state(AdminStates.waiting_for_plan_duration)
            await message.answer("📅 Enter duration in days (0 = never expires):", reply_markup=kb_cancel())
        except ValueError:
            await message.answer("❌ Please enter a number. Try again:", reply_markup=kb_cancel())
    
    @router.message(AdminStates.waiting_for_plan_duration)
    async def ms_plan_duration(message: Message, state: FSMContext):
        try:
            days = int(message.text.strip())
            await state.update_data(duration_days=days)
            await state.set_state(AdminStates.waiting_for_plan_price)
            await message.answer(f"💵 Enter price in {CURRENCY}:", reply_markup=kb_cancel())
        except ValueError:
            await message.answer("❌ Please enter a number. Try again:", reply_markup=kb_cancel())
    
    @router.message(AdminStates.waiting_for_plan_price)
    async def ms_plan_price(message: Message, state: FSMContext):
        try:
            price = float(message.text.strip())
            await state.update_data(price=price)
            await state.set_state(AdminStates.waiting_for_plan_limit_ip)
            await message.answer("🔢 Enter max simultaneous IPs (0 = unlimited):", reply_markup=kb_cancel())
        except ValueError:
            await message.answer("❌ Please enter a number. Try again:", reply_markup=kb_cancel())
    
    @router.message(AdminStates.waiting_for_plan_limit_ip)
    async def ms_plan_limit_ip(message: Message, state: FSMContext):
        try:
            limit_ip = int(message.text.strip())
            await state.update_data(limit_ip=limit_ip)
            # Now ask for server selection
            servers = await db.get_servers(active_only=True)
            kb = InlineKeyboardBuilder()
            for srv in servers:
                kb.button(text=f"🖥 {srv['alias']}", callback_data=ServerCB(action="select_for_plan", server_id=srv["id"]).pack())
            kb.adjust(2)
            await state.set_state(AdminStates.waiting_for_plan_inbounds)
            await message.answer(f"📡 <b>Select Server for Plan</b>\n\nChoose the server where this plan's accounts will be created:", reply_markup=kb.as_markup())
        except ValueError:
            await message.answer("❌ Please enter a number. Try again:", reply_markup=kb_cancel())
    
    @router.callback_query(ServerCB.filter(F.action == "select_for_plan"))
    async def cb_select_server_for_plan(callback: CallbackQuery, callback_data: ServerCB, state: FSMContext):
        await state.update_data(server_id=callback_data.server_id)
        # Get inbounds for selected server
        inbounds = await db.get_inbounds(callback_data.server_id)
        if not inbounds:
            await callback.answer("❌ No inbounds found on this server.", show_alert=True)
            return
        
        kb = InlineKeyboardBuilder()
        for ib in inbounds:
            protocol = ib.get('protocol', 'unknown')
            remark = ib.get('remark', f'Inbound {ib["inbound_id"]}')
            kb.button(text=f"🔌 {remark} ({protocol})", callback_data=f"inbound_{ib['inbound_id']}")
        kb.button(text="✅ All Inbounds", callback_data="inbound_all")
        kb.adjust(2)
        
        await state.set_state(AdminStates.waiting_for_plan_inbounds)
        await callback.message.edit_text(f"🔌 <b>Select Inbound(s)</b>\n\nChoose which inbound(s) this plan should use:", reply_markup=kb.as_markup())
        await callback.answer()
    
    @router.callback_query(lambda c: c.data.startswith("inbound_"))
    async def cb_select_inbound_for_plan(callback: CallbackQuery, state: FSMContext):
        inbound_selection = callback.data.split("_", 1)[1]  # "all" or inbound_id
        data = await state.get_data()
        await state.clear()
        
        # Store inbound selection as comma-separated string
        inbound_ids = inbound_selection if inbound_selection == "all" else inbound_selection
        
        plan_id = await db.add_plan(
            name=data["name"],
            description=data["description"],
            traffic_gb=data["traffic_gb"],
            duration_days=data["duration_days"],
            price=data["price"],
            limit_ip=data["limit_ip"],
            inbound_group=inbound_ids,  # Store inbound IDs or "all"
        )
        
        plan = await db.get_plan(plan_id)
        await callback.message.edit_text(
            f"✅ <b>Plan Created!</b>\n\n{fmt_plan_card(plan)}",
            reply_markup=kb_admin_plans(await db.get_plans(active_only=False))
        )
    
    @router.callback_query(PlanCB.filter(F.action == "admin_view"))
    async def cb_admin_plan_view(callback: CallbackQuery, callback_data: PlanCB):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer("Plan not found.", show_alert=True)
            return
        
        text = fmt_plan_card(plan)
        text += f"\n\n<b>Status:</b> {'✅ Active' if plan['is_active'] else '❌ Inactive'}"
        await callback.message.edit_text(text, reply_markup=kb_admin_plan_view(plan["id"]))
        await callback.answer()
    
    @router.callback_query(PlanCB.filter(F.action == "delete"))
    async def cb_plan_delete(callback: CallbackQuery, callback_data: PlanCB):
        await db.delete_plan(callback_data.plan_id)
        plans = await db.get_plans(active_only=False)
        await callback.message.edit_text("✅ Plan deleted.", reply_markup=kb_admin_plans(plans))
        await callback.answer("Deleted!")
    
    # --- Users ---
    @router.callback_query(AdminCB.filter(F.action == "users"))
    async def cb_users(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_user_search)
        await callback.message.edit_text(
            "👥 <b>User Management</b>\n\n"
            "Search by Telegram ID, username, or email:\n"
            "• Enter <code>all</code> to list recent users",
            reply_markup=kb_cancel()
        )
        await callback.answer()
    
    @router.message(AdminStates.waiting_for_user_search)
    async def ms_user_search(message: Message, state: FSMContext):
        query = message.text.strip()
        await state.clear()
        
        if query.lower() == "all":
            users = await db.get_all_users()[:20]
        else:
            users = await db.search_user(query)
        
        if not users:
            await message.answer("❌ No users found.", reply_markup=kb_back_to_menu())
            return
        
        text = f"👥 <b>Search Results ({len(users)} found)</b>\n\n<pre>"
        text += f"{'TG ID':<12} | {'Username':<18} | {'Balance':>8} | {'Orders':>6}\n"
        text += "─" * 55 + "\n"
        for u in users[:20]:
            text += f"{u['tg_id']:<12} | {(u.get('username') or 'N/A')[:18]:<18} | ${u.get('balance', 0):>7.2f} | {u.get('total_orders', 0):>6}\n"
        text += "</pre>"
        
        kb = InlineKeyboardBuilder()
        for u in users[:10]:
            kb.button(
                text=f"👤 {u['tg_id']} - {(u.get('username') or 'N/A')[:15]}",
                callback_data=AdminCB(action="user_view", data=str(u["tg_id"])).pack()
            )
        kb.button(text="🔙 Back", callback_data=AdminCB(action="main").pack(), style="danger")
        kb.adjust(1)
        
        await message.answer(text, reply_markup=kb.as_markup())
    
    @router.callback_query(AdminCB.filter(F.action == "user_view"))
    async def cb_user_view(callback: CallbackQuery, callback_data: AdminCB):
        tg_id = int(callback_data.data)
        user = await db.get_user(tg_id)
        if not user:
            await callback.answer("User not found.", show_alert=True)
            return
        
        accounts = await db.get_user_accounts(tg_id)
        transactions = await db.get_user_transactions(tg_id, limit=5)
        
        text = f"👤 <b>User Details</b>\n"
        text += f"<pre>┌────────────────────────────┐\n"
        text += f"│ TG ID:    {user['tg_id']:<18} │\n"
        text += f"│ Username: {(user.get('username') or 'N/A')[:18]:<18} │\n"
        text += f"│ Balance:  {user.get('balance', 0):<17.0f} Toman │\n"
        text += f"│ Orders:   {user.get('total_orders', 0):<18} │\n"
        text += f"│ Spent:    {user.get('total_spent', 0):<17.0f} Toman │\n"
        text += f"│ Banned:   {'Yes' if user.get('is_banned') else 'No':<18} │\n"
        text += f"│ Joined:   {user['created_at'][:18]:<18} │\n"
        text += f"└────────────────────────────┘</pre>\n"
        
        if accounts:
            text += f"\n<b>📱 Accounts ({len(accounts)}):</b>\n"
            for a in accounts:
                status = "🟢" if a["is_active"] else "🔴"
                trial = " [Trial]" if a["is_trial"] else ""
                text += f"{status} <code>{escape_html(a['email'])}</code>{trial}\n"
        
        kb = InlineKeyboardBuilder()
        if user.get("is_banned"):
            kb.button(text="✅ Unban", callback_data=AdminCB(action="unban", data=str(tg_id)).pack(), style="success")
        else:
            kb.button(text="🚫 Ban", callback_data=AdminCB(action="ban", data=str(tg_id)).pack(), style="danger")
        kb.button(text="💰 Add Balance", callback_data=AdminCB(action="add_balance", data=str(tg_id)).pack(), style="primary")
        kb.button(text="🔙 Back", callback_data=AdminCB(action="users").pack(), style="danger")
        kb.adjust(2, 1)
        
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()
    
    @router.callback_query(AdminCB.filter(F.action == "ban"))
    async def cb_ban_user(callback: CallbackQuery, callback_data: AdminCB):
        tg_id = int(callback_data.data)
        await db.ban_user(tg_id, True)
        await callback.answer("✅ User banned.", show_alert=True)
    
    @router.callback_query(AdminCB.filter(F.action == "unban"))
    async def cb_unban_user(callback: CallbackQuery, callback_data: AdminCB):
        tg_id = int(callback_data.data)
        await db.ban_user(tg_id, False)
        await callback.answer("✅ User unbanned.", show_alert=True)
    
    @router.callback_query(AdminCB.filter(F.action == "add_balance"))
    async def cb_add_balance_start(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        tg_id = int(callback_data.data)
        user = await db.get_user(tg_id)
        if not user:
            await callback.answer("❌ User not found.", show_alert=True)
            return
        
        await state.update_data(add_balance_tg_id=tg_id)
        await state.set_state(AdminStates.waiting_for_add_balance_amount)
        await callback.message.edit_text(
            f"💰 <b>Add Balance for {escape_html(user.get('first_name', 'User'))}</b>\n\n"
            f"Current balance: {user.get('balance', 0):.0f} Toman\n\n"
            f"Enter amount to add (in Toman):",
            reply_markup=kb_cancel()
        )
        await callback.answer()
    
    @router.message(AdminStates.waiting_for_add_balance_amount)
    async def ms_add_balance_amount(message: Message, state: FSMContext):
        try:
            amount = float(message.text.strip())
            if amount <= 0:
                await message.answer("❌ Amount must be positive. Try again:", reply_markup=kb_cancel())
                return
            
            data = await state.get_data()
            tg_id = data.get("add_balance_tg_id")
            if not tg_id:
                await message.answer("❌ Session expired. Please try again.", reply_markup=kb_cancel())
                await state.clear()
                return
            
            # Add balance to user
            await db.update_user_balance(tg_id, amount, add=True)
            
            # Record transaction
            await db.add_transaction(
                user_tg_id=tg_id,
                amount=amount,
                type="deposit",
                description="Manual balance increase by admin"
            )
            
            # Update user totals
            await db._db.execute(
                "UPDATE users SET total_spent = total_spent + ? WHERE tg_id = ?",
                (-amount, tg_id)  # Negative because it's a deposit (money added to user)
            )
            await db._db.commit()
            
            await state.clear()
            
            # Notify user
            try:
                await message.bot.send_message(
                    tg_id,
                    f"💰 <b>Balance Increased!</b>\n\n"
                    f"Admin has added <b>{amount:.0f} Toman</b> to your balance.\n"
                    f"Your new balance: <b>{(await db.get_user(tg_id))['balance']:.0f} Toman</b>"
                )
            except Exception:
                pass  # User might have blocked the bot
            
            await message.answer(
                f"✅ Successfully added {amount:.0f} Toman to user's balance.\n"
                f"New balance: {(await db.get_user(tg_id))['balance']:.0f} Toman",
                reply_markup=InlineKeyboardBuilder.from_button(
                    InlineKeyboardButton(text="🔙 Back to Users", callback_data=AdminCB(action="users").pack())
                ).as_markup()
            )
        except ValueError:
            await message.answer("❌ Please enter a valid number. Try again:", reply_markup=kb_cancel())
    
    # --- Finance ---
    @router.callback_query(AdminCB.filter(F.action == "finance"))
    async def cb_finance(callback: CallbackQuery):
        revenue = await db.get_revenue_stats(days=30)
        
        text = "💰 <b>Financial Report (30 days)</b>\n"
        text += f"<pre>┌────────────────────────────┐\n"
        text += f"│ Revenue (30d):  ${revenue['total_revenue']:>11.2f} │\n"
        text += f"│ Revenue (Today):${revenue['today_revenue']:>11.2f} │\n"
        text += f"│ Transactions:   {revenue['transaction_count']:>11} │\n"
        text += f"│ Avg Order:      ${revenue['total_revenue']/max(revenue['transaction_count'],1):>11.2f} │\n"
        text += f"└────────────────────────────┘</pre>\n"
        
        if revenue["top_plans"]:
            text += "\n<b>🏆 Top Plans:</b>\n<pre>"
            text += f"{'Plan':<15} | {'Sales':>5} | {'Revenue':>10}\n"
            text += "─" * 38 + "\n"
            for p in revenue["top_plans"]:
                text += f"{p['name'][:15]:<15} | {p['cnt']:>5} | ${p['revenue']:>9.2f}\n"
            text += "</pre>"
        
        await callback.message.edit_text(text, reply_markup=kb_admin_menu())
        await callback.answer()
    
    # --- Promo Codes ---
    @router.callback_query(AdminCB.filter(F.action == "promos"))
    async def cb_promos(callback: CallbackQuery):
        promos = await db.get_promo_codes()
        
        text = "🎫 <b>Promo Codes</b>\n\n"
        if promos:
            text += "<pre>"
            text += f"{'Code':<15} | {'Disc%':>5} | {'Used':>5} | {'Max':>5}\n"
            text += "─" * 40 + "\n"
            for p in promos:
                disc = f"{p['discount_percent']}%" if p['discount_percent'] > 0 else f"${p['discount_amount']}"
                text += f"{p['code']:<15} | {disc:>5} | {p['used_count']:>5} | {p['max_uses'] or '∞':>5}\n"
            text += "</pre>"
        else:
            text += "No promo codes yet."
        
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Create Promo", callback_data=AdminCB(action="create_promo").pack(), style="success")
        kb.button(text="🔙 Back", callback_data=AdminCB(action="main").pack(), style="danger")
        kb.adjust(1)
        
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()
    
    @router.callback_query(AdminCB.filter(F.action == "create_promo"))
    async def cb_create_promo(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_promo_code_str)
        await callback.message.edit_text(
            "🎫 <b>Create Promo Code</b>\n\nEnter code (or '-' for random):",
            reply_markup=kb_cancel()
        )
        await callback.answer()
    
    @router.message(AdminStates.waiting_for_promo_code_str)
    async def ms_promo_code_str(message: Message, state: FSMContext):
        code = message.text.strip().upper()
        if code == "-":
            code = gen_gift_code().replace("-", "")[:10]
        await state.update_data(code=code)
        await state.set_state(AdminStates.waiting_for_promo_discount)
        await message.answer("💰 Enter discount percentage (0-100):", reply_markup=kb_cancel())
    
    @router.message(AdminStates.waiting_for_promo_discount)
    async def ms_promo_discount(message: Message, state: FSMContext):
        try:
            disc = int(message.text.strip())
            await state.update_data(disc=disc)
            await state.set_state(AdminStates.waiting_for_promo_max_uses)
            await message.answer("🔢 Enter max uses (0 = unlimited):", reply_markup=kb_cancel())
        except ValueError:
            await message.answer("❌ Enter a number:", reply_markup=kb_cancel())
    
    @router.message(AdminStates.waiting_for_promo_max_uses)
    async def ms_promo_max_uses(message: Message, state: FSMContext):
        try:
            max_uses = int(message.text.strip())
            data = await state.get_data()
            await state.clear()
            
            await db.add_promo_code(
                code=data["code"],
                discount_percent=data["disc"],
                max_uses=max_uses
            )
            
            await message.answer(
                f"✅ <b>Promo Code Created!</b>\n\n"
                f"🎫 Code: <code>{data['code']}</code>\n"
                f"💰 Discount: {data['disc']}%\n"
                f"🔢 Max uses: {max_uses or 'Unlimited'}",
                reply_markup=kb_admin_menu()
            )
        except ValueError:
            await message.answer("❌ Enter a number:", reply_markup=kb_cancel())
    
    # --- Gift Codes ---
    @router.callback_query(AdminCB.filter(F.action == "gift_codes"))
    async def cb_gift_codes(callback: CallbackQuery, state: FSMContext):
        gifts = await db.get_gift_codes(unused_only=False)
        
        text = "🎁 <b>Gift Codes</b>\n\n"
        if gifts:
            text += "<pre>"
            text += f"{'Code':<20} | {'Type':<8} | {'Value':<10} | {'Used':<4}\n"
            text += "─" * 50 + "\n"
            for g in gifts[:20]:
                text += f"{g['code']:<20} | {g['type']:<8} | {g['value'][:10]:<10} | {'Yes' if g['is_used'] else 'No':<4}\n"
            text += "</pre>"
        else:
            text += "No gift codes yet."
        
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Create Gift (Balance)", callback_data=AdminCB(action="create_gift_balance").pack(), style="success")
        kb.button(text="➕ Create Gift (Plan)", callback_data=AdminCB(action="create_gift_plan").pack(), style="primary")
        kb.button(text="🔙 Back", callback_data=AdminCB(action="main").pack(), style="danger")
        kb.adjust(1, 1, 1)
        
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()
    
    @router.callback_query(AdminCB.filter(F.action == "create_gift_balance"))
    async def cb_create_gift_balance(callback: CallbackQuery):
        code = gen_gift_code()
        await db.create_gift_code(code, "balance", "5.00")  # Default $5
        await callback.answer(f"Gift code created: {code}", show_alert=True)
    
    @router.callback_query(AdminCB.filter(F.action == "create_gift_plan"))
    async def cb_create_gift_plan(callback: CallbackQuery):
        plans = await db.get_plans(active_only=True)
        if not plans:
            await callback.answer("No plans available.", show_alert=True)
            return
        
        code = gen_gift_code()
        plan = plans[0]
        await db.create_gift_code(code, "plan", str(plan["id"]), plan_id=plan["id"])
        await callback.answer(f"Gift code created: {code} for plan {plan['name']}", show_alert=True)
    
    # --- Tickets ---
    @router.callback_query(AdminCB.filter(F.action == "tickets"))
    async def cb_tickets(callback: CallbackQuery):
        tickets = await db.get_open_tickets()
        if not tickets:
            await callback.message.edit_text(
                "💬 <b>Support Tickets</b>\n\n✅ No open tickets!",
                reply_markup=kb_admin_menu()
            )
            await callback.answer()
            return
        
        text = f"💬 <b>Open Tickets ({len(tickets)})</b>\n"
        await callback.message.edit_text(text, reply_markup=kb_tickets(tickets))
        await callback.answer()
    
    @router.callback_query(TicketCB.filter(F.action == "close"))
    async def cb_ticket_close(callback: CallbackQuery, callback_data: TicketCB):
        ticket = await db.get_ticket(callback_data.ticket_id)
        if not ticket:
            await callback.answer("Ticket not found.", show_alert=True)
            return
        
        await db.close_ticket(callback_data.ticket_id)
        
        # Notify user
        try:
            await bot.send_message(
                ticket["user_tg_id"],
                f"🔒 <b>Ticket #{callback_data.ticket_id} has been closed.</b>\n\n"
                f"If you need further assistance, feel free to open a new ticket."
            )
        except:
            pass
        
        await callback.answer("✅ Ticket closed.", show_alert=True)
        tickets = await db.get_open_tickets()
        if tickets:
            await callback.message.edit_text(
                f"💬 <b>Open Tickets ({len(tickets)})</b>",
                reply_markup=kb_tickets(tickets)
            )
        else:
            await callback.message.edit_text("✅ All tickets resolved!", reply_markup=kb_admin_menu())
    
    # --- Broadcast ---
    @router.callback_query(AdminCB.filter(F.action == "broadcast"))
    async def cb_broadcast(callback: CallbackQuery):
        await callback.message.edit_text(
            "📣 <b>Broadcast Message</b>\n\nSelect target audience:",
            reply_markup=kb_broadcast_targets()
        )
        await callback.answer()
    
    @router.callback_query(AdminCB.filter(F.action.startswith("broadcast_")))
    async def cb_broadcast_target(callback: CallbackQuery, state: FSMContext):
        target = callback_data.action.replace("broadcast_", "")
        await state.set_state(AdminStates.waiting_for_broadcast_message)
        await state.update_data(target=target)
        await callback.message.edit_text(
            f"📣 <b>Broadcast to: {target}</b>\n\nSend the message to broadcast:",
            reply_markup=kb_cancel()
        )
        await callback.answer()
    
    @router.message(AdminStates.waiting_for_broadcast_message)
    async def ms_broadcast(message: Message, state: FSMContext):
        msg_text = message.text.strip()[:4000]
        data = await state.get_data()
        target = data.get("target", "all")
        await state.clear()
        
        # Get target users
        user_ids = await db.get_users_by_filter(target)
        
        if not user_ids:
            await message.answer("❌ No users found for this target.", reply_markup=kb_admin_menu())
            return
        
        broadcast_id = await db.create_broadcast(message.from_user.id, msg_text, target)
        
        await message.answer(f"📤 Broadcasting to {len(user_ids)} users...")
        
        sent = 0
        failed = 0
        for uid in user_ids:
            try:
                await bot.send_message(uid, msg_text)
                sent += 1
                await asyncio.sleep(0.05)  # Rate limit
            except TelegramForbiddenError:
                failed += 1
            except TelegramBadRequest:
                failed += 1
            except Exception:
                failed += 1
        
        await db.update_broadcast_stats(broadcast_id, sent, failed)
        
        await message.answer(
            f"✅ <b>Broadcast Complete</b>\n\n"
            f"📤 Sent: {sent}\n"
            f"❌ Failed: {failed}\n"
            f"📊 Total: {len(user_ids)}",
            reply_markup=kb_admin_menu()
        )
    
    # --- Settings ---
    @router.callback_query(AdminCB.filter(F.action == "settings"))
    async def cb_settings(callback: CallbackQuery):
        text = (
            "⚙️ <b>Bot Settings</b>\n\n"
            f"<pre>┌────────────────────────────┐\n"
            f"│ Trial:    {'Enabled' if TRIAL_ENABLED else 'Disabled':<18} │\n"
            f"│ Trial Days: {TRIAL_DAYS:>19} │\n"
            f"│ Trial GB:   {TRIAL_GB:>19} │\n"
            f"│ Referral Days: {REFERRAL_BONUS_DAYS:>15} │\n"
            f"│ Referral GB:   {REFERRAL_BONUS_GB:>15} │\n"
            f"│ Currency: {CURRENCY:<19} │\n"
            f"└────────────────────────────┘</pre>\n"
            f"💡 You can modify trial settings below."
        )
        
        kb = InlineKeyboardBuilder()
        kb.button(text="🎁 Trial Settings", callback_data=AdminCB(action="trial_settings").pack(), style="primary")
        kb.button(text="🔄 Refresh Servers", callback_data=AdminCB(action="refresh_servers").pack())
        kb.button(text="💾 Database Backup", callback_data=AdminCB(action="db_backup").pack())
        kb.button(text="🔙 Back", callback_data=AdminCB(action="main").pack(), style="danger")
        kb.adjust(2, 1, 1)
        
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()
    
    @router.callback_query(AdminCB.filter(F.action == "trial_settings"))
    async def cb_trial_settings(callback: CallbackQuery, state: FSMContext):
        text = (
            f"🎁 <b>Trial Configuration</b>\n\n"
            f"Current settings:\n"
            f"• Trial Enabled: {'✅ Yes' if TRIAL_ENABLED else '❌ No'}\n"
            f"• Trial Duration: {TRIAL_DAYS} days\n"
            f"• Trial Traffic: {TRIAL_GB} GB\n\n"
            f"To change these settings, use the buttons below.\n"
            f"Note: Changes will apply to new trial accounts only."
        )
        
        kb = InlineKeyboardBuilder()
        kb.button(text=f"{'✅' if TRIAL_ENABLED else '❌'} Toggle Trial ({'Enable' if not TRIAL_ENABLED else 'Disable'})", 
                  callback_data=AdminCB(action="trial_toggle").pack(), style="primary")
        kb.button(text=f"📅 Change Days ({TRIAL_DAYS})", callback_data=AdminCB(action="trial_days").pack())
        kb.button(text=f"💾 Change GB ({TRIAL_GB})", callback_data=AdminCB(action="trial_gb").pack())
        kb.button(text="🔙 Back", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(1, 1, 1)
        
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()
    
    @router.callback_query(AdminCB.filter(F.action == "trial_toggle"))
    async def cb_trial_toggle(callback: CallbackQuery):
        global TRIAL_ENABLED
        TRIAL_ENABLED = not TRIAL_ENABLED
        # Save to database settings
        await db.save_setting("trial_enabled", str(TRIAL_ENABLED))
        await callback.answer(f"✅ Trial {'enabled' if TRIAL_ENABLED else 'disabled'}.", show_alert=True)
        # Refresh the settings view
        await cb_trial_settings(callback, FSMContext())
    
    @router.callback_query(AdminCB.filter(F.action == "trial_days"))
    async def cb_trial_days_edit(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_trial_days)
        await callback.message.edit_text(
            f"📅 <b>Change Trial Days</b>\n\n"
            f"Current: {TRIAL_DAYS} days\n\n"
            f"Enter new value:",
            reply_markup=kb_cancel()
        )
        await callback.answer()
    
    @router.message(AdminStates.waiting_for_trial_days)
    async def ms_trial_days(message: Message, state: FSMContext):
        try:
            days = int(message.text.strip())
            if days <= 0:
                await message.answer("❌ Days must be positive. Try again:", reply_markup=kb_cancel())
                return
            
            global TRIAL_DAYS
            TRIAL_DAYS = days
            await db.save_setting("trial_days", str(days))
            await state.clear()
            await message.answer(
                f"✅ Trial days updated to {days} days.",
                reply_markup=InlineKeyboardBuilder.from_button(
                    InlineKeyboardButton(text="🔙 Back to Trial Settings", callback_data=AdminCB(action="trial_settings").pack())
                ).as_markup()
            )
        except ValueError:
            await message.answer("❌ Please enter a valid number.", reply_markup=kb_cancel())
    
    @router.callback_query(AdminCB.filter(F.action == "trial_gb"))
    async def cb_trial_gb_edit(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_trial_gb)
        await callback.message.edit_text(
            f"💾 <b>Change Trial Traffic</b>\n\n"
            f"Current: {TRIAL_GB} GB\n\n"
            f"Enter new value:",
            reply_markup=kb_cancel()
        )
        await callback.answer()
    
    @router.message(AdminStates.waiting_for_trial_gb)
    async def ms_trial_gb(message: Message, state: FSMContext):
        try:
            gb = int(message.text.strip())
            if gb <= 0:
                await message.answer("❌ GB must be positive. Try again:", reply_markup=kb_cancel())
                return
            
            global TRIAL_GB
            TRIAL_GB = gb
            await db.save_setting("trial_gb", str(gb))
            await state.clear()
            await message.answer(
                f"✅ Trial traffic updated to {gb} GB.",
                reply_markup=InlineKeyboardBuilder.from_button(
                    InlineKeyboardButton(text="🔙 Back to Trial Settings", callback_data=AdminCB(action="trial_settings").pack())
                ).as_markup()
            )
        except ValueError:
            await message.answer("❌ Please enter a valid number.", reply_markup=kb_cancel())
    
    @router.callback_query(AdminCB.filter(F.action == "refresh_servers"))
    async def cb_refresh_servers(callback: CallbackQuery):
        servers = await db.get_servers(active_only=True)
        for srv in servers:
            inbounds = await api.get_inbounds(srv["panel_url"], srv["api_token"])
            await db.sync_inbounds(srv["id"], inbounds)
            success, msg = await api.test_panel_connection(srv["panel_url"], srv["api_token"])
            await db.update_server_health(srv["id"], success, "" if success else msg)
        
        await callback.answer(f"✅ Refreshed {len(servers)} servers.", show_alert=True)
    
    @router.callback_query(AdminCB.filter(F.action == "db_backup"))
    async def cb_db_backup(callback: CallbackQuery):
        # Send DB file to admin
        try:
            from aiogram.types import FSInputFile
            db_file = FSInputFile(DATABASE_PATH)
            await bot.send_document(callback.from_user.id, db_file, caption="💾 Database backup")
            await callback.answer("✅ Backup sent to your PM.", show_alert=True)
        except Exception as e:
            await callback.answer(f"❌ Failed: {str(e)[:50]}", show_alert=True)
    
    return router


# ============================================================================
# SECTION 11: BACKGROUND TASKS
# ============================================================================

async def task_expiry_checker(bot: Bot, db: Database, api: PanelAPI):
    """Check for expiring accounts and send reminders."""
    logger.info("Expiry checker task started")
    
    while True:
        try:
            for days in EXPIRY_REMINDER_DAYS:
                accounts = await db.get_expiring_accounts(days)
                for acc in accounts:
                    # Check if already reminded
                    if await db.has_expiry_reminder(acc["email"], days):
                        continue
                    
                    # Send reminder
                    user = await db.get_user(acc["user_tg_id"])
                    if not user:
                        continue
                    
                    try:
                        kb = InlineKeyboardBuilder()
                        kb.button(text="🔄 Renew Now", callback_data=AccountCB(action="renew", email=acc["email"]).pack(), style="success")
                        kb.button(text="📱 My Accounts", callback_data=MenuCB(action="my_accounts").pack())
                        kb.adjust(2)
                        
                        await bot.send_message(
                            acc["user_tg_id"],
                            f"⏰ <b>Subscription Expiring Soon!</b>\n\n"
                            f"📱 Account: <code>{escape_html(acc['email'])}</code>\n"
                            f"📅 Expires in: {days} day(s)\n"
                            f"⏰ Expiry: {fmt_ts_local(acc['expiry_time'])}\n\n"
                            f"Renew now to avoid interruption!",
                            reply_markup=kb.as_markup()
                        )
                        
                        await db.add_expiry_reminder(acc["email"], days)
                        logger.info(f"Expiry reminder sent to {acc['user_tg_id']} for {acc['email']} ({days}d)")
                    except Exception as e:
                        logger.error(f"Failed to send expiry reminder: {e}")
            
            # Check for fully expired accounts and disable them
            now_ms = int(datetime.now().timestamp() * 1000)
            active_accounts = await db.get_all_active_accounts()
            for acc in active_accounts:
                if acc["expiry_time"] > 0 and acc["expiry_time"] < now_ms:
                    server = await db.get_server(acc["server_id"])
                    if server:
                        await api.disable_client(server["panel_url"], server["api_token"], acc["email"])
                        await db.update_account(acc["email"], is_active=False)
                        logger.info(f"Disabled expired account: {acc['email']}")
                        
                        # Notify user
                        try:
                            await bot.send_message(
                                acc["user_tg_id"],
                                f"🔴 <b>Account Expired</b>\n\n"
                                f"📱 Account: <code>{escape_html(acc['email'])}</code>\n"
                                f"⏰ Expired: {fmt_ts_local(acc['expiry_time'])}\n\n"
                                f"Renew to reactivate your account!",
                                reply_markup=InlineKeyboardBuilder()
                                .button(text="🔄 Renew", callback_data=AccountCB(action="renew", email=acc["email"]).pack(), style="success")
                                .button(text="🛒 Buy New", callback_data=MenuCB(action="buy").pack())
                                .adjust(2)
                                .as_markup()
                            )
                        except:
                            pass
            
        except Exception as e:
            logger.error(f"Error in expiry checker: {e}")
        
        await asyncio.sleep(3600)  # Check every hour


async def task_traffic_alerts(bot: Bot, db: Database, api: PanelAPI):
    """Monitor traffic usage and send alerts at thresholds."""
    logger.info("Traffic alerts task started")
    
    while True:
        try:
            accounts = await db.get_all_active_accounts()
            
            for acc in accounts:
                server = await db.get_server(acc["server_id"])
                if not server:
                    continue
                
                traffic = await api.get_client_traffic(server["panel_url"], server["api_token"], acc["email"])
                if not traffic:
                    continue
                
                total = traffic.get("total", 0)
                used = traffic.get("up", 0) + traffic.get("down", 0)
                
                if total <= 0:
                    continue  # Unlimited
                
                pct = (used / total) * 100
                
                # Check thresholds
                for threshold in [TRAFFIC_ALERT_THRESHOLD_1, TRAFFIC_ALERT_THRESHOLD_2]:
                    if pct >= threshold and not await db.has_traffic_alert(acc["email"], threshold):
                        try:
                            emoji = "⚠️" if threshold == TRAFFIC_ALERT_THRESHOLD_1 else "🚨"
                            
                            kb = InlineKeyboardBuilder()
                            kb.button(text="🔄 Renew / Extend", callback_data=AccountCB(action="renew", email=acc["email"]).pack(), style="success")
                            kb.button(text="📱 Account Details", callback_data=AccountCB(action="view", email=acc["email"]).pack())
                            kb.adjust(2)
                            
                            await bot.send_message(
                                acc["user_tg_id"],
                                f"{emoji} <b>Traffic Alert: {threshold}%</b>\n\n"
                                f"📱 Account: <code>{escape_html(acc['email'])}</code>\n"
                                f"📊 Used: {fmt_bytes(used)} / {fmt_bytes(total)}\n"
                                f"📉 Remaining: {fmt_bytes(total - used)}\n\n"
                                f"{'Your account will be depleted soon!' if threshold < 90 else 'Your account is almost out of traffic!'}\n"
                                f"Renew or extend to avoid interruption.",
                                reply_markup=kb.as_markup()
                            )
                            
                            await db.add_traffic_alert(acc["email"], threshold)
                            logger.info(f"Traffic alert ({threshold}%) sent to {acc['user_tg_id']} for {acc['email']}")
                        except Exception as e:
                            logger.error(f"Failed to send traffic alert: {e}")
                
                # Auto-disable depleted accounts
                if pct >= 100 and acc["is_active"]:
                    await api.disable_client(server["panel_url"], server["api_token"], acc["email"])
                    await db.update_account(acc["email"], is_active=False)
                    logger.info(f"Auto-disabled depleted account: {acc['email']}")
                    
                    try:
                        await bot.send_message(
                            acc["user_tg_id"],
                            f"🔴 <b>Traffic Depleted</b>\n\n"
                            f"📱 Account: <code>{escape_html(acc['email'])}</code>\n"
                            f"📊 Your traffic limit has been reached.\n\n"
                            f"Renew to continue using the VPN.",
                            reply_markup=InlineKeyboardBuilder()
                            .button(text="🔄 Renew", callback_data=AccountCB(action="renew", email=acc["email"]).pack(), style="success")
                            .adjust(1)
                            .as_markup()
                        )
                    except:
                        pass
        
        except Exception as e:
            logger.error(f"Error in traffic alerts: {e}")
        
        await asyncio.sleep(600)  # Check every 10 minutes


async def task_server_health(bot: Bot, db: Database, api: PanelAPI):
    """Monitor server health every 5 minutes."""
    logger.info("Server health monitor started")
    
    while True:
        try:
            servers = await db.get_servers(active_only=True)
            for srv in servers:
                success, msg = await api.test_panel_connection(srv["panel_url"], srv["api_token"])
                
                was_healthy = srv["is_healthy"]
                await db.update_server_health(srv["id"], success, "" if success else msg)
                
                if was_healthy and not success:
                    # Server just went down
                    logger.warning(f"Server {srv['alias']} is now unhealthy: {msg}")
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"🔴 <b>Server Down!</b>\n\n"
                                f"🖥 Server: {escape_html(srv['alias'])}\n"
                                f"🔗 URL: <code>{escape_html(srv['panel_url'])}</code>\n"
                                f"❌ Error: {escape_html(msg)}"
                            )
                        except:
                            pass
                
                if not was_healthy and success:
                    # Server recovered
                    logger.info(f"Server {srv['alias']} is now healthy")
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"🟢 <b>Server Recovered</b>\n\n"
                                f"🖥 Server: {escape_html(srv['alias'])}\n"
                                f"✅ Connection restored"
                            )
                        except:
                            pass
        
        except Exception as e:
            logger.error(f"Error in server health monitor: {e}")
        
        await asyncio.sleep(300)  # Check every 5 minutes


# ============================================================================
# SECTION 12: MAIN APPLICATION
# ============================================================================

async def main():
    """Main entry point — initialize and run the bot."""
    logger.info("=" * 60)
    logger.info("3X-UI Telegram Sales Bot — Starting up")
    logger.info("=" * 60)
    
    # Initialize database
    db = Database(DATABASE_PATH)
    await db.connect()
    
    # Initialize API client
    api = PanelAPI()
    
    # Initialize load balancer
    lb = LoadBalancer(db, api)
    
    # Initialize bot
    bot = Bot(token=BOT_TOKEN, default=ParseMode.HTML)
    dp = Dispatcher(storage=MemoryStorage())
    
    # Register middleware
    auth_middleware = AuthMiddleware(db)
    
    # Create routers
    user_router = create_user_router(db, api, lb, bot)
    admin_router = create_admin_router(db, api, lb, bot)
    
    # Apply auth middleware to user router
    user_router.message.middleware()(auth_middleware)
    user_router.callback_query.middleware()(auth_middleware)
    admin_router.message.middleware()(auth_middleware)
    admin_router.callback_query.middleware()(auth_middleware)
    
    # Include routers
    dp.include_router(user_router)
    dp.include_router(admin_router)
    
    # Start background tasks
    tasks = [
        asyncio.create_task(task_expiry_checker(bot, db, api)),
        asyncio.create_task(task_traffic_alerts(bot, db, api)),
        asyncio.create_task(task_server_health(bot, db, api)),
    ]
    logger.info("Background tasks started: expiry_checker, traffic_alerts, server_health")
    
    # Startup message to admins
    me = await bot.get_me()
    logger.info(f"Bot started as @{me.username}")
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"✅ <b>Bot Started Successfully!</b>\n\n"
                f"🤖 Bot: @{me.username}\n"
                f"📡 Status: Online\n"
                f"⚙️ Admin panel: /admin\n"
                f"🔧 Background tasks: Active\n\n"
                f"Use the admin panel to manage servers, plans, and users."
            )
        except:
            pass
    
    # Run polling
    try:
        logger.info("Starting polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        # Cleanup
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await api.close()
        await db.disconnect()
        await bot.session.close()
        logger.info("Bot shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
