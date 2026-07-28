#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
3X-UI Telegram Sales Bot  —  Production Edition
================================================
A complete, bilingual (English / فارسی) Telegram bot for selling and managing
3X-UI VPN subscriptions.  Built with aiogram 3.x.

Features
--------
Customer side
    • Effortless purchase flow with **custom account name**, instant delivery
    • Beautiful colored-button UI (success / danger / primary) throughout
    • Account management: view, renew, traffic-only top-up, enable/disable,
      QR code, connection links, subscription URL, custom labels, delete
    • Free trial with admin-configurable duration / traffic / inbounds
    • Referral program (fires only once per referral, on first purchase)
    • Promo codes & gift codes (balance / plan)
    • In-bot support tickets
    • Persian (RTL) + English (LTR) UI, Toman currency
    • Setup guide for every platform

Admin side (fully inside Telegram, zero panel web-UI needed)
    • Live dashboard with real revenue (all-time / 30d / today)
    • Multi-panel servers: alias, capacity, priority, location, sub-URI
      auto-fetched from panel settings, health monitoring, restart, backup
    • Plan builder with **per-server inbound selection**, edit, toggle, delete
    • User management: search, ban/unban, **manual balance add/deduct**,
      manual account creation, extend/disable/delete user accounts
    • Finance report with top plans
    • Promo & gift code management (custom amount / chosen plan)
    • Support tickets, targeted broadcasts
    • Editable bot settings (trial, referral, currency, language, thresholds)
    • Panel groups management, depleted-client cleanup, DB backup
    • Smart load balancer (capacity / priority / online load aware)

Architecture
------------
Single file, logically split into clearly commented sections:
    0. Imports & environment
    1. Internationalisation (EN / FA)
    2. Database layer
    3. 3X-UI panel API client
    4. Load balancer
    5. Formatters & utilities
    6. Callback-data factories
    7. Keyboards
    8. FSM states
    9. Middleware
   10. User handlers
   11. Admin handlers
   12. Background tasks
   13. Main entry point

Author : Senior Backend Developer
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
import io
import re
import math
import string
import secrets
import uuid
import signal
import contextvars
import contextlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Dict, List, Tuple, Callable, Iterable, Sequence

from urllib.parse import quote
import httpx
import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    User as TgUser,
    FSInputFile,
    BufferedInputFile,
    InputRichMessage,
    InputRichBlockDivider,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    RichBlockTableCell,
)
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

# Optional QR-code support (graceful fallback if not installed)
try:
    import qrcode
    _HAS_QR = True
except Exception:  # pragma: no cover
    _HAS_QR = False

# ---------------------------------------------------------------------------
# Rich Messages table builders — INLINED (was a separate rich_tables.py module).
#
# Pure, side-effect-free builders for aiogram 3.30+ / Telegram Bot API 10.2
# native tables (InputRichMessage + InputRichBlockTable + RichBlockTableCell).
# Used by show_view() throughout the admin panel to render bordered/striped
# tables instead of the old ASCII-art <pre> boxes.
#
# Why a namespace class instead of a separate module:
#   The whole bot is delivered as a SINGLE bot.py file (per user request), so
#   these builders are inlined here. All functions are @staticmethod and
#   reference siblings via rich_tables.X(...) so existing call sites
#   (rich_tables.dashboard_rich(...), rich_tables.kv_table(...), …) work
#   unchanged.
#
# NOTE: Telegram Rich Messages CANNOT be edited in place — aiogram 3.30
# exposes only Message.answer_rich with no edit_rich. So the bot sends them
# as fresh messages via show_view() (delete + answer_rich).
#
# Formatters (fmt_price, fmt_bytes) are injected so the class stays
# dependency-free and unit-testable.
# ---------------------------------------------------------------------------

# Type aliases for the injected formatters (kept at module level for clarity).
PriceFmt = Callable[..., str]
BytesFmt = Callable[[int], str]


class rich_tables:
    """Inlined Rich Messages table builders (aiogram 3.30+ / Bot API 10.2).

    All methods are @staticmethod and reference siblings via
    ``rich_tables.X(...)`` so existing call sites keep working unchanged.
    """

    _ALIGN_OK = {"left", "center", "right", "start", "end"}

    # ------------------------------------------------------------------
    # low-level cell / block factories
    # ------------------------------------------------------------------

    @staticmethod
    def _align(a: str) -> str:
        return a if a in rich_tables._ALIGN_OK else "left"

    @staticmethod
    def hcell(text: str, align: str = "center") -> RichBlockTableCell:
        """Header cell (bold, centered by default)."""
        return RichBlockTableCell(align=rich_tables._align(align), valign="middle",
                                  text="" if text is None else str(text), is_header=True)

    @staticmethod
    def cell(text, align: str = "left") -> RichBlockTableCell:
        """Data cell."""
        return RichBlockTableCell(align=rich_tables._align(align), valign="middle",
                                  text="" if text is None else str(text))

    @staticmethod
    def heading(text: str, size: int = 3) -> InputRichBlockSectionHeading:
        """Section heading block. ``size`` is 1..6 (smaller number = bigger)."""
        if size not in (1, 2, 3, 4, 5, 6):
            size = 3
        return InputRichBlockSectionHeading(text=str(text), size=size)

    @staticmethod
    def divider() -> InputRichBlockDivider:
        return InputRichBlockDivider()

    @staticmethod
    def paragraph(text: str) -> InputRichBlockParagraph:
        """Paragraph block (plain text). Accepts a plain string."""
        return InputRichBlockParagraph(text="" if text is None else str(text))

    # ------------------------------------------------------------------
    # table builders
    # ------------------------------------------------------------------

    @staticmethod
    def kv_table(pairs: Sequence[Tuple[str, object]],
                 align_key: str = "left",
                 align_val: str = "right") -> InputRichBlockTable:
        """Two-column key/value table with a header row."""
        cells: List[List[RichBlockTableCell]] = [[rich_tables.hcell("Field", align_key),
                                                  rich_tables.hcell("Value", align_val)]]
        for k, v in pairs:
            cells.append([rich_tables.cell(k, align_key), rich_tables.cell(v, align_val)])
        return InputRichBlockTable(cells=cells, is_bordered=True, is_striped=True)

    @staticmethod
    def grid_table(headers: Sequence[str],
                   rows: Iterable[Sequence[object]],
                   aligns: Optional[Sequence[str]] = None,
                   is_striped: bool = True) -> InputRichBlockTable:
        """N-column table with a header row."""
        headers = list(headers)
        n = len(headers)
        if aligns is None:
            aligns = ["left"] * n
        aligns = list(aligns) + ["left"] * (n - len(aligns))

        cells: List[List[RichBlockTableCell]] = [[rich_tables.hcell(headers[i], aligns[i]) for i in range(n)]]
        for row in rows:
            row = list(row)
            cells.append([rich_tables.cell(row[i] if i < len(row) else "", aligns[i]) for i in range(n)])
        return InputRichBlockTable(cells=cells, is_bordered=True, is_striped=is_striped)

    @staticmethod
    def rich_message(*blocks, is_rtl: bool = False) -> InputRichMessage:
        """Assemble an InputRichMessage from the given blocks."""
        return InputRichMessage(blocks=list(blocks), is_rtl=is_rtl or None)

    # ==================================================================
    # View-specific builders (one per admin screen)
    # ==================================================================

    @staticmethod
    def dashboard_rich(stats: dict, currency: str, top_plans: Optional[list],
                       fmt: PriceFmt) -> InputRichMessage:
        blocks: List = [rich_tables.heading("📊 Admin Dashboard")]
        blocks.append(rich_tables.kv_table([
            ("Date", datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d")),
            ("Total Users", stats.get("total_users", 0)),
            ("Active Accounts", stats.get("active_accounts", 0)),
            ("Total Accounts", stats.get("total_accounts", 0)),
            ("Open Tickets", stats.get("open_tickets", 0)),
            ("Servers Online", stats.get("servers_online", 0)),
            ("Revenue 30d", fmt(stats.get("revenue_30d", 0), "en", currency)),
            ("Revenue Today", fmt(stats.get("revenue_today", 0), "en", currency)),
            ("Revenue All", fmt(stats.get("revenue_all", 0), "en", currency)),
        ]))
        if top_plans:
            blocks.append(rich_tables.divider())
            blocks.append(rich_tables.heading("🏆 Top Plans (30d)", size=4))
            blocks.append(rich_tables.grid_table(
                ["Plan", "#", "Revenue"],
                [(p.get("name") or "—", p.get("cnt", 0), fmt(p.get("revenue") or 0, "en", currency))
                 for p in top_plans],
                aligns=["left", "center", "right"],
            ))
        return rich_tables.rich_message(*blocks)

    @staticmethod
    def finance_rich(rev: dict, currency: str, fmt: PriceFmt) -> InputRichMessage:
        avg = rev["total_revenue"] / max(rev.get("transaction_count", 1), 1)
        blocks: List = [rich_tables.heading("💰 Finance (30d)")]
        blocks.append(rich_tables.kv_table([
            ("Revenue 30d", fmt(rev.get("total_revenue", 0), "en", currency)),
            ("Today", fmt(rev.get("today_revenue", 0), "en", currency)),
            ("All-time", fmt(rev.get("all_time_revenue", 0), "en", currency)),
            ("Transactions", rev.get("transaction_count", 0)),
            ("Avg order", fmt(avg, "en", currency)),
        ]))
        if rev.get("top_plans"):
            blocks.append(rich_tables.divider())
            blocks.append(rich_tables.heading("🏆 Top Plans", size=4))
            blocks.append(rich_tables.grid_table(
                ["Plan", "#", "Revenue"],
                [(p.get("name") or "—", p.get("cnt", 0), fmt(p.get("revenue") or 0, "en", currency))
                 for p in rev["top_plans"]],
                aligns=["left", "center", "right"],
            ))
        return rich_tables.rich_message(*blocks)

    @staticmethod
    def server_health_rich(server: dict, online_count: int, fmt_b: BytesFmt) -> InputRichMessage:
        is_active = server.get("is_active", False)
        is_healthy = server.get("is_healthy", False)
        if not is_active:
            status = "⚪ Disabled"
        elif is_healthy:
            status = "🟢 Healthy"
        else:
            status = "🔴 Unhealthy"
        cap = server.get("capacity", 0)
        cap_s = f"{server.get('total_clients', 0)}/{cap}" if cap > 0 else f"{server.get('total_clients', 0)}/∞"
        pairs = [
            ("Alias", server.get("alias", "")),
            ("Status", status),
            ("URL", server.get("panel_url", "")),
            ("Location", server.get("location") or "-"),
            ("Priority", server.get("priority", 10)),
            ("Clients", cap_s),
            ("Online", online_count),
            ("Traffic", fmt_b(server.get("total_traffic", 0))),
        ]
        if server.get("last_check"):
            pairs.append(("Last check", fmt_iso(server["last_check"], "%Y-%m-%d %H:%M:%S")))
        if server.get("last_error"):
            pairs.append(("Last error", str(server["last_error"])[:60]))
        return rich_tables.rich_message(rich_tables.heading(f"🖥 Server: {server.get('alias','')}"),
                                        rich_tables.kv_table(pairs))

    @staticmethod
    def server_summary_rich(alias: str, summary: dict, total: int, online_count: int) -> InputRichMessage:
        return rich_tables.rich_message(
            rich_tables.heading(f"📊 {alias}"),
            rich_tables.kv_table([
                ("Total", total),
                ("Active", summary.get("active", 0)),
                ("Online", online_count),
                ("Depleted", len(summary.get("depleted", []))),
                ("Expiring", len(summary.get("expiring", []))),
                ("Deactive", len(summary.get("deactive", []))),
            ]),
        )

    @staticmethod
    def server_clients_rich(alias: str, items: list) -> InputRichMessage:
        rows = []
        for it in items[:10]:
            em = (it.get("email") or "—")
            st = "Active" if it.get("enable") else "Off"
            ex = rich_tables._short_ts(it.get("expiryTime", 0))
            rows.append((em, st, ex))
        return rich_tables.rich_message(
            rich_tables.heading(f"📋 Clients — {alias}", size=4),
            rich_tables.grid_table(["Email", "Status", "Expiry"], rows,
                                   aligns=["left", "center", "center"]),
        )

    @staticmethod
    def inbounds_rich(alias: str, inbounds: list) -> InputRichMessage:
        rows = [(ib.get("inbound_id", 0), ib.get("protocol", "?"), ib.get("port", ""),
                 ib.get("remark") or "") for ib in inbounds]
        return rich_tables.rich_message(
            rich_tables.heading(f"📡 Inbounds — {alias}"),
            rich_tables.grid_table(["ID", "Proto", "Port", "Remark"], rows,
                                   aligns=["center", "center", "center", "left"]),
        )

    @staticmethod
    def user_search_rich(users: list, currency: str, fmt: PriceFmt) -> InputRichMessage:
        rows = [(u["tg_id"], (u.get("username") or "-"), fmt(u.get("balance", 0), "en", currency),
                 u.get("total_orders", 0)) for u in users[:20]]
        return rich_tables.rich_message(
            rich_tables.heading(f"👥 Results ({len(users)})"),
            rich_tables.grid_table(["TG ID", "Username", "Balance", "Orders"], rows,
                                   aligns=["left", "left", "right", "center"]),
        )

    @staticmethod
    def user_detail_rich(user: dict, currency: str, fmt: PriceFmt) -> InputRichMessage:
        return rich_tables.rich_message(
            rich_tables.heading("👤 User"),
            rich_tables.kv_table([
                ("TG ID", user["tg_id"]),
                ("Username", (user.get("username") or "-")),
                ("Balance", fmt(user.get("balance", 0), "en", currency)),
                ("Orders", user.get("total_orders", 0)),
                ("Spent", fmt(user.get("total_spent", 0), "en", currency)),
                ("Banned", "Yes" if user.get("is_banned") else "No"),
                ("Joined", fmt_iso(user.get("created_at"), "%Y-%m-%d %H:%M:%S")),
            ]),
        )

    @staticmethod
    def promos_rich(promos: list, currency: str, fmt: PriceFmt) -> InputRichMessage:
        rows = []
        for p in promos:
            disc = (f"{p['discount_percent']}%" if p.get("discount_percent", 0) > 0
                    else fmt(p.get("discount_amount", 0), "en", currency))
            rows.append((p.get("code", ""), disc, p.get("used_count", 0),
                         p.get("max_uses") or "∞"))
        return rich_tables.rich_message(
            rich_tables.heading("🎫 Promo Codes"),
            rich_tables.grid_table(["Code", "Discount", "Used", "Max"], rows,
                                   aligns=["left", "right", "center", "center"]),
        )

    @staticmethod
    def gift_codes_rich(gifts: list) -> InputRichMessage:
        rows = [(g.get("code", ""), g.get("type", ""), (g.get("value") or "")[:12],
                 "Yes" if g.get("is_used") else "No") for g in gifts[:25]]
        return rich_tables.rich_message(
            rich_tables.heading("🎁 Gift Codes"),
            rich_tables.grid_table(["Code", "Type", "Value", "Used"], rows,
                                   aligns=["left", "center", "left", "center"]),
        )

    @staticmethod
    def settings_overview_rich(cur: str, trial_en: int, pay_en: int, fj_en: int) -> InputRichMessage:
        return rich_tables.rich_message(
            rich_tables.heading("⚙️ Settings"),
            rich_tables.kv_table([
                ("Currency", cur),
                ("Trial", "✅" if trial_en else "❌"),
                ("Payment", "✅" if pay_en else "❌"),
                ("Force Join", "✅" if fj_en else "❌"),
            ]),
        )

    @staticmethod
    def trial_settings_rich(trial_en: int, trial_days: int, trial_gb: int) -> InputRichMessage:
        return rich_tables.rich_message(
            rich_tables.heading("🎉 Trial Settings"),
            rich_tables.kv_table([
                ("Enabled", "Yes" if trial_en else "No"),
                ("Days", trial_days),
                ("GB", trial_gb),
            ]),
        )

    @staticmethod
    def referral_settings_rich(ref_en: int, ref_days: int, ref_gb: int,
                                share_fa: str = "", share_en: str = "",
                                extra_fa: str = "", extra_en: str = "") -> InputRichMessage:
        blocks: List = [
            rich_tables.heading("🔗 Referral Settings"),
            rich_tables.kv_table([
                ("Enabled", "Yes" if ref_en else "No"),
                ("Bonus days", ref_days),
                ("Bonus GB", ref_gb),
                ("Share text 🇮🇷", "Custom" if (share_fa and share_fa.strip()) else "Default"),
                ("Share text 🇬🇧", "Custom" if (share_en and share_en.strip()) else "Default"),
                ("Extra note 🇮🇷", "✏️ set" if (extra_fa and extra_fa.strip()) else "—"),
                ("Extra note 🇬🇧", "✏️ set" if (extra_en and extra_en.strip()) else "—"),
            ]),
        ]
        # Short previews of any customised texts so the admin can see at a
        # glance what's currently configured without opening each editor.
        previews: list = []
        if share_fa and share_fa.strip():
            previews.append(f"🇮🇷 Share: {share_fa.strip()[:60]}{'…' if len(share_fa.strip()) > 60 else ''}")
        if share_en and share_en.strip():
            previews.append(f"🇬🇧 Share: {share_en.strip()[:60]}{'…' if len(share_en.strip()) > 60 else ''}")
        if extra_fa and extra_fa.strip():
            previews.append(f"🇮🇷 Note: {extra_fa.strip()[:60]}{'…' if len(extra_fa.strip()) > 60 else ''}")
        if extra_en and extra_en.strip():
            previews.append(f"🇬🇧 Note: {extra_en.strip()[:60]}{'…' if len(extra_en.strip()) > 60 else ''}")
        if previews:
            blocks.append(rich_tables.divider())
            blocks.append(rich_tables.paragraph("\n".join(previews)))
        return rich_tables.rich_message(*blocks)

    @staticmethod
    def payment_settings_rich(pay_en: int, card: str, holder: str, min_amt: int) -> InputRichMessage:
        return rich_tables.rich_message(
            rich_tables.heading("💳 Payment Settings"),
            rich_tables.kv_table([
                ("Enabled", "Yes" if pay_en else "No"),
                ("Card", card or "-"),
                ("Holder", holder or "-"),
                ("Min amount", min_amt),
            ]),
        )

    @staticmethod
    def force_join_settings_rich(fj_en: int, channels: list) -> InputRichMessage:
        blocks: List = [
            rich_tables.heading("📢 Force Join Settings"),
            rich_tables.kv_table([
                ("Enabled", "Yes" if fj_en else "No"),
                ("Channels", len(channels)),
            ]),
        ]
        if channels:
            rows = [(ch.get("title", ch.get("username", "Unknown")), str(ch.get("chat_id", "")))
                    for ch in channels]
            blocks.append(rich_tables.divider())
            blocks.append(rich_tables.heading("Channels", size=4))
            blocks.append(rich_tables.grid_table(["Title", "Chat ID"], rows, aligns=["left", "right"]))
        return rich_tables.rich_message(*blocks)

    @staticmethod
    def topup_settings_rich(topup_price: int, packages: list) -> InputRichMessage:
        return rich_tables.rich_message(
            rich_tables.heading("➕ Topup Settings"),
            rich_tables.kv_table([
                ("Price/GB", topup_price),
                ("Packages", ", ".join(str(p) for p in packages) if packages else "-"),
            ]),
        )

    # ------------------------------------------------------------------
    # USER-FACING wallet view (used by cb_balance / Wallet hub)
    # ------------------------------------------------------------------
    #
    # Why a dedicated builder (instead of reusing kv_table directly in the
    # handler): the wallet page is the one USER-FACING screen that benefits
    # from a real bordered/striped table (balance + orders + spent summary,
    # plus a recent-transactions grid).  Building it here keeps the handler
    # thin and lets us localize the column headers (kv_table hard-codes
    # "Field"/"Value" which looks wrong in Farsi).
    #
    # IMPORTANT: Rich Message heading/paragraph/cell text is PLAIN TEXT —
    # HTML tags like <b> are NOT rendered and would show up literally.  This
    # is exactly the bug that previously pushed the wallet page off tables
    # onto plain HTML text.  The fix is simply to NOT use HTML here: the
    # heading block is already bold by design, and emphasis comes from
    # emojis + column layout, not from <b> tags.

    @staticmethod
    def wallet_rich(balance, total_orders, total_spent, txs,
                    lang: str, currency: str, fmt: PriceFmt) -> InputRichMessage:
        """Build the user-facing Wallet rich message (summary + transactions).

        Renders a bordered/striped key/value table for the balance summary and
        a 3-column grid (Date | Type | Amount) for recent transactions.  Farsi
        gets ``is_rtl=True`` so the table lays out right-to-left.
        """
        is_rtl = (lang == "fa")
        if lang == "fa":
            title = "💳 کیف پول"
            sum_headers = ["بخش", "مقدار"]
            sum_rows = [
                ("💰 موجودی", fmt(balance, lang, currency)),
                ("🛒 سفارش‌ها", fmt_num(total_orders, lang)),
                ("💸 هزینه‌شده", fmt(total_spent, lang, currency)),
            ]
            tx_title = "📋 تراکنش‌های اخیر"
            tx_headers = ["تاریخ", "نوع", "مبلغ"]
            empty = "💡 هنوز تراکنشی ثبت نشده. با شارژ کیف پول شروع کن!"
            type_labels = {
                "purchase": "🛒 خرید",
                "renewal": "🔄 تمدید",
                "topup": "➕ حجم",
                "deposit": "💰 شارژ",
                "gift_balance": "🎁 هدیه",
                "gift_plan": "🎁 هدیه",
                "trial": "🆓 رایگان",
                "admin_adjust": "⚙️ ادمین",
            }
        else:
            title = "💳 Wallet"
            sum_headers = ["Field", "Value"]
            sum_rows = [
                ("💰 Balance", fmt(balance, lang, currency)),
                ("🛒 Total orders", fmt_num(total_orders, lang)),
                ("💸 Total spent", fmt(total_spent, lang, currency)),
            ]
            tx_title = "📋 Recent transactions"
            tx_headers = ["Date", "Type", "Amount"]
            empty = "💡 No transactions yet. Charge your wallet to get started!"
            type_labels = {
                "purchase": "🛒 Purchase",
                "renewal": "🔄 Renewal",
                "topup": "➕ Top-up",
                "deposit": "💰 Deposit",
                "gift_balance": "🎁 Gift",
                "gift_plan": "🎁 Gift",
                "trial": "🆓 Trial",
                "admin_adjust": "⚙️ Admin",
            }

        blocks: List = [rich_tables.heading(title),
                        rich_tables.grid_table(sum_headers, sum_rows,
                                               aligns=["left", "right"])]
        if txs:
            rows = []
            for tx in txs:
                label = type_labels.get(tx["type"], f"• {tx['type']}")
                amt = fmt(abs(tx["amount"]), lang, currency)
                # TX-SIGN-FIX: determine the display sign by transaction TYPE,
                # not by the stored amount.  purchase/renewal/topup store a
                # POSITIVE amount (so SUM(amount) in get_revenue_stats works),
                # but they represent money LEAVING the wallet — they must
                # display as "-".  deposit/gift_balance are always "+".
                # admin_adjust stores the real sign (+ for add, - for deduct).
                # Zero-amount rows (trial, gift_plan) show no sign at all.
                _t = tx["type"]
                if _t in ("purchase", "renewal", "topup"):
                    sign = "-"
                elif _t in ("deposit", "gift_balance"):
                    sign = "+"
                elif _t == "admin_adjust":
                    if tx["amount"] > 0:
                        sign = "+"
                    elif tx["amount"] < 0:
                        sign = "-"
                    else:
                        sign = ""
                else:
                    # trial, gift_plan, etc. — amount is 0
                    sign = "+" if tx["amount"] > 0 else ("-" if tx["amount"] < 0 else "")
                iso = fmt_iso(tx["created_at"])
                date = iso[5:16] if iso else ""
                rows.append((date, label, f"{sign}{amt}"))
            blocks.append(rich_tables.divider())
            blocks.append(rich_tables.heading(tx_title, size=4))
            blocks.append(rich_tables.grid_table(tx_headers, rows,
                                                 aligns=["center", "left", "right"]))
        else:
            blocks.append(rich_tables.divider())
            blocks.append(rich_tables.paragraph(empty))
        return rich_tables.rich_message(*blocks, is_rtl=is_rtl)

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _short_ts(ts_ms: int) -> str:
        """Render a unix-ms timestamp as YYYY-MM-DD in Iran time (or '-' if 0/None)."""
        if not ts_ms:
            return "-"
        try:
            return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(TEHRAN_TZ).strftime("%Y-%m-%d")
        except Exception:
            return "-"


# Rich Messages support is now ALWAYS available (inlined, no external module).
_HAS_RICH = True

# Load environment variables
load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot.db")
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "fa")  # default Persian
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "toman")  # toman | usd
EXPIRY_REMINDER_DAYS = [int(x) for x in os.getenv("EXPIRY_REMINDER_DAYS", "3,1").split(",") if x]
TRAFFIC_ALERT_THRESHOLD_1 = int(os.getenv("TRAFFIC_ALERT_THRESHOLD_1", "80"))
TRAFFIC_ALERT_THRESHOLD_2 = int(os.getenv("TRAFFIC_ALERT_THRESHOLD_2", "95"))

# Validation
if not BOT_TOKEN:
    print("FATAL: BOT_TOKEN is not set. Please configure the .env file.")
    sys.exit(1)
if not ADMIN_IDS:
    print("FATAL: ADMIN_IDS is not set. Please configure the .env file.")
    sys.exit(1)

# Logging — L6: log to BOTH stdout and a rotating file (5 MB x 3 backups by
# default). The rotating file lets admins inspect prior startup cycles without
# needing to scrape journald; the stdout handler keeps systemd/docker logs
# working as before.
from logging.handlers import RotatingFileHandler

file_handler = RotatingFileHandler(
    os.getenv("LOG_FILE", "bot.log"),
    maxBytes=int(os.getenv("LOG_MAX_BYTES", str(5 * 1024 * 1024))),  # 5 MB
    backupCount=int(os.getenv("LOG_BACKUP_COUNT", "3")),
    encoding="utf-8",
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        file_handler,
    ],
)
logger = logging.getLogger("vpnbot")
logging.getLogger("aiogram").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Constants
GB = 1073741824            # 1 GB in bytes
MS_PER_DAY = 86_400_000    # milliseconds per day
SUPPORTED_LANGS = ("en", "fa")
# Display timezone — all user/admin-facing timestamps are rendered in Iran
# Standard Time (UTC+03:30). Storage stays UTC; conversion happens at display.
TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30), name="IRST")

# ---------------------------------------------------------------------------
# Tunable constants (L1) — values that may need to be tweaked per deployment
# without editing the source. Centralised here so they don't get sprinkled
# through the code as magic numbers.
# ---------------------------------------------------------------------------
HTTP_TIMEOUT_SECONDS = 30.0                # httpx default timeout for panel API
HTTP_CONNECT_TIMEOUT = 10.0                # httpx connect timeout
TRIAL_DEFAULT_DAYS = 3                     # default trial duration (days)
TRIAL_DEFAULT_GB = 5                       # default trial traffic (GB)
TOPUP_DEFAULT_PRICE_PER_GB = 2000          # default top-up price per GB (toman)
PAYMENT_UNIQUE_SUFFIX_MIN = 100            # small 3-digit suffix lower bound (100–999)
PAYMENT_UNIQUE_SUFFIX_RANGE = 900          # small 3-digit suffix range → max +999 toman surcharge
EXPIRY_CHECK_INTERVAL_SECONDS = 3600       # expiry-checker task cadence (1h)
TRAFFIC_CHECK_INTERVAL_SECONDS = 600       # traffic-alert task cadence (10min)
SERVER_HEALTH_INTERVAL_SECONDS = 300       # server-health task cadence (5min)
SYNC_COUNTS_INTERVAL_SECONDS = 1800        # client-count sync cadence (30min)
DATA_RETENTION_INTERVAL_SECONDS = 86400    # data-retention task cadence (24h)
BROADCAST_THROTTLE_SECONDS = 0.05          # per-user delay between broadcast sends
PANEL_API_CONCURRENCY = 5                  # max concurrent panel API calls
TICKET_MESSAGE_MAX_CHARS = 800             # truncate ticket message body in views
TICKET_REPLY_MAX_CHARS = 2000              # max length of a ticket reply (caption/text)
GIFT_CODE_GROUPS = 4                       # gift code: number of "-"-separated groups
GIFT_CODE_GROUP_LEN = 4                    # gift code: chars per group
REFERRAL_CODE_LEN = 6                      # length of random suffix in REFxxxxxx
EMAIL_ENTROPY_BYTES = 8                    # bytes of entropy in gen_email
DEFAULT_PAYMENT_MIN_AMOUNT = 50000         # default payment minimum amount (toman)
BROADCAST_MAX_TEXT_CHARS = 4000            # truncate broadcast text (Telegram limit)
DB_BACKUP_INTERVAL_SECONDS = 86400         # scheduled DB backup cadence (24h, default fallback)

# ---- Purchase warning text (PURCHASE-WARNING) — shown as a collapsible
# <blockquote expandable> in the plan view + review page, in both EN and FA.
# The user asked for this exact wording so buyers understand the fair-use
# policy before paying.
PURCHASE_WARNING_EN = (
    "⚠️ <b>Please note:</b> If you share any of the provided configurations in "
    "public \u201cfree internet\u201d channels or with a large group of people "
    "(such as extended family, etc.), your configuration will be automatically "
    "deactivated once the system detects unusual traffic patterns and a heavy "
    "load. Therefore, please restrict usage to immediate family members only, "
    "so that we are not forced to disable your access and let you down."
)
PURCHASE_WARNING_FA = (
    "⚠️ <b>توجه:</b> در صورتی که هر یک از کانفیگ‌های ارائه‌شده را در کانال‌های "
    "عمومی اینترنت آزاد منتشر کنید یا در اختیار افراد زیاد (از جمله فامیل و ...) "
    "قرار دهید، سیستم به‌طور خودکار با تشخیص الگوی غیرعادی ترافیک و بار سنگین، "
    "کانفیگ شما را غیرفعال خواهد کرد. خواهشمندیم استفاده را صرفاً به اعضای خانواده "
    "محدود نمایید تا مجبور به غیرفعال‌سازی نشویم و شرمندهٔ شما نشویم."
)

# ---- Default guide texts (GUIDES) — editable by the admin. These are the
# rich defaults shown when the admin hasn't customised them yet. The admin
# can override each one individually from Settings → Guides.
DEFAULT_GUIDE_USAGE_EN = (
    "📖 <b>Using the bot — quick walkthrough</b>\n\n"
    "<b>1. Buy a plan</b>\n"
    "Tap 🛒 <b>Buy VPN</b> → pick a plan → review → <b>Confirm & Pay</b>. "
    "Your account is created instantly and the subscription link appears right away.\n\n"
    "<b>2. Check your account</b>\n"
    "📱 <b>My Accounts</b> shows every config you own: status, traffic used, "
    "expiry date, and the subscription link. Tap any account for details.\n\n"
    "<b>3. Renew or top up</b>\n"
    "Inside an account, tap 🔄 <b>Renew</b> to extend the expiry, or ➕ <b>Top-up Traffic</b> "
    "to add more data without changing the expiry.\n\n"
    "<b>4. Balance & payments</b>\n"
    "💳 <b>Charge Wallet</b> lets you pay by card. After paying, send your receipt "
    "(photo or text). An admin approves it and the balance lands in your wallet. "
    "You can also redeem 🎫 <b>Gift Codes</b> for instant balance.\n\n"
    "<b>5. Free trial</b>\n"
    "🎁 <b>Free Trial</b> gives you a small account to test the service. One trial per user.\n\n"
    "<b>6. Referrals</b>\n"
    "🔗 <b>Referral</b> gives you a personal invite link. When a friend buys their first "
    "plan using your link, you can claim bonus days + GB on your own account.\n\n"
    "<b>7. Support</b>\n"
    "💬 <b>Support</b> opens a ticket. Describe your issue and the team will reply — "
    "all within Telegram.\n\n"
    "<b>8. QR code</b>\n"
    "Inside any account, tap 📱 <b>QR</b> to get a scannable code for the subscription link — "
    "perfect for quickly importing into a mobile app."
)
DEFAULT_GUIDE_USAGE_FA = (
    "📖 <b>استفاده از ربات — راهنمای سریع</b>\n\n"
    "<b>۱. خرید پلن</b>\n"
    "روی 🛒 <b>خرید VPN</b> بزن → یه پلن انتخاب کن → بررسی → <b>تأیید و پرداخت</b>. "
    "اکانتت همین‌جا ساخته می‌شه و لینک سابسکریپشن فوراً نشون داده می‌شه.\n\n"
    "<b>۲. مشاهده اکانت</b>\n"
    "📱 <b>اکانت‌های من</b> همه‌ی کانفیگ‌هات رو نشون می‌ده: وضعیت، مصرف حجم، "
    "تاریخ انقضا و لینک سابسکریپشن. روی هر اکانت بزن تا جزئیاتش رو ببینی.\n\n"
    "<b>۳. تمدید یا افزایش حجم</b>\n"
    "توی هر اکانت، 🔄 <b>تمدید</b> رو بزن تا تاریخ انقضا عقب بره، یا ➕ <b>افزایش حجم</b> "
    "رو بزن تا بدون تغییر تاریخ، حجمت زیاد بشه.\n\n"
    "<b>۴. موجودی و پرداخت</b>\n"
    "💳 <b>شارژ کیف پول</b> بهت اجازه می‌ده با کارت پرداخت کنی. بعد از پرداخت، رسیدت "
    "(عکس یا متن) رو بفرست. یه ادمین تأییدش می‌کنه و موجودی به کیف پولت میاد. "
    "همچنین می‌تونی 🎫 <b>کد هدیه</b> برای موجودی آنی استفاده کنی.\n\n"
    "<b>۵. اکانت رایگان</b>\n"
    "🎁 <b>اکانت رایگان</b> یه اکانت کوچیک بهت می‌ده تا سرویس رو امتحان کنی. هر کاربر یه‌بار.\n\n"
    "<b>۶. دعوت دوستان</b>\n"
    "🔗 <b>دعوت دوستان</b> بهت یه لینک شخصی می‌ده. وقتی یه دوست با لینکت اولین پلنش رو خرید، "
    "می‌تونی پاداش روز + گیگ روی اکانت خودت دریافت کنی.\n\n"
    "<b>۷. پشتیبانی</b>\n"
    "💬 <b>پشتیبانی</b> یه تیکت باز می‌کنه. مشکلت رو بنویس تا تیم پشتیبانی جواب بده — "
    "همه‌چیز همین توی تلگرام.\n\n"
    "<b>۸. بارکد QR</b>\n"
    "توی هر اکانت، 📱 <b>QR</b> رو بزن تا یه بارکد از لینک سابسکریپشن بگیری — "
    "عالی برای وارد کردن سریع توی اپ موبایل."
)
DEFAULT_GUIDE_CONNECTION_EN = (
    "🔌 <b>How to connect — step by step</b>\n\n"
    "<b>What is a subscription link?</b>\n"
    "A subscription (sub) link is a single URL that contains all your VPN servers. "
    "When you paste it into a V2Ray client, the client fetches the full server list "
    "and keeps it updated automatically. You only need to add it once.\n\n"
    "<b>Pick your app:</b>\n\n"
    "📱 <b>Android — v2rayNG</b> (free, recommended)\n"
    "1. Install <b>v2rayNG</b> from Google Play or GitHub.\n"
    "2. Open the app → tap the menu (≡) → <b>Subscription</b> → <b>Subscription settings</b>.\n"
    "3. Tap <b>+</b> → paste your subscription URL → tap <b>✔</b>.\n"
    "4. Tap <b>Update subscription</b> (the ↻ icon).\n"
    "5. Go back → tap the server name → tap <b>V</b> at the bottom to connect.\n"
    "6. The key icon in your status bar means you're connected.\n\n"
    "📱 <b>iOS — Streisand</b> (free) or <b>V2Box</b>\n"
    "1. Install <b>Streisand</b> from the App Store.\n"
    "2. Open the app → <b>Settings</b> → <b>Subscriptions</b> → <b>Add</b>.\n"
    "3. Paste your subscription URL → <b>Save</b>.\n"
    "4. Go back → pick a server → toggle the switch to <b>On</b>.\n"
    "5. Allow Streisand to add a VPN configuration when iOS asks.\n\n"
    "💻 <b>Windows — v2rayN</b> (free)\n"
    "1. Download <b>v2rayN</b> from GitHub (get the latest release zip).\n"
    "2. Unzip and run <b>v2rayN.exe</b>.\n"
    "3. Menu → <b>Subscription</b> → <b>Subscription settings</b> → <b>Add</b>.\n"
    "4. Paste your subscription URL → <b>OK</b>.\n"
    "5. Menu → <b>Subscription</b> → <b>Update subscription</b>.\n"
    "6. Right-click the server in the list → <b>Set as active server</b>.\n"
    "7. Press <b>Enter</b> or click the big <b>V</b> icon to connect.\n\n"
    "🍎 <b>macOS — Foxray</b> (App Store) or <b>V2RayU</b>\n"
    "1. Install <b>Foxray</b> from the App Store.\n"
    "2. Open Foxray → <b>Subscription</b> tab → <b>+</b>.\n"
    "3. Paste your subscription URL → <b>Update</b>.\n"
    "4. Switch to the <b>Servers</b> tab → pick one → toggle <b>On</b>.\n\n"
    "💡 <b>Tips</b>\n"
    "• If a server feels slow, try another one from the subscription list.\n"
    "• If your connection suddenly stops working, tap <b>Update subscription</b> "
    "in your app — the server list refreshes and new configs appear.\n"
    "• The subscription link is personal. Don't share it (see the warning on the buy page).\n\n"
    "🛠 <b>Not working?</b>\n"
    "1. Make sure you tapped <b>Update subscription</b> after pasting the link.\n"
    "2. Try a different server from the list.\n"
    "3. If nothing works, open a 💬 <b>Support</b> ticket — send a screenshot of the error."
)
DEFAULT_GUIDE_CONNECTION_FA = (
    "🔌 <b>نحوه اتصال — قدم به قدم</b>\n\n"
    "<b>لینک سابسکریپشن چیه؟</b>\n"
    "لینک سابسکریپشن (sub) یه آدرسه که همه‌ی سرورهای VPN توش هست. وقتی توی یه کلاینت V2Ray "
    "می‌ذاریش، کلاینت لیست کامل سرورها رو می‌گیره و خودکار به‌روز نگه می‌داره. فقط یه‌بار لازمه اضافه کنی.\n\n"
    "<b>برنامه‌ت رو انتخاب کن:</b>\n\n"
    "📱 <b>اندروید — v2rayNG</b> (رایگان، پیشنهاد ما)\n"
    "۱. <b>v2rayNG</b> رو از گوگل پلی یا گیت‌هاب نصب کن.\n"
    "۲. اپ رو باز کن → منو (≡) → <b>Subscription</b> → <b>Subscription settings</b>.\n"
    "۳. <b>+</b> رو بزن → لینک سابسکریپشنت رو پیست کن → <b>✔</b>.\n"
    "۴. <b>Update subscription</b> (آیکون ↻) رو بزن.\n"
    "۵. برگرد → اسم سرور رو بزن → <b>V</b> پایین صفحه رو بزن تا وصل بشی.\n"
    "۶. آیکون کلید توی نوار وضعیت یعنی وصل شدی.\n\n"
    "📱 <b>آی‌او‌اس — Streisand</b> (رایگان) یا <b>V2Box</b>\n"
    "۱. <b>Streisand</b> رو از اپ استور نصب کن.\n"
    "۲. اپ رو باز کن → <b>Settings</b> → <b>Subscriptions</b> → <b>Add</b>.\n"
    "۳. لینک سابسکریپشنت رو پیست کن → <b>Save</b>.\n"
    "۴. برگرد → یه سرور انتخاب کن → کلید رو روی <b>On</b> بذار.\n"
    "۵. وقتی آی‌او‌اس پرسید، اجازه بده تنظیمات VPN رو اضافه کنه.\n\n"
    "💻 <b>ویندوز — v2rayN</b> (رایگان)\n"
    "۱. <b>v2rayN</b> رو از گیت‌هاب دانلود کن (آخرین نسخه zip).\n"
    "۲. از حالت فشرده خارج کن و <b>v2rayN.exe</b> رو اجرا کن.\n"
    "۳. منو → <b>Subscription</b> → <b>Subscription settings</b> → <b>Add</b>.\n"
    "۴. لینک سابسکریپشنت رو پیست کن → <b>OK</b>.\n"
    "۵. منو → <b>Subscription</b> → <b>Update subscription</b>.\n"
    "۶. روی سرور توی لیست راست‌کلیک کن → <b>Set as active server</b>.\n"
    "۷. <b>Enter</b> رو بزن یا آیکون بزرگ <b>V</b> رو بزن تا وصل بشی.\n\n"
    "🍎 <b>مک — Foxray</b> (اپ استور) یا <b>V2RayU</b>\n"
    "۱. <b>Foxray</b> رو از اپ استور نصب کن.\n"
    "۲. Foxray رو باز کن → تب <b>Subscription</b> → <b>+</b>.\n"
    "۳. لینک سابسکریپشنت رو پیست کن → <b>Update</b>.\n"
    "۴. به تب <b>Servers</b> برو → یکی رو انتخاب کن → <b>On</b> کن.\n\n"
    "💡 <b>نکته‌ها</b>\n"
    "• اگه یه سرور کند به نظر میاد، یه سرور دیگه از لیست ساب امتحان کن.\n"
    "• اگه اتصالت یهو قطع شد، توی اپ <b>Update subscription</b> رو بزن — لیست سرورها به‌روز می‌شه.\n"
    "• لینک سابسکریپشن شخصیه. به کسی ندید (هشدار صفحه خرید رو ببین).\n\n"
    "🛠 <b>کار نمی‌کنه؟</b>\n"
    "۱. مطمئن شو بعد از پیست کردن، <b>Update subscription</b> رو زدی.\n"
    "۲. یه سرور دیگه از لیست امتحان کن.\n"
    "۳. اگه هیچ‌کدوم جواب نداد، یه تیکت 💬 <b>پشتیبانی</b> باز کن — اسکرین‌شات خطا رو بفرست."
)

# Concurrency limiter for panel API calls from background tasks (M15).
# The bot runs on a 1-core / 1 GB RAM box alongside the 3x-ui panel, so we
# cannot fire 100 concurrent HTTP requests when iterating servers/accounts.
# PANEL_API_CONCURRENCY is a safe ceiling that keeps memory predictable while
# still parallelising.
PANEL_API_SEMAPHORE = asyncio.Semaphore(PANEL_API_CONCURRENCY)
FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"


# ============================================================================
# SECTION 1: INTERNATIONALISATION (EN / FA)
# ============================================================================
#
# Every user-facing string lives in MESSAGES.  Admin reports use English box
# tables for alignment; user cards use a soft emoji format that renders well
# in both LTR and RTL.

MESSAGES: Dict[str, Dict[str, str]] = {
    # ---------------------------------------------------------------- English
    "en": {
        # generic
        "cancel": "❌ Cancel",
        "back": "🔙 Back",
        "back_menu": "🏠 Main Menu",
        "back_admin": "🔙 Admin Menu",
        "action_cancelled": "❌ Action cancelled.\n\nBack to main menu 👇",
        "yes": "Yes",
        "no": "No",
        "loading": "⏳ Please wait...",
        "not_found": "Not found.",
        "access_denied": "⛔ Access denied.",
        "admin_only": "⛔ Admin only.",
        "banned": "🚫 You have been banned from using this bot.",
        "send_or_cancel": "Send your answer, or tap Cancel.",
        "invalid_number": "❌ Please enter a valid number.",
        "copied": "📋 Copied to clipboard.",
        # main menu
        "welcome": (
            "👋 <b>Hey, welcome to {bot_name}!</b>\n\n"
            "Glad to see you here. This is your VPN control panel — "
            "everything happens right inside Telegram, nice and simple.\n\n"
            "<b>Here's what you can do:</b>\n"
            "• 🛒 Grab a VPN plan in a few taps\n"
            "• 📱 Check your account status and traffic\n"
            "• 🔄 Renew or top up whenever you need\n"
            "• 🎁 Try a free trial first, no strings attached\n"
            "• 🔗 Invite friends and earn bonus data\n"
            "• 💬 Reach support without ever leaving the chat\n\n"
            "Tap something below to get going 👇"
        ),
        "menu_main": "🏠 <b>Main Menu</b>\n\nWhat can I do for you?",
        "buy": "🛒 Buy Service",
        "my_accounts": "📱 My Accounts",
        "trial": "🎁 Free Trial",
        "balance": "💳 Wallet",
        "referral": "🔗 Referral",
        "gift": "🎫 Gift Code",
        "support": "💬 Support",
        "guide": "📚 Guide",
        "language": "🌐 Language",
        "admin_panel": "⚙️ Admin Panel",
        # MENU-RESTRUCTURE: new combined sections
        "wallet": "💳 Wallet",
        "wallet_title": "💳 <b>Wallet</b>",
        "help": "📚 Help & Support",
        "help_title": "📚 <b>Help & Support</b>",
        "help_desc": "Need a hand? Browse the guides or open a support ticket — we're here to help.\n\n• 📖 Guides for using the bot and connecting\n• 🎫 Open a ticket for any issue\n• ⏱ We usually reply within a few hours",
        "more_features": "✨ More Features",
        "more_features_title": "✨ <b>More Features</b>\n\nExtra settings and options:",
        # buy flow
        "choose_plan": "🛒 <b>Pick a plan</b>\n\nChoose the one that fits you — you can always renew or top up later:",
        "no_plans": "😔 No plans available yet. Please check back later or contact support.",
        "your_balance": "💳 Your balance: <b>{balance}</b>",
        "sufficient": "✅ Your balance is enough for this plan.",
        "insufficient": "⚠️ <b>Not enough balance.</b>\nYou still need <b>{diff}</b> to buy this plan.",
        "review_short_hint": "👇 Tap the <b>“Pay Exact Shortfall”</b> button below to top up your wallet for this plan. You can also use a <b>gift code</b> if you have one.",
        # BUG-7 FIX: separate hint for the renew page (which shows a
        # "Charge Wallet" button, NOT the purchase-review's shortfall
        # button). The review_short_hint above was wrongly reused here and
        # pointed at a button that doesn't exist on the renew page.
        "renew_short_hint": "👇 Tap the <b>“Charge Wallet”</b> button below to top up your wallet, then come back to renew.",
        "ask_account_name": (
            "✏️ <b>Name this config (optional)</b>\n\n"
            "Send a short label like <code>phone</code> or <code>laptop</code> so you can tell your configs apart.\n"
            "Only letters, numbers, <code>-</code> and <code>_</code>.\n\n"
            "Send <code>-</code> or just hit Cancel to use an automatic name."
        ),
        "invalid_name": "❌ Hmm, that name won't work. Use 2-24 characters: letters, digits, dash or underscore.",
        "review_purchase": "📋 <b>Review your order</b>\nCheck the details, add a name or promo if you like, then confirm.",
        "confirm_pay": "✅ Confirm & Pay",
        "apply_promo": "🎟 Add Promo Code",
        "set_name_btn": "✏️ Name Config",
        # SHORTFALL-REQUEST: shown on the purchase review page when the user
        # can't afford the plan and card payments are enabled.
        "request_shortfall_btn": "⚡ Pay Exact Shortfall",
        "shortfall_payment_info": (
            "⚡ <b>Shortfall payment for {plan_name}</b>\n\n"
            "You need exactly <b>{shortfall}</b> more to buy this plan.\n\n"
            "💳 Card number: <code>{card_number}</code>\n"
            "👤 Card holder: {card_holder}\n\n"
            "💵 Amount to transfer (with unique suffix, tap a number to copy):\n"
            "{amount_block}\n\n"
            "💡 Tap an amount to copy it — no commas, ready to paste.\n\n"
            "After transferring, tap \"Send Receipt\" below and attach your receipt."
        ),
        "name_auto": "(auto)",
        "name_label": "🏷 Name",
        "promo_label": "🎟 Promo",
        "promo_none": "none",
        "price_label": "💵 Price",
        "final_price_label": "💰 To pay",
        "discount_label": "✂️ You save",
        "creating_account": "⏳ Creating your VPN account...",
        "purchase_success": "✅ <b>Account created successfully!</b>",
        "purchase_failed": "❌ Failed to create account: {msg}\n\nPlease try again or contact support.",
        "no_servers": "❌ No servers available right now. Please contact support.",
        "no_inbounds": "❌ This plan has no usable inbounds on any server. Please contact support.",
        # promo
        "enter_promo": "🎟 <b>Enter promo code</b>\n\nSend me your code:",
        "promo_invalid": "❌ Invalid or expired promo code.\n\nTry again or cancel:",
        "promo_applied": "🎟 Promo <b>{code}</b> applied — you saved <b>{discount}</b>!",
        # accounts
        "my_accounts_title": "📱 <b>My Accounts</b>",
        "no_accounts": "📱 <b>My Accounts</b>\n\nYou don't have any VPN accounts yet.\n\n🛒 Buy your first plan below!",
        "select_account": "Select an account to view details:",
        "renew": "🔄 Renew",
        "traffic": "📈 Traffic",
        "get_link": "🔗 Links",
        "disable": "⛔ Disable",
        "enable": "✅ Enable",
        "qr": "📱 QR",
        "topup_traffic": "➕ Top-up Traffic",
        "set_label": "🏷 Label",
        "delete": "🗑 Delete",
        "acc_disabled": "⛔ Account <code>{email}</code> disabled.\n\nYou can re-enable it anytime.",
        "acc_enabled": "✅ Account <code>{email}</code> enabled.\n\nYou can now connect.",
        "renew_success": "✅ <b>Account renewed!</b>",
        "renew_failed": "❌ Renewal failed: {msg}",
        # RENEW-EXPLAIN: confirmation screen explaining what renewal does to
        # remaining days/traffic before the user commits.  Keys with {…}
        # placeholders are filled in cb_account_renew with computed values.
        "renew_how_title": "ℹ️ <b>How renewal works</b>",
        "renew_how_deduct": "💵 Plan price is deducted from your wallet balance.",
        "renew_how_days_add": "📅 Remaining days are <b>added</b> to the plan's duration.",
        "renew_how_expired": "⏰ Your subscription has expired — the new period starts from now.",
        "renew_how_traffic_add": "💾 Remaining traffic is <b>added</b> to the plan's traffic.",
        "renew_how_unlimited": "♾️ Traffic stays <b>Unlimited</b>.",
        "renew_after_title": "📋 <b>After renewal:</b>",
        "renew_after_days": "📅 Duration: <b>{days}</b>",
        "renew_after_traffic": "💾 Traffic: <b>{traffic}</b>",
        "renew_after_balance": "💳 Balance: <b>{balance}</b>",
        "renew_sure": "❓ <b>Are you sure you want to renew this account?</b>",
        "renew_confirm_btn": "✅ Yes, Renew Now",
        "delete_confirm": "🗑 <b>Delete account?</b>\n\n<code>{email}</code>\n\nThis permanently removes the account from the panel. This cannot be undone.",
        "confirm_delete": "🗑 Confirm Delete",
        "acc_deleted": "🗑 Account <code>{email}</code> deleted.",
        "ask_label": "🏷 <b>Set account label</b>\n\nSend a short label (max 30 chars) or <code>-</code> to clear:",
        "label_set": "🏷 Label updated to <b>{label}</b>.",
        "label_cleared": "🏷 Label cleared.",
        # traffic
        "traffic_title": "📈 <b>Traffic Details</b>",
        "online": "Online",
        "offline": "Offline",
        "active_ips": "Active IPs",
        # account-card labels — clearer for the user (was just bare values)
        "card_plan": "Plan",
        "card_remaining_traffic": "Remaining traffic",
        "card_remaining_time": "Time remaining",
        "card_used": "Used",
        "card_total": "Total quota",
        "card_account_status": "Account status",
        "card_uploaded": "Uploaded",
        "card_downloaded": "Downloaded",
        "unlimited": "Unlimited",
        # top-up
        "topup_title": "➕ <b>Traffic Top-up</b>\n\nChoose a package to add traffic without changing your expiry date:",
        "topup_success": "✅ <b>Traffic added!</b>\n+{gb} GB added to <code>{email}</code>.",
        # trial
        "trial_disabled": "😔 Free trials are currently disabled.\n\nPlease check back later.",
        "trial_used": "🎁 <b>Free Trial</b>\n\nYou have already used your free trial.\nEach user is limited to one trial — even if the trial account is deleted.\n\n🛒 Check out our affordable plans!",
        "trial_offer": "🎁 <b>Free Trial Offer</b>",
        "get_trial": "✅ Get Free Trial",
        "trial_created": "🎉 <b>Trial account created!</b>",
        "trial_failed": "❌ Failed to create trial: {msg}",
        "trial_no_renew": "🎁 Trial accounts cannot be renewed or topped up.\nBuy a paid plan to continue.",
        # L10N-GAPS: user-facing strings that were previously hardcoded English.
        "plan_not_found": "❌ Plan not found.",
        "plan_not_found_buy": "❌ Plan not found — buy a new plan.",
        "qr_caption_sub": "📡 Subscription",
        "qr_caption_link": "🔗 Connection link",
        "action_failed": "❌ Failed: {msg}",
        "gift_plan_create_failed": "❌ {msg}\n\n⚠️ Your gift code was claimed but the account could not be created. Please contact support with code <code>{code}</code>.",
        "gift_plan_db_failed": "❌ Internal error. Please contact support with code <code>{code}</code>.",
        # balance
        "balance_title": "💳 <b>Your Balance</b>",
        "recent_tx": "📋 <b>Recent transactions</b>",
        "topup_hint": "💡 Use Charge Wallet to add balance, or redeem a gift code.",
        # referral
        "referral_title": "🔗 <b>Referral Program</b>",
        "referral_disabled": "😔 The referral program is currently disabled.",
        "referral_desc": "Invite friends and earn rewards automatically when they buy their first plan!",
        "referral_how": "📤 <b>How it works</b>\n1️⃣ Share your referral link with friends\n2️⃣ They sign up and buy their first plan\n3️⃣ You get +{days} days and +{gb} GB — claim it on your account anytime",
        "your_link": "📤 <b>Your referral link</b>",
        "share_link": "📤 Share Link",
        "referral_share_text": "🚀 Hey! I've been using this VPN bot and it's awesome — fast, cheap, and super easy. Sign up with my link and we BOTH get a bonus 🎁 (+{days} days & +{gb} GB for me!). Tap and let's get you connected 👇",
        "referral_stats": "📊 <b>Your Stats</b>",
        "referral_history": "📋 <b>Recent referrals</b>",
        "referral_no_history": "No referrals yet — share your link to start earning!",
        "ref_status_bought": "✅ Bought",
        "ref_status_pending": "⏳ Pending",
        "ref_claim_btn": "🎁 Claim Reward",
        "ref_claimable": "🎁 You have <b>{count}</b> unclaimed referral reward(s)!",
        "ref_claim_success": "✅ <b>Reward claimed!</b>\n\n🎁 +{days} days and +{gb} GB added to <code>{email}</code>.\n\nThanks for spreading the word!",
        "ref_claim_no_account": "⚠️ You need an active paid account to claim referral rewards.\n\nBuy a plan first, then come back here to claim your bonus.",
        "ref_claim_none": "✅ No unclaimed rewards right now.\n\nShare your link to earn more!",
        "ref_claim_pick": "Pick the account to receive the bonus:",
        "ref_claim_failed": "❌ <b>Couldn't apply the reward</b>\n\nThe panel returned an error: <code>{msg}</code>\n\nYour referral bonus is untouched — please try again in a moment.",
        "delete_failed": "⚠️ <b>Couldn't delete the account</b>\n\nThe panel returned an error: <code>{msg}</code>\n\nYour account is still active. Please try again in a moment.",
        # gift
        "enter_gift": "🎫 <b>Redeem gift code</b>\n\nSend me your code:",
        "gift_invalid": "❌ Invalid gift code. Try again:",
        "gift_used_code": "❌ This code has already been used.",
        "gift_balance_ok": "✅ <b>Gift redeemed!</b>\n💰 <b>{amount}</b> added to your balance.",
        "gift_plan_ok": "✅ <b>Gift redeemed!</b>\n🎁 Plan: <b>{plan}</b>",
        # Gift code inside the Buy Service review page (purchase context).
        # Only balance-type codes are accepted here because the purpose is
        # to top up the wallet so the user can afford the selected plan.
        "gift_in_purchase_hint": "🎫 <b>Redeem gift code</b>\n\nSend me your code.\n\nℹ️ Only <b>balance-type</b> gift codes are accepted here — they top up your wallet so you can buy this plan.",
        "gift_plan_not_allowed_in_purchase": "❌ This is a <b>plan-type</b> gift code. Here only <b>balance-type</b> codes are accepted (to top up your wallet). Please send a balance-type code, or redeem this plan code from the main menu → Wallet → Gift Code.",
        "gift_balance_ok_back_to_purchase": "✅ <b>Gift redeemed!</b>\n💰 <b>{amount}</b> added to your balance.\n\nReturning to your plan…",
        "payment_disabled_gift_only": "⚠️ Card-to-card payment is currently disabled. The only way to top up your wallet here is by using a <b>gift code</b>.",
        "gift_btn": "🎫 Gift Code",
        # support
        "support_title": "💬 <b>Support Center</b>",
        "support_desc": "Need a hand? Open a ticket and our team will jump in.\n\n• 🎫 Open a ticket for any issue\n• ⏱ We usually reply within a few hours\n• 🔒 Your conversation is private",
        "new_ticket": "🎫 New Ticket",
        "my_tickets": "📋 My Tickets",
        "choose_category": "🎫 <b>New support ticket</b>\n\nChoose a category:",
        "cat_technical": "🔧 Technical",
        "cat_payment": "💰 Payment",
        "cat_account": "👤 Account",
        "cat_other": "📝 Other",
        "ask_subject": "📝 <b>Category:</b> {category}\n\nNow send a short subject for your ticket:",
        "ask_message": "📝 <b>Subject:</b> {subject}\n\nNow describe your issue in detail.\n\n💡 You can also attach a file (photo, video, voice, audio, document, sticker, GIF, or round-video) — send it with a caption.",
        "ticket_created": "✅ <b>Ticket #{id} created!</b>\n\n📝 Subject: {subject}\n🏷 Category: {category}\n⏱ We will respond as soon as possible.",
        "reply": "💬 Reply",
        "reopen": "🔓 Reopen",
        "close": "🔒 Close",
        "ask_reply": "💬 <b>Reply to ticket #{id}</b>\n📝 {subject}\n\nType your message:",
        "ask_reply_with_media": "💬 Send your reply. You can send text OR attach ANY file type: photo, video, voice, audio/music, document, sticker, GIF, or round-video. (Attach with a caption if you want to add text.)",
        "manage_user": "👤 Manage User",
        "view_media": "📎 View Media",
        "media_photo": "Photo",
        "media_document": "Document",
        "media_video": "Video",
        "media_voice": "Voice",
        "media_audio": "Audio / Music",
        "media_animation": "GIF",
        "media_video_note": "Round Video",
        "media_sticker": "Sticker",
        "media_sent": "✅ Media sent.",
        "reply_sent_admin": "✅ Reply sent to user.",
        "reply_sent_user": "✅ Reply sent to admin.",
        "ticket_closed": "🔒 <b>Ticket #{id} has been closed.</b>\n\nIf you need further help, reopen it or open a new ticket.",
        "ticket_reopened": "🔓 <b>Ticket #{id} reopened.</b>\n\nOur team will respond shortly.",
        "no_tickets": "📋 <b>My Tickets</b>\n\nYou have no tickets yet.",
        "ticket_status_open": "🟢 Open",
        "ticket_status_waiting_admin": "🟡 Waiting for admin",
        "ticket_status_waiting_user": "🔵 Waiting for you",
        "ticket_status_closed": "🔴 Closed",
        "tickets_filter_open": "🟢 Open",
        "tickets_filter_all": "📋 All",
        # guide
        "guide_title": "📚 <b>Guide Center</b>",
        "guide_usage_title": "📖 <b>Using the bot</b>",
        "guide_connection_title": "🔌 <b>Connection Guide</b>",
        "guide_usage_btn": "📖 Using the Bot",
        "guide_connection_btn": "🔌 How to Connect",
        # language
        "lang_title": "🌐 <b>Language / زبان</b>\n\nChoose your language:",
        "lang_set": "✅ Language set to English.",
        # delivery
        "conn_links": "🔗 <b>Connection links</b>",
        "sub_url": "📡 <b>Subscription URL</b> (auto-updates all servers)",
        "links_sub_only": "📡 <b>Your subscription link</b>\n\nUse this single URL in any V2Ray client (v2rayNG, Streisand, v2rayN, Foxray…) — it auto-syncs all servers and stays up to date.",
        "qr_sub": "🖼 QR Code",
        "how_to_use": "📱 How to connect",
        # misc
        "help_text": (
            "ℹ️ <b>Help</b>\n\n"
            "Use the menu buttons to navigate. Commands:\n"
            "/start — open main menu\n"
            "/language — switch language\n"
            "/help — this message\n"
            "/admin — admin panel (admins only)\n"
            "/cancel — cancel any in-progress action"
        ),
        "cancelled_action": "❌ Cancelled.",

        "charge_wallet": "💳 Charge Wallet",
        "payment_disabled": "💳 Payment is currently disabled.",
        "choose_amount": "💳 <b>Choose amount to charge</b>\n\nSelect a preset amount or enter a custom amount:",
        "custom_amount": "✏️ Custom Amount",
        "enter_custom_amount": "💳 Enter the amount you want to charge (minimum: {min} Toman):",
        "payment_info": (
            "💳 <b>Payment Details</b>\n\n"
            "💳 Card: <code>{card_number}</code>\n"
            "👤 Card holder: {card_holder}\n\n"
            "💰 <b>Pay EXACTLY this amount (tap a number to copy):</b>\n"
            "{amount_block}\n\n"
            "⚠️ The extra digits are for verification. Pay the exact amount shown above.\n"
            "💡 Tap an amount to copy it — no commas, ready to paste in your banking app.\n\n"
            "After payment, send your receipt (photo or text) using the button below."
        ),
        "send_receipt": "📤 Send Receipt",
        "enter_receipt_text": "📸 <b>Send your receipt</b>\n\nYou can send a <b>photo</b> (screenshot of the payment) or <b>type</b> the details (transaction ID, time, etc).\n\nTip: a photo with a caption is best.",
        "receipt_empty": "❌ Please send a photo or some text as the receipt.",
        "receipt_received": "✅ Receipt received! Your payment is pending admin review.\n\nAmount: {amount} Toman\nYou'll be notified once it's approved.",
        "payment_approved": "✅ <b>Payment Approved!</b>\n\n💰 {amount} Toman added to your balance.\n💳 New balance: {balance}",
        "payment_rejected": "❌ <b>Payment Rejected</b>\n\nReason: {reason}\n\nPlease contact support if you have questions.",
        "pending_payments": "💰 <b>Pending Payments</b>",
        "approve_payment": "✅ Approve",
        "reject_payment": "❌ Reject",
        "enter_reject_reason": "❌ Enter rejection reason (or send <code>-</code> for no reason):",
        # TOPUP-TOGGLE: shown as an alert toast when a user taps a stale top-up
        # button (sitting in an old message from before the admin disabled
        # top-ups).
        "topup_disabled": "❌ Top-ups are currently disabled by the admin. Please try again later.",
        "topup_unlimited_noop": "❌ This account is unlimited — top-up is not needed. Use Renew to extend the duration.",
        "force_join": (
            "🔒 <b>Please join our channel first!</b>\n\n"
            "You must join the following channel(s) to use this bot:\n\n"
            "{channels}\n\n"
            "After joining, click ✅ below to continue."
        ),
        "verify_join": "✅ I Joined",
        "force_join_success": "✅ Membership verified! You can now use the bot.",
        "force_join_failed": "❌ You haven't joined all required channels yet.\nPlease join the channels listed above first, then press the ✅ button.",
        # FORCE-JOIN-FEEDBACK: persistent message shown when the user taps
        # "✅ I Joined" without actually joining.  Distinct from the original
        # force_join prompt (starts with ❌ instead of 🔒) so Telegram's
        # "message is not modified" error is NOT triggered — the message
        # visibly changes every time, giving the user clear feedback.
        "force_join_not_joined": (
            "❌ <b>You haven't joined yet!</b>\n\n"
            "You are still not a member of all required channels:\n\n"
            "{channels}\n\n"
            "👉 Please join the channels above first, then press ✅ below."
        ),
        "no_inbounds_configured": "❌ This plan has no configured inbounds. Please contact admin.",
        "broadcast_header_en": "📢 <b>Public Announcement</b>\n\n",
        "charge_wallet_btn": "💳 Charge Wallet",
        # background-task subject lines (M5 — moved out of inline if/else)
        "expiry_reminder_subject": "Subscription expiring soon!",
        "account_expired_subject": "Account expired",
        "traffic_depleted_subject": "Traffic depleted",
        # admin panel i18n (M11) — most-used admin strings
        "admin_dashboard": "⚙️ Admin Dashboard",
        "servers": "Servers",
        "users": "Users",
        "tickets": "Tickets",
        "plans": "Plans",
        "promos": "Promo Codes",
        "gift_codes": "Gift Codes",
        "settings": "Settings",
        "broadcast": "Broadcast",
        "cleanup": "Cleanup",
        "approved": "✅ Approved",
        "rejected": "❌ Rejected",
        "toggled": "✅ Toggled",
        "not_pending": "Not pending.",
        "already_processed": "⚠️ Already processed by another admin.",
        # ---- Payment-admin screens (PA-LANG) ----
        # Full admins always see English; payment-only admins see their
        # selected language.  These keys are used by the payment-admin
        # menu, pending-payments view, payment-history view, payment-detail
        # view, and approve/reject flows.
        "pa_menu_title": "💰 Payment Admin Panel",
        "pa_menu_desc": "You can approve or reject pending payments.",
        "pa_pending_btn": "💰 Pending Payments",
        "pa_history_btn": "📋 Payment History",
        "pa_no_pending": "💰 No pending payments",
        "pa_no_more_pending": "💰 No more pending payments",
        "pa_pending_header": "💰 Pending Payments — {i}/{n}",
        "pa_payment_title": "💰 Payment #{id}",
        "pa_user": "👤 User: {name} ({id})",
        "pa_base_amount": "💵 Base amount: {amt} Toman",
        "pa_unique_amount": "💵 Unique amount: {amt} Toman",
        "pa_card": "💳 Card: {num}",
        "pa_created": "📅 Created: {date}",
        "pa_receipt_text": "{icon} Receipt text: {text}",
        "pa_receipt_kind": "{icon} Receipt: {kind}",
        "pa_status": "Status: {status}",
        "pa_status_pending": "pending",
        "pa_status_approved": "approved",
        "pa_status_rejected": "rejected",
        "pa_approve_btn": "✅ Approve",
        "pa_reject_btn": "❌ Reject",
        "pa_next_btn": "⏭ Next pending ({i}/{n})",
        "pa_full_history_btn": "📋 Full history",
        "pa_history_btn2": "📋 History",
        "pa_pending_back_btn": "🔙 Pending",
        "pa_admin_back_btn": "🔙 Admin",
        "pa_receipt_caption": "📎 Receipt for payment #{id} — {name}",
        "pa_reviewed_by": "🛡 {action} by: {admin} ({id})",
        "pa_action_approved": "Approved",
        "pa_action_rejected": "Rejected",
        "pa_reviewed_at": "🕒 Reviewed: {date}",
        "pa_reject_reason": "❌ Reason: {reason}",
        "pa_no_payments": "📋 No payments yet",
        "pa_history_title": "📋 Payment History (latest 20)",
        "pa_history_header": "ID • User • Amount • Status • Receipt • Approved by",
        "pa_approve_failed": "❌ Approve failed: {err}",
        "pa_approved_msg": "✅ Payment #{id} approved\n💰 {amt} Toman added to user {uid}\n\n✅ No more pending payments",
        "pa_approved_toast": "✅ Approved",
        "pa_not_found_processed": "Payment not found or already processed.",
        "pa_already_processed_msg": "⚠️ Payment was already processed by another admin.",
        "pa_rejected_msg": "❌ Payment #{id} rejected",
        # PAY-HISTORY-REWORK: table view, per-admin history, payment-admin picker.
        "pa_history_my_title": "📋 My Approval History",
        "pa_history_all_title": "📋 Payment History (latest 30)",
        "pa_history_admins_title": "📋 Approvals by Payment Admin",
        "pa_history_admin_title": "📋 Approvals by {admin}",
        "pa_history_admins_header": "Admin • Approved • Rejected • Total",
        "pa_history_admins_pick": "👇 Tap an admin to review their approvals",
        "pa_history_admins_none": "📋 No payment admins yet — add one from the main admin panel.",
        "pa_history_admin_none": "📋 This admin hasn't approved any payments yet.",
        "pa_no_own_approvals": "📋 You haven't approved any payments yet.",
        "pa_my_history_btn": "📋 My Approvals",
        "pa_all_history_btn": "📋 All Receipts",
        "pa_admins_history_btn": "👥 By Admin",
        "pa_view_user_finance": "💼 View User Finance",
        "pa_view_user_payments": "🧾 User Receipts",
        # User-finance view (admin → user detail financial history).
        "uf_title": "💼 Financial History — {name}",
        "uf_no_data": "No financial records for this user.",
        "uf_tx_header": "ID • Type • Amount • Date • Description",
        "uf_pay_header": "ID • Amount • Status • Receipt • Date",
        "uf_balance": "Balance: {amt}",
        "uf_spent": "Total Spent: {amt}",
        "uf_orders": "Total Orders: {n}",
        "uf_back_user": "🔙 User",
        # Referral invitee list.
        "ref_invitees_title": "📋 Your Invitees",
        "ref_invitees_header": "ID • Name • Status • Joined",
        "ref_invitees_none": "No invitees yet.",
        "ref_invitees_btn": "👥 Show Invitees",
        "admin_ref_invitees_btn": "👥 View Invitees",
        # ADMIN-MENU-REWORK: top-level admin panel reorganised into submenus.
        "am_payments_menu": "💳 Payments",
        "am_payments_menu_title": "💳 Payments Management",
        "am_payments_menu_desc": "Approve pending payments, review receipt history, or audit each payment admin's approvals.",
        "am_promos_menu": "🎁 Promotions",
        "am_promos_menu_title": "🎁 Promotions & Marketing",
        "am_promos_menu_desc": "Promo codes, gift codes, and broadcasts.",
        "am_support_menu": "💬 Support",
        "am_support_menu_title": "💬 Support & Tickets",
        "am_support_menu_desc": "View and reply to user support tickets.",
        "am_back_admin": "🔙 Admin Panel",
        # USER-ACCOUNTS-REWORK: dedicated Accounts sub-section inside the
        # Users admin area (accounts are no longer scattered in user_view).
        "am_user_accounts_btn": "📱 Accounts",
        "am_user_accounts_none": "No accounts.",
    },
    # ------------------------------------------------------------------ Farsi
    "fa": {
        "cancel": "❌ لغو",
        "back": "🔙 بازگشت",
        "back_menu": "🏠 منوی اصلی",
        "back_admin": "🔙 منوی مدیریت",
        "action_cancelled": "❌ عملیات لغو شد.\n\nبازگشت به منوی اصلی 👇",
        "yes": "بله",
        "no": "خیر",
        "loading": "⏳ لطفاً صبر کنید...",
        "not_found": "یافت نشد.",
        "access_denied": "⛔ دسترسی مجاز نیست.",
        "admin_only": "⛔ مخصوص مدیریت.",
        "banned": "🚫 شما از استفادهٔ این ربات مسدود شده‌اید.",
        "send_or_cancel": "پاسخ خود را بفرستید، یا لغو را بزنید.",
        "invalid_number": "❌ لطفاً یک عدد معتبر وارد کنید.",
        "copied": "📋 کپی شد.",
        "welcome": (
            "👋 <b>سلام، به {bot_name} خوش اومدی!</b>\n\n"
            "خوشحالیم که اینجایی. این پنل کنترل VPN توئه — \u200cهمه‌چیز همین توی تلگرام انجام می‌شه، ساده و راحت.\n\n"
            "<b>کارایی که می‌تونی بکنی:</b>\n"
            "• 🛒 با چند تا ضربه، اشتراک VPN بگیر\n"
            "• 📱 وضعیت اکانت و مصرف حجمت رو ببین\n"
            "• 📅 هر وقت خواستی تمدید کن یا حجم بگیر\n"
            "• 🎁 اول یه اکانت رایگان امتحان کن، بی‌قید و شرط\n"
            "• 🔗 دوستات رو دعوت کن و پاداش بگیر\n"
            "• 💬 بدون خروج از چت، با پشتیبانی حرف بزن\n\n"
            "یکی از دکمه‌های زیر رو بزن تا شروع کنیم 👇"
        ),
        "menu_main": "🏠 <b>منوی اصلی</b>\n\nچیکار برات انجام بدم؟",
        "buy": "🛒 خرید سرویس",
        "my_accounts": "📱 اکانت‌های من",
        "trial": "🎁 تست رایگان",
        "balance": "💳 کیف پول",
        "referral": "🔗 دعوت دوستان",
        "gift": "🎫 کد هدیه",
        "support": "💬 پشتیبانی",
        "guide": "📚 راهنما",
        "language": "🌐 زبان",
        "admin_panel": "⚙️ پنل مدیریت",
        # MENU-RESTRUCTURE: new combined sections
        "wallet": "💳 کیف پول",
        "wallet_title": "💳 <b>کیف پول</b>",
        "help": "📚 راهنما و پشتیبانی",
        "help_title": "📚 <b>راهنما و پشتیبانی</b>",
        "help_desc": "نیاز به کمکی؟ راهنماها رو ببین یا یه تیکت پشتیبانی باز کن — اینجاییم که کمکت کنم.\n\n• 📖 راهنمای استفاده از ربات و اتصال\n• 🎫 برای هر مشکلی تیکت بزن\n• ⏱ معمولاً ظرف چند ساعت جواب می‌دیم",
        "more_features": "✨ قابلیت‌های بیشتر",
        "more_features_title": "✨ <b>قابلیت‌های بیشتر</b>\n\nتنظیمات و گزینه‌های اضافه:",
        "choose_plan": "🛒 <b>یک پلن انتخاب کن</b>\n\nهمون پلنی رو بگیر که به کارت میاد — هر وقت خواستی می‌تونی تمدید کنی یا حجم بگیری:",
        "no_plans": "😔 هنوز پلنی تعریف نشده. بعداً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.",
        "your_balance": "💳 موجودی شما: <b>{balance}</b>",
        "sufficient": "✅ موجودیت برای این پلن کافیه.",
        "insufficient": "⚠️ <b>موجودی کافی نیست.</b>\nبرای خرید این پلن <b>{diff}</b> دیگه نیاز داری.",
        "review_short_hint": "👇 برای شارژ موجودی و خرید این پلن، روی دکمه‌ی <b>«پرداخت مبلغ مورد نیاز»</b> زیر کلیک کن. اگه کد هدیه داری می‌تونی از آن هم استفاده کنی.",
        # BUG-7 FIX: راهنمای مخصوص صفحه‌ی تمدید (که دکمه‌ی «شارژ کیف پول»
        # رو نشون می‌ده، نه دکمه‌ی «پرداخت مبلغ مورد نیاز» صفحه‌ی خرید).
        "renew_short_hint": "👇 برای شارژ موجودی روی دکمه‌ی <b>«شارژ کیف پول»</b> زیر کلیک کن، بعد برگرد و تمدید کن.",
        "ask_account_name": (
            "✏️ <b>اسم این کانفیگ (اختیاری)</b>\n\n"
            "یه اسم کوتاه بذار مثل <code>phone</code> یا <code>laptop</code> تا کانفیگ‌هات رو از هم تشخیص بدی.\n"
            "فقط حروف انگلیسی، عدد، خط تیره و زیرخط.\n\n"
            "برای اسم خودکار، <code>-</code> بفرست یا فقط لغو رو بزن."
        ),
        "invalid_name": "❌ این اسم جواب نمی‌ده. ۲ تا ۲۴ کاراکتر: حروف، عدد، خط تیره یا زیرخط.",
        "review_purchase": "📋 <b>بررسی سفارش</b>\nجزئیات رو ببین، اگه خواستی اسم یا کد تخفیف بزن، بعد تأیید کن.",
        "confirm_pay": "✅ تأیید و پرداخت",
        "apply_promo": "🎟 کد تخفیف",
        "set_name_btn": "✏️ اسم کانفیگ",
        # SHORTFALL-REQUEST (FA)
        "request_shortfall_btn": "⚡ پرداخت مبلغ مورد نیاز",
        "shortfall_payment_info": (
            "⚡ <b>پرداخت مبلغ باقی مانده برای {plan_name}</b>\n\n"
            "دقیقاً <b>{shortfall}</b> دیگه برای خرید این پلن لازم داری.\n\n"
            "💳 شماره کارت: <code>{card_number}</code>\n"
            "👤 صاحب کارت: {card_holder}\n\n"
            "💵 مبلغ قابل واریز (با پسوند یکتا، برای کپی روی عدد بزنید):\n"
            "{amount_block}\n\n"
            "💡 برای کپی، روی عدد داخل کادر بزنید — بدون کاما، آمادهٔ پیست.\n\n"
            "بعد از واریز، روی «ارسال رسید» بزن و رسیدت رو بفرست."
        ),
        "name_auto": "(خودکار)",
        "name_label": "🏷 اسم",
        "promo_label": "🎟 تخفیف",
        "promo_none": "بدون",
        "price_label": "💵 قیمت",
        "final_price_label": "💰 قابل پرداخت",
        "discount_label": "✂️ تخفیف شما",
        "creating_account": "⏳ در حال ساخت اکانت VPN...",
        "purchase_success": "✅ <b>اکانت با موفقیت ساخته شد!</b>",
        "purchase_failed": "❌ ساخت اکانت ناموفق بود: {msg}\n\nدوباره تلاش کنید یا با پشتیبانی تماس بگیرید.",
        "no_servers": "❌ در حال حاضر سروری موجود نیست. با پشتیبانی تماس بگیرید.",
        "no_inbounds": "❌ این پلن روی هیچ سروری اینباند فعال ندارد. با پشتیبانی تماس بگیرید.",
        "enter_promo": "🎟 <b>کد تخفیف</b>\n\nکد خود را بفرستید:",
        "promo_invalid": "❌ کد تخفیف نامعتبر یا منقضی است.\n\nدوباره تلاش کنید یا لغو کنید:",
        "promo_applied": "🎟 کد <b>{code}</b> اعمال شد — <b>{discount}</b> تخفیف!",
        "my_accounts_title": "📱 <b>اکانت‌های من</b>",
        "no_accounts": "📱 <b>اکانت‌های من</b>\n\nهنوز اکانتی ندارید.\n\n🛒 اولین پلن خود را بخرید!",
        "select_account": "برای مشاهدهٔ جزئیات، یک اکانت انتخاب کنید:",
        "renew": "🔄 تمدید",
        "traffic": "📈 حجم",
        "get_link": "🔗 لینک‌ها",
        "disable": "⛔ غیرفعال",
        "enable": "✅ فعال",
        "qr": "📱 QR",
        "topup_traffic": "➕ افزایش حجم",
        "set_label": "🏷 نام",
        "delete": "🗑 حذف",
        "acc_disabled": "⛔ اکانت <code>{email}</code> غیرفعال شد.\n\nهر زمان بخواهید می‌توانید دوباره فعالش کنید.",
        "acc_enabled": "✅ اکانت <code>{email}</code> فعال شد.\n\nحالا می‌توانید وصل شوید.",
        "renew_success": "✅ <b>اکانت تمدید شد!</b>",
        "renew_failed": "❌ تمدید ناموفق بود: {msg}",
        # RENEW-EXPLAIN: صفحهٔ تأیید تمدید که قبل از اقدام، نحوهٔ تمدید را
        # برای کاربر توضیح می‌دهد.
        "renew_how_title": "ℹ️ <b>نحوهٔ تمدید</b>",
        "renew_how_deduct": "💵 هزینهٔ پلن از موجودی کیف پول شما کسر می‌شود.",
        "renew_how_days_add": "📅 روزهای باقی‌مانده با مدت پلن <b>جمع می‌شوند</b>.",
        "renew_how_expired": "⏰ اشتراک شما منقضی شده — دورهٔ جدید از الان شروع می‌شود.",
        "renew_how_traffic_add": "💾 حجم باقی‌مانده با حجم پلن <b>جمع می‌شود</b>.",
        "renew_how_unlimited": "♾️ حجم <b>نامحدود</b> باقی می‌ماند.",
        "renew_after_title": "📋 <b>بعد از تمدید:</b>",
        "renew_after_days": "📅 مدت: <b>{days}</b>",
        "renew_after_traffic": "💾 حجم: <b>{traffic}</b>",
        "renew_after_balance": "💳 موجودی: <b>{balance}</b>",
        "renew_sure": "❓ <b>آیا از تمدید این اکانت مطمئن هستید؟</b>",
        "renew_confirm_btn": "✅ بله، تمدید کن",
        "delete_confirm": "🗑 <b>حذف اکانت؟</b>\n\n<code>{email}</code>\n\nاین کار اکانت را برای همیشه از پنل حذف می‌کند و قابل بازگشت نیست.",
        "confirm_delete": "🗑 تأیید حذف",
        "acc_deleted": "🗑 اکانت <code>{email}</code> حذف شد.",
        "ask_label": "🏷 <b>نام اکانت</b>\n\nیک نام کوتاه (حداکثر ۳۰ کاراکتر) بفرستید یا <code>-</code> برای پاک کردن:",
        "label_set": "🏷 نام به <b>{label}</b> تغییر کرد.",
        "label_cleared": "🏷 نام اکانت پاک شد.",
        "traffic_title": "📈 <b>جزئیات حجم</b>",
        "online": "آنلاین",
        "offline": "آفلاین",
        "active_ips": "IP های فعال",
        # برچسب‌های کارت اکانت — برای درک بهتر کاربر (قبلاً فقط مقدار خالی بود)
        "card_plan": "پلن",
        "card_remaining_traffic": "حجم باقی‌مانده",
        "card_remaining_time": "مدت باقی‌مانده",
        "card_used": "حجم مصرفی",
        "card_total": "حجم کل",
        "card_account_status": "وضعیت اکانت",
        "card_uploaded": "حجم آپلود شده",
        "card_downloaded": "حجم دانلود شده",
        "unlimited": "نامحدود",
        "topup_title": "➕ <b>افزایش حجم</b>\n\nیک بسته انتخاب کنید تا بدون تغییر تاریخ انقضا، حجم اکانت افزایش یابد:",
        "topup_success": "✅ <b>حجم اضافه شد!</b>\n+{gb} گیگابایت به <code>{email}</code> اضافه شد.",
        "trial_disabled": "😔 در حال حاضر اکانت رایگان غیرفعال است.\n\nبعداً دوباره تلاش کنید.",
        "trial_used": "🎁 <b>اکانت رایگان</b>\n\nشما قبلاً اکانت رایگان دریافت کرده‌اید.\nهر کاربر فقط یک‌بار می‌تواند استفاده کند — حتی اگر اکانت تریال حذف شده باشد.\n\n🛒 پلن‌های مقرون‌به‌صرفه ما را ببینید!",
        "trial_offer": "🎁 <b>پیشنهاد اکانت رایگان</b>",
        "get_trial": "✅ دریافت اکانت رایگان",
        "trial_created": "🎉 <b>اکانت آزمایشی ساخته شد!</b>",
        "trial_failed": "❌ ساخت اکانت آزمایشی ناموفق بود: {msg}",
        "trial_no_renew": "🎁 اکانت‌های آزمایشی قابل تمدید یا افزایش حجم نیستند.\nبرای ادامه، یک پلن خریداری کنید.",
        # L10N-GAPS: رشته‌های کاربر-رو که قبلاً انگلیسی هاردکد شده بودند.
        "plan_not_found": "❌ پلن یافت نشد.",
        "plan_not_found_buy": "❌ پلن یافت نشد — یک پلن جدید بخرید.",
        "qr_caption_sub": "📡 اشتراک",
        "qr_caption_link": "🔗 لینک اتصال",
        "action_failed": "❌ ناموفق: {msg}",
        "gift_plan_create_failed": "❌ {msg}\n\n⚠️ کد هدیه شما استفاده شد اما اکانت ایجاد نشد. لطفاً با کد <code>{code}</code> با پشتیبانی تماس بگیرید.",
        "gift_plan_db_failed": "❌ خطای داخلی. لطفاً با کد <code>{code}</code> با پشتیبانی تماس بگیرید.",
        "balance_title": "💳 <b>موجودی شما</b>",
        "recent_tx": "📋 <b>تراکنش‌های اخیر</b>",
        "topup_hint": "💡 از شارژ کیف پول برای افزایش موجودی استفاده کنید یا کد هدیه دریافت کنید.",
        "referral_title": "🔗 <b>برنامهٔ دعوت دوستان</b>",
        "referral_disabled": "😔 برنامهٔ دعوت دوستان در حال حاضر غیرفعال است.",
        "referral_desc": "دوستان خود را دعوت کنید و با اولین خریدشان، به‌طور خودکار پاداش بگیرید!",
        "referral_how": "📤 <b>نحوه کار</b>\n۱️⃣ لینک دعوت خودت رو برای دوستات بفرست\n۲️⃣ اونا ثبت‌نام می‌کنن و اولین پلن رو می‌خرن\n۳️⃣ تو +{days} روز و +{gb} گیگابایت می‌گیری — هر وقت خواستی روی اکانتت دریافتش کن",
        "your_link": "📤 <b>لینک دعوت شما</b>",
        "share_link": "📤 اشتراک‌گذاری",
        "referral_share_text": "🚀 سلام! من دارم از این ربات VPN استفاده می‌کنم و واقعاً راضی‌ام — سریع، ارزون و به‌دردبخور. با لینک دعوت من ثبت‌نام کن تا هر دومون پاداش بگیریم 🎁 (برای من +{days} روز و +{gb} گیگابایت هدیه‌ست!). بزن تا وصل بشی 👇",
        "referral_stats": "📊 <b>آمار شما</b>",
        "referral_history": "📋 <b>دعوت‌های اخیر</b>",
        "referral_no_history": "هنوز دعوتی ندارید — لینک خود را اشتراک بگذارید تا پاداش بگیرید!",
        "ref_status_bought": "✅ خرید کرده",
        "ref_status_pending": "⏳ در انتظار",
        "ref_claim_btn": "🎁 دریافت پاداش",
        "ref_claimable": "🎁 شما <b>{count}</b> پاداش رفرال دریافت‌نشده دارید!",
        "ref_claim_success": "✅ <b>پاداش دریافت شد!</b>\n\n🎁 +{days} روز و +{gb} گیگابایت به <code>{email}</code> اضافه شد.\n\nممنون که لینک رو پخش کردی!",
        "ref_claim_no_account": "⚠️ برای دریافت پاداش رفرال، باید یه اکانت فعال پرداختی داشته باشی.\n\nاول یه پلن بخر، بعد بیا اینجا پاداشت رو بگیر.",
        "ref_claim_none": "✅ الان پاداش دریافت‌نشده‌ای نداری.\n\nلینکت رو پخش کن تا بیشتر بگیری!",
        "ref_claim_pick": "اکانتی که می‌خوای پاداش روش اعمال بشه رو انتخاب کن:",
        "ref_claim_failed": "❌ <b>پاداش اعمال نشد</b>\n\nپنل این خطا رو داد: <code>{msg}</code>\n\nپاداش رفرالت دست‌نخورده‌ست — یه لحظه دیگه دوباره امتحان کن.",
        "delete_failed": "⚠️ <b>اکانت حذف نشد</b>\n\nپنل این خطا رو داد: <code>{msg}</code>\n\nاکانتت هنوز فعاله. یه لحظه دیگه دوباره امتحان کن.",
        "enter_gift": "🎫 <b>کد هدیه</b>\n\nکد خود را بفرستید:",
        "gift_invalid": "❌ کد هدیه نامعتبر است. دوباره تلاش کنید:",
        "gift_used_code": "❌ این کد قبلاً استفاده شده است.",
        "gift_balance_ok": "✅ <b>کد ثبت شد!</b>\n💰 <b>{amount}</b> به موجودی شما اضافه شد.",
        "gift_plan_ok": "✅ <b>کد ثبت شد!</b>\n🎁 پلن: <b>{plan}</b>",
        # کد هدیه داخل صفحه‌ی خرید سرویس (بافت purchasing).
        # فقط کدهای نوع موجودی اینجا پذیرفته می‌شن چون هدف شارژ کیف پوله.
        "gift_in_purchase_hint": "🎫 <b>کد هدیه</b>\n\nکد خود را بفرستید.\n\nℹ️ در این بخش فقط کدهای هدیه‌ی <b>موجودی</b> پذیرفته می‌شوند — موجودی کیف پولت رو شارژ می‌کنن تا بتونی این پلن رو بخری.",
        "gift_plan_not_allowed_in_purchase": "❌ این یک کد هدیه‌ی <b>پلن</b> است. در اینجا فقط کدهای <b>موجودی</b> پذیرفته می‌شوند (برای شارژ کیف پول). لطفاً یک کد موجودی بفرستید، یا از مسیر منوی اصلی ← کیف پول ← کد هدیه از این کد استفاده کنید.",
        "gift_balance_ok_back_to_purchase": "✅ <b>کد ثبت شد!</b>\n💰 <b>{amount}</b> به موجودی شما اضافه شد.\n\nبرگشت به پلن شما…",
        "payment_disabled_gift_only": "⚠️ در حال حاضر پرداخت کارت‌به‌کارت غیرفعال است. تنها راه شارژ کیف پول در این بخش، استفاده از <b>کد هدیه</b> است.",
        "gift_btn": "🎫 کد هدیه",
        "support_title": "💬 <b>مرکز پشتیبانی</b>",
        "support_desc": "نیاز به کمکی؟ یه تیکت باز کن تا تیم ما کمکت کنه.\n\n• 🎫 برای هر مشکلی تیکت بزن\n• ⏱ معمولاً ظرف چند ساعت جواب می‌دیم\n• 🔒 گفتگو کاملاً محرمانه‌ست",
        "new_ticket": "🎫 تیکت جدید",
        "my_tickets": "📋 تیکت‌های من",
        "choose_category": "🎫 <b>تیکت پشتیبانی جدید</b>\n\nیک دسته‌بندی انتخاب کنید:",
        "cat_technical": "🔧 فنی",
        "cat_payment": "💰 پرداخت",
        "cat_account": "👤 اکانت",
        "cat_other": "📝 سایر",
        "ask_subject": "📝 <b>دسته:</b> {category}\n\nحالا یک موضوع کوتاه برای تیکت بنویسید:",
        "ask_message": "📝 <b>موضوع:</b> {subject}\n\nحالا مشکل خود را شرح دهید.\n\n💡 می‌توانید فایل هم پیوست کنید (عکس، ویدیو، ویس، آهنگ/صوت، فایل، استیکر، GIF یا ویدیو دایره‌ای) — همراه با کپشن بفرستید.",
        "ticket_created": "✅ <b>تیکت #{id} ساخته شد!</b>\n\n📝 موضوع: {subject}\n🏷 دسته: {category}\n⏱ به‌زودی پاسخ می‌دهیم.",
        "reply": "💬 پاسخ",
        "reopen": "🔓 باز کردن مجدد",
        "close": "🔒 بستن",
        "ask_reply": "💬 <b>پاسخ به تیکت #{id}</b>\n📝 {subject}\n\nپیام خود را بنویسید:",
        "ask_reply_with_media": "💬 پاسخ خود را بفرستید. می‌توانید متن بفرستید یا هر نوع فایلی پیوست کنید: عکس، ویدیو، ویس، آهنگ/صوت، فایل، استیکر، GIF یا ویدیو دایره‌ای. (اگه می‌خواید متن هم بگید، فایل رو همراه کپشن بفرستید.)",
        "manage_user": "👤 مدیریت کاربر",
        "view_media": "📎 مشاهده رسانه",
        "media_photo": "عکس",
        "media_document": "فایل",
        "media_video": "ویدیو",
        "media_voice": "پیام صوتی",
        "media_audio": "آهنگ / صوت",
        "media_animation": "GIF",
        "media_video_note": "ویدیو دایره‌ای",
        "media_sticker": "استیکر",
        "media_sent": "✅ رسانه ارسال شد.",
        "reply_sent_admin": "✅ پاسخ به کاربر ارسال شد.",
        "reply_sent_user": "✅ پاسخ به مدیریت ارسال شد.",
        "ticket_closed": "🔒 <b>تیکت #{id} بسته شد.</b>\n\nاگر کمک بیشتری نیاز دارید، دوباره بازش کنید یا تیکت جدیدی بزنید.",
        "ticket_reopened": "🔓 <b>تیکت #{id} دوباره باز شد.</b>\n\nبه‌زودی پاسخ می‌دهیم.",
        "no_tickets": "📋 <b>تیکت‌های من</b>\n\nهنوز تیکتی ندارید.",
        "ticket_status_open": "🟢 باز",
        "ticket_status_waiting_admin": "🟡 در انتظار پاسخ مدیریت",
        "ticket_status_waiting_user": "🔵 در انتظار پاسخ شما",
        "ticket_status_closed": "🔴 بسته شده",
        "tickets_filter_open": "🟢 باز",
        "tickets_filter_all": "📋 همه",
        "guide_title": "📚 <b>مرکز راهنما</b>",
        "guide_usage_title": "📖 <b>استفاده از ربات</b>",
        "guide_connection_title": "🔌 <b>راهنمای اتصال</b>",
        "guide_usage_btn": "📖 استفاده از ربات",
        "guide_connection_btn": "🔌 نحوه اتصال",
        "lang_title": "🌐 <b>Language / زبان</b>\n\nزبان خود را انتخاب کنید:",
        "lang_set": "✅ زبان به فارسی تغییر یافت.",
        "conn_links": "🔗 <b>لینک‌های اتصال</b>",
        "sub_url": "📡 <b>لینک سابسکریپشن</b> (همهٔ سرورها را خودکار به‌روز می‌کند)",
        "links_sub_only": "📡 <b>لینک سابسکریپشن شما</b>\n\nاز همین یک لینک در هر کلاینت V2Ray (v2rayNG، Streisand، v2rayN، Foxray…) استفاده کنید — همهٔ سرورها را خودکار همگام و همیشه به‌روز نگه می‌دارد.",
        "qr_sub": "🖼 بارکد QR",
        "how_to_use": "📱 نحوه اتصال",
        "help_text": (
            "ℹ️ <b>راهنما</b>\n\n"
            "از دکمه‌های منو برای جابه‌جایی استفاده کنید. دستورات:\n"
            "/start — باز کردن منوی اصلی\n"
            "/language — تغییر زبان\n"
            "/help — این پیام\n"
            "/admin — پنل مدیریت (فقط مدیران)\n"
            "/cancel — لغو هر عملیات در حال انجام"
        ),
        "cancelled_action": "❌ لغو شد.",

        "charge_wallet": "💳 شارژ کیف پول",
        "payment_disabled": "💳 پرداخت در حال حاضر غیرفعال است.",
        "choose_amount": "💳 <b>مبلغ شارژ را انتخاب کنید</b>\n\nیک مبلغ از پیش تعیین‌شده انتخاب کنید یا مبلغ دلخواه وارد کنید:",
        "custom_amount": "✏️ مبلغ دلخواه",
        "enter_custom_amount": "💳 مبلغ مورد نظر برای شارژ را وارد کنید (حداقل: {min} تومان):",
        "payment_info": (
            "💳 <b>اطلاعات پرداخت</b>\n\n"
            "💳 شماره کارت: <code>{card_number}</code>\n"
            "👤 صاحب کارت: {card_holder}\n\n"
            "💰 <b>دقیقاً این مبلغ را پرداخت کنید (برای کپی روی عدد بزنید):</b>\n"
            "{amount_block}\n\n"
            "⚠️ ارقام مازاد برای تأیید تراکنش هستند. دقیقاً همین مبلغ را پرداخت کنید.\n"
            "💡 برای کپی، روی عدد داخل کادر بزنید — بدون کاما، آمادهٔ پیست در اپلیکیشن بانکی.\n\n"
            "پس از پرداخت، رسید خود (عکس یا متن) را با دکمهٔ زیر بفرستید."
        ),
        "send_receipt": "📤 ارسال رسید",
        "enter_receipt_text": "📸 <b>ارسال رسید</b>\n\nمی‌توانید یک <b>عکس</b> (اسکرین‌شات پرداخت) بفرستید یا جزئیات را <b>تایپ کنید</b> (شماره تراکنش، زمان و ...).\n\nپیشنهاد: عکس همراه با کپشن بهترین گزینه است.",
        "receipt_empty": "❌ لطفاً یک عکس یا متن به‌عنوان رسید ارسال کنید.",
        "receipt_received": "✅ رسید دریافت شد! پرداخت شما در انتظار بررسی مدیریت است.\n\nمبلغ: {amount} تومان\nپس از تأیید، مبلغ به کیف پول شما اضافه می‌شود.",
        "payment_approved": "✅ <b>پرداخت تأیید شد!</b>\n\n💰 {amount} تومان به موجودی شما اضافه شد.\n💳 موجودی جدید: {balance}",
        "payment_rejected": "❌ <b>پرداخت رد شد</b>\n\nدلیل: {reason}\n\nاگر سؤالی دارید، با پشتیبانی تماس بگیرید.",
        "pending_payments": "💰 <b>پرداخت‌های در انتظار</b>",
        "approve_payment": "✅ تأیید",
        "reject_payment": "❌ رد",
        "enter_reject_reason": "❌ دلیل رد را بنویسید (یا <code>-</code> برای بدون دلیل):",
        # TOPUP-TOGGLE: alert toast for stale top-up button taps after the
        # admin disabled top-ups.
        "topup_disabled": "❌ افزایش حجم در حال حاضر توسط ادمین غیرفعال شده است. لطفاً بعداً دوباره تلاش کنید.",
        "topup_unlimited_noop": "❌ این اکانت نامحدود است — افزایش حجم لازم ندارد. برای تمدید مدت از دکمهٔ تمدید استفاده کنید.",
        "force_join": (
            "🔒 <b>ابتدا عضو کانال ما شوید!</b>\n\n"
            "برای استفاده از ربات، باید عضو کانال‌های زیر باشید:\n\n"
            "{channels}\n\n"
            "پس از عضویت، دکمه ✅ زیر را بزنید."
        ),
        "verify_join": "✅ عضو شدم",
        "force_join_success": "✅ عضویت تأیید شد! حالا می‌توانید از ربات استفاده کنید.",
        "force_join_failed": "❌ شما هنوز عضو همهٔ کانال‌های مورد نیاز نشده‌اید.\nابتدا عضو کانال‌های ذکرشده در بالا شوید، سپس دکمهٔ ✅ را بزنید.",
        # FORCE-JOIN-FEEDBACK: پیام ماندگار وقتی کاربر بدون عضویت واقعی
        # دکمهٔ «عضو شدم» را می‌زند. با ❌ شروع می‌شود (نه 🔒) تا پیام
        # تغییر کند و کاربر بازخورد واضح ببیند.
        "force_join_not_joined": (
            "❌ <b>هنوز عضو نشده‌اید!</b>\n\n"
            "شما هنوز عضو همهٔ کانال‌های مورد نیاز نشده‌اید:\n\n"
            "{channels}\n\n"
            "👉 ابتدا عضو کانال‌های بالا شوید، سپس دکمهٔ ✅ زیر را بزنید."
        ),
        "no_inbounds_configured": "❌ این پلن اینباند تنظیم‌شده ندارد. با مدیریت تماس بگیرید.",
        "broadcast_header_fa": "📢 <b>اطلاعیه همگانی</b>\n\n",
        "charge_wallet_btn": "💳 شارژ کیف پول",
        # background-task subject lines (M5 — moved out of inline if/else)
        "expiry_reminder_subject": "اشتراک شما به‌زودی منقضی می‌شود!",
        "account_expired_subject": "اکانت منقضی شد",
        "traffic_depleted_subject": "حجم تمام شد",
        # admin panel i18n (M11) — most-used admin strings
        "admin_dashboard": "⚙️ داشبورد مدیریت",
        "servers": "سرورها",
        "users": "کاربران",
        "tickets": "تیکت‌ها",
        "plans": "پلن‌ها",
        "promos": "کدهای تخفیف",
        "gift_codes": "کدهای هدیه",
        "settings": "تنظیمات",
        "broadcast": "اطلاعیه همگانی",
        "cleanup": "پاک‌سازی",
        "approved": "✅ تأیید شد",
        "rejected": "❌ رد شد",
        "toggled": "✅ تغییر کرد",
        "not_pending": "در انتظار نیست.",
        "already_processed": "⚠️ قبلاً توسط مدیریت دیگری پردازش شده است.",
        # ---- Payment-admin screens (PA-LANG) ----
        # ترجمهٔ فارسی بخش ادمین پرداخت. ادمین‌های اصلی همیشه انگلیسی
        # می‌بینند؛ ادمین‌های پرداخت زبان انتخابی خودشان را می‌بینند.
        "pa_menu_title": "💰 پنل ادمین پرداخت",
        "pa_menu_desc": "می‌توانید پرداخت‌های در انتظار را تأیید یا رد کنید.",
        "pa_pending_btn": "💰 پرداخت‌های در انتظار",
        "pa_history_btn": "📋 تاریخچه پرداخت‌ها",
        "pa_no_pending": "💰 پرداخت در انتظاری وجود ندارد",
        "pa_no_more_pending": "💰 پرداخت در انتظار دیگری وجود ندارد",
        "pa_pending_header": "💰 پرداخت‌های در انتظار — {i}/{n}",
        "pa_payment_title": "💰 پرداخت #{id}",
        "pa_user": "👤 کاربر: {name} ({id})",
        "pa_base_amount": "💵 مبلغ پایه: {amt} تومان",
        "pa_unique_amount": "💵 مبلغ یکتا: {amt} تومان",
        "pa_card": "💳 کارت: {num}",
        "pa_created": "📅 ایجادشده: {date}",
        "pa_receipt_text": "{icon} متن رسید: {text}",
        "pa_receipt_kind": "{icon} رسید: {kind}",
        "pa_status": "وضعیت: {status}",
        "pa_status_pending": "در انتظار",
        "pa_status_approved": "تأییدشده",
        "pa_status_rejected": "ردشده",
        "pa_approve_btn": "✅ تأیید",
        "pa_reject_btn": "❌ رد",
        "pa_next_btn": "⏭ بعدی ({i}/{n})",
        "pa_full_history_btn": "📋 کل تاریخچه",
        "pa_history_btn2": "📋 تاریخچه",
        "pa_pending_back_btn": "🔙 در انتظار",
        "pa_admin_back_btn": "🔙 ادمین",
        "pa_receipt_caption": "📎 رسید پرداخت #{id} — {name}",
        "pa_reviewed_by": "🛡 {action} توسط: {admin} ({id})",
        "pa_action_approved": "تأییدشده",
        "pa_action_rejected": "ردشده",
        "pa_reviewed_at": "🕒 بررسی‌شده: {date}",
        "pa_reject_reason": "❌ دلیل: {reason}",
        "pa_no_payments": "📋 هنوز پرداختی وجود ندارد",
        "pa_history_title": "📋 تاریخچه پرداخت‌ها (آخرین ۲۰)",
        "pa_history_header": "شناسه • کاربر • مبلغ • وضعیت • رسید • تأییدکننده",
        "pa_approve_failed": "❌ تأیید ناموفق: {err}",
        "pa_approved_msg": "✅ پرداخت #{id} تأیید شد\n💰 {amt} تومان به موجودی کاربر {uid} اضافه شد\n\n✅ پرداخت در انتظار دیگری وجود ندارد",
        "pa_approved_toast": "✅ تأیید شد",
        "pa_not_found_processed": "پرداخت یافت نشد یا قبلاً پردازش شده است.",
        "pa_already_processed_msg": "⚠️ این پرداخت قبلاً توسط ادمین دیگری پردازش شده است.",
        "pa_rejected_msg": "❌ پرداخت #{id} رد شد",
        # PAY-HISTORY-REWORK: نمایش جدولی، تاریخچهٔ هر ادمین، انتخاب ادمین پرداخت.
        "pa_history_my_title": "📋 تاریخچهٔ تأییدهای من",
        "pa_history_all_title": "📋 تاریخچهٔ پرداخت‌ها (آخرین ۳۰)",
        "pa_history_admins_title": "📋 تأییدها بر اساس ادمین پرداخت",
        "pa_history_admin_title": "📋 تأییدهای {admin}",
        "pa_history_admins_header": "ادمین • تأییدشده • ردشده • کل",
        "pa_history_admins_pick": "👇 برای بررسی تأییدهای هر ادمین روی آن بزنید",
        "pa_history_admins_none": "📋 هنوز ادمین پرداختی اضافه نشده — از پنل ادمین اصلی اضافه کنید.",
        "pa_history_admin_none": "📋 این ادمین هنوز پرداختی را تأیید نکرده است.",
        "pa_no_own_approvals": "📋 شما هنوز پرداختی را تأیید نکرده‌اید.",
        "pa_my_history_btn": "📋 تأییدهای من",
        "pa_all_history_btn": "📋 همهٔ رسیدها",
        "pa_admins_history_btn": "👥 بر اساس ادمین",
        "pa_view_user_finance": "💼 سوابق مالی کاربر",
        "pa_view_user_payments": "🧾 رسیدهای کاربر",
        # User-finance view (admin → user detail financial history).
        "uf_title": "💼 سوابق مالی — {name}",
        "uf_no_data": "سابقهٔ مالی برای این کاربر وجود ندارد.",
        "uf_tx_header": "شناسه • نوع • مبلغ • تاریخ • توضیحات",
        "uf_pay_header": "شناسه • مبلغ • وضعیت • رسید • تاریخ",
        "uf_balance": "موجودی: {amt}",
        "uf_spent": "کل خرید: {amt}",
        "uf_orders": "کل سفارش‌ها: {n}",
        "uf_back_user": "🔙 کاربر",
        # Referral invitee list.
        "ref_invitees_title": "📋 دعوت‌شدگان شما",
        "ref_invitees_header": "شناسه • نام • وضعیت • تاریخ عضویت",
        "ref_invitees_none": "هنوز دعوت‌شده‌ای وجود ندارد.",
        "ref_invitees_btn": "👥 نمایش دعوت‌شدگان",
        "admin_ref_invitees_btn": "👥 دعوت‌شدگان",
        # ADMIN-MENU-REWORK: پنل ادمین اصلی به زیرمنوهای دسته‌بندی‌شده مرتب شد.
        "am_payments_menu": "💳 پرداخت‌ها",
        "am_payments_menu_title": "💳 مدیریت پرداخت‌ها",
        "am_payments_menu_desc": "تأیید پرداخت‌های در انتظار، بررسی تاریخچهٔ رسیدها، یا بررسی تأییدهای هر ادمین پرداخت.",
        "am_promos_menu": "🎁 تبلیغات",
        "am_promos_menu_title": "🎁 تبلیغات و بازاریابی",
        "am_promos_menu_desc": "کدهای تخفیف، کدهای هدیه، و پیام همگانی.",
        "am_support_menu": "💬 پشتیبانی",
        "am_support_menu_title": "💬 پشتیبانی و تیکت‌ها",
        "am_support_menu_desc": "مشاهده و پاسخ به تیکت‌های پشتیبانی کاربران.",
        "am_back_admin": "🔙 پنل ادمین",
        # USER-ACCOUNTS-REWORK: زیربخش اختصاصی اکانت‌ها در بخش کاربران ادمین.
        "am_user_accounts_btn": "📱 اکانت‌ها",
        "am_user_accounts_none": "اکانتی وجود ندارد.",
    },
}


def t(key: str, lang: str = "en", **kwargs) -> str:
    """Translate a key for the given language with optional formatting."""
    table = MESSAGES.get(lang) or MESSAGES["en"]
    text = table.get(key) or MESSAGES["en"].get(key) or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception as e:
            # LOW — log format failures so missing/extra placeholders are
            # visible during development instead of silently leaking {x} to users.
            logger.warning("t() format failed for key=%r lang=%r: %s", key, lang, e)
    return text


def to_fa_digits(s: str) -> str:
    """Convert ASCII digits in a string to Persian digits."""
    return s.translate(str.maketrans("0123456789", FA_DIGITS))


def fmt_num(value, lang: str = "en") -> str:
    """Format a number with thousands separators, localized digits for FA."""
    try:
        s = f"{value:,}"
    except Exception:
        s = str(value)
    return to_fa_digits(s) if lang == "fa" else s


def fmt_price(amount, lang: str = "en", currency: str = "toman") -> str:
    """Format a monetary amount.  Toman is the default currency."""
    try:
        amt = float(amount)
    except Exception:
        amt = 0.0
    if currency == "toman":
        # Whole tomans (no decimals) — typical for IRR/Toman pricing
        s = f"{int(round(amt)):,}"
        if lang == "fa":
            return f"{to_fa_digits(s)} تومان"
        return f"{s} Toman"
    # USD / other — keep 2 decimals
    s = f"{amt:,.2f}"
    if lang == "fa":
        return f"{to_fa_digits(s)} دلار"
    return f"${s}"


def payment_unit_str(currency: str, lang: str) -> str:
    """Localised currency-unit word for the ``{unit}`` placeholder in
    ``payment_info`` (L8).

    Kept separate from :func:`fmt_price` because the ``payment_info`` template
    splits the amount and the unit into two placeholders — so the amount is
    rendered via :func:`fmt_num` (no unit) and the unit is rendered via this
    helper. This avoids the pre-L8 bug where ``fmt_price`` already appended
    "Toman" / "تومان" and the template then appended *another* "Toman"."""
    if currency == "usd":
        return "دلار" if lang == "fa" else "USD"
    return "تومان" if lang == "fa" else "Toman"


def _amount_block(unique_amount, currency: str, lang: str) -> str:
    """Build the copyable amount block for the ``payment_info`` and
    ``shortfall_payment_info`` templates.

    Each amount is wrapped in a ``<code>`` tag so the user can copy it
    with a single tap, and rendered with raw ASCII digits (no thousands
    separators, no Persian-digit conversion) so it pastes cleanly into
    Iranian banking apps which only accept plain ASCII numerals.

    For the toman currency both the toman and the rial (×10) amounts are
    shown, since some banking apps/approx receipt forms expect rial. For
    USD only the dollar amount is shown.
    """
    try:
        amt = int(unique_amount)
    except (ValueError, TypeError):
        amt = 0
    if currency == "toman":
        rial = amt * 10
        if lang == "fa":
            return (
                f"🪙 تومان: <code>{amt}</code> تومان\n"
                f"🪙 ریال: <code>{rial}</code> ریال"
            )
        return (
            f"🪙 Toman: <code>{amt}</code> Toman\n"
            f"🪙 Rial: <code>{rial}</code> Rial"
        )
    # USD / other currencies
    if lang == "fa":
        return f"💵 دلار: <code>{amt}</code> دلار"
    return f"💵 USD: <code>{amt}</code> USD"


def L(lang: str) -> str:
    """Normalise a language code to one we support."""
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANGUAGE


# ============================================================================
# SECTION 2: DATABASE LAYER
# ============================================================================

class Database:
    """Async SQLite wrapper for all bot state."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------ init
    async def connect(self):
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        # ---- Tuning for low-RAM single-core host (1GB RAM, 1 vCPU) ----
        # WAL = concurrent readers + 1 writer, fewer lock conflicts.
        # cache_size negative = KB; 2048 KB ≈ 2 MB page-cache (small but enough).
        # synchronous=NORMAL is safe under WAL and dramatically faster than FULL.
        # temp_store=MEMORY keeps temporary tables/indexes out of disk.
        # mmap_size=0 disables memory-mapped I/O (avoids RSS bloat on low RAM).
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA cache_size=-2048")
        await self._db.execute("PRAGMA temp_store=MEMORY")
        await self._db.execute("PRAGMA mmap_size=0")
        await self._create_tables()
        await self._migrate()
        await self._seed_settings()
        await self._auto_commit()
        logger.info("Database initialised")

    async def disconnect(self):
        if self._db:
            await self._db.close()
            logger.info("Database connection closed")

    # ---- Transaction context manager (C7/H10) ----
    # Allows multi-step writes to be wrapped in BEGIN IMMEDIATE / COMMIT,
    # so a crash mid-flow can't leave partial state. Use:
    #     async with db.transaction():
    #         await db.add_account(...)
    #         await db.update_user_balance(...)
    # All db.* methods inside auto-detect the active transaction and skip
    # their own commit() — see `_auto_commit()`.
    _TXN: contextvars.ContextVar[bool] = contextvars.ContextVar("_db_txn", default=False)

    @contextlib.asynccontextmanager
    async def transaction(self):
        """BEGIN IMMEDIATE ... COMMIT (or ROLLBACK on exception).

        Acquires a write lock up-front so no other writer can interleave.
        Inner db methods that auto-commit will detect the active transaction
        and skip their own commit (the context manager commits everything at once).
        """
        token = self._TXN.set(True)
        begun = False
        try:
            await self._db.execute("BEGIN IMMEDIATE")
            begun = True
            yield
            await self._db.commit()
        except Exception:
            if begun:
                try:
                    await self._db.execute("ROLLBACK")
                except Exception:
                    pass
            raise
        finally:
            self._TXN.reset(token)

    async def _auto_commit(self):
        """Commit only if NOT inside an explicit transaction()."""
        if not self._TXN.get():
            await self._db.commit()

    async def _create_tables(self):
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id            INTEGER PRIMARY KEY,
                username         TEXT,
                first_name       TEXT,
                language         TEXT DEFAULT 'fa',
                balance          REAL DEFAULT 0  CHECK (balance >= 0),
                is_banned        INTEGER DEFAULT 0,
                referred_by      INTEGER,
                referral_code    TEXT UNIQUE,
                referral_rewarded INTEGER DEFAULT 0,
                created_at       TEXT DEFAULT (datetime('now')),
                last_activity    TEXT,
                total_spent      REAL DEFAULT 0,
                total_orders     INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS servers (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                alias        TEXT NOT NULL,
                panel_url    TEXT NOT NULL,
                api_token    TEXT NOT NULL,
                sub_uri      TEXT,
                capacity     INTEGER DEFAULT 0,    -- 0 = unlimited
                priority     INTEGER DEFAULT 10,   -- lower = preferred
                location     TEXT,
                is_active    INTEGER DEFAULT 1,
                is_healthy   INTEGER DEFAULT 1,
                last_check   TEXT,
                last_error   TEXT,
                total_clients INTEGER DEFAULT 0,
                total_traffic INTEGER DEFAULT 0,
                created_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS inbounds (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id   INTEGER NOT NULL,
                inbound_id  INTEGER NOT NULL,
                remark      TEXT,
                protocol    TEXT,
                port        INTEGER,
                enable      INTEGER DEFAULT 1,
                tag         TEXT,
                tls_flow_capable INTEGER DEFAULT 0,
                UNIQUE(server_id, inbound_id),
                FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS plans (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                description   TEXT,
                traffic_gb    REAL NOT NULL,         -- GB (fractional OK: 0.2 = 200 MB; 0 = unlimited)
                duration_days INTEGER NOT NULL,
                price         REAL NOT NULL,
                limit_ip      INTEGER DEFAULT 0,
                inbound_ids   TEXT,            -- JSON: ["server_id:inbound_id", ...]
                is_active     INTEGER DEFAULT 1,
                sort_order    INTEGER DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_tg_id  INTEGER NOT NULL,
                server_id   INTEGER NOT NULL,
                email       TEXT NOT NULL UNIQUE,
                sub_id      TEXT,
                label       TEXT,
                plan_id     INTEGER,
                traffic_gb  REAL,                    -- GB (fractional OK: 0.2 = 200 MB; 0 = unlimited)
                expiry_time INTEGER,
                limit_ip    INTEGER DEFAULT 0,
                is_active   INTEGER DEFAULT 1,
                is_trial    INTEGER DEFAULT 0,
                inbound_ids TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                renewed_at  TEXT,
                FOREIGN KEY (user_tg_id) REFERENCES users(tg_id),
                FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE SET NULL,
                FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_tg_id   INTEGER NOT NULL,
                amount       REAL NOT NULL,
                type         TEXT NOT NULL,      -- purchase|renewal|topup|deposit|gift_balance|gift_plan|trial|admin_adjust
                description  TEXT,
                account_email TEXT,
                plan_id      INTEGER,
                status       TEXT DEFAULT 'completed',
                admin_id     INTEGER,
                created_at   TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_tg_id) REFERENCES users(tg_id)
            );

            CREATE TABLE IF NOT EXISTS promo_codes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                code              TEXT UNIQUE NOT NULL,
                discount_percent  INTEGER DEFAULT 0,
                discount_amount   REAL DEFAULT 0,
                max_uses          INTEGER DEFAULT 0,
                used_count        INTEGER DEFAULT 0,
                expires_at        TEXT,
                is_active         INTEGER DEFAULT 1,
                created_at        TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS gift_codes (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                code          TEXT UNIQUE NOT NULL,
                type          TEXT NOT NULL,       -- balance | plan
                value         TEXT NOT NULL,
                plan_id       INTEGER,
                created_by    INTEGER,
                is_used       INTEGER DEFAULT 0,
                used_by       INTEGER,
                used_at       TEXT,
                created_at    TEXT DEFAULT (datetime('now')),
                expires_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_tg_id   INTEGER NOT NULL,
                subject      TEXT,
                category     TEXT DEFAULT 'other',     -- technical|payment|account|other
                status       TEXT DEFAULT 'open',       -- open|closed
                last_sender  TEXT DEFAULT 'user',       -- user|admin (for waiting indicator)
                priority     TEXT DEFAULT 'normal',
                created_at   TEXT DEFAULT (datetime('now')),
                updated_at   TEXT,
                closed_at    TEXT,
                FOREIGN KEY (user_tg_id) REFERENCES users(tg_id)
            );

            CREATE TABLE IF NOT EXISTS ticket_messages (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id      INTEGER NOT NULL,
                sender         TEXT NOT NULL,        -- user | admin
                message        TEXT,
                media_type     TEXT,                 -- photo | document | video | voice | None
                media_file_id  TEXT,                 -- Telegram file_id of the attached media
                media_caption  TEXT,                 -- original caption (already in message for text-only)
                created_at     TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS broadcasts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id     INTEGER,
                message      TEXT,
                target       TEXT DEFAULT 'all',
                total_sent   INTEGER DEFAULT 0,
                total_failed INTEGER DEFAULT 0,
                status       TEXT DEFAULT 'pending',
                created_at   TEXT DEFAULT (datetime('now')),
                scheduled_at TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS traffic_alerts (
                account_email TEXT,
                threshold     INTEGER,
                alerted_at    TEXT DEFAULT (datetime('now')),
                UNIQUE(account_email, threshold)
            );

            CREATE TABLE IF NOT EXISTS expiry_reminders (
                account_email TEXT,
                days_before   INTEGER,
                reminded_at   TEXT DEFAULT (datetime('now')),
                UNIQUE(account_email, days_before)
            );

            CREATE TABLE IF NOT EXISTS referral_rewards (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_tg_id  INTEGER,
                referred_tg_id  INTEGER,
                account_email   TEXT,
                bonus_days      INTEGER,
                bonus_gb        INTEGER,
                created_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(referrer_tg_id, referred_tg_id)
            );

            CREATE TABLE IF NOT EXISTS payments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_tg_id    INTEGER NOT NULL,
                amount        REAL NOT NULL,
                unique_amount REAL NOT NULL,
                card_number   TEXT,
                card_holder   TEXT,
                receipt_type  TEXT,
                receipt_file_id TEXT,
                receipt_text  TEXT,
                status        TEXT DEFAULT 'pending',
                admin_id      INTEGER,
                admin_note    TEXT,
                created_at    TEXT DEFAULT (datetime('now')),
                reviewed_at   TEXT,
                FOREIGN KEY (user_tg_id) REFERENCES users(tg_id)
            );

            CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_tg_id);
            CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);

            CREATE INDEX IF NOT EXISTS idx_accounts_user   ON accounts(user_tg_id);
            CREATE INDEX IF NOT EXISTS idx_accounts_email  ON accounts(email);
            CREATE INDEX IF NOT EXISTS idx_tx_user          ON transactions(user_tg_id);
            CREATE INDEX IF NOT EXISTS idx_tickets_user     ON tickets(user_tg_id);
            CREATE INDEX IF NOT EXISTS idx_users_referral  ON users(referral_code);
            CREATE INDEX IF NOT EXISTS idx_inbounds_server ON inbounds(server_id);
        """)

    async def _migrate(self):
        """Add columns that may not exist on legacy databases."""
        add_cols = {
            "users": [("referral_rewarded", "INTEGER DEFAULT 0"), ("language_selected", "INTEGER DEFAULT 0"),
                      ("trial_used_at", "TEXT"), ("blocked_bot", "INTEGER DEFAULT 0")],
            "servers": [
                ("sub_uri", "TEXT"),
                ("capacity", "INTEGER DEFAULT 0"),
                ("priority", "INTEGER DEFAULT 10"),
                ("location", "TEXT"),
            ],
            "plans": [("inbound_ids", "TEXT")],
            "accounts": [("label", "TEXT")],
            "transactions": [("admin_id", "INTEGER")],
            "tickets": [("category", "TEXT DEFAULT 'other'"), ("last_sender", "TEXT DEFAULT 'user'")],
            "ticket_messages": [("media_type", "TEXT"), ("media_file_id", "TEXT"), ("media_caption", "TEXT")],
            # SHORTFALL-REQUEST: when a user clicks "Request Shortfall" on
            # the purchase review page, we create a payment for the exact
            # missing amount and store the plan they wanted to buy here.
            # On approval, the bot asks the user "ready to buy plan X?" and
            # offers a one-tap "Buy Now" button so they don't have to
            # navigate back to the plan list.
            "payments": [("resume_plan_id", "INTEGER"),
                         # RECEIPT-HISTORY: store the approving/rejecting admin's
                         # username (or first_name fallback) at decision time so
                         # the history view can show "approved by @admin" without
                         # an extra JOIN — and survives even if the admin later
                         # blocks the bot (so they're not in `users` anymore).
                         ("admin_username", "TEXT"),
                         # RECEIPT-CROSS-ADMIN-CLEANUP: JSON map of
                         # {admin_id_str: {"chat_id": int, "message_id": int,
                         #                 "type": "photo"|"document"|"text"}}
                         # populated when the "new payment" notification is
                         # broadcast to all admins.  When any admin
                         # approves/rejects, we iterate this map and edit each
                         # notification to "✅ Approved by …" (or ❌ Rejected)
                         # with no action buttons — so the other admins see the
                         # outcome and can't tap Approve on an already-processed
                         # receipt.  Falls back to delete if the edit fails.
                         ("notif_msg_ids", "TEXT")],
        }
        for table, cols in add_cols.items():
            async with self._db.execute(f"PRAGMA table_info({table})") as cur:
                existing = {row[1] for row in await cur.fetchall()}
            for col, decl in cols:
                if col not in existing:
                    await self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    logger.info("Migrated: added %s.%s", table, col)

        # ---- Versioned migrations (M8) ----
        # Each numbered migration is applied in order and recorded. This
        # replaces ad-hoc ALTER TABLE calls with a traceable schema history.
        await self._db.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TEXT DEFAULT (datetime('now'))"
            ")"
        )
        await self._db.commit()
        async with self._db.execute("SELECT MAX(version) AS v FROM schema_version") as cur:
            row = await cur.fetchone()
            current = (row["v"] if row else None) or 0

        migrations = [
            (1, "schema_v1_baseline", []),
            (2, "balance_nonnegative", [
                # C8 — clamp any pre-existing negative balances to 0 so the
                # CHECK constraint (added on fresh DBs by _create_tables)
                # won't reject the row. Old installs may have -X from a race.
                "UPDATE users SET balance = 0 WHERE balance < 0",
            ]),
            (3, "tickets_user_index", [
                # Speed up admin ticket list (which joins users).
                "CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);",
            ]),
            (4, "payments_pending_index", [
                # Speed up admin "pending payments" view.
                "CREATE INDEX IF NOT EXISTS idx_payments_pending "
                "  ON payments(status) WHERE status = 'pending';",
            ]),
            (5, "referral_rewards_unique_and_indexes", [
                # H7 — prevent double-claim race: two concurrent Claim Reward
                # calls would both INSERT a reward row for the same
                # (referrer, referred) pair. The UNIQUE index below makes the
                # second INSERT fail (handled via INSERT OR IGNORE by callers).
                # SQLite can't add a UNIQUE constraint to an existing table
                # directly, so we create a unique INDEX (equivalent enforcement).
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_rewards_pair "
                "  ON referral_rewards(referrer_tg_id, referred_tg_id);",
                # Speed up get_expiring_accounts / get_all_active_accounts.
                "CREATE INDEX IF NOT EXISTS idx_accounts_active_expiry "
                "  ON accounts(expiry_time) WHERE is_active = 1 AND expiry_time > 0;",
                "CREATE INDEX IF NOT EXISTS idx_accounts_active "
                "  ON accounts(is_active) WHERE is_active = 1;",
            ]),
        ]
        for ver, name, stmts in migrations:
            if ver <= current:
                continue
            for stmt in stmts:
                await self._db.execute(stmt)
            await self._db.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (ver,)
            )
            logger.info("Migrated: applied v%d (%s)", ver, name)
        await self._db.commit()

    async def _seed_settings(self):
        defaults = {
            "trial_enabled": "1",
            "trial_days": str(int(os.getenv("TRIAL_DAYS", "3"))),
            # BUG-9 FIX: use float() not int() so fractional env values like
            # TRIAL_GB=0.2 (200 MB) don't crash startup with ValueError. Task 12
            # added fractional GB support everywhere else; the seed path was
            # missed. TRIAL_DAYS / REFERRAL_BONUS_DAYS stay int (whole days).
            "trial_gb": str(float(os.getenv("TRIAL_GB", "5"))),
            "trial_limit_ip": "1",
            "trial_inbounds": "[]",   # JSON list of "server_id_inbound_id"
            "referral_bonus_days": str(int(os.getenv("REFERRAL_BONUS_DAYS", "5"))),
            "referral_bonus_gb": str(float(os.getenv("REFERRAL_BONUS_GB", "2"))),
            "referral_enabled": "1",   # admin can disable the whole referral program
            # REFERRAL-TEXT-CFG: main admin can customise the share pitch
            # (the message users forward to friends) and add an extra note
            # shown at the bottom of the referral section. Both are per
            # language (fa/en). Empty share text → built-in locale default;
            # empty extra note → nothing appended.
            "referral_share_text_fa": "",
            "referral_share_text_en": "",
            "referral_extra_text_fa": "",
            "referral_extra_text_en": "",
            "currency": DEFAULT_CURRENCY,
            "default_language": DEFAULT_LANGUAGE,
            "topup_packages": json.dumps([5, 10, 20, 50]),  # GB options
            # TOPUP-TOGGLE: admin can disable the whole top-up feature so
            # users can't add traffic to existing accounts (renew still works
            # — only the standalone "+traffic" path is gated). When disabled,
            # the topup button is hidden from account-detail / traffic alerts
            # AND a guard at the top of cb_account_topup / cb_topup_buy
            # rejects any in-flight callback (in case a stale button is
            # sitting in a user's old message).
            "topup_enabled": "1",
            "payment_enabled": "1",
            "payment_card_number": "",
            "payment_card_holder": "",
            "payment_presets": json.dumps([50000, 100000, 200000, 500000]),
            "payment_min_amount": "50000",
            "force_join_enabled": "0",
            "force_join_channels": json.dumps([]),
            "help_text_en": "",
            "help_text_fa": "",
            # GUIDES — dual guides (usage + connection), editable per language.
            # Empty default → the handler falls back to DEFAULT_GUIDE_*_EN/FA
            # constants so users always see something useful, even before the
            # admin customises them.
            "guide_usage_en": "",
            "guide_usage_fa": "",
            "guide_connection_en": "",
            "guide_connection_fa": "",
            # BACKUP-CFG — auto DB backup cadence, configurable from the bot.
            # backup_enabled=0 → off (use fixed DB_BACKUP_INTERVAL_SECONDS only
            # as the historical fallback). backup_interval_minutes=1440 = 24h.
            "backup_enabled": "0",
            "backup_interval_minutes": "1440",
            "backup_keep": "3",
        }
        for k, v in defaults.items():
            await self._db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
            )

    # ------------------------------------------------------------- users
    async def get_or_create_user(self, tg_id: int, username: str = "",
                                 first_name: str = "", ref_code: str = "") -> dict:
        async with self._db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
        if row:
            await self._db.execute(
                "UPDATE users SET last_activity = ?, username = ?, first_name = ? WHERE tg_id = ?",
                (datetime.now(timezone.utc).isoformat(), username, first_name, tg_id),
            )
            await self._auto_commit()
            return dict(row)

        ref_code_generated = await self._gen_referral_code()
        referred_by = None
        if ref_code and ref_code != ref_code_generated:
            async with self._db.execute(
                "SELECT tg_id FROM users WHERE referral_code = ?", (ref_code,)
            ) as cur:
                ref_row = await cur.fetchone()
                if ref_row and ref_row["tg_id"] != tg_id:
                    referred_by = ref_row["tg_id"]

        lang = await self.get_setting("default_language", DEFAULT_LANGUAGE)
        await self._db.execute(
            """INSERT INTO users
               (tg_id, username, first_name, language, referred_by, referral_code, last_activity)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tg_id, username, first_name, L(lang), referred_by, ref_code_generated,
             datetime.now(timezone.utc).isoformat()),
        )
        await self._auto_commit()
        async with self._db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            return dict(await cur.fetchone())

    async def _gen_referral_code(self) -> str:
        """Generate a 'REFxxxxxx' code using cryptographic RNG (C6), guaranteed
        DB-unique (L7).

        ``secrets.choice`` is used instead of ``random.choices`` because
        referral codes are security-sensitive (they grant bonuses).

        L7 — the legacy generator picked a 6-char suffix with no uniqueness
        check. ``get_or_create_user`` does not catch ``IntegrityError``, so a
        collision (5.4 × 36^6 ≈ 1 in 2 billion chance per attempt) would
        crash the /start handler for that user. We now retry up to 5 times,
        each time probing the DB for an existing row, and fall back to
        ``secrets.token_hex(4).upper()`` (still unique by entropy) if every
        retry collides (astronomically unlikely)."""
        chars = string.ascii_uppercase + string.digits
        for _ in range(5):
            code = "REF" + "".join(secrets.choice(chars) for _ in range(REFERRAL_CODE_LEN))
            async with self._db.execute(
                "SELECT 1 FROM users WHERE referral_code = ?", (code,)
            ) as cur:
                if not await cur.fetchone():
                    return code
        # Extremely unlikely fallback — 4 bytes of hex (8 chars) of entropy.
        return "REF" + secrets.token_hex(4).upper()

    async def get_user(self, tg_id: int) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_user_language(self, tg_id: int, lang: str):
        await self._db.execute(
            "UPDATE users SET language = ? WHERE tg_id = ?", (L(lang), tg_id)
        )
        await self._auto_commit()

    async def update_user_balance(self, tg_id: int, amount: float, add: bool = True) -> bool:
        """Atomically adjust balance.

        Returns True on success. For deductions (add=False), the operation is
        conditional: it only succeeds if the resulting balance would be >= 0,
        preventing race-condition double-spending (C8).
        """
        if add:
            await self._db.execute(
                "UPDATE users SET balance = balance + ? WHERE tg_id = ?", (amount, tg_id)
            )
            await self._auto_commit()
            return True
        # Deduction: conditional to prevent balance going negative (C8).
        cur = await self._db.execute(
            "UPDATE users SET balance = balance - ? WHERE tg_id = ? AND balance >= ?",
            (amount, tg_id, amount),
        )
        await self._auto_commit()
        return cur.rowcount == 1

    async def try_deduct_balance(self, tg_id: int, amount: float) -> bool:
        """Atomic check-and-deduct. Returns False if balance is insufficient.

        This replaces the dangerous pattern:
            balance = db_user.get('balance')
            if balance < price: return
            await db.update_user_balance(uid, price, add=False)
        which has a TOCTOU race window between the read and the write.
        Use this method instead so the check and the deduction are a single
        atomic SQL operation.
        """
        cur = await self._db.execute(
            "UPDATE users SET balance = balance - ? WHERE tg_id = ? AND balance >= ?",
            (amount, tg_id, amount),
        )
        await self._auto_commit()
        return cur.rowcount == 1

    async def set_user_balance(self, tg_id: int, amount: float):
        await self._db.execute(
            "UPDATE users SET balance = ? WHERE tg_id = ?", (amount, tg_id)
        )
        await self._auto_commit()

    async def mark_referral_rewarded(self, tg_id: int) -> bool:
        """Atomic claim: marks the user as referral-rewarded.

        Returns True if THIS call actually performed the transition (i.e. the
        user was previously unrewarded). Returns False if the user was already
        marked rewarded — this prevents double-reward races (C2): if two
        concurrent purchases both reach this point, only one wins the row.
        """
        cur = await self._db.execute(
            "UPDATE users SET referral_rewarded = 1 WHERE tg_id = ? AND referral_rewarded = 0",
            (tg_id,),
        )
        await self._auto_commit()
        return cur.rowcount == 1

    async def ban_user(self, tg_id: int, banned: bool = True):
        await self._db.execute(
            "UPDATE users SET is_banned = ? WHERE tg_id = ?", (1 if banned else 0, tg_id)
        )
        await self._auto_commit()

    async def get_all_users(self) -> List[dict]:
        async with self._db.execute("SELECT * FROM users ORDER BY created_at DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def count_users(self) -> int:
        async with self._db.execute("SELECT COUNT(*) AS cnt FROM users") as cur:
            return (await cur.fetchone())["cnt"]

    async def mark_user_blocked(self, tg_id: int):
        """Flag a user as having blocked the bot (M19). Set on
        TelegramForbiddenError during broadcast so the user is excluded from
        future broadcasts instead of being retried every cycle."""
        await self._db.execute(
            "UPDATE users SET blocked_bot = 1 WHERE tg_id = ?", (tg_id,)
        )
        await self._auto_commit()

    async def get_users_by_filter(self, filter_type: str) -> List[int]:
        # M19 — exclude users who blocked the bot (flagged on
        # TelegramForbiddenError during broadcast). Sending to them again just
        # wastes throttle slots and re-raises the same error every cycle.
        blocked_clause = "AND blocked_bot = 0"
        if filter_type == "all":
            sql = f"SELECT tg_id FROM users WHERE is_banned = 0 {blocked_clause}"
        elif filter_type == "active":
            sql = f"""SELECT DISTINCT u.tg_id FROM users u
                     JOIN accounts a ON u.tg_id = a.user_tg_id
                     WHERE a.is_active = 1 AND u.is_banned = 0 {blocked_clause}"""
        elif filter_type == "expired":
            sql = f"""SELECT DISTINCT u.tg_id FROM users u
                     JOIN accounts a ON u.tg_id = a.user_tg_id
                     WHERE a.is_active = 0 AND u.is_banned = 0 {blocked_clause}"""
        elif filter_type == "trial":
            sql = f"""SELECT DISTINCT u.tg_id FROM users u
                     JOIN accounts a ON u.tg_id = a.user_tg_id
                     WHERE a.is_trial = 1 AND u.is_banned = 0 {blocked_clause}"""
        elif filter_type == "banned":
            sql = "SELECT tg_id FROM users WHERE is_banned = 1"
        else:
            return []
        async with self._db.execute(sql) as cur:
            return [r["tg_id"] for r in await cur.fetchall()]

    async def get_user_languages_by_ids(self, tg_ids: Iterable[int]) -> Dict[int, str]:
        """Batch-fetch languages for many users in ONE query (H2 — N+1 fix).

        Returns {tg_id: language_code}. Missing users are omitted from the
        result; callers should default to DEFAULT_LANGUAGE for them.
        """
        ids = list(tg_ids)
        if not ids:
            return {}
        # SQLite parameter limit is typically 999 — chunk if needed.
        out: Dict[int, str] = {}
        chunk_size = 500
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            q = f"SELECT tg_id, language FROM users WHERE tg_id IN ({placeholders})"
            async with self._db.execute(q, chunk) as cur:
                async for row in cur:
                    out[row["tg_id"]] = row["language"] or DEFAULT_LANGUAGE
        return out

    async def search_user(self, query: str) -> List[dict]:
        results: List[dict] = []
        seen = set()
        # Escape SQL LIKE wildcards in the user-supplied query so an admin
        # searching for a literal "%" or "_" doesn't match every row.
        # (Parameter-bound — not a security issue, just a UX correctness fix.)
        esc_q = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        if query.isdigit():
            async with self._db.execute("SELECT * FROM users WHERE tg_id = ? LIMIT 20", (int(query),)) as cur:
                for r in await cur.fetchall():
                    results.append(dict(r))
                    seen.add(r["tg_id"])
        async with self._db.execute(
            "SELECT * FROM users WHERE username LIKE ? ESCAPE '\\' OR first_name LIKE ? ESCAPE '\\' LIMIT 20",
            (f"%{esc_q}%", f"%{esc_q}%"),
        ) as cur:
            for r in await cur.fetchall():
                if r["tg_id"] not in seen:
                    results.append(dict(r))
                    seen.add(r["tg_id"])
        async with self._db.execute(
            """SELECT u.* FROM users u JOIN accounts a ON u.tg_id = a.user_tg_id
               WHERE a.email LIKE ? ESCAPE '\\' LIMIT 20""", (f"%{esc_q}%",),
        ) as cur:
            for r in await cur.fetchall():
                if r["tg_id"] not in seen:
                    results.append(dict(r))
                    seen.add(r["tg_id"])
        return results

    # ------------------------------------------------------------ servers
    async def add_server(self, alias: str, panel_url: str, api_token: str,
                         capacity: int = 0, priority: int = 10,
                         location: str = "") -> int:
        cur = await self._db.execute(
            """INSERT INTO servers (alias, panel_url, api_token, capacity, priority, location)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (alias, panel_url.rstrip("/"), api_token, capacity, priority, location),
        )
        await self._auto_commit()
        return cur.lastrowid

    async def get_servers(self, active_only: bool = False) -> List[dict]:
        q = "SELECT * FROM servers"
        if active_only:
            q += " WHERE is_active = 1"
        q += " ORDER BY priority ASC, id ASC"
        async with self._db.execute(q) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_server(self, server_id: int) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM servers WHERE id = ?", (server_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # Allowlists for safe dynamic UPDATE column names (C5 — SQL-injection
    # hardening). Any column name outside this list is rejected.
    _SERVER_FIELDS = {"alias", "panel_url", "api_token", "sub_uri", "capacity",
                      "priority", "location", "is_active",
                      # SYNC-COUNTS-1: total_clients and total_traffic are
                      # legitimate columns updated by task_sync_client_counts
                      # (every 30 min) and the admin "Sync Counts" button
                      # (cb_cleanup_sync_counts).  They were missing from this
                      # allowlist, which caused:
                      #   ValueError: Invalid server field(s): {'total_clients'}
                      # at startup.  (update_server_health uses a raw SQL
                      # query so it bypassed this check, but the sync task
                      # goes through the kwargs path.)
                      "total_clients", "total_traffic"}
    _PLAN_FIELDS = {"name", "description", "traffic_gb", "duration_days", "price",
                    "limit_ip", "inbound_ids", "is_active", "sort_order"}
    _ACCOUNT_FIELDS = {"label", "plan_id", "traffic_gb", "expiry_time", "limit_ip",
                       "inbound_ids", "is_active", "is_trial", "server_id", "renewed_at"}

    async def update_server(self, server_id: int, **kwargs):
        """Update a server row. Column names are validated against a strict
        allowlist to prevent SQL injection via crafted callback_data (C5)."""
        if not kwargs:
            return
        bad = set(kwargs) - self._SERVER_FIELDS
        if bad:
            raise ValueError(f"Invalid server field(s): {bad}")
        # M8 — strip trailing slash from panel_url to avoid double-slash URLs.
        if "panel_url" in kwargs and isinstance(kwargs["panel_url"], str):
            kwargs["panel_url"] = kwargs["panel_url"].rstrip("/")
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [server_id]
        await self._db.execute(f"UPDATE servers SET {sets} WHERE id = ?", vals)
        await self._auto_commit()

    async def update_server_health(self, server_id: int, healthy: bool,
                                   error: str = "", total_clients: int = 0,
                                   total_traffic: int = 0):
        await self._db.execute(
            """UPDATE servers SET is_healthy = ?, last_check = ?, last_error = ?,
               total_clients = ?, total_traffic = ? WHERE id = ?""",
            (1 if healthy else 0, datetime.now(timezone.utc).isoformat(),
             error, total_clients, total_traffic, server_id),
        )
        await self._auto_commit()

    async def toggle_server(self, server_id: int, active: bool):
        await self._db.execute(
            "UPDATE servers SET is_active = ? WHERE id = ?",
            (1 if active else 0, server_id),
        )
        await self._auto_commit()

    async def delete_server(self, server_id: int):
        await self._db.execute("DELETE FROM servers WHERE id = ?", (server_id,))
        await self._auto_commit()

    # ------------------------------------------------------------ inbounds
    async def sync_inbounds(self, server_id: int, inbounds: List[dict]):
        await self._db.execute("DELETE FROM inbounds WHERE server_id = ?", (server_id,))
        for ib in inbounds:
            await self._db.execute(
                """INSERT INTO inbounds
                   (server_id, inbound_id, remark, protocol, port, enable, tag, tls_flow_capable)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (server_id, ib.get("id", 0), ib.get("remark", ""),
                 ib.get("protocol", ""), ib.get("port", 0),
                 1 if ib.get("enable", True) else 0, ib.get("tag", ""),
                 1 if ib.get("tlsFlowCapable", False) else 0),
            )
        await self._auto_commit()

    async def get_inbounds(self, server_id: int, enabled_only: bool = False) -> List[dict]:
        q = "SELECT * FROM inbounds WHERE server_id = ?"
        if enabled_only:
            q += " AND enable = 1"
        q += " ORDER BY inbound_id"
        async with self._db.execute(q, (server_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_all_inbounds(self) -> List[Tuple[dict, dict]]:
        """Return list of (server, inbound) for all active servers."""
        out = []
        for srv in await self.get_servers(active_only=True):
            for ib in await self.get_inbounds(srv["id"], enabled_only=True):
                out.append((srv, ib))
        return out

    # -------------------------------------------------------------- plans
    async def add_plan(self, name: str, description: str, traffic_gb: float,
                       duration_days: int, price: float, limit_ip: int = 0,
                       inbound_ids: Optional[List[str]] = None) -> int:
        """Insert a new plan row.

        ``inbound_ids`` is a list of ``"server_id_inbound_id"`` strings (e.g.
        ``["1_3", "2_5"]``) which is JSON-encoded into the ``plans.inbound_ids``
        column. A plan may span multiple servers, so each entry MUST carry its
        server context — this is the OPPOSITE of ``accounts.inbound_ids`` which
        stores bare integers because ``accounts.server_id`` already pins the
        server. See M13 (inbound_ids format standardisation).
        """
        cur = await self._db.execute(
            """INSERT INTO plans
               (name, description, traffic_gb, duration_days, price, limit_ip, inbound_ids)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, description, traffic_gb, duration_days, price, limit_ip,
             json.dumps(inbound_ids or [])),
        )
        await self._auto_commit()
        return cur.lastrowid

    async def get_plans(self, active_only: bool = True) -> List[dict]:
        q = "SELECT * FROM plans"
        if active_only:
            q += " WHERE is_active = 1"
        q += " ORDER BY sort_order, price"
        async with self._db.execute(q) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_plan(self, plan_id: int) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def toggle_plan(self, plan_id: int, active: bool):
        await self._db.execute(
            "UPDATE plans SET is_active = ? WHERE id = ?", (1 if active else 0, plan_id)
        )
        await self._auto_commit()

    async def delete_plan(self, plan_id: int):
        await self._db.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        await self._auto_commit()

    async def update_plan(self, plan_id: int, **kwargs):
        """Update a plan row. Column names validated against allowlist (C5)."""
        if not kwargs:
            return
        bad = set(kwargs) - self._PLAN_FIELDS
        if bad:
            raise ValueError(f"Invalid plan field(s): {bad}")
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [plan_id]
        await self._db.execute(f"UPDATE plans SET {sets} WHERE id = ?", vals)
        await self._auto_commit()

    # ------------------------------------------------------------ accounts
    async def add_account(self, user_tg_id: int, server_id: int, email: str,
                          sub_id: str, plan_id: Optional[int], traffic_gb: float,
                          expiry_time: int, limit_ip: int, inbound_ids: str,
                          is_trial: bool = False, label: str = "") -> int:
        """Insert a new account row.

        ``inbound_ids`` MUST be a JSON string of BARE inbound_id integers
        (e.g. ``'[3, 5]'``) — NOT the ``"server_id_inbound_id"`` strings used
        by ``plans.inbound_ids``. The server context is already pinned by the
        ``accounts.server_id`` column, so the server prefix would be redundant.
        See M13 (inbound_ids format standardisation).
        """
        cur = await self._db.execute(
            """INSERT INTO accounts
               (user_tg_id, server_id, email, sub_id, label, plan_id, traffic_gb,
                expiry_time, limit_ip, inbound_ids, is_trial)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_tg_id, server_id, email, sub_id, label, plan_id, traffic_gb,
             expiry_time, limit_ip, inbound_ids, 1 if is_trial else 0),
        )
        await self._auto_commit()
        return cur.lastrowid

    async def upsert_user_minimal(self, tg_id: int, username: str = "",
                                  first_name: str = "") -> None:
        """Ensure a ``users`` row exists for ``tg_id`` without clobbering an
        existing row. Used by the panel-client import flow (MIGRATE-1) so an
        imported account is linked to a user stub immediately; when the real
        user later runs ``/start``, :meth:`get_or_create_user` finds the row
        and only refreshes ``username``/``first_name``/``last_activity``.

        A real referral code is generated (NULL would permanently break that
        user's ability to act as a referrer, since ``get_or_create_user``
        does not backfill it on an existing row)."""
        if not tg_id:
            return
        async with self._db.execute(
            "SELECT 1 FROM users WHERE tg_id = ?", (tg_id,)
        ) as cur:
            if await cur.fetchone():
                return
        ref_code = await self._gen_referral_code()
        lang = await self.get_setting("default_language", DEFAULT_LANGUAGE)
        await self._db.execute(
            """INSERT OR IGNORE INTO users
               (tg_id, username, first_name, language, referral_code, last_activity)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tg_id, username, first_name, L(lang), ref_code,
             datetime.now(timezone.utc).isoformat()),
        )
        await self._auto_commit()

    async def upsert_account(self, user_tg_id: int, server_id: int, email: str,
                             sub_id: str, traffic_gb: float, expiry_time: int,
                             limit_ip: int, inbound_ids: str,
                             is_active: bool = True, is_trial: bool = False,
                             plan_id: Optional[int] = None, label: str = "") -> int:
        """Idempotent insert-or-update of an account row keyed by ``email``
        (UNIQUE). Used by the panel-client import flow (MIGRATE-1) so
        re-importing the same client updates the row instead of crashing
        with an IntegrityError. Returns the account row id."""
        # M20 — detect silent ownership transfer. If the account already exists
        # with a DIFFERENT user_tg_id, log a warning so the admin is aware that
        # re-importing this client moved the account from one user to another.
        async with self._db.execute(
            "SELECT user_tg_id FROM accounts WHERE email = ?", (email,)
        ) as cur:
            existing = await cur.fetchone()
        if existing and existing["user_tg_id"] != user_tg_id:
            logger.warning(
                "upsert_account: ownership change for %s — was tg_id=%s, now tg_id=%s",
                email, existing["user_tg_id"], user_tg_id,
            )
        await self._db.execute(
            """INSERT INTO accounts
               (user_tg_id, server_id, email, sub_id, label, plan_id, traffic_gb,
                expiry_time, limit_ip, inbound_ids, is_active, is_trial)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET
                 user_tg_id  = excluded.user_tg_id,
                 server_id   = excluded.server_id,
                 sub_id      = excluded.sub_id,
                 traffic_gb  = excluded.traffic_gb,
                 expiry_time = excluded.expiry_time,
                 limit_ip    = excluded.limit_ip,
                 inbound_ids = excluded.inbound_ids,
                 is_active   = excluded.is_active,
                 is_trial    = excluded.is_trial""",
            (user_tg_id, server_id, email, sub_id, label, plan_id, traffic_gb,
             expiry_time, limit_ip, inbound_ids, 1 if is_active else 0,
             1 if is_trial else 0),
        )
        await self._auto_commit()
        async with self._db.execute(
            "SELECT id FROM accounts WHERE email = ?", (email,)
        ) as cur:
            row = await cur.fetchone()
            return row["id"] if row else 0

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
            "SELECT * FROM accounts WHERE user_tg_id = ? ORDER BY created_at DESC", (tg_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def update_account(self, email: str, **kwargs):
        """Update an account row. Column names validated against allowlist (C5)."""
        if not kwargs:
            return
        bad = set(kwargs) - self._ACCOUNT_FIELDS
        if bad:
            raise ValueError(f"Invalid account field(s): {bad}")
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [email]
        await self._db.execute(f"UPDATE accounts SET {sets} WHERE email = ?", vals)
        await self._auto_commit()

    async def delete_account(self, email: str):
        await self._db.execute("DELETE FROM accounts WHERE email = ?", (email,))
        await self._auto_commit()

    async def get_all_active_accounts(self) -> List[dict]:
        async with self._db.execute("SELECT * FROM accounts WHERE is_active = 1") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_expiring_accounts(self, days: int) -> List[dict]:
        future = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp() * 1000)
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        async with self._db.execute(
            """SELECT * FROM accounts WHERE is_active = 1
               AND expiry_time > 0 AND expiry_time <= ? AND expiry_time > ?""",
            (future, now),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def has_used_trial(self, tg_id: int) -> bool:
        """Check if a user has EVER used their free trial.

        We check TWO sources so that deleting the trial account manually
        does NOT let the user claim another trial:
          1) users.trial_used_at  — set once, the moment a trial is created.
          2) accounts WHERE is_trial = 1  — still-existing trial accounts.
        """
        async with self._db.execute(
            "SELECT COUNT(*) AS cnt FROM accounts WHERE user_tg_id = ? AND is_trial = 1",
            (tg_id,),
        ) as cur:
            if (await cur.fetchone())["cnt"] > 0:
                return True
        async with self._db.execute(
            "SELECT trial_used_at FROM users WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row["trial_used_at"])

    async def mark_trial_used(self, tg_id: int) -> bool:
        """Permanently record that this user has consumed their free trial.

        Atomic claim: returns True only if THIS call set the timestamp (i.e.
        the user had NOT consumed a trial before). Returns False if the user
        already had a trial — prevents double-claim races (C4).

        Callers MUST check the return value: if False, abort trial creation
        because another concurrent request already claimed it.
        """
        cur = await self._db.execute(
            "UPDATE users SET trial_used_at = ? WHERE tg_id = ? AND trial_used_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), tg_id),
        )
        await self._auto_commit()
        return cur.rowcount == 1

    async def _unmark_trial_used(self, tg_id: int):
        """Undo a trial claim. Used by cb_trial_activate as COMPENSATION when
        a downstream step (panel API, server selection, DB write) fails AFTER
        mark_trial_used succeeded. Without this, a transient failure would
        permanently block the user from getting a trial."""
        await self._db.execute(
            "UPDATE users SET trial_used_at = NULL WHERE tg_id = ?", (tg_id,)
        )
        await self._auto_commit()

    # --------------------------------------------------------- transactions
    async def add_transaction(self, user_tg_id: int, amount: float, type_: str,
                              description: str = "", account_email: str = "",
                              plan_id: Optional[int] = None,
                              admin_id: Optional[int] = None) -> int:
        cur = await self._db.execute(
            """INSERT INTO transactions
               (user_tg_id, amount, type, description, account_email, plan_id, admin_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_tg_id, amount, type_, description, account_email, plan_id, admin_id),
        )
        if type_ in ("purchase", "renewal", "topup"):
            await self._db.execute(
                "UPDATE users SET total_spent = total_spent + ?, total_orders = total_orders + 1 WHERE tg_id = ?",
                (amount, user_tg_id),
            )
        await self._auto_commit()
        return cur.lastrowid

    async def get_user_transactions(self, tg_id: int, limit: int = 10) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM transactions WHERE user_tg_id = ? ORDER BY created_at DESC LIMIT ?",
            (tg_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_revenue_stats(self, days: int = 30) -> dict:
        # transactions.created_at is stored via SQLite datetime('now') which
        # yields UTC 'YYYY-MM-DD HH:MM:SS'. Compare in that same format so the
        # string comparison is chronologically correct.
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.execute(
            """SELECT SUM(amount) AS total, COUNT(*) AS cnt
               FROM transactions
               WHERE type IN ('purchase','renewal','topup') AND created_at >= ?""",
            (since,),
        ) as cur:
            row = await cur.fetchone()

        # "Today" = since start of today in TEHRAN, expressed in UTC to match
        # the stored format. At Tehran midnight (00:00 +03:30) the UTC clock
        # reads 20:30 the previous day, so we compute that boundary explicitly.
        now_tehran = datetime.now(TEHRAN_TZ)
        start_today_tehran = now_tehran.replace(hour=0, minute=0, second=0, microsecond=0)
        since_today_utc = start_today_tehran.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.execute(
            """SELECT SUM(amount) AS total FROM transactions
               WHERE type IN ('purchase','renewal','topup') AND created_at >= ?""",
            (since_today_utc,),
        ) as cur:
            today_row = await cur.fetchone()

        async with self._db.execute(
            """SELECT SUM(amount) AS total FROM transactions
               WHERE type IN ('purchase','renewal','topup')""",
        ) as cur:
            all_row = await cur.fetchone()

        async with self._db.execute(
            """SELECT p.name AS name, COUNT(*) AS cnt, SUM(t.amount) AS revenue
               FROM transactions t LEFT JOIN plans p ON t.plan_id = p.id
               WHERE t.type IN ('purchase','renewal','topup') AND t.created_at >= ?
               GROUP BY p.id ORDER BY revenue DESC LIMIT 5""",
            (since,),
        ) as cur:
            top = [dict(r) for r in await cur.fetchall()]

        return {
            "total_revenue": row["total"] or 0,
            "transaction_count": row["cnt"] or 0,
            "today_revenue": today_row["total"] or 0,
            "all_time_revenue": all_row["total"] or 0,
            "top_plans": top,
        }

    # ----------------------------------------------------------- promo codes
    async def add_promo_code(self, code: str, discount_percent: int = 0,
                             discount_amount: float = 0, max_uses: int = 0,
                             expires_at: Optional[str] = None) -> int:
        cur = await self._db.execute(
            """INSERT INTO promo_codes
               (code, discount_percent, discount_amount, max_uses, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (code.upper(), discount_percent, discount_amount, max_uses, expires_at),
        )
        await self._auto_commit()
        return cur.lastrowid

    async def validate_promo_code(self, code: str) -> Optional[dict]:
        async with self._db.execute(
            "SELECT * FROM promo_codes WHERE code = ? AND is_active = 1", (code.upper(),)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            if row["max_uses"] > 0 and row["used_count"] >= row["max_uses"]:
                return None
            if row["expires_at"]:
                dt = datetime.fromisoformat(row["expires_at"])
                # H17 — defend against naive expiry strings stored by older
                # code or manual DB edits. Treat naive as UTC.
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < datetime.now(timezone.utc):
                    return None
            return dict(row)

    async def use_promo_code(self, code: str) -> bool:
        """Atomic increment with capacity check (H6).

        Only increments if the code is still under its max_uses (or max_uses=0
        meaning unlimited). Returns True if THIS call consumed a use. Prevents
        the race where two concurrent users both validate-then-increment and
        end up exceeding max_uses.
        """
        cur = await self._db.execute(
            "UPDATE promo_codes SET used_count = used_count + 1 "
            "WHERE code = ? AND is_active = 1 AND (max_uses = 0 OR used_count < max_uses)",
            (code.upper(),),
        )
        await self._auto_commit()
        return cur.rowcount == 1

    async def get_promo_codes(self) -> List[dict]:
        async with self._db.execute("SELECT * FROM promo_codes ORDER BY created_at DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def delete_promo_code(self, code_id: int):
        await self._db.execute("DELETE FROM promo_codes WHERE id = ?", (code_id,))
        await self._auto_commit()

    # ----------------------------------------------------------- gift codes
    async def create_gift_code(self, code: str, type_: str, value: str,
                               plan_id: Optional[int] = None,
                               created_by: Optional[int] = None,
                               expires_at: Optional[str] = None) -> int:
        cur = await self._db.execute(
            """INSERT INTO gift_codes (code, type, value, plan_id, created_by, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (code.upper(), type_, value, plan_id, created_by, expires_at),
        )
        await self._auto_commit()
        return cur.lastrowid

    async def get_gift_code(self, code: str) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM gift_codes WHERE code = ?", (code.upper(),)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def use_gift_code(self, code: str, user_tg_id: int) -> bool:
        """Atomic claim: marks a gift code as used.

        Returns True if THIS call claimed the code (it was previously unused).
        Returns False if the code was already used or doesn't exist — this
        prevents double-redemption races (C3).
        """
        cur = await self._db.execute(
            "UPDATE gift_codes SET is_used = 1, used_by = ?, used_at = ? "
            "WHERE code = ? AND is_used = 0",
            (user_tg_id, datetime.now(timezone.utc).isoformat(), code.upper()),
        )
        await self._auto_commit()
        return cur.rowcount == 1

    async def get_gift_codes(self, unused_only: bool = False) -> List[dict]:
        q = "SELECT * FROM gift_codes"
        if unused_only:
            q += " WHERE is_used = 0"
        q += " ORDER BY created_at DESC"
        async with self._db.execute(q) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # -------------------------------------------------------------- tickets
    async def create_ticket(self, user_tg_id: int, subject: str, category: str = "other") -> int:
        cur = await self._db.execute(
            "INSERT INTO tickets (user_tg_id, subject, category, last_sender) VALUES (?, ?, ?, 'user')",
            (user_tg_id, subject, category),
        )
        await self._auto_commit()
        return cur.lastrowid

    async def add_ticket_message(self, ticket_id: int, sender: str, message: str,
                                  media_type: str = "", media_file_id: str = "",
                                  media_caption: str = ""):
        """Append a message to a ticket. Supports optional media attachments
        (photo/document/video/voice) for the ticket-system media feature."""
        await self._db.execute(
            "INSERT INTO ticket_messages (ticket_id, sender, message, media_type, media_file_id, media_caption) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ticket_id, sender, message, media_type, media_file_id, media_caption),
        )
        await self._db.execute(
            "UPDATE tickets SET updated_at = ?, last_sender = ?, status = 'open' WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), sender, ticket_id),
        )
        await self._auto_commit()

    async def get_ticket(self, ticket_id: int) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_ticket_messages(self, ticket_id: int) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY created_at", (ticket_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_user_tickets(self, tg_id: int) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM tickets WHERE user_tg_id = ? ORDER BY updated_at DESC", (tg_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_open_tickets(self) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM tickets WHERE status = 'open' ORDER BY updated_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_all_tickets(self, limit: int = 100) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM tickets ORDER BY updated_at DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def close_ticket(self, ticket_id: int):
        await self._db.execute(
            "UPDATE tickets SET status = 'closed', closed_at = ?, last_sender = 'admin' WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), ticket_id),
        )
        await self._auto_commit()

    async def reopen_ticket(self, ticket_id: int):
        """Reopen a previously closed ticket (user continues the same issue)."""
        await self._db.execute(
            "UPDATE tickets SET status = 'open', closed_at = NULL, last_sender = 'user', "
            "updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), ticket_id),
        )
        await self._auto_commit()

    async def count_open_tickets(self) -> int:
        async with self._db.execute("SELECT COUNT(*) AS cnt FROM tickets WHERE status = 'open'") as cur:
            return (await cur.fetchone())["cnt"]

    # ----------------------------------------------------------- broadcasts
    async def create_broadcast(self, admin_id: int, message: str, target: str = "all") -> int:
        cur = await self._db.execute(
            "INSERT INTO broadcasts (admin_id, message, target) VALUES (?, ?, ?)",
            (admin_id, message, target),
        )
        await self._auto_commit()
        return cur.lastrowid

    async def update_broadcast_stats(self, broadcast_id: int, sent: int, failed: int,
                                     status: str = "completed"):
        await self._db.execute(
            "UPDATE broadcasts SET total_sent = ?, total_failed = ?, status = ? WHERE id = ?",
            (sent, failed, status, broadcast_id),
        )
        await self._auto_commit()

    async def get_broadcasts(self, limit: int = 10) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM broadcasts ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ----------------------------------------------------- data retention (M9)
    async def purge_old_data(self) -> Dict[str, int]:
        """Delete old rows that are no longer needed for day-to-day operation.

        Policy (parameters are passed in via ?, never interpolated):
          * ticket_messages older than 180 days WHERE the ticket is closed
          * payments older than the configured retention (default 1825 days =
            5 years) WHERE status != 'pending'. M2 — was 365 days which is too
            short for financial audit / dispute resolution.
          * broadcasts older than 90 days

        Returns a dict with the per-table purge counts:
          ``{"ticket_messages": N, "payments": N, "broadcasts": N}``.
        """
        now = datetime.now(timezone.utc)
        cutoff_msgs = (now - timedelta(days=180)).strftime("%Y-%m-%d %H:%M:%S")
        # M2 — configurable payment retention; default 5 years for audit.
        pay_days = await self.get_setting_int("payment_retention_days", 1825)
        cutoff_pay = (now - timedelta(days=pay_days)).strftime("%Y-%m-%d %H:%M:%S")
        cutoff_bcast = (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")

        cur = await self._db.execute(
            """DELETE FROM ticket_messages
               WHERE created_at < ?
                 AND ticket_id IN (SELECT id FROM tickets WHERE status = 'closed')""",
            (cutoff_msgs,),
        )
        n_msgs = cur.rowcount or 0

        cur = await self._db.execute(
            "DELETE FROM payments WHERE created_at < ? AND status != ?",
            (cutoff_pay, "pending"),
        )
        n_pay = cur.rowcount or 0

        cur = await self._db.execute(
            "DELETE FROM broadcasts WHERE created_at < ?",
            (cutoff_bcast,),
        )
        n_bcast = cur.rowcount or 0

        await self._auto_commit()
        return {"ticket_messages": n_msgs, "payments": n_pay, "broadcasts": n_bcast}

    # ------------------------------------------------------- alert tracking
    async def has_traffic_alert(self, email: str, threshold: int) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM traffic_alerts WHERE account_email = ? AND threshold = ?",
            (email, threshold),
        ) as cur:
            return await cur.fetchone() is not None

    async def add_traffic_alert(self, email: str, threshold: int):
        await self._db.execute(
            "INSERT OR IGNORE INTO traffic_alerts (account_email, threshold) VALUES (?, ?)",
            (email, threshold),
        )
        await self._auto_commit()

    async def clear_traffic_alerts(self, email: str):
        await self._db.execute("DELETE FROM traffic_alerts WHERE account_email = ?", (email,))
        await self._auto_commit()

    async def has_expiry_reminder(self, email: str, days: int) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM expiry_reminders WHERE account_email = ? AND days_before = ?",
            (email, days),
        ) as cur:
            return await cur.fetchone() is not None

    async def add_expiry_reminder(self, email: str, days: int):
        await self._db.execute(
            "INSERT OR IGNORE INTO expiry_reminders (account_email, days_before) VALUES (?, ?)",
            (email, days),
        )
        await self._auto_commit()

    async def clear_expiry_reminders(self, email: str):
        await self._db.execute("DELETE FROM expiry_reminders WHERE account_email = ?", (email,))
        await self._auto_commit()

    # ------------------------------------------------------------- referrals
    async def add_referral_reward(self, referrer_tg_id: int, referred_tg_id: int,
                                  account_email: str, bonus_days: int, bonus_gb: int) -> bool:
        """Record a referral reward. Returns True if THIS call inserted the row.

        Uses INSERT OR IGNORE so a concurrent double-claim (two rapid taps on
        "Claim Reward") won't produce two rows for the same (referrer, referred)
        pair — the UNIQUE index enforces it. Callers should check the return
        value to avoid applying the panel bonus twice (H7).
        """
        cur = await self._db.execute(
            """INSERT OR IGNORE INTO referral_rewards
               (referrer_tg_id, referred_tg_id, account_email, bonus_days, bonus_gb)
               VALUES (?, ?, ?, ?, ?)""",
            (referrer_tg_id, referred_tg_id, account_email, bonus_days, bonus_gb),
        )
        await self._auto_commit()
        return cur.rowcount == 1

    async def get_referral_stats(self, tg_id: int) -> dict:
        """Return referral stats for the user.

        total_referrals   — everyone who signed up with this user's code
        completed         — those who went on to make a paid purchase
        pending           — signed up but haven't bought yet
        bonus_days_total  — total bonus days ever earned
        bonus_gb_total    — total bonus GB ever earned
        """
        async with self._db.execute(
            "SELECT COUNT(*) AS cnt FROM users WHERE referred_by = ?", (tg_id,)
        ) as cur:
            total = (await cur.fetchone())["cnt"]
        async with self._db.execute(
            "SELECT COUNT(*) AS cnt FROM referral_rewards WHERE referrer_tg_id = ?", (tg_id,)
        ) as cur:
            completed = (await cur.fetchone())["cnt"]
        async with self._db.execute(
            "SELECT COALESCE(SUM(bonus_days),0) AS s FROM referral_rewards WHERE referrer_tg_id = ?",
            (tg_id,),
        ) as cur:
            bonus_days_total = (await cur.fetchone())["s"]
        async with self._db.execute(
            "SELECT COALESCE(SUM(bonus_gb),0) AS s FROM referral_rewards WHERE referrer_tg_id = ?",
            (tg_id,),
        ) as cur:
            bonus_gb_total = (await cur.fetchone())["s"]
        return {
            "total_referrals": total,
            "completed_referrals": completed,
            "pending_referrals": max(0, total - completed),
            "bonus_days_total": bonus_days_total,
            "bonus_gb_total": bonus_gb_total,
        }

    async def get_referral_history(self, tg_id: int, limit: int = 10) -> List[dict]:
        """Recent referrals: each row has the referred user's tg_id, username,
        whether they completed a purchase (referral_rewarded=1), and join date."""
        async with self._db.execute(
            """SELECT u.tg_id, u.username, u.first_name, u.referral_rewarded, u.created_at,
                      (SELECT MAX(created_at) FROM transactions
                         WHERE user_tg_id = u.tg_id AND type IN ('purchase','renewal')) AS purchased_at
               FROM users u
               WHERE u.referred_by = ?
               ORDER BY u.created_at DESC LIMIT ?""",
            (tg_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_referral_invitees(self, tg_id: int, limit: int = 50) -> List[dict]:
        """REFERRAL-INVITEES: full list of users invited by ``tg_id`` — every
        referred user's tg_id, username, first_name, reward status, and join
        date.  Shown to BOTH the customer (their own invitees) and the admin
        (any user's invitees) per the user's request."""
        async with self._db.execute(
            """SELECT tg_id, username, first_name, referral_rewarded, created_at
               FROM users WHERE referred_by = ?
               ORDER BY created_at DESC LIMIT ?""",
            (tg_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_claimable_referral_count(self, tg_id: int) -> int:
        """Count referred users who bought (referral_rewarded=1) but whose
        reward hasn't been claimed yet (no row in referral_rewards).

        REFERRAL-CLAIM: a referred user's purchase creates eligibility (marks
        referral_rewarded=1) but does NOT auto-apply the bonus. The referrer
        must press "Claim Reward" in the referral section. This method counts
        how many are waiting to be claimed.
        """
        async with self._db.execute(
            """SELECT COUNT(*) AS cnt FROM users
               WHERE referred_by = ? AND referral_rewarded = 1
                 AND tg_id NOT IN (SELECT referred_tg_id FROM referral_rewards
                                    WHERE referrer_tg_id = ?)""",
            (tg_id, tg_id),
        ) as cur:
            return (await cur.fetchone())["cnt"]

    async def get_claimable_referrals(self, tg_id: int) -> List[dict]:
        """Return the list of claimable referred users (bought but not rewarded)."""
        async with self._db.execute(
            """SELECT u.tg_id, u.username, u.first_name, u.created_at
               FROM users u
               WHERE u.referred_by = ? AND u.referral_rewarded = 1
                 AND u.tg_id NOT IN (SELECT referred_tg_id FROM referral_rewards
                                      WHERE referrer_tg_id = ?)
               ORDER BY u.created_at DESC""",
            (tg_id, tg_id),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------- payments
    async def add_payment(self, user_tg_id: int, amount: float, unique_amount: float,
                          card_number: str = "", card_holder: str = "",
                          receipt_type: str = "", receipt_file_id: str = "",
                          receipt_text: str = "",
                          resume_plan_id: Optional[int] = None) -> int:
        """Record a new card-payment request.

        ``resume_plan_id`` is set when the payment was created via the
        "Request Shortfall" flow on the purchase review page. On approval,
        the bot uses it to send the user a one-tap "Buy Now" button for
        that plan instead of leaving them to navigate back to the plan list.
        """
        cur = await self._db.execute(
            """INSERT INTO payments
               (user_tg_id, amount, unique_amount, card_number, card_holder,
                receipt_type, receipt_file_id, receipt_text, resume_plan_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_tg_id, amount, unique_amount, card_number, card_holder,
             receipt_type, receipt_file_id, receipt_text, resume_plan_id),
        )
        await self._auto_commit()
        return cur.lastrowid

    async def get_payment(self, payment_id: int) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_pending_payments(self) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM payments WHERE status = 'pending' ORDER BY created_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_recent_payments(self, limit: int = 20) -> List[dict]:
        """RECEIPT-HISTORY: most-recent payments of any status, for the admin
        history view. Ordered newest-first so the admin sees the latest
        approvals/rejects at the top."""
        async with self._db.execute(
            "SELECT * FROM payments ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_user_payments(self, tg_id: int, limit: int = 10) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM payments WHERE user_tg_id = ? ORDER BY created_at DESC LIMIT ?",
            (tg_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # PAY-HISTORY-REWORK: payment-admin filtering helpers.
    async def get_payments_by_admin(self, admin_id: int, limit: int = 30) -> List[dict]:
        """All payments (any status) reviewed by the given admin — newest first.
        Used by the per-admin history view (full admin reviewing a payment
        admin's approvals) and by payment admins viewing their own history."""
        async with self._db.execute(
            "SELECT * FROM payments WHERE admin_id = ? AND status IN ('approved','rejected') "
            "ORDER BY reviewed_at DESC LIMIT ?",
            (admin_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_payment_admins_with_counts(self, pa_ids: set) -> List[dict]:
        """For each payment-admin tg_id, return {tg_id, approved, rejected, total}.
        Used by the 'Approvals by Payment Admin' picker so the main admin can
        see at a glance who's approving what."""
        result: List[dict] = []
        for aid in pa_ids:
            async with self._db.execute(
                "SELECT "
                "SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) AS approved, "
                "SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rejected, "
                "COUNT(*) AS total "
                "FROM payments WHERE admin_id = ? AND status IN ('approved','rejected')",
                (aid,),
            ) as cur:
                row = await cur.fetchone()
                result.append({
                    "tg_id": aid,
                    "approved": row["approved"] if row else 0,
                    "rejected": row["rejected"] if row else 0,
                    "total": row["total"] if row else 0,
                })
        # Sort by total desc so the most-active admins appear first.
        result.sort(key=lambda r: (r["total"], r["approved"]), reverse=True)
        return result

    async def approve_payment(self, payment_id: int, admin_id: int,
                              admin_username: str = "") -> bool:
        """Atomic approve: only succeeds if the payment is still pending.

        Returns True if THIS call performed the transition (pending → approved).
        Returns False if the payment was already approved/rejected by another
        admin — prevents double-credit races (C1) when two admins click
        "Approve" simultaneously.

        ``admin_username`` is stored denormalized so the history view can show
        "approved by @admin" without an extra JOIN — and survives even if the
        admin later blocks the bot (so they're not in `users` anymore).
        """
        cur = await self._db.execute(
            "UPDATE payments SET status = 'approved', admin_id = ?, admin_username = ?, reviewed_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (admin_id, admin_username, datetime.now(timezone.utc).isoformat(), payment_id),
        )
        await self._auto_commit()
        return cur.rowcount == 1

    async def reject_payment(self, payment_id: int, admin_id: int, note: str = "",
                             admin_username: str = "") -> bool:
        """Atomic reject: only succeeds if the payment is still pending."""
        cur = await self._db.execute(
            "UPDATE payments SET status = 'rejected', admin_id = ?, admin_username = ?, admin_note = ?, reviewed_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (admin_id, admin_username, note, datetime.now(timezone.utc).isoformat(), payment_id),
        )
        await self._auto_commit()
        return cur.rowcount == 1

    async def update_payment_notif_ids(self, payment_id: int, notif_json: str) -> None:
        """Persist the JSON map of admin→notification-message IDs for a payment.

        Called right after the "new payment" notifications are broadcast to
        all admins (in ``ms_receipt``).  The map is later read by
        :func:`_mark_payment_notifs_processed` to edit/delete each admin's
        notification once ANY admin approves or rejects the payment — so the
        other admins don't see stale "awaiting your review" prompts.
        """
        await self._db.execute(
            "UPDATE payments SET notif_msg_ids = ? WHERE id = ?",
            (notif_json, payment_id),
        )
        await self._auto_commit()

    async def update_language_selected(self, tg_id: int, selected: bool = True):
        await self._db.execute(
            "UPDATE users SET language_selected = ? WHERE tg_id = ?",
            (1 if selected else 0, tg_id),
        )
        await self._auto_commit()

    # ------------------------------------------------------------- settings
    # TTL cache for settings — they're read on nearly every request but change
    # rarely. Caching eliminates a DB roundtrip per read. The cache is
    # invalidated on `set_setting`. TTL is a safety net in case a set_setting
    # happens via raw SQL (rare).
    _SETTINGS_CACHE: Dict[str, Tuple[float, Optional[str]]] = {}
    _SETTINGS_CACHE_TTL = 60.0  # seconds

    async def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        # Check TTL cache first.
        cached = self._SETTINGS_CACHE.get(key)
        now = time.time()
        if cached and (now - cached[0]) < self._SETTINGS_CACHE_TTL:
            return cached[1] if cached[1] is not None else default
        async with self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            value = row["value"] if row else None
        self._SETTINGS_CACHE[key] = (now, value)
        return value if value is not None else default

    async def get_setting_int(self, key: str, default: int = 0) -> int:
        v = await self.get_setting(key)
        try:
            return int(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    async def get_setting_float(self, key: str, default: float = 0.0) -> float:
        """Like :meth:`get_setting_int` but for fractional settings.

        Used for GB quotas (trial_gb, referral_bonus_gb) and per-GB prices
        which may legitimately be decimal (0.2 GB = 200 MB).  Falls back to
        ``int()`` parsing first so old integer-stored values still work, then
        to ``float()`` for fractional values, then to ``default``.
        """
        v = await self.get_setting(key)
        if v is None:
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    async def get_setting_json(self, key: str, default: Any = None) -> Any:
        v = await self.get_setting(key)
        if v is None:
            return default
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return default

    async def set_setting(self, key: str, value: str):
        await self._db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        await self._auto_commit()
        # Invalidate cache for this key.
        self._SETTINGS_CACHE[key] = (time.time(), value)

    def invalidate_settings_cache(self, key: Optional[str] = None):
        """Force-clear the settings cache. If `key` is None, clears all."""
        if key is None:
            self._SETTINGS_CACHE.clear()
        else:
            self._SETTINGS_CACHE.pop(key, None)


# ============================================================================
# SECTION 3: 3X-UI PANEL API CLIENT
# ============================================================================

class PanelAPI:
    """Async client for the 3X-UI panel API with multi-panel support."""

    def __init__(self):
        # Resource-constrained tuning: cap the connection pool so a burst of
        # panel calls can't exhaust file descriptors or RAM on our 1-core/1GB
        # host. The PANEL_API_SEMAPHORE already bounds concurrency to 5, so
        # the pool max keepalive is set to match.
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS, connect=HTTP_CONNECT_TIMEOUT),
            verify=False,            # panels often use self-signed certs
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=PANEL_API_CONCURRENCY + 2,
                max_keepalive_connections=PANEL_API_CONCURRENCY,
                keepalive_expiry=30.0,
            ),
        )

    async def close(self):
        await self.client.aclose()

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _e(email: str) -> str:
        """URL-encode an email for use in a path segment (M7).

        Bot-generated emails (tg_<16hex>) are safe, but admin-imported clients
        may contain '+', '%', or other reserved URI characters that would
        break the path. quote(safe='') encodes everything except A-Za-z0-9_.-~
        """
        return quote(str(email), safe='')

    async def _request(self, method: str, panel_url: str, token: str,
                       path: str, **kwargs) -> dict:
        url = f"{panel_url}{path}"
        try:
            resp = await self.client.request(method, url, headers=self._headers(token), **kwargs)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success", False):
                msg = data.get("msg", "Unknown API error")
                logger.warning("API error from %s: %s", panel_url, msg)
                return {"success": False, "msg": msg, "obj": None}
            return data
        except httpx.HTTPStatusError as e:
            logger.error("HTTP %s from %s%s", e.response.status_code, panel_url, path)
            return {"success": False, "msg": f"HTTP {e.response.status_code}", "obj": None}
        except httpx.RequestError as e:
            logger.error("Request error to %s%s: %s", panel_url, path, e)
            return {"success": False, "msg": f"Connection error: {str(e)[:120]}", "obj": None}
        except Exception as e:
            logger.error("Unexpected error %s%s: %s", panel_url, path, e)
            return {"success": False, "msg": f"Unexpected error: {str(e)[:120]}", "obj": None}

    # ------------------------------------------------------------ inbounds
    async def get_inbounds(self, panel_url: str, token: str) -> List[dict]:
        r = await self._request("GET", panel_url, token, "/panel/api/inbounds/options")
        return r.get("obj", []) if r.get("success") else []

    async def get_inbound(self, panel_url: str, token: str, inbound_id: int) -> Optional[dict]:
        r = await self._request("GET", panel_url, token, f"/panel/api/inbounds/get/{inbound_id}")
        return r.get("obj") if r.get("success") else None

    async def get_all_links(self, panel_url: str, token: str) -> str:
        r = await self._request("GET", panel_url, token, "/panel/api/inbounds/allLinks")
        return r.get("obj", "") if r.get("success") else ""

    async def reset_inbound_traffic(self, panel_url: str, token: str, inbound_id: int) -> dict:
        return await self._request("POST", panel_url, token, f"/panel/api/inbounds/{inbound_id}/resetTraffic")

    # ------------------------------------------------------------- clients
    async def create_client(self, panel_url: str, token: str, email: str,
                            inbound_ids: List[int], total_gb: float = 0,
                            expiry_time: int = 0, limit_ip: int = 0,
                            tg_id: int = 0, flow: str = "", sub_id: str = "") -> dict:
        client: Dict[str, Any] = {"email": email, "enable": True}
        if total_gb > 0:
            # 3x-ui stores the `totalGB` value directly as the traffic `total`
            # (in bytes) — despite the misleading field name. Sending 5 makes
            # the limit 5 bytes; we must send bytes to get N GB.
            # int() rounds down so fractional GB (0.2 GB = 214_748_364 bytes)
            # yields an exact byte count the panel accepts.
            client["totalGB"] = int(total_gb * GB)
        if expiry_time > 0:
            client["expiryTime"] = expiry_time
        if limit_ip > 0:
            client["limitIp"] = limit_ip
        if tg_id > 0:
            client["tgId"] = tg_id
        if flow:
            client["flow"] = flow
        if sub_id:
            client["subId"] = sub_id
        return await self._request(
            "POST", panel_url, token, "/panel/api/clients/add",
            json={"client": client, "inboundIds": inbound_ids},
        )

    async def get_client(self, panel_url: str, token: str, email: str) -> Optional[dict]:
        """Fetch a single client's full record.

        The 3X-UI ``/clients/get/{email}`` endpoint returns a WRAPPED object:
        ``{"success": true, "obj": {"client": {...}, "inboundIds": [...]}}`` —
        NOT the raw client. Many callers expect the raw client dict (with
        ``email``, ``tgId``, etc. at the top level), so we unwrap here and
        merge ``inboundIds`` into the client dict for convenience.

        Older panel versions may return the raw client directly (with
        ``inboundIds`` merged in). We handle both shapes defensively so
        ``set_client_tg_id`` works regardless of panel version — this was
        the root cause of the persistent "client email is required" error
        when assigning Telegram IDs to migrated clients.
        """
        r = await self._request("GET", panel_url, token, f"/panel/api/clients/get/{self._e(email)}")
        if not r.get("success"):
            return None
        obj = r.get("obj")
        if not isinstance(obj, dict):
            return None
        # Wrapped shape: {"client": {...}, "inboundIds": [...]}
        if "client" in obj and isinstance(obj["client"], dict):
            client = dict(obj["client"])
            if "inboundIds" in obj:
                client["inboundIds"] = obj["inboundIds"]
            return client
        # Raw shape (older panels): the client dict itself, possibly with
        # inboundIds merged in at the top level.
        return obj

    async def get_client_traffic(self, panel_url: str, token: str, email: str) -> Optional[dict]:
        r = await self._request("GET", panel_url, token, f"/panel/api/clients/traffic/{self._e(email)}")
        return r.get("obj") if r.get("success") else None

    async def get_client_links(self, panel_url: str, token: str, email: str) -> List[str]:
        r = await self._request("GET", panel_url, token, f"/panel/api/clients/links/{self._e(email)}")
        return r.get("obj", []) if r.get("success") else []

    async def get_sub_links(self, panel_url: str, token: str, sub_id: str) -> List[str]:
        r = await self._request("GET", panel_url, token, f"/panel/api/clients/subLinks/{sub_id}")
        return r.get("obj", []) if r.get("success") else []

    async def update_client(self, panel_url: str, token: str, email: str,
                            client_data: dict,
                            inbound_ids: Optional[List[int]] = None) -> dict:
        """Update a client via ``/clients/update/{email}``.

        Per the 3X-UI source (``web/controller/inbound.go`` → ``upClient``),
        the ``/clients/update/{email}`` handler binds the request body
        **directly** to a ``model.Client`` struct — i.e. it expects the BARE
        client JSON in the body:

            {"email": "...", "tgId": 123, "enable": true, ...}

        It does NOT expect the wrapped ``{"client": {...}, "inboundIds": [...]}`
        shape that ``/clients/add`` uses. Sending the wrapped shape makes the
        panel look for ``email`` at the top level, find nothing, and return
        ``"client email is required"`` — which is exactly the error this call
        used to produce.

        ``inbound_ids`` is accepted for signature compatibility but is NOT
        sent in the body: the panel's update handler does not read it. Inbound
        memberships are managed separately via ``/clients/{email}/attach`` and
        ``/clients/{email}/detach``, so omitting the field here leaves the
        client's existing inbound memberships untouched.
        """
        return await self._request(
            "POST", panel_url, token, f"/panel/api/clients/update/{self._e(email)}", json=client_data
        )

    async def delete_client(self, panel_url: str, token: str, email: str,
                            keep_traffic: bool = False) -> dict:
        return await self._request(
            "POST", panel_url, token,
            f"/panel/api/clients/del/{self._e(email)}?keepTraffic={'1' if keep_traffic else '0'}",
        )

    async def reset_client_traffic(self, panel_url: str, token: str, email: str) -> dict:
        return await self._request("POST", panel_url, token, f"/panel/api/clients/resetTraffic/{self._e(email)}")

    async def enable_client(self, panel_url: str, token: str, email: str) -> dict:
        """Enable a client via bulkEnable (single API call)."""
        return await self._request(
            "POST", panel_url, token, "/panel/api/clients/bulkEnable", json={"emails": [email]}
        )

    async def disable_client(self, panel_url: str, token: str, email: str) -> dict:
        """Disable a client via bulkDisable (single API call)."""
        return await self._request(
            "POST", panel_url, token, "/panel/api/clients/bulkDisable", json={"emails": [email]}
        )

    async def bulk_adjust(self, panel_url: str, token: str, emails: List[str],
                          add_days: int = 0, add_bytes: int = 0,
                          flow: str = "") -> dict:
        payload: Dict[str, Any] = {"emails": emails}
        if add_days:
            payload["addDays"] = add_days
        if add_bytes:
            payload["addBytes"] = add_bytes
        if flow:
            payload["flow"] = flow
        return await self._request("POST", panel_url, token, "/panel/api/clients/bulkAdjust", json=payload)

    async def bulk_enable(self, panel_url: str, token: str, emails: List[str]) -> dict:
        return await self._request("POST", panel_url, token, "/panel/api/clients/bulkEnable", json={"emails": emails})

    async def bulk_disable(self, panel_url: str, token: str, emails: List[str]) -> dict:
        return await self._request("POST", panel_url, token, "/panel/api/clients/bulkDisable", json={"emails": emails})

    async def bulk_delete(self, panel_url: str, token: str, emails: List[str]) -> dict:
        return await self._request("POST", panel_url, token, "/panel/api/clients/bulkDel",
                                   json={"emails": emails, "keepTraffic": False})

    async def delete_depleted(self, panel_url: str, token: str) -> dict:
        """POST /panel/api/clients/delDepleted — cleanup exhausted clients."""
        return await self._request("POST", panel_url, token, "/panel/api/clients/delDepleted")

    async def get_online_clients(self, panel_url: str, token: str) -> List[str]:
        r = await self._request("POST", panel_url, token, "/panel/api/clients/onlines")
        return r.get("obj", []) if r.get("success") else []

    async def get_client_ips(self, panel_url: str, token: str, email: str) -> List[str]:
        r = await self._request("POST", panel_url, token, f"/panel/api/clients/ips/{self._e(email)}")
        return r.get("obj", []) if r.get("success") else []

    async def clear_client_ips(self, panel_url: str, token: str, email: str) -> dict:
        return await self._request("POST", panel_url, token, f"/panel/api/clients/clearIps/{self._e(email)}")

    async def get_last_online(self, panel_url: str, token: str, emails: List[str]) -> dict:
        r = await self._request("POST", panel_url, token, "/panel/api/clients/lastOnline",
                                json={"emails": emails})
        return r.get("obj", {}) if r.get("success") else {}

    async def get_clients_paged(self, panel_url: str, token: str, page: int = 1,
                                page_size: int = 25, search: str = "",
                                filter_type: str = "", sort: str = "expiryTime",
                                order: str = "descend") -> dict:
        params = {"page": page, "pageSize": page_size, "sort": sort, "order": order}
        if search:
            params["search"] = search
        if filter_type:
            params["filter"] = filter_type
        r = await self._request("GET", panel_url, token, "/panel/api/clients/list/paged", params=params)
        return r.get("obj", {}) if r.get("success") else {}

    async def get_all_clients(self, panel_url: str, token: str,
                              max_count: int = 2000) -> List[dict]:
        """Fetch every client on the panel with FULL details (MIGRATE-1).

        Uses ``GET /panel/api/clients/list`` (non-paged) because the paged
        endpoint ``/clients/list/paged`` returns a SLIM object that does NOT
        include ``tgId`` — so admins would see "NO ID" for every client even
        when the panel has tgId set. The non-paged list returns full client
        objects including ``tgId``, ``email``, ``subId``, ``totalGB``,
        ``expiryTime``, ``limitIp``, ``inboundIds``, ``enable``.

        Capped at ``max_count`` to protect the low-resource server; 2000 is
        well above typical sales-bot scale. If the panel is larger, only the
        first ``max_count`` clients are returned.
        """
        r = await self._request("GET", panel_url, token, "/panel/api/clients/list")
        if not r.get("success"):
            return []
        items = r.get("obj", []) or []
        if not isinstance(items, list):
            return []
        return items[:max_count]

    async def set_client_tg_id(self, panel_url: str, token: str,
                               email: str, tg_id: int) -> dict:
        """Set the ``tgId`` field on a panel client (MIGRATE-1).

        ``/clients/update/{email}`` expects the BARE client JSON in the body
        (NOT wrapped in ``{"client": ...}`` — that wrapper shape is only for
        ``/clients/add``). So we fetch the current record via
        :meth:`get_client`, set ``tgId``, and POST the bare client back.

        Robustness notes:
        * ``get_client`` unwraps the panel's ``{"client": {...},
          "inboundIds": [...]}`` response, so ``client`` here is the RAW
          client dict (with ``email``, ``tgId``, etc. at the top level).
        * Always force ``client["email"] = email`` as a belt-and-suspenders
          guard against panels that omit ``email`` from the get response.
        * Strip ``inboundIds`` from the client dict before sending — the
          update endpoint doesn't read it, and leaving an array field on a
          struct that expects a scalar can cause binding issues on some
          panel builds. The client's inbound memberships are preserved by
          the panel (they're managed via separate attach/detach endpoints).
        * Strip DB-metadata fields the panel's Go unmarshaler may reject
          (``id``, ``createdAt``, ``updatedAt``, ``traffic``).
        * Detailed debug logging of the outgoing body + response so the next
          failure leaves a clear trace in the logs.
        """
        client = await self.get_client(panel_url, token, email)
        if not client or not isinstance(client, dict):
            logger.warning("set_client_tg_id: get_client(%s) returned %r", email, client)
            return {"success": False, "msg": "client not found on panel", "obj": None}
        client = dict(client)
        # Pop inboundIds — the update endpoint doesn't read it. We keep it
        # only for the debug log below.
        inbound_ids = client.pop("inboundIds", None)
        # Strip DB-metadata fields the panel's Go unmarshaler may reject.
        for k in ("id", "createdAt", "updatedAt", "traffic"):
            client.pop(k, None)
        # TGID-FIX v3: normalize array-typed fields. The 3X-UI Go struct
        # (model.Client) defines some fields as []string, but the /clients/get
        # endpoint may serialize them as bare strings (e.g. allowedIPs="" or
        # allowedIPs="1.2.3.4,5.6.7.8" instead of ["1.2.3.4","5.6.7.8"]).
        # Sending the string form back to /clients/update makes Go's
        # json.Unmarshal fail with "cannot unmarshal string into Go struct
        # field Client.allowedIPs of type []string". We coerce every known
        # array field to a proper list before posting the body back.
        for arr_field in ("allowedIPs",):
            val = client.get(arr_field)
            if val is None:
                client[arr_field] = []
            elif isinstance(val, str):
                # Comma-separated → list. Empty string becomes [].
                client[arr_field] = [s.strip() for s in val.split(",") if s.strip()]
            elif isinstance(val, list):
                pass  # already correct
            else:
                # Unexpected type — safest to send an empty list.
                client[arr_field] = []
        # Guarantee email is present — some panel versions omit it from the
        # get response, and /clients/update REQUIRES email at the top level
        # of the bare client body.
        client["email"] = email
        client["tgId"] = tg_id
        logger.info("set_client_tg_id: updating %s tgId=%d, body keys=%s, inbounds=%s",
                    email, tg_id, sorted(client.keys()), inbound_ids)
        result = await self.update_client(panel_url, token, email, client)
        if not result.get("success"):
            logger.warning("set_client_tg_id: panel rejected update for %s: %s | sent body client keys=%s",
                           email, result.get("msg"), sorted(client.keys()))
        return result

    async def attach_client(self, panel_url: str, token: str, email: str,
                            inbound_ids: List[int]) -> dict:
        return await self._request("POST", panel_url, token,
                                   f"/panel/api/clients/{self._e(email)}/attach", json={"inboundIds": inbound_ids})

    async def detach_client(self, panel_url: str, token: str, email: str,
                            inbound_ids: List[int]) -> dict:
        return await self._request("POST", panel_url, token,
                                   f"/panel/api/clients/{self._e(email)}/detach", json={"inboundIds": inbound_ids})

    async def set_external_links(self, panel_url: str, token: str, email: str,
                                 links: List[dict]) -> dict:
        return await self._request("POST", panel_url, token,
                                   f"/panel/api/clients/{self._e(email)}/externalLinks",
                                   json={"externalLinks": links})

    # ------------------------------------------------------------- groups
    async def get_groups(self, panel_url: str, token: str) -> List[dict]:
        r = await self._request("GET", panel_url, token, "/panel/api/clients/groups")
        return r.get("obj", []) if r.get("success") else []

    async def create_group(self, panel_url: str, token: str, name: str) -> dict:
        return await self._request("POST", panel_url, token, "/panel/api/clients/groups/create", json={"name": name})

    async def delete_group(self, panel_url: str, token: str, name: str) -> dict:
        return await self._request("POST", panel_url, token, "/panel/api/clients/groups/delete", json={"name": name})

    async def rename_group(self, panel_url: str, token: str, old_name: str, new_name: str) -> dict:
        return await self._request("POST", panel_url, token, "/panel/api/clients/groups/rename",
                                   json={"old": old_name, "new": new_name})

    async def bulk_add_to_group(self, panel_url: str, token: str, emails: List[str], group: str) -> dict:
        return await self._request("POST", panel_url, token, "/panel/api/clients/groups/bulkAdd",
                                   json={"emails": emails, "group": group})

    async def reset_group_traffic(self, panel_url: str, token: str, group: str) -> dict:
        return await self._request("POST", panel_url, token, "/panel/api/clients/groups/resetTraffic",
                                   json={"group": group})

    # ------------------------------------------------------------- nodes
    async def get_nodes(self, panel_url: str, token: str) -> List[dict]:
        r = await self._request("GET", panel_url, token, "/panel/api/nodes/list")
        return r.get("obj", []) if r.get("success") else []

    # ----------------------------------------------------------- backup
    async def backup_to_telegram(self, panel_url: str, token: str) -> dict:
        return await self._request("POST", panel_url, token, "/panel/api/backuptotgbot")

    # ----------------------------------------------------------- settings
    async def get_panel_settings(self, panel_url: str, token: str) -> dict:
        r = await self._request("POST", panel_url, token, "/panel/api/setting/all")
        return r.get("obj", {}) if r.get("success") else {}

    async def restart_panel(self, panel_url: str, token: str) -> dict:
        return await self._request("POST", panel_url, token, "/panel/api/setting/restartPanel")

    async def test_panel_connection(self, panel_url: str, token: str) -> Tuple[bool, str]:
        r = await self._request("GET", panel_url, token, "/panel/api/inbounds/options")
        if r.get("success"):
            return True, "Connection successful"
        return False, r.get("msg", "Connection failed")

    async def get_api_tokens(self, panel_url: str, token: str) -> List[dict]:
        r = await self._request("GET", panel_url, token, "/panel/api/setting/apiTokens")
        return r.get("obj", []) if r.get("success") else []

    async def fetch_sub_uri(self, panel_url: str, token: str) -> str:
        """Extract the subscription base URI from panel settings.
        Returns '' if not available — caller should fall back to panel_url + /sub/."""
        settings = await self.get_panel_settings(panel_url, token)
        if not settings:
            return ""
        # 3x-ui exposes these as a JSON string under "subURI" / "subPath" / "subPort"
        sub_uri = settings.get("subURI", "") or ""
        if sub_uri:
            if not sub_uri.endswith("/"):
                sub_uri += "/"
            return sub_uri
        # Fallback: build from panel host + subPort + subPath
        sub_path = settings.get("subPath", "/sub/") or "/sub/"
        sub_port = settings.get("subPort", "")
        if sub_port:
            try:
                from urllib.parse import urlparse, urlunparse
                p = urlparse(panel_url)
                host = p.hostname
                scheme = p.scheme or "https"
                if str(sub_port) == "443":
                    netloc = host
                else:
                    netloc = f"{host}:{sub_port}"
                base = urlunparse((scheme, netloc, "", "", "", ""))
                if not sub_path.startswith("/"):
                    sub_path = "/" + sub_path
                if not sub_path.endswith("/"):
                    sub_path += "/"
                return base + sub_path
            except Exception:
                pass
        return ""


# ============================================================================
# SECTION 4: LOAD BALANCER
# ============================================================================

class LoadBalancer:
    """Capacity / priority / online-load aware server selection."""

    def __init__(self, db: Database, api: PanelAPI):
        self.db = db
        self.api = api

    async def select_best_server(self, allowed_server_ids: Optional[List[int]] = None) -> Optional[dict]:
        """Pick the healthiest, least-loaded active server.

        If ``allowed_server_ids`` is given, only those servers are considered
        (used when a plan is restricted to specific servers/inbounds)."""
        servers = await self.db.get_servers(active_only=True)
        if allowed_server_ids is not None:
            servers = [s for s in servers if s["id"] in allowed_server_ids]
        if not servers:
            return None
        healthy = [s for s in servers if s["is_healthy"]] or servers

        best = None
        best_score = float("inf")
        all_full = True  # M6 — track whether every healthy server is at capacity
        for srv in healthy:
            local_count = srv.get("total_clients", 0)
            capacity = srv.get("capacity", 0) or 0
            # Capacity check: skip servers that are full
            if capacity > 0 and local_count >= capacity:
                continue
            all_full = False
            # Online load (best-effort, non-fatal)
            online_count = 0
            try:
                online = await self.api.get_online_clients(srv["panel_url"], srv["api_token"])
                online_count = len(online) if isinstance(online, list) else 0
            except Exception:
                pass
            # Score: capacity utilisation (0..100) + priority penalty + online load
            util = (local_count / capacity * 100) if capacity > 0 else 0
            score = util + srv.get("priority", 10) * 2 + online_count * 0.5
            if score < best_score:
                best_score = score
                best = srv
        # M6 — do NOT oversell when every server is at capacity. Returning the
        # first healthy server would breach the admin's capacity limit. Return
        # None so the caller can show "no servers available".
        if best is None and all_full and healthy:
            logger.warning("LoadBalancer: all %d healthy server(s) at capacity — refusing to oversell", len(healthy))
            return None
        return best or (healthy[0] if healthy else None)

    async def select_inbounds_for_plan(self, server: dict,
                                       plan: dict) -> List[int]:
        """Return the inbound IDs on ``server`` that this plan allows."""
        raw = plan.get("inbound_ids") if plan else None
        wanted: List[str] = []
        if raw:
            try:
                wanted = json.loads(raw)
            except Exception:
                wanted = []
        # wanted entries look like "server_id_inbound_id".
        # H18 — guard against malformed entries (manual DB edits, migration
        # bugs) that would crash int() and break every purchase on that server.
        allowed = set()
        for x in wanted:
            if "_" not in x:
                continue
            sid_s, _sep, iid_s = x.partition("_")
            if sid_s.isdigit() and iid_s.isdigit() and int(sid_s) == server["id"]:
                allowed.add(int(iid_s))
        inbounds = await self.db.get_inbounds(server["id"], enabled_only=True)
        if not inbounds:
            panel_inbounds = await self.api.get_inbounds(server["panel_url"], server["api_token"])
            await self.db.sync_inbounds(server["id"], panel_inbounds)
            inbounds = await self.db.get_inbounds(server["id"], enabled_only=True)
        if allowed:
            return [ib["inbound_id"] for ib in inbounds if ib["inbound_id"] in allowed]
        # No restriction → NO inbounds (plan must explicitly select inbounds)
        # Returning empty list means the plan cannot be purchased until inbounds are configured
        return []

    async def select_trial_inbounds(self, server: dict, trial_inbounds: List[str]) -> List[int]:
        """Select inbounds for a trial account on ``server``.
        Unlike plans, trial uses ALL inbounds when no specific ones are configured."""
        allowed = set()
        for x in trial_inbounds:
            if "_" not in x:
                continue
            sid_s, _sep, iid_s = x.partition("_")
            if sid_s.isdigit() and iid_s.isdigit() and int(sid_s) == server["id"]:
                allowed.add(int(iid_s))
        inbounds = await self.db.get_inbounds(server["id"], enabled_only=True)
        if not inbounds:
            panel_inbounds = await self.api.get_inbounds(server["panel_url"], server["api_token"])
            await self.db.sync_inbounds(server["id"], panel_inbounds)
            inbounds = await self.db.get_inbounds(server["id"], enabled_only=True)
        if allowed:
            return [ib["inbound_id"] for ib in inbounds if ib["inbound_id"] in allowed]
        # For trial, empty config means use all inbounds (different from plans)
        return [ib["inbound_id"] for ib in inbounds]

    def plan_server_ids(self, plan: dict) -> List[int]:
        """Return the distinct server IDs referenced by a plan's inbound list."""
        raw = plan.get("inbound_ids") if plan else None
        if not raw:
            return []
        try:
            entries = json.loads(raw)
        except Exception:
            return []
        return list({int(x.partition("_")[0]) for x in entries
                     if "_" in x and x.partition("_")[0].isdigit()})


# ============================================================================
# SECTION 5: FORMATTERS & UTILITIES
# ============================================================================

def fmt_bytes(num_bytes: int, lang: str = "en") -> str:
    """Format a byte count with the appropriate SI unit (B/KB/MB/GB/TB/PB).

    LOCALIZATION: the unit abbreviations (KB, MB, GB, …) are standard
    technical notation used as-is even in Persian text, but the DIGITS are
    converted to Persian numerals when ``lang == "fa"`` so they're consistent
    with the rest of an otherwise-FA screen.
    """
    if num_bytes <= 0:
        s = "0 B"
        return to_fa_digits(s) if lang == "fa" else s
    n = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(n) < 1024.0:
            s = f"{n:.1f} {unit}"
            return to_fa_digits(s) if lang == "fa" else s
        n /= 1024.0
    s = f"{n:.1f} EB"
    return to_fa_digits(s) if lang == "fa" else s


def fmt_gb(gb, lang: str = "en") -> str:
    """Format a traffic quota (in GB) for display.

    Accepts int OR float — fractional GB values (e.g. 0.2 for 200 MB) are
    supported because the plan/account tables store traffic as a REAL value
    and the panel API receives bytes (``total_gb * GB``), so 0.2 GB becomes
    214_748_364 bytes cleanly.  Integer values render without a decimal
    point ("5 GB"); fractional values use up to 2 decimals ("0.2 GB").

    LOCALIZATION: for Persian the unit words are translated ("گیگابایت" /
    "ترابایت") so users don't see Latin "GB" in an otherwise-FA screen.
    The number itself is also converted to Persian digits + Persian decimal
    separator (٫).
    """
    try:
        v = float(gb)
    except (TypeError, ValueError):
        v = 0.0
    if v <= 0:
        # 0 == unlimited; negative shouldn't happen but treat as unlimited.
        return t("unlimited", lang)
    if v >= 1024:
        if lang == "fa":
            s = f"{v/1024:.1f} ترابایت"
        else:
            s = f"{v/1024:.1f} TB"
    elif v == int(v):
        # whole GB — no decimal point (e.g. "5 GB" not "5.0 GB")
        if lang == "fa":
            s = f"{int(v)} گیگابایت"
        else:
            s = f"{int(v)} GB"
    else:
        # fractional GB — strip trailing zeros (0.20 → "0.2 GB", 0.25 → "0.25 GB")
        num = f"{v:.2f}".rstrip("0").rstrip(".")
        if lang == "fa":
            s = f"{num} گیگابایت"
        else:
            s = f"{num} GB"
    if lang == "fa":
        # Persian uses the Arabic decimal separator ٫ (U+066B) instead of "."
        s = s.replace(".", "٫")
        s = to_fa_digits(s)
    return s


def fmt_days(days: int, lang: str = "en") -> str:
    """Human-friendly duration formatting.

    Avoids the awkward "1.0mo" / "0.5y" shorthand in favour of readable
    units. For Persian we use the proper month/year words so the user sees
    "۱ ماه" instead of "۱.۰mo".
    """
    if days == 0:
        return "∞" if lang == "en" else "نامحدود"
    if days >= 365:
        years = days / 365
        whole = int(years)
        if whole >= 1 and abs(years - whole) < 0.05:
            if lang == "fa":
                return f"{to_fa_digits(str(whole))} سال"
            return f"{whole} year{'s' if whole != 1 else ''}"
        # Fallback to days if not a clean year count.
        if lang == "fa":
            return to_fa_digits(f"{days} روز")
        return f"{days} days"
    if days >= 30:
        months = days / 30
        whole = int(months)
        if whole >= 1 and abs(months - whole) < 0.05:
            if lang == "fa":
                return f"{to_fa_digits(str(whole))} ماه"
            return f"{whole} month{'s' if whole != 1 else ''}"
        if lang == "fa":
            return to_fa_digits(f"{days} روز")
        return f"{days} days"
    if lang == "fa":
        return to_fa_digits(f"{days} روز")
    return f"{days} day{'s' if days != 1 else ''}"


def fmt_reward_days(days: int, lang: str = "en") -> str:
    """Format a SUM of reward days.

    Unlike :func:`fmt_days`, ``0`` means "0 days" (nothing earned yet), NOT
    "unlimited".  ``fmt_days`` treats 0 as unlimited because for a traffic /
    time QUOTA, 0 means "no limit" — but for an EARNED-BONUS total, 0 means
    "you haven't earned any bonus yet", which must not display as "Unlimited".

    Used by the referral stats line so a new user with no referrals sees
    "+0 days / +0 GB" instead of the misleading "+Unlimited / +Unlimited".
    """
    try:
        d = int(days)
    except (TypeError, ValueError):
        d = 0
    if d <= 0:
        return to_fa_digits("۰ روز") if lang == "fa" else "0 days"
    return fmt_days(d, lang)


def fmt_reward_gb(gb, lang: str = "en") -> str:
    """Format a SUM of reward GB.

    Unlike :func:`fmt_gb`, ``0`` means "0 GB" (nothing earned yet), NOT
    "unlimited".  See :func:`fmt_reward_days` for the rationale.
    """
    try:
        v = float(gb)
    except (TypeError, ValueError):
        v = 0.0
    if v <= 0:
        return to_fa_digits("۰ گیگابایت") if lang == "fa" else "0 GB"
    return fmt_gb(v, lang)


def fmt_iso(iso_str, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Render a stored ISO-8601 timestamp (kept in UTC) in Iran time.

    Handles both forms stored in the DB:
      * explicit-UTC:  '2026-07-25T22:41:06.123456+00:00'  (Python isoformat)
      * SQLite default: '2026-07-25 22:41:06'              (datetime('now'), naive UTC)
    Returns '' for empty input and the raw string (truncated) on parse failure.
    """
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TEHRAN_TZ).strftime(fmt)
    except Exception:
        return str(iso_str)[:19]


def fmt_ts(ts_ms: int, lang: str = "en") -> str:
    if ts_ms == 0:
        return "∞" if lang == "en" else "نامحدود"
    # fromtimestamp with explicit UTC, then convert to Tehran for display.
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(TEHRAN_TZ)
    s = dt.strftime("%Y-%m-%d %H:%M")
    return to_fa_digits(s) if lang == "fa" else s


def fmt_remaining(expiry_ms: int, lang: str = "en") -> str:
    if expiry_ms == 0:
        return "∞" if lang == "en" else "نامحدود"
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    diff = expiry_ms - now_ms
    if diff <= 0:
        return "Expired" if lang == "en" else "منقضی"
    days = diff // MS_PER_DAY
    hours = (diff % MS_PER_DAY) // 3_600_000
    if lang == "fa":
        # LOCALIZATION: use full Persian words ("روز"/"ساعت"/"دقیقه") instead
        # of the Latin "d"/"h"/"m" shorthand — the shorthand looks out of place
        # in an otherwise-Persian screen.  Digits are converted to Persian.
        if days > 0:
            s = f"{days} روز"
            if hours > 0:
                s += f" و {hours} ساعت"
        elif hours > 0:
            minutes = (diff % 3_600_000) // 60_000
            s = f"{hours} ساعت"
            if minutes > 0:
                s += f" و {minutes} دقیقه"
        else:
            # LOW — sub-hour durations showed "0h Nm" which looks odd.
            # Show just the minutes when there are no hours.
            minutes = (diff % 3_600_000) // 60_000
            s = f"{minutes} دقیقه"
        return to_fa_digits(s)
    if days > 0:
        s = f"{days}d {hours}h"
    elif hours > 0:
        minutes = (diff % 3_600_000) // 60_000
        s = f"{hours}h {minutes}m"
    else:
        # LOW — sub-hour durations showed "0h Nm" which looks odd.
        # Show just the minutes when there are no hours.
        minutes = (diff % 3_600_000) // 60_000
        s = f"{minutes}m"
    return s


def fmt_progress_bar(pct: float, width: int = 10, lang: str = "en") -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(width * pct / 100)
    s = "█" * filled + "░" * (width - filled) + f" {pct:.0f}%"
    return to_fa_digits(s) if lang == "fa" else s


def sanitize_name(name: str) -> Optional[str]:
    """Validate a user-supplied account name.  Returns cleaned name or None."""
    name = name.strip()
    if not name or name == "-":
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,24}", name):
        return None
    return name


def gen_email(tg_id: int, name: str = "") -> str:
    """Generate a panel-unique email (used as client ID).

    Uses 64 bits of cryptographic entropy (secrets.token_hex(EMAIL_ENTROPY_BYTES))
    and does NOT embed the user's Telegram ID, so a screenshot of the email
    can't leak the user's identity (C6/H12)."""
    suffix = secrets.token_hex(EMAIL_ENTROPY_BYTES)
    if name:
        return f"{name}_{suffix}"
    return f"tg_{suffix}"


def gen_gift_code() -> str:
    """Generate a 16-char gift code (4 groups of 4) using cryptographic RNG.

    Uses secrets.choice instead of random.choices (C6) — gift codes grant real
    balance/plans and must not be predictable."""
    chars = string.ascii_uppercase + string.digits
    return "-".join(
        "".join(secrets.choice(chars) for _ in range(GIFT_CODE_GROUP_LEN))
        for _ in range(GIFT_CODE_GROUPS)
    )


def gen_sub_id() -> str:
    """Generate a subscription ID in UUID format (compatible with 3x-ui panel)."""
    return str(uuid.uuid4())


def escape_html(text: str) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---- Bidirectional-text helper ------------------------------------------------
# Unicode bidi control characters used to force a run of LTR text (e.g. a
# 16-digit card number written as "6037 9919 2616 0239") to render
# left-to-right even when it appears inside an RTL (Farsi) paragraph.
#
# Why this is needed: the Unicode Bidirectional Algorithm (UAX #9) treats each
# space-separated group of digits as an independent "European Number" run.
# Inside an RTL paragraph the neutral characters (spaces) between those runs
# take the paragraph direction, so the groups are laid out right-to-left and
# the card number visually reverses to "0239 2616 9919 6037". Wrapping the
# whole string in an LTR embedding (LRE … PDF) overrides the paragraph
# direction for that run and keeps the digits in their original order.
#
# This works inside <code>…</code> (HTML) and inside Rich Message cells (plain
# text) alike, because bidi controls operate at the Unicode layer, below the
# rendering markup.
LRE = "\u202a"   # LEFT-TO-RIGHT EMBEDDING
PDF = "\u202c"   # POP DIRECTIONAL FORMATTING


def ltr(text) -> str:
    """Wrap ``text`` so it always renders left-to-right.

    Use for card numbers, transaction IDs, URLs — anything that is logically
    LTR but would be visually reordered inside an RTL (Farsi) message.
    Returns the input unchanged if it is empty / not a string.
    """
    if not text or not isinstance(text, str):
        return text if isinstance(text, str) else str(text) if text is not None else ""
    # Strip any pre-existing LRE/PDF/LRM/RLM marks to avoid double-wrapping.
    cleaned = text.replace(LRE, "").replace(PDF, "").replace("\u200e", "").replace("\u200f", "")
    return f"{LRE}{cleaned}{PDF}"


async def safe_notify(coro, context: str = "notify"):
    """Await a bot.send_message / send_document / send_photo coroutine and
    swallow only the *expected* Telegram errors:

      * ``TelegramForbiddenError`` — the user blocked the bot.
      * ``TelegramBadRequest`` whose message contains "chat not found" or
        "blocked" — the user deleted the chat or blocked the bot.

    Any other ``TelegramBadRequest`` (e.g. malformed HTML, message too long)
    is logged as a warning. Any non-Telegram exception is logged with
    ``exc_info`` so silent swallows never hide real bugs. (M2)

    Use this for out-of-band notifications (ticket replies, payment receipts,
    expiry reminders, …) where a single blocked user must NOT abort the whole
    handler or background-task iteration.
    """
    try:
        return await coro
    except TelegramForbiddenError:
        # User blocked the bot — expected, swallow silently.
        pass
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "chat not found" in msg or "blocked" in msg:
            # User deleted the chat or blocked the bot — expected.
            pass
        else:
            logger.warning("%s — TelegramBadRequest: %s", context, e)
    except Exception as e:
        logger.warning("%s — unexpected error: %s", context, e, exc_info=True)


# ---- Media-type helpers (used by ticket + receipt flows) -------------------

#: Every Telegram message type the bot can persist as a ticket/receipt
#: attachment.  Each entry maps the media_type string (stored in the DB) to
#: the label-translation key used in inline-keyboard buttons and the inline
#: "[📎 label]" marker shown in the ticket thread view.
TICKET_MEDIA_TYPES: dict = {
    "photo":       "media_photo",
    "document":    "media_document",
    "video":       "media_video",
    "voice":       "media_voice",
    "audio":       "media_audio",
    "animation":   "media_animation",
    "video_note":  "media_video_note",
    "sticker":     "media_sticker",
}


def extract_ticket_media(message: Message) -> tuple:
    """Inspect an incoming message and extract any attached media.

    Returns a 4-tuple ``(media_type, media_file_id, caption, text_only)``:
    - ``media_type``  : one of the keys in :data:`TICKET_MEDIA_TYPES`, or
                        ``""`` for plain-text messages.
    - ``media_file_id``: the Telegram ``file_id`` of the attachment (or ``""``).
    - ``caption``     : the attachment's caption (or ``""``).  Stickers and
                        round-video notes can't carry a caption.
    - ``text_only``   : the message text for plain-text messages (already
                        trimmed to ``TICKET_REPLY_MAX_CHARS``), or ``""``.

    Supports every commonly-sent Telegram content type: photo, document,
    video, voice, audio (music/MP3), animation (GIF), video_note (round
    video), and sticker.
    """
    cap = (message.caption or "").strip()
    if message.photo:
        return "photo", message.photo[-1].file_id, cap, ""
    if message.document:
        return "document", message.document.file_id, cap, ""
    if message.video:
        return "video", message.video.file_id, cap, ""
    if message.voice:
        # Voice messages can't have a caption in Telegram.
        return "voice", message.voice.file_id, "", ""
    if message.audio:
        return "audio", message.audio.file_id, cap, ""
    if message.animation:
        return "animation", message.animation.file_id, cap, ""
    if message.video_note:
        # Round-video notes can't have a caption in Telegram.
        return "video_note", message.video_note.file_id, "", ""
    if message.sticker:
        # Stickers can't have a caption in Telegram.
        return "sticker", message.sticker.file_id, "", ""
    txt = (message.text or "").strip()
    return "", "", "", txt[:TICKET_REPLY_MAX_CHARS]


def _media_label_map(lang: str) -> dict:
    """Build a {media_type: localised_label} map for every supported
    attachment type.  Used by the ticket thread view, the inline
    "View Media" buttons, and the reopen re-render."""
    return {mt: t(lkey, lang) for mt, lkey in TICKET_MEDIA_TYPES.items()}


async def _send_ticket_reply_notify(bot: Bot, chat_id: int, notify_text: str,
                                    media_type: str, media_file_id: str,
                                    reply_markup: Optional[InlineKeyboardMarkup] = None,
                                    context: str = "ticket-reply notify"):
    """Send a ticket-reply notification, optionally carrying the attached media.

    Supports every attachment type in :data:`TICKET_MEDIA_TYPES`.  For most
    types the message is delivered via the matching ``bot.send_*`` method
    with ``notify_text`` as caption (Telegram allows captions up to 1024
    chars).  Stickers can't carry a caption, so the sticker is sent first
    and the notification text is sent as a separate follow-up message.

    All Telegram-side errors are funnelled through :func:`safe_notify` so a
    blocked admin / user never aborts the surrounding handler. (TICKET-1)
    """
    notify_text = notify_text[:1000]  # leave headroom under the 1024 caption cap
    if media_type == "photo" and media_file_id:
        coro = bot.send_photo(chat_id, photo=media_file_id, caption=notify_text,
                              reply_markup=reply_markup)
    elif media_type == "document" and media_file_id:
        coro = bot.send_document(chat_id, document=media_file_id, caption=notify_text,
                                 reply_markup=reply_markup)
    elif media_type == "video" and media_file_id:
        coro = bot.send_video(chat_id, video=media_file_id, caption=notify_text,
                              reply_markup=reply_markup)
    elif media_type == "voice" and media_file_id:
        coro = bot.send_voice(chat_id, voice=media_file_id, caption=notify_text,
                              reply_markup=reply_markup)
    elif media_type == "audio" and media_file_id:
        coro = bot.send_audio(chat_id, audio=media_file_id, caption=notify_text,
                              reply_markup=reply_markup)
    elif media_type == "animation" and media_file_id:
        coro = bot.send_animation(chat_id, animation=media_file_id, caption=notify_text,
                                  reply_markup=reply_markup)
    elif media_type == "video_note" and media_file_id:
        # Round-video notes don't render captions well in some clients, but
        # the API does accept a caption — send it through.
        coro = bot.send_video_note(chat_id, video_note=media_file_id,
                                   caption=notify_text[:200],
                                   reply_markup=reply_markup)
    elif media_type == "sticker" and media_file_id:
        # Stickers can't carry a caption: send the sticker, then a follow-up
        # text message carrying the notification + reply keyboard.
        await safe_notify(
            bot.send_sticker(chat_id, sticker=media_file_id),
            context=context + " (sticker)",
        )
        coro = bot.send_message(chat_id, notify_text, reply_markup=reply_markup)
    else:
        coro = bot.send_message(chat_id, notify_text, reply_markup=reply_markup)
    await safe_notify(coro, context=context)


# ---- Localised account / plan cards ----------------------------------------

def _category_emoji(category: str) -> str:
    """Small emoji prefix for a ticket category, used in lists."""
    return {"technical": "🔧", "payment": "💰", "account": "👤", "other": "📝"}.get(category, "📝")


def _ticket_status_label(ticket: dict, lang: str) -> str:
    """Human-readable ticket status with a waiting indicator.

    open + last_sender=user  → 'Waiting for admin' (admin owes a reply)
    open + last_sender=admin → 'Waiting for user'   (user owes a reply)
    closed                   → 'Closed'
    """
    status = ticket.get("status", "open")
    if status == "closed":
        return t("ticket_status_closed", lang)
    last = ticket.get("last_sender", "user")
    if last == "user":
        return t("ticket_status_waiting_admin", lang)
    return t("ticket_status_waiting_user", lang)


def _ticket_status_badge(ticket: dict, lang: str) -> str:
    """One-emoji badge for a ticket, used in compact lists."""
    status = ticket.get("status", "open")
    if status == "closed":
        return "🔴"
    last = ticket.get("last_sender", "user")
    if last == "user":
        return "🟡"   # waiting for admin
    return "🔵"       # waiting for user


def fmt_account_card(account: dict, lang: str = "en", traffic_data: Optional[dict] = None,
                     server_alias: str = "", plan_name: str = "",
                     currency: str = "toman") -> str:
    """Render an account status card.  Soft format — works in LTR & RTL.

    CLEAR-LABELS: every value now carries a human-readable label (Plan,
    Remaining traffic, Time remaining, Used, Total quota) so the user can
    tell at a glance what each number means.  Previously the card showed
    bare values like "۵ گیگابایت / ۶ روز و ۲۳ ساعت / ۰ B / ۵.۰ GB" stacked
    on top of each other — confusing because two of them are GB figures and
    the user couldn't tell which was total vs. used vs. remaining.
    """
    is_active = account.get("is_active", False)
    is_trial = account.get("is_trial", False)
    label = account.get("label") or ""
    status_icon = "🟢" if is_active else "🔴"
    badge = " 🎁" if is_trial else ""

    lines = [f"{status_icon} <b>VPN</b>{badge}"]
    if label:
        lines.append(f"🏷 {escape_html(label)}")
    lines.append(f"📧 <code>{escape_html(account.get('email',''))}</code>")
    if server_alias:
        lines.append(f"🖥 {escape_html(server_alias)}")
    if plan_name:
        lines.append(f"📦 {t('card_plan', lang)} : {escape_html(plan_name)}")

    # Remaining TIME — always available from the account row.
    lines.append(f"📅 {t('card_remaining_time', lang)} : {fmt_remaining(account.get('expiry_time',0), lang)}")

    if traffic_data:
        up = traffic_data.get("up", 0)
        down = traffic_data.get("down", 0)
        total = traffic_data.get("total", 0)
        used = up + down
        if total > 0:
            remaining = max(0, total - used)
            pct = (used / total) * 100
            # Remaining TRAFFIC (live from panel: total − used).  Use fmt_gb
            # (bytes→GB) so the Persian UI reads "۵ گیگابایت" instead of the
            # Latin "5.0 GB" — consistent with the L10N work in task 24-g.
            lines.append(f"💾 {t('card_remaining_traffic', lang)} : {fmt_gb(remaining / GB, lang)}")
            # Used traffic — keep fmt_bytes here for byte-precise "۰ B / ۵.۰ GB"
            # since usage can be sub-GB and the B/KB/MB granularity is useful.
            lines.append(f"{t('card_used', lang)} : 📈 {fmt_bytes(used, lang)} / {fmt_bytes(total, lang)}")
            lines.append(f"<code>{fmt_progress_bar(pct, lang=lang)}</code>")
            # Total quota — fmt_gb for the friendly "گیگابایت" wording.
            lines.append(f"{t('card_total', lang)} ✅ {fmt_gb(total / GB, lang)}")
        else:
            # Unlimited traffic plan — total == 0 in the panel.
            lines.append(f"💾 {t('card_remaining_traffic', lang)} : {t('unlimited', lang)}")
            lines.append(f"{t('card_used', lang)} : 📈 {fmt_bytes(used, lang)} ({t('unlimited', lang)})")
    else:
        # No live traffic data (e.g. right after purchase, before the first
        # panel poll).  Fall back to the plan's total GB as the best
        # available "remaining" estimate — for a fresh account used≈0 so
        # remaining ≈ total.  fmt_gb already handles the 0→"Unlimited" case.
        lines.append(f"💾 {t('card_remaining_traffic', lang)} : {fmt_gb(account.get('traffic_gb',0), lang)}")
    return "\n".join(lines)


def fmt_plan_card(plan: dict, lang: str = "en", currency: str = "toman") -> str:
    lines = [f"📦 <b>{escape_html(plan['name'])}</b>"]
    lines.append(f"💾 {fmt_gb(plan['traffic_gb'], lang)}")
    lines.append(f"📅 {fmt_days(plan['duration_days'], lang)}")
    lines.append(f"💵 {fmt_price(plan['price'], lang, currency)}")
    if plan.get("limit_ip", 0) > 0:
        lines.append(f"🔌 {plan['limit_ip']} IP")
    if plan.get("description"):
        lines.append(f"\n<i>{escape_html(plan['description'])}</i>")
    return "\n".join(lines)


# (M4 — removed dead code: fmt_dashboard and fmt_server_health were
#  superseded by rich_tables.dashboard_rich / server_summary_rich and
#  never called anywhere in the codebase.)


async def show_view(message: Message,
                    *,
                    text: Optional[str] = None,
                    rich: Optional[InputRichMessage] = None,
                    reply_markup=None,
                    disable_web_page_preview: bool = False,
                    state: "Optional[FSMContext]" = None) -> Message:
    """Replace the current chat message with a new view.

    Why this exists
    ---------------
    Telegram Rich Messages (tables, headings …) **cannot be edited in place**
    — aiogram 3.30 exposes only ``Message.answer_rich`` (send new) with no
    ``edit_rich``.  The admin panel is driven by ``callback.message.edit_text``
    which edits the existing message in place.  Once a screen sends a rich
    table, the *next* ``edit_text`` would target a rich message and fail.

    ``show_view`` unifies both worlds:

    * ``rich`` provided  → delete the old message and send a fresh rich message
      (with the inline keyboard attached).  Returns the new ``Message``.
    * ``text`` provided  → try ``edit_text`` first (keeps the message id, so
      multi-step "⏳ … → ✅ done" status updates within one handler keep
      working).  If that fails — because the current message is a rich message,
      a photo/media message, or was already deleted — fall back to delete +
      send a new text message.

    Photo-message handling (TICKET-MEDIA-1)
    ---------------------------------------
    When a user replies to a ticket with a screenshot, the bot sends a PHOTO
    message (with caption + inline keyboard) to the admin/user via
    ``_send_ticket_reply_notify``.  When the recipient taps any button on that
    photo (Reply / Close / Reopen / Back), the handler's ``callback.message``
    is a photo, not text — and Telegram's ``editMessageText`` API only works
    on text messages (it returns ``"Bad Request: there is no text in the
    message to edit"``).  ``show_view`` detects this specific error and falls
    back to delete + answer, so ticket buttons work on photo notifications.

    "Message is not modified" handling
    ----------------------------------
    When the new content is byte-identical to the current message (e.g. the
    user taps the same button twice), Telegram returns ``"message is not
    modified"``.  We swallow this silently — no flicker, no delete+resend.

    FSM prompt tracking (CHAT-CLUTTER-FIX)
    --------------------------------------
    When ``state`` is supplied, the returned message's id is stored in FSM
    state under ``_prompt_msg_id`` so that :func:`del_inbound` can delete it
    after the user types their response.  This is what makes the bot's OWN
    "👥 Users — Send Telegram ID…" prompt disappear from the chat once the
    admin responds, instead of piling up.  FSM entrypoint callbacks should
    pass ``state=state``; non-FSM screens should omit it (default ``None``).
    """
    if rich is not None:
        try:
            await message.delete()
        except Exception:
            pass
        result = await message.answer_rich(rich_message=rich, reply_markup=reply_markup)
        if state is not None:
            await track_prompt(result, state)
        return result
    # Text view: edit in place when possible (best UX, stable message id),
    # otherwise delete + resend (handles rich→text, photo→text, and
    # already-deleted cases).  ``disable_web_page_preview`` is forwarded so
    # screens that show a raw subscription URL don't trigger an ugly
    # link-preview card.
    try:
        result = await message.edit_text(text, reply_markup=reply_markup,
                                         disable_web_page_preview=disable_web_page_preview)
        if state is not None:
            await track_prompt(result, state)
        return result
    except TelegramBadRequest as e:
        msg_low = str(e).lower()
        # "message is not modified" → content identical; swallow silently to
        # avoid an unnecessary delete+resend flicker (e.g. double-tap).
        if "not modified" in msg_low:
            if state is not None:
                await track_prompt(message, state)
            return message
        # "no text in the message to edit" → current message is a photo/media
        # message (ticket-reply notification with a screenshot).  Fall through
        # to the delete + answer fallback below so the new text view replaces
        # the photo cleanly.
        # "message to edit not found" → the message was already deleted; fall
        # through to the answer-only path.
        if "no text" not in msg_low and "not found" not in msg_low and "message to edit" not in msg_low:
            # Unknown TelegramBadRequest — re-raise so real errors aren't masked.
            raise
    except Exception:
        # Non-Telegram errors → fall through to the delete + answer fallback.
        pass
    # Fallback: delete the old (photo / rich / deleted) message and send a
    # fresh text message with the new content + keyboard.
    try:
        await message.delete()
    except Exception:
        pass
    result = await message.answer(text, reply_markup=reply_markup,
                                  disable_web_page_preview=disable_web_page_preview)
    if state is not None:
        await track_prompt(result, state)
    return result


async def del_inbound(message: Message, state: "Optional[FSMContext]" = None):
    """Delete the admin's inbound text message AND the bot's tracked prompt.

    FSM input handlers (``@router.message(AdminStates.…`` / ``UserStates.…``)
    receive the admin's TYPED query as ``message``.  If not deleted, every
    search query, every "enter amount", every "enter label" the admin types
    piles up in the chat and clutters the admin panel.

    Additionally — and this is the part that fixes the "bot prompt stays in
    chat" clutter — when ``state`` is supplied, this helper looks up the
    ``_prompt_msg_id`` previously stored by :func:`track_prompt` and deletes
    that bot prompt message too.  Without this, the "👥 Users — Send Telegram
    ID…" prompt would remain in the chat after the admin types a query,
    defeating the purpose of cleaning up.

    Both deletes are best-effort (in groups the bot may lack delete rights).
    """
    try:
        await message.delete()
    except Exception:
        pass
    if state is None:
        return
    try:
        data = await state.get_data()
    except Exception:
        return
    prompt_id = data.get("_prompt_msg_id")
    if prompt_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
        except Exception:
            pass
        # Clear it so a stale id is never re-deleted.
        try:
            await state.update_data(_prompt_msg_id=None)
        except Exception:
            pass


async def track_prompt(prompt_msg: Message, state: "FSMContext"):
    """Track a bot FSM-prompt message so :func:`del_inbound` can delete it.

    Call this in FSM entrypoint callbacks right after ``show_view`` (or in
    multi-step FSM handlers right after ``message.answer``) — i.e. immediately
    after sending the prompt that asks the user for the next piece of input.

    Storing the message_id in FSM state (rather than e.g. a module-global dict)
    keeps it per-user and auto-clears when the FSM flow finishes / is cleared.
    """
    try:
        await state.update_data(_prompt_msg_id=prompt_msg.message_id)
    except Exception:
        pass


def make_qr_png(data: str) -> Optional[bytes]:
    """Generate a PNG QR code for ``data``.  Returns None if qrcode is missing."""
    if not _HAS_QR:
        return None
    try:
        img = qrcode.make(data)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.error("QR generation failed: %s", e)
        return None


def build_sub_url(server: dict, sub_id: str) -> str:
    """Construct the subscription URL for a client."""
    if not sub_id:
        return ""
    base = server.get("sub_uri") or ""
    if base:
        return base + sub_id
    # Fallback (may not work if sub port differs from panel port)
    return f"{server['panel_url']}/sub/{sub_id}"


# ============================================================================
# SECTION 6: CALLBACK-DATA FACTORIES
# ============================================================================

class MenuCB(CallbackData, prefix="menu"):
    action: str
    data: str = ""


class PlanCB(CallbackData, prefix="plan"):
    action: str
    plan_id: int = 0


class AccountCB(CallbackData, prefix="acct"):
    action: str
    email: str = ""


class AdminCB(CallbackData, prefix="admin"):
    action: str
    data: str = ""


class ServerCB(CallbackData, prefix="srv"):
    action: str
    server_id: int = 0


class TicketCB(CallbackData, prefix="ticket"):
    action: str
    ticket_id: int = 0


class TicketMediaCB(CallbackData, prefix="tmedia"):
    """Dedicated callback for the per-message "View Media" button. Kept separate
    from :class:`TicketCB` so adding a new field does not break in-flight
    ``ticket:view:N`` / ``ticket:reply:N`` callbacks already sitting in users'
    Telegram clients. (TICKET-1 Feature 3)"""
    ticket_id: int = 0
    message_id: int = 0


class BuyCB(CallbackData, prefix="buy"):
    action: str
    plan_id: int = 0
    step: str = ""


class TopupCB(CallbackData, prefix="topup"):
    action: str
    email: str = ""
    gb: int = 0


class LangCB(CallbackData, prefix="lang"):
    code: str


class InboundCB(CallbackData, prefix="ib"):
    action: str
    key: str = ""          # "server_id_inbound_id"
    plan_id: int = 0


class PaymentCB(CallbackData, prefix="pay"):
    action: str
    payment_id: int = 0
    amount: int = 0


class ForceJoinCB(CallbackData, prefix="fj"):
    action: str


class SettingsCatCB(CallbackData, prefix="scat"):
    category: str


class NoopCB(CallbackData, prefix="noop"):
    """Empty callback used for section-header buttons (L5).

    Telegram inline-keyboard buttons MUST carry callback_data, but section
    headers (e.g. "— Server alias —" rows) are non-interactive labels. The
    catch-all :func:`cb_noop` handler acknowledges the tap silently so the
    user does not see a "loading…" spinner. Using a typed CallbackData class
    rather than the raw ``"noop_0"`` literal keeps the packed prefix stable
    ("noop") so the existing ``F.data.startswith("noop")`` filter — and any
    in-flight old callbacks — keep working."""
    pass


class ImportCB(CallbackData, prefix="imp"):
    """Panel-client import flow (MIGRATE-1). Actions:
      main      -> server picker (admin menu entry)
      server    -> paginated client list for one server
      client    -> single client detail + actions
      set_tgid  -> enter FSM to assign a Telegram numeric ID to this client
      do        -> import this single client (must already have tgId)
      all       -> bulk-import every client on this server that has tgId set
      page      -> change page in the client list
    """
    action: str
    server_id: int = 0
    email: str = ""
    page: int = 1


# ============================================================================
# SECTION 7: KEYBOARDS
# ============================================================================

def kb_main_menu(is_admin: bool, lang: str = "en") -> InlineKeyboardMarkup:
    """Main menu — restructured (MENU-RESTRUCTURE).

    Layout:
      Row 1 — Buy Service (single, full-width; the primary action)
      Row 2 — Free Trial | My Accounts
      Row 3 — Wallet | Referral
      Row 4 — Help & Support | More Features

    Rationale:
      * "Buy Service" is the single most-used action → gets its own row at the
        top so it's impossible to miss.
      * "Free Trial" is the second priority (per user request) → first button
        in the grid, right under Buy.
      * "Wallet" consolidates Balance + Charge Wallet + Gift Code into one
        section (opened via a single button here).
      * "Help & Support" merges the old separate Guide and Support buttons
        into one section.
      * "More Features" holds Language (and Admin Panel for admins) so the
        main menu stays uncluttered.
    """
    kb = InlineKeyboardBuilder()
    # Row 1 — primary action, single button.
    kb.button(text=t("buy", lang), callback_data=MenuCB(action="buy").pack(), style="primary")
    # Row 2 — trial (first priority after buy) + my accounts.
    kb.button(text=t("trial", lang), callback_data=MenuCB(action="trial").pack(), style="success")
    kb.button(style="primary", text=t("my_accounts", lang), callback_data=MenuCB(action="my_accounts").pack())
    # Row 3 — wallet (balance + charge + gift) + referral.
    kb.button(style="primary", text=t("wallet", lang), callback_data=MenuCB(action="balance").pack())
    kb.button(style="primary", text=t("referral", lang), callback_data=MenuCB(action="referral").pack())
    # Row 4 — merged help & support + more features.
    kb.button(style="primary", text=t("help", lang), callback_data=MenuCB(action="help").pack())
    kb.button(style="primary", text=t("more_features", lang), callback_data=MenuCB(action="more_features").pack())
    # adjust: row1=1, row2=2, row3=2, row4=2
    kb.adjust(1, 2, 2, 2)
    return kb.as_markup()


def kb_admin_menu(lang: str = "en") -> InlineKeyboardMarkup:
    """ADMIN-MENU-REWORK: top-level admin panel — reorganised into submenus.

    Previously 14 flat buttons (Dashboard, Servers, Plans, Users, Finance,
    Pending Pay, Pay History, By Admin, Promos, Gift Codes, Tickets,
    Broadcast, Cleanup, Settings).  Now grouped into 6 category buttons +
    Dashboard + Settings + Back, so the main panel is clean (5 rows × 2).

    Submenus:
      💳 Payments  → Pending Pay, Pay History, By Admin
      🎁 Promotions → Promos, Gift Codes, Broadcast
      💬 Support   → Tickets
      🖥 Servers   → (existing) + 🧹 Cleanup moved here

    Builders: kb_payments_menu, kb_promos_menu, kb_support_menu.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Dashboard", callback_data=AdminCB(action="dashboard").pack(), style="primary")
    kb.button(style="primary", text="👥 Users", callback_data=AdminCB(action="users").pack())
    kb.button(style="primary", text="🖥 Servers", callback_data=AdminCB(action="servers").pack())
    kb.button(style="primary", text="📦 Plans", callback_data=AdminCB(action="plans").pack())
    kb.button(style="primary", text="💳 Payments", callback_data=AdminCB(action="payments_menu").pack())
    kb.button(style="primary", text="💰 Finance", callback_data=AdminCB(action="finance").pack())
    kb.button(style="primary", text="🎁 Promotions", callback_data=AdminCB(action="promos_menu").pack())
    kb.button(style="primary", text="💬 Support", callback_data=AdminCB(action="support_menu").pack())
    kb.button(style="primary", text="⚙️ Settings", callback_data=AdminCB(action="settings").pack())
    kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack(), style="danger")
    kb.adjust(2, 2, 2, 2, 2, 1)
    return kb.as_markup()


def kb_payments_menu() -> InlineKeyboardMarkup:
    """💳 Payments submenu: Pending Pay, Pay History, Back.

    "By Admin" is intentionally NOT on this landing page — its proper home is
    inside the Pay History view (see ``_render_history_table``'s bottom nav,
    which shows a "👥 By Admin" button for full admins).  Keeping the Payments
    landing page to just Pending + History makes it cleaner, per the user's
    request.
    """
    kb = InlineKeyboardBuilder()
    kb.button(style="primary", text="💰 Pending Pay",
              callback_data=AdminCB(action="pending_payments").pack())
    kb.button(style="primary", text="📋 Pay History",
              callback_data=AdminCB(action="payment_history").pack())
    kb.button(text="🔙 Admin Panel", callback_data=AdminCB(action="main").pack(), style="danger")
    kb.adjust(2, 1)
    return kb.as_markup()


def kb_promos_menu() -> InlineKeyboardMarkup:
    """🎁 Promotions submenu: Promos, Gift Codes, Broadcast, Back."""
    kb = InlineKeyboardBuilder()
    kb.button(style="primary", text="🎫 Promos", callback_data=AdminCB(action="promos").pack())
    kb.button(style="success", text="🎁 Gift Codes", callback_data=AdminCB(action="gift_codes").pack())
    kb.button(style="primary", text="📣 Broadcast", callback_data=AdminCB(action="broadcast").pack())
    kb.button(text="🔙 Admin Panel", callback_data=AdminCB(action="main").pack(), style="danger")
    kb.adjust(2, 1, 1)
    return kb.as_markup()


def kb_support_menu() -> InlineKeyboardMarkup:
    """💬 Support submenu: Tickets, Back."""
    kb = InlineKeyboardBuilder()
    kb.button(style="primary", text="💬 Tickets", callback_data=AdminCB(action="tickets").pack())
    kb.button(text="🔙 Admin Panel", callback_data=AdminCB(action="main").pack(), style="danger")
    kb.adjust(1, 1)
    return kb.as_markup()


def kb_payment_admin_menu(lang: str = "en") -> InlineKeyboardMarkup:
    """Limited menu for payment-only admins — pending + my-approvals + history + back.

    PA-LANG: buttons are localised via the pa_* i18n keys so payment admins
    see their selected language.  Full admins see English (the caller passes
    lang="en" via _pa_lang).

    PAY-HISTORY-REWORK: split the old single "Payment History" button into
    two — "My Approvals" (their own reviewed receipts) and "All Receipts"
    (which redirects to my-approvals for payment admins, or shows the full
    list for full admins).  This makes the per-admin isolation explicit in
    the UI.
    """
    kb = InlineKeyboardBuilder()
    kb.button(style="success", text=t("pa_pending_btn", lang),
              callback_data=AdminCB(action="pending_payments").pack())
    kb.button(style="primary", text=t("pa_my_history_btn", lang),
              callback_data=AdminCB(action="my_history").pack())
    kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack(), style="danger")
    kb.adjust(1)
    return kb.as_markup()


def kb_plans(plans: List[dict], lang: str, currency: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for plan in plans:
        kb.button(
            text=f"📦 {plan['name']} — {fmt_price(plan['price'], lang, currency)}",
            callback_data=PlanCB(action="view", plan_id=plan["id"]).pack(),
            style="primary",
        )
    kb.button(text=t("back", lang), callback_data=MenuCB(action="main").pack(), style="danger")
    kb.adjust(1)
    return kb.as_markup()


def kb_plan_view(plan_id: int, lang: str) -> InlineKeyboardMarkup:
    """Plan detail page — single Buy button (UI-REDESIGN).

    The old layout had separate Buy + Promo buttons here, which forked the
    flow into two paths (and the promo path skipped the name step, causing
    the "no label after promo" bug). Now there's one Buy button that enters
    a single review page where name + promo + payment are all handled.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text=t("buy", lang), callback_data=BuyCB(action="start", plan_id=plan_id, step="review").pack(), style="success")
    kb.button(text=t("back", lang), callback_data=MenuCB(action="buy").pack(), style="danger")
    kb.adjust(1)
    return kb.as_markup()


def kb_purchase_review(plan_id: int, lang: str, has_name: bool = False,
                       has_promo: bool = False, can_afford: bool = True,
                       payment_enabled: bool = True) -> InlineKeyboardMarkup:
    """Single review page keyboard (UI-REDESIGN + PURCHASE-UX-2 + UX-SHORTFALL-ONLY).

    One unified page where the user can: set/change a name, add a promo code,
    and confirm payment. This replaces the old two-path flow where promo and
    name were separate entry points that never met.

    PURCHASE-UX-2: the "Confirm & Pay" button is **only** shown when the user
    can actually afford the plan.  When they can't, tapping it would just
    trigger a rejection toast — confusing and frustrating.

    UX-SHORTFALL-ONLY (this revision): the insufficient-balance state used to
    offer three top-up paths — Charge Wallet, Gift Code, and Pay Exact
    Shortfall.  Per product decision the only path on this page is now
    **Pay Exact Shortfall** (only when card payments are enabled): it creates
    a card payment for exactly the missing amount, tagged with this plan so
    after admin approval the user gets a one-tap "buy this plan" button.

    Wallet charge and gift-code redemption are still reachable from the main
    menu (Wallet section) — they're just no longer surfaced mid-purchase.

    Name + promo buttons remain available in both states (the user can set
    them before paying).  If card payments are disabled, the can't-afford
    state has no payment action on this page (the user must use the main-menu
    Wallet flow); this matches the admin's choice to turn off card payments.
    """
    kb = InlineKeyboardBuilder()
    name_label = t("set_name_btn", lang) + (" ✏️" if has_name else "")
    promo_label = t("apply_promo", lang) + (" ✅" if has_promo else "")
    if can_afford:
        # Row 1: Confirm & Pay (full width).
        kb.button(text=t("confirm_pay", lang),
                  callback_data=BuyCB(action="confirm", plan_id=plan_id, step="execute").pack(),
                  style="success")
        # Row 2: Name | Promo.
        kb.button(text=name_label,
                  callback_data=BuyCB(action="set_name", plan_id=plan_id, step="enter").pack(),
                  style="primary")
        kb.button(text=promo_label,
                  callback_data=BuyCB(action="promo", plan_id=plan_id, step="enter").pack(),
                  style="primary")
        # Row 3: Back.
        kb.button(text=t("back", lang), callback_data=MenuCB(action="buy").pack(), style="danger")
        kb.adjust(1, 2, 1)
    else:
        # Can't afford — NO Confirm & Pay button (would just reject).
        # UX-SHORTFALL-ONLY + UX-GIFT-IN-PURCHASE + UX-TWO-ROWS: the top-up
        # paths on this page are (1) Pay Exact Shortfall (card-to-card, only
        # when payment_enabled) and (2) Gift Code (balance-type only). Charge
        # Wallet was removed per product decision (still reachable via the
        # main-menu Wallet section).
        # UX-TWO-ROWS: each top-up button gets its OWN full-width row, with
        # Shortfall first (it's the primary path the hint points at when card
        # payments are on). Stacking them vertically instead of side-by-side
        # removes ambiguity about which button to tap first.
        # Row 1: Pay Exact Shortfall (primary action, only if card payments on).
        if payment_enabled:
            kb.button(text=t("request_shortfall_btn", lang),
                      callback_data=BuyCB(action="shortfall", plan_id=plan_id, step="request").pack(),
                      style="success")
        # Row 2 (or Row 1 when payments off): Gift Code (always shown — gift
        # codes are an independent wallet top-up channel that works whether or
        # not card payments are enabled).
        kb.button(text=t("gift_btn", lang),
                  callback_data=BuyCB(action="gift", plan_id=plan_id, step="redeem").pack(),
                  style="primary")
        # Row 3: Name | Promo (still available).
        kb.button(text=name_label,
                  callback_data=BuyCB(action="set_name", plan_id=plan_id, step="enter").pack(),
                  style="primary")
        kb.button(text=promo_label,
                  callback_data=BuyCB(action="promo", plan_id=plan_id, step="enter").pack(),
                  style="primary")
        # Row 4: Back.
        kb.button(text=t("back", lang), callback_data=MenuCB(action="buy").pack(), style="danger")
        # Layout: payments on  -> 1 (shortfall) / 1 (gift) / 2 (name, promo) / 1 (back);
        #         payments off -> 1 (gift) / 2 (name, promo) / 1 (back).
        kb.adjust(1, 1, 2, 1) if payment_enabled else kb.adjust(1, 2, 1)
    return kb.as_markup()


def kb_account_details(email: str, is_active: bool, lang: str, is_trial: bool = False,
                       topup_enabled: bool = True, is_unlimited: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    # Trial accounts cannot be renewed or topped up (one-shot free account).
    # TOPUP-TOGGLE: hide the top-up button entirely when the admin has
    # disabled top-ups (so the user can't even tap a dead-end button).
    # UNLIMITED-TOGGLE: also hide top-up for unlimited accounts (traffic_gb=0)
    # because adding GB to an unlimited account is a no-op — the user would
    # pay money for nothing.  The panel's add_bytes is ignored by 3x-ui when
    # total=0 (unlimited), and the DB keeps traffic_gb=0 (see H2 block in
    # cb_topup_buy).  Renewal is still available for unlimited accounts.
    show_topup = topup_enabled and not is_unlimited
    if not is_trial:
        kb.button(text=t("renew", lang), callback_data=AccountCB(action="renew", email=email).pack(), style="success")
        if show_topup:
            kb.button(text=t("topup_traffic", lang), callback_data=AccountCB(action="topup", email=email).pack(), style="primary")
    kb.button(style="primary", text=t("traffic", lang), callback_data=AccountCB(action="traffic", email=email).pack())
    kb.button(style="primary", text=t("get_link", lang), callback_data=AccountCB(action="links", email=email).pack())
    kb.button(style="primary", text=t("qr", lang), callback_data=AccountCB(action="qr", email=email).pack())
    kb.button(style="primary", text=t("set_label", lang), callback_data=AccountCB(action="label", email=email).pack())
    if is_active:
        kb.button(text=t("disable", lang), callback_data=AccountCB(action="disable", email=email).pack(), style="danger")
    else:
        kb.button(text=t("enable", lang), callback_data=AccountCB(action="enable", email=email).pack(), style="success")
    kb.button(text=t("delete", lang), callback_data=AccountCB(action="delete_ask", email=email).pack(), style="danger")
    kb.button(text=t("back", lang), callback_data=MenuCB(action="my_accounts").pack(), style="primary")
    if is_trial:
        kb.adjust(2, 2, 2, 1)
    else:
        # Row layout varies: when top-up is hidden, the first row has only
        # Renew (1 button) instead of Renew+TopUp (2 buttons). The adjust()
        # must match so Telegram doesn't auto-pair buttons in ugly ways.
        if show_topup:
            kb.adjust(2, 2, 2, 2, 1)
        else:
            kb.adjust(1, 2, 2, 2, 1)
    return kb.as_markup()


def kb_accounts_list(accounts: List[dict], lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for acc in accounts:
        status = "🟢" if acc["is_active"] else "🔴"
        trial = "🎁" if acc["is_trial"] else ""
        label = acc.get("label") or acc["email"][:20]
        kb.button(style="primary", 
            text=f"{status}{trial} {label[:22]}",
            callback_data=AccountCB(action="view", email=acc["email"]).pack(),
        )
    kb.button(text=t("back", lang), callback_data=MenuCB(action="main").pack(), style="danger")
    kb.adjust(1)
    return kb.as_markup()


def kb_servers(servers: List[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for srv in servers:
        status = "🟢" if srv["is_healthy"] else "🔴"
        if not srv["is_active"]:
            status = "⚪"
        kb.button(style="primary", text=f"{status} {srv['alias']}", callback_data=ServerCB(action="view", server_id=srv["id"]).pack())
    kb.button(text="📥 Import from Panel", callback_data=AdminCB(action="import_main").pack(), style="primary")
    kb.button(text="➕ Add Server", callback_data=ServerCB(action="add").pack(), style="success")
    kb.button(text="🔄 Sync All", callback_data=ServerCB(action="sync_all").pack(), style="primary")
    # ADMIN-MENU-REWORK: 🧹 Cleanup moved here from the top-level admin menu
    # (it's a server-maintenance tool — delete depleted clients across all
    # panels + sync client counts — so it belongs with the other server ops).
    kb.button(text="🧹 Cleanup & Maintenance", callback_data=AdminCB(action="cleanup").pack(), style="danger")
    kb.button(text="🔙 Admin", callback_data=AdminCB(action="main").pack(), style="danger")
    kb.adjust(1, 2, 1, 1, 1)
    return kb.as_markup()


def kb_server_view(server_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Stats", callback_data=ServerCB(action="stats", server_id=server_id).pack(), style="primary")
    kb.button(style="primary", text="📡 Inbounds", callback_data=ServerCB(action="inbounds", server_id=server_id).pack())
    kb.button(style="primary", text="🔄 Sync", callback_data=ServerCB(action="sync", server_id=server_id).pack())
    kb.button(style="primary", text="✏️ Edit", callback_data=ServerCB(action="edit", server_id=server_id).pack())
    kb.button(style="primary", text="📶 Test", callback_data=ServerCB(action="test", server_id=server_id).pack())
    kb.button(style="primary", text="💾 Backup", callback_data=ServerCB(action="backup", server_id=server_id).pack())
    kb.button(style="primary", text="📥 Import", callback_data=ImportCB(action="server", server_id=server_id, page=1).pack())
    kb.button(text="🔄 Restart", callback_data=ServerCB(action="restart_ask", server_id=server_id).pack(), style="danger")
    kb.button(text="🗑 Delete", callback_data=ServerCB(action="delete_ask", server_id=server_id).pack(), style="danger")
    kb.button(text="🔙 Servers", callback_data=AdminCB(action="servers").pack(), style="danger")
    # SERVER-RESTART-CONFIRM: Restart and Delete are both destructive — never
    # share a row. Restart on its own row, Delete on its own row, Back pairs
    # with nothing (own row). Old layout (2,2,2,1,2,1) put Restart+Delete
    # side-by-side, inviting a wrong-tap.
    kb.adjust(2, 2, 2, 1, 1, 1, 1)
    return kb.as_markup()


def kb_import_server_picker(servers: List[dict]) -> InlineKeyboardMarkup:
    """Server picker for the panel-client import flow (MIGRATE-1)."""
    kb = InlineKeyboardBuilder()
    for srv in servers:
        if not srv["is_active"]:
            continue
        status = "🟢" if srv["is_healthy"] else "🔴"
        kb.button(style="primary",
            text=f"{status} {srv['alias']}",
            callback_data=ImportCB(action="server", server_id=srv["id"], page=1).pack())
    kb.button(text="🔙 Servers", callback_data=AdminCB(action="servers").pack(), style="danger")
    kb.adjust(1)
    return kb.as_markup()


def kb_import_client_list(server_id: int, clients: List[dict], page: int,
                          per_page: int = 10) -> InlineKeyboardMarkup:
    """Paginated list of panel clients for the import flow. Each row shows the
    client email, enabled status, and whether a Telegram ID is set. The nav
    row (Prev / page / Next) is grouped, then the bulk-import and back
    buttons follow."""
    kb = InlineKeyboardBuilder()
    total = len(clients)
    pages = max(1, (total + per_page - 1) // per_page)
    if page < 1:
        page = 1
    if page > pages:
        page = pages
    start = (page - 1) * per_page
    page_items = clients[start:start + per_page]
    for c in page_items:
        email = c.get("email") or "—"
        has_tg = bool(c.get("tgId"))
        mark = "✅" if has_tg else "➖"
        tg_disp = f" · tg:{c.get('tgId')}" if has_tg else " · no TG"
        en = "🟢" if c.get("enable") else "🔴"
        kb.button(style="primary",
            text=f"{mark}{en} {email[:28]}{tg_disp}",
            callback_data=ImportCB(action="client", server_id=server_id,
                                   email=email, page=page).pack())
    if page > 1:
        kb.button(style="primary", text="◀️ Prev",
                  callback_data=ImportCB(action="page", server_id=server_id,
                                         page=page - 1).pack())
    kb.button(style="primary", text=f"{page}/{pages}", callback_data=NoopCB().pack())
    if page < pages:
        kb.button(style="primary", text="Next ▶️",
                  callback_data=ImportCB(action="page", server_id=server_id,
                                         page=page + 1).pack())
    with_tg = sum(1 for c in clients if c.get("tgId"))
    footer_sizes = [(1 if page > 1 else 0) + 1 + (1 if page < pages else 0)]
    if with_tg > 0:
        kb.button(style="success",
            text=f"⚡ Import all ({with_tg} with TG)",
            callback_data=ImportCB(action="all", server_id=server_id, page=page).pack())
        footer_sizes.append(1)
    kb.button(text="🔙 Picker", callback_data=AdminCB(action="import_main").pack(), style="danger")
    footer_sizes.append(1)
    sizes = [1] * len(page_items) + footer_sizes
    kb.adjust(*sizes)
    return kb.as_markup()


def kb_import_client_view(server_id: int, email: str, has_tg_id: bool,
                          already_imported: bool, page: int) -> InlineKeyboardMarkup:
    """Per-client action view in the import flow."""
    kb = InlineKeyboardBuilder()
    if has_tg_id:
        if already_imported:
            kb.button(style="primary", text="🔄 Re-sync",
                      callback_data=ImportCB(action="do", server_id=server_id,
                                             email=email, page=page).pack())
        else:
            kb.button(style="success", text="✅ Import this client",
                      callback_data=ImportCB(action="do", server_id=server_id,
                                             email=email, page=page).pack())
        kb.button(style="primary", text="✏️ Change Telegram ID",
                  callback_data=ImportCB(action="set_tgid", server_id=server_id,
                                         email=email, page=page).pack())
    else:
        kb.button(style="success", text="🔗 Set Telegram ID",
                  callback_data=ImportCB(action="set_tgid", server_id=server_id,
                                         email=email, page=page).pack())
    kb.button(text="🔙 List", callback_data=ImportCB(action="server",
              server_id=server_id, page=page).pack(), style="danger")
    kb.adjust(1, 1)
    return kb.as_markup()


def kb_admin_plans(plans: List[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for plan in plans:
        status = "✅" if plan["is_active"] else "❌"
        kb.button(style="primary", text=f"{status} {plan['name']}", callback_data=PlanCB(action="admin_view", plan_id=plan["id"]).pack())
    kb.button(text="➕ Add Plan", callback_data=PlanCB(action="add", plan_id=0).pack(), style="success")
    kb.button(text="🔙 Admin", callback_data=AdminCB(action="main").pack(), style="danger")
    kb.adjust(1, 2, 1)
    return kb.as_markup()


def kb_admin_plan_view(plan_id: int, is_active: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Edit", callback_data=PlanCB(action="edit", plan_id=plan_id).pack(), style="primary")
    kb.button(style="primary", text="🔗 Inbounds", callback_data=PlanCB(action="inbounds", plan_id=plan_id).pack())
    if is_active:
        kb.button(text="❌ Disable", callback_data=PlanCB(action="toggle", plan_id=plan_id).pack(), style="danger")
    else:
        kb.button(text="✅ Enable", callback_data=PlanCB(action="toggle", plan_id=plan_id).pack(), style="success")
    kb.button(text="🗑 Delete", callback_data=PlanCB(action="delete_ask", plan_id=plan_id).pack(), style="danger")
    kb.button(text="🔙 Plans", callback_data=AdminCB(action="plans").pack(), style="danger")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def kb_inbound_picker(server_alias: str, inbounds: List[dict], selected: set,
                      plan_id: int) -> InlineKeyboardMarkup:
    """Multi-select inbound picker for plan configuration."""
    kb = InlineKeyboardBuilder()
    for ib in inbounds:
        key = f"{ib['server_id']}_{ib['inbound_id']}"
        mark = "✅" if key in selected else "⬜"
        proto = ib.get("protocol", "?")
        remark = ib.get("remark", "") or f"id{ib['inbound_id']}"
        kb.button(style="primary", 
            text=f"{mark} {server_alias} · {remark} ({proto})",
            callback_data=InboundCB(action="toggle", key=key, plan_id=plan_id).pack(),
        )
    kb.button(text="💾 Save", callback_data=InboundCB(action="save", plan_id=plan_id).pack(), style="success")
    kb.button(text="🔙 Back", callback_data=PlanCB(action="admin_view", plan_id=plan_id).pack(), style="danger")
    kb.adjust(1, 2)
    return kb.as_markup()


def kb_tickets(tickets: List[dict], back_cb: str = "support_menu") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for tk in tickets:
        status = "🟢" if tk["status"] == "open" else "🔴"
        kb.button(style="primary", text=f"{status} #{tk['id']} - {tk['subject'][:20]}",
                  callback_data=TicketCB(action="view", ticket_id=tk["id"]).pack())
    kb.button(text="🔙 Back", callback_data=AdminCB(action=back_cb).pack(), style="danger")
    kb.adjust(1)
    return kb.as_markup()


def kb_ticket_view(ticket_id: int, is_admin: bool, lang: str = "en", status: str = "open",
                   user_tg_id: int = 0, messages: Optional[List[dict]] = None) -> InlineKeyboardMarkup:
    """Inline keyboard for the ticket-detail view.

    When ``is_admin`` is True and ``user_tg_id`` is provided, an extra
    "👤 Manage User" button is appended (jumps straight to the admin
    user-detail screen for the ticket owner). When ``messages`` is provided,
    one "📎 View Media" button is appended per ticket-message that carries a
    media attachment (photo / document / video / voice). (TICKET-1)
    """
    kb = InlineKeyboardBuilder()
    if status != "closed":
        kb.button(text=t("reply", lang), callback_data=TicketCB(action="reply", ticket_id=ticket_id).pack(), style="primary")
    if is_admin:
        if status != "closed":
            kb.button(text=t("close", lang), callback_data=TicketCB(action="close", ticket_id=ticket_id).pack(), style="danger")
        # Manage-User shortcut: only when we know the ticket owner's tg_id.
        if user_tg_id:
            kb.button(text=t("manage_user", lang), callback_data=AdminCB(action="user_view", data=str(user_tg_id)).pack(), style="primary")
        kb.button(text="🔙 Tickets", callback_data=AdminCB(action="tickets").pack(), style="danger")
    else:
        if status == "closed":
            kb.button(text=t("reopen", lang), callback_data=TicketCB(action="reopen", ticket_id=ticket_id).pack(), style="success")
        kb.button(text=t("back", lang), callback_data=MenuCB(action="my_tickets").pack(), style="danger")
    # Per-message "View Media" buttons (one per media-bearing message).
    media_buttons = 0
    if messages:
        media_label_map = _media_label_map(lang)
        for m in messages:
            mt = m.get("media_type") or ""
            if mt and m.get("media_file_id"):
                label = media_label_map.get(mt, "Media")
                kb.button(text=f"📎 {label}",
                          callback_data=TicketMediaCB(ticket_id=ticket_id,
                                                      message_id=m.get("id", 0)).pack(),
                          style="primary")
                media_buttons += 1
    # Layout: 2 primary action buttons per row, then 1 per row for the rest.
    # adjust() applies a repeating pattern; the count of leading "2" groups is
    # enough to fit Reply+Close, then each subsequent button on its own row.
    kb.adjust(2, 1)
    return kb.as_markup()


def kb_cancel(lang: str = "en") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t("cancel", lang), callback_data=MenuCB(action="cancel").pack(), style="danger")
    return kb.as_markup()


def kb_confirm_purchase(plan_id: int, lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t("confirm_pay", lang), callback_data=BuyCB(action="confirm", plan_id=plan_id, step="execute").pack(), style="success")
    kb.button(text=t("apply_promo", lang), callback_data=BuyCB(action="promo", plan_id=plan_id, step="enter").pack(), style="primary")
    kb.button(text=t("cancel", lang), callback_data=MenuCB(action="buy").pack(), style="danger")
    kb.adjust(1, 2)
    return kb.as_markup()


def kb_back_to_menu(lang: str = "en") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack(), style="primary")
    return kb.as_markup()


def kb_broadcast_targets() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👥 All", callback_data=AdminCB(action="broadcast_all").pack(), style="primary")
    kb.button(text="🟢 Active", callback_data=AdminCB(action="broadcast_active").pack(), style="success")
    kb.button(text="🔴 Expired", callback_data=AdminCB(action="broadcast_expired").pack(), style="danger")
    kb.button(style="success", text="🎁 Trial", callback_data=AdminCB(action="broadcast_trial").pack())
    # ADMIN-MENU-REWORK: back → Promotions submenu.
    kb.button(text="🔙 Promotions", callback_data=AdminCB(action="promos_menu").pack(), style="danger")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def kb_topup_packages(email: str, packages: List[int], lang: str, currency: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for gb in packages:
        # LOCALIZATION: use fmt_gb so the button reads "۵ گیگابایت" in Persian
        # instead of "5 GB".  Price is shown alongside so the user knows the
        # cost before tapping.
        price_per_gb = None  # price computed by caller via db.get_setting_float
        # Use the localized GB label for the button text.
        gb_label = fmt_gb(gb, lang)
        kb.button(text=f"➕ {gb_label}", callback_data=TopupCB(action="buy", email=email, gb=gb).pack(), style="primary")
    kb.button(text=t("back", lang), callback_data=AccountCB(action="view", email=email).pack(), style="danger")
    kb.adjust(2)
    return kb.as_markup()


def kb_language(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(style="primary", text="🇬🇧 English", callback_data=LangCB(code="en").pack())
    kb.button(style="primary", text="🇮🇷 فارسی", callback_data=LangCB(code="fa").pack())
    # MENU-RESTRUCTURE: back goes to "More Features" instead of main menu,
    # since the language picker is now reached via More Features.
    kb.button(text=t("back", lang), callback_data=MenuCB(action="more_features").pack(), style="danger")
    kb.adjust(2, 1)
    return kb.as_markup()


def kb_more_features(lang: str, is_admin: bool = False) -> InlineKeyboardMarkup:
    """MENU-RESTRUCTURE: "More Features" submenu.

    Holds secondary settings so the main menu stays clean:
      • Language picker
      • Admin Panel (only for full admins; routed through AdminCB:main so
        payment-only admins get their limited menu)
    """
    kb = InlineKeyboardBuilder()
    kb.button(style="primary", text=t("language", lang), callback_data=MenuCB(action="language").pack())
    if is_admin:
        kb.button(text=t("admin_panel", lang), callback_data=AdminCB(action="main").pack(), style="danger")
    kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack(), style="primary")
    kb.adjust(1)
    return kb.as_markup()


# ============================================================================
# SECTION 8: FSM STATES
# ============================================================================

class UserStates(StatesGroup):
    waiting_for_promo_code = State()
    waiting_for_gift_code = State()
    # Separate state for gift codes entered from the Buy Service review page.
    # Keeping it separate from waiting_for_gift_code lets ms_purchase_gift_code
    # apply purchase-specific rules (balance-type only) and return the user to
    # the same plan's review page instead of the main menu. (UX-GIFT-IN-PURCHASE)
    waiting_for_purchase_gift_code = State()
    waiting_for_account_name = State()
    waiting_for_ticket_category = State()
    waiting_for_ticket_subject = State()
    waiting_for_ticket_message = State()
    waiting_for_ticket_reply = State()
    waiting_for_label = State()
    waiting_for_custom_amount = State()
    waiting_for_receipt = State()
    waiting_for_language_on_start = State()


class AdminStates(StatesGroup):
    # server
    waiting_for_server_alias = State()
    waiting_for_server_url = State()
    waiting_for_server_token = State()
    waiting_for_server_capacity = State()
    waiting_for_server_priority = State()
    waiting_for_server_location = State()
    server_edit_which = State()        # which field to edit
    # plan
    waiting_for_plan_name = State()
    waiting_for_plan_desc = State()
    waiting_for_plan_traffic = State()
    waiting_for_plan_duration = State()
    waiting_for_plan_price = State()
    waiting_for_plan_limit_ip = State()
    plan_edit_which = State()
    plan_edit_value = State()
    # broadcast
    waiting_for_broadcast_message = State()
    # user
    waiting_for_user_search = State()
    waiting_for_add_balance = State()  # data=tg_id
    waiting_for_deduct_balance = State()
    waiting_for_admin_account_create = State()
    # import from panel (MIGRATE-1)
    waiting_for_import_tg_id = State()
    # ticket
    waiting_for_admin_ticket_reply = State()
    # promo
    waiting_for_promo_code_str = State()
    waiting_for_promo_discount = State()
    waiting_for_promo_max_uses = State()
    # gift
    waiting_for_gift_amount = State()
    waiting_for_gift_plan = State()
    # settings
    setting_edit_value = State()
    # payment
    waiting_for_reject_reason = State()
    # force join
    waiting_for_force_join_channel = State()
    # payment admins management
    waiting_for_payment_admin_id = State()


# ============================================================================
# SECTION 9: MIDDLEWARE
# ============================================================================

class AuthMiddleware:
    """Resolve / create the Telegram user, enforce bans and force join."""

    def __init__(self, db: Database, bot: Bot):
        self.db = db
        self.bot = bot

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)
        # Skip force-join checks for admins
        is_admin = user.id in ADMIN_IDS

        ref_code = ""
        if isinstance(event, Message) and event.text and event.text.startswith("/start "):
            ref_code = event.text.split(maxsplit=1)[1].strip()

        db_user = await self.db.get_or_create_user(user.id, user.username or "",
                                                   user.first_name or "", ref_code)
        if db_user.get("is_banned"):
            try:
                if isinstance(event, Message):
                    await event.answer(t("banned", db_user.get("language", "en")))
                elif isinstance(event, CallbackQuery):
                    await event.answer(t("banned", db_user.get("language", "en")), show_alert=True)
            except TelegramForbiddenError:
                pass  # user blocked the bot — expected
            except TelegramBadRequest as e:
                logger.warning("banned-notify failed: %s", e)
            except Exception as e:
                logger.warning("banned-notify failed: %s", e, exc_info=True)
            return

        # Force-join check (skip for admins and language-selection state)
        if not is_admin:
            fj_enabled = await self.db.get_setting_int("force_join_enabled", 0)
            if fj_enabled:
                channels = await self.db.get_setting_json("force_join_channels", [])
                if channels:
                    lang = L(db_user.get("language", DEFAULT_LANGUAGE))
                    not_joined = []
                    for ch in channels:
                        chat_id = ch.get("chat_id")
                        if chat_id:
                            try:
                                member = await self.bot.get_chat_member(int(chat_id), user.id)
                                if member.status not in ("member", "administrator", "creator"):
                                    not_joined.append(ch)
                            except (TelegramBadRequest, TelegramForbiddenError):
                                # Bot not in channel / chat not found — treat as not joined.
                                not_joined.append(ch)
                            except Exception as e:
                                logger.warning("get_chat_member failed for %s: %s", ch, e, exc_info=True)
                                not_joined.append(ch)
                    if not_joined:
                        # Allow language selection callbacks to pass through.
                        # LangCB = the user picked a specific language (en/fa).
                        # MenuCB(action="language") = the user tapped the
                        # "🌐 Language" button to OPEN the language picker.
                        # ForceJoinCB = the "✅ I Joined" verify button.
                        # All three must pass through so the user can change
                        # their language from within the force-join prompt.
                        if isinstance(event, CallbackQuery):
                            cb_data = data.get("callback_data")
                            if isinstance(cb_data, LangCB):
                                return await handler(event, data)
                            if isinstance(cb_data, ForceJoinCB):
                                return await handler(event, data)
                            if isinstance(cb_data, MenuCB) and cb_data.action == "language":
                                return await handler(event, data)
                        # Build channels list text
                        channels_text = ""
                        for ch in not_joined:
                            username = ch.get("username", "")
                            title = ch.get("title", "")
                            if username:
                                channels_text += f"• @{username}\n"
                            elif title:
                                channels_text += f"• {title}\n"
                        kb = InlineKeyboardBuilder()
                        for ch in not_joined:
                            username = ch.get("username", "")
                            if username:
                                kb.button(style="primary", text=f"📢 {username}", url=f"https://t.me/{username}")
                        kb.button(text=t("verify_join", lang), callback_data=ForceJoinCB(action="verify").pack(), style="success")
                        kb.button(style="primary", text=t("language", lang), callback_data=MenuCB(action="language").pack())
                        kb.adjust(1, 2)
                        try:
                            if isinstance(event, Message):
                                await event.answer(t("force_join", lang, channels=channels_text),
                                                   reply_markup=kb.as_markup())
                            elif isinstance(event, CallbackQuery):
                                # FORCE-JOIN-PHOTO: use show_view() instead of
                                # event.message.edit_text(). When the user taps a
                                # button on a PHOTO message (e.g. a ticket-reply
                                # notification that carried a screenshot, sent by
                                # _send_ticket_reply_notify), edit_text would fail
                                # with "there is no text in the message to edit"
                                # and the button spinner would hang forever.
                                # show_view() detects the photo, deletes it, and
                                # sends the force-join prompt as a fresh text
                                # message so the user can act on it.
                                await show_view(event.message,
                                                text=t("force_join", lang, channels=channels_text),
                                                reply_markup=kb.as_markup())
                                await event.answer(t("force_join_failed", lang), show_alert=True)
                        except TelegramForbiddenError:
                            pass  # user blocked the bot — expected
                        except TelegramBadRequest as e:
                            logger.warning("force-join prompt failed: %s", e)
                        except Exception as e:
                            logger.warning("force-join prompt failed: %s", e, exc_info=True)
                        return

        data["db_user"] = db_user
        return await handler(event, data)


async def get_payment_admin_ids(db: "Database") -> set:
    """Return the set of Telegram user IDs configured as payment-only admins.

    Stored as a JSON array in the settings table under key
    ``payment_admin_ids``. Payment admins can ONLY approve/reject pending
    payments — they cannot access servers, plans, users, settings, etc.
    """
    raw = await db.get_setting_json("payment_admin_ids", [])
    if not isinstance(raw, list):
        return set()
    return {int(x) for x in raw if x}


def _admin_display(user: Optional[dict]) -> str:
    """Best-effort human-readable label for an admin row (used in receipt
    history so the admin can see "approved by @username").

    Preference order: @username → first_name+last_name → first_name → tg_id.
    Returns ``"-"`` if the admin row is missing (e.g. legacy rows approved
    before this column existed).
    """
    if not user:
        return "-"
    uname = (user.get("username") or "").strip()
    if uname:
        return f"@{uname}"
    parts = [(user.get("first_name") or "").strip(), (user.get("last_name") or "").strip()]
    parts = [p for p in parts if p]
    if parts:
        return " ".join(parts)
    return str(user.get("tg_id") or "-")


def _admin_handle_from_callback(user) -> str:
    """Build a human-readable admin handle straight from a Telegram User
    object (the one aiogram injects on callback handlers).

    Used when we're about to store ``admin_username`` on the payment row at
    approve/reject time — we capture it before any DB lookup so it survives
    even if the admin later blocks the bot.
    """
    uname = (getattr(user, "username", None) or "").strip()
    if uname:
        return f"@{uname}"
    parts = [(getattr(user, "first_name", None) or "").strip(),
             (getattr(user, "last_name", None) or "").strip()]
    parts = [p for p in parts if p]
    if parts:
        return " ".join(parts)
    return str(getattr(user, "id", "-"))


async def _mark_payment_notifs_processed(payment: dict, status: str,
                                          admin_label: str, bot,
                                          db: "Optional[Database]" = None) -> None:
    """Edit (or delete) every admin's "new payment" notification once any
    admin approves or rejects the payment.

    RECEIPT-CROSS-ADMIN-CLEANUP: when a user submits a receipt, the bot sends
    a "💰 New payment needs approval" notification (with a 👁 Review button)
    to EVERY admin.  Without this helper, those notifications stay in each
    admin's chat — and the other admins have no idea that admin A already
    processed the receipt.  They'd tap 👁 Review, see "already approved", and
    wonder why the notification is still there.

    ROLE-AWARE BEHAVIOUR (per user request): the cleanup now distinguishes
    between the two admin tiers:

    * **Main admins** (``ADMIN_IDS`` env) — the notification is EDITED to
      "✅ Approved by @admin" / "❌ Rejected by @admin" with a single
      disabled button.  They keep a visible audit trail of every receipt's
      outcome (the "edit mode" the user asked to retain for main admins).

    * **Payment-verifier admins** (``payment_admin_ids`` DB setting) — the
      notification is DELETED outright.  These admins only exist to approve
      pending receipts; once a receipt is resolved it's noise in their chat,
      so removing it keeps their workspace clean.  (The user explicitly
      asked: "برای ادمین های تایید کننده ی پرداخت حذف کن رسید Reject یا
      Approve شده رو".)

    For a main admin, if editing fails (e.g. the message is older than 48h,
    or it's a photo and Telegram rejects the caption edit), it falls back to
    DELETING the message — better a clean chat than a stale actionable prompt.

    All operations are best-effort and logged at WARNING level — a failure
    here (e.g. admin blocked the bot) must never break the approve/reject
    flow itself.

    Args:
        payment: the payment row (must include ``notif_msg_ids``).
        status: "approved" or "rejected".
        admin_label: human-readable label of the acting admin (e.g. "@alice").
        bot: the aiogram Bot instance (for edit/delete API calls).
        db: the Database instance — needed to look up which admins are
            payment-verifier-only (so we can delete their notifications
            instead of editing them).  When ``None`` (legacy callers), every
            admin is treated as a main admin and the old edit-everyone
            behaviour is preserved.
    """
    raw = payment.get("notif_msg_ids") if isinstance(payment, dict) else None
    if not raw:
        return
    try:
        notif_map = json.loads(raw)
    except Exception:
        return
    if not isinstance(notif_map, dict) or not notif_map:
        return

    # Resolve the payment-verifier admin set ONCE (one DB round-trip) so we
    # can branch per-recipient below.  Falls back to an empty set when no db
    # is available — in that case everyone is treated as a main admin.
    verifier_ids: set = set()
    if db is not None:
        try:
            verifier_ids = await get_payment_admin_ids(db)
        except Exception as e:
            logger.warning("get_payment_admin_ids failed in notif cleanup: %s", e)

    icon = "✅" if status == "approved" else "❌"
    word_en = "Approved" if status == "approved" else "Rejected"
    word_fa = "تأیید شد" if status == "approved" else "رد شد"
    # Build the replacement keyboard — a single disabled-style button so the
    # admin can see the outcome but can't tap anything.  (Telegram has no
    # truly "disabled" button, so we use a no-op callback.)
    done_kb = InlineKeyboardBuilder()
    done_kb.button(text=f"{icon} {word_en} / {word_fa}",
                   callback_data=PaymentCB(action="noop", payment_id=0).pack())
    done_kb.adjust(1)

    for admin_id_str, info in notif_map.items():
        if not isinstance(info, dict):
            continue
        chat_id = info.get("chat_id")
        message_id = info.get("message_id")
        msg_type = info.get("type") or "text"
        if not chat_id or not message_id:
            continue

        # ---- ROLE BRANCH ------------------------------------------------
        # Payment-verifier admins: DELETE the notification outright (their
        # job is done once any admin resolves the receipt; the resolved
        # receipt is just clutter in their chat).
        try:
            admin_id_int = int(admin_id_str)
        except (TypeError, ValueError):
            admin_id_int = -1
        is_verifier_only = (
            admin_id_int != -1
            and admin_id_int not in ADMIN_IDS
            and admin_id_int in verifier_ids
        )
        if is_verifier_only:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception as e:
                logger.debug("verifier notif delete failed for admin %s msg %s: %s",
                             admin_id_str, message_id, e)
            continue

        # ---- MAIN-ADMIN PATH: edit the notification to show the outcome --
        new_caption = (
            f"{icon} <b>Payment {word_en}</b>\n\n"
            f"👤 By: <b>{escape_html(admin_label)}</b>\n"
            f"🧾 Payment #{payment.get('id', '?')}"
        )
        try:
            if msg_type in ("photo", "document"):
                # edit_message_caption works for photo/document messages.
                try:
                    await bot.edit_message_caption(
                        chat_id=chat_id, message_id=message_id,
                        caption=new_caption, reply_markup=done_kb.as_markup(),
                    )
                    continue
                except TelegramBadRequest as e:
                    msg_low = str(e).lower()
                    # "message is not modified" is harmless — already in the
                    # desired state (e.g. the acting admin's own notif was
                    # already replaced by the approve handler's show_view).
                    if "not modified" in msg_low:
                        continue
                    # For any other error, fall through to the delete path.
            else:
                # Plain-text notification — edit_message_text.
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id,
                        text=new_caption, reply_markup=done_kb.as_markup(),
                    )
                    continue
                except TelegramBadRequest as e:
                    msg_low = str(e).lower()
                    if "not modified" in msg_low:
                        continue
                    # fall through to delete
        except Exception as e:
            logger.debug("notif edit failed for admin %s msg %s: %s",
                         admin_id_str, message_id, e)
        # Fallback: delete the notification message so at least the stale
        # "awaiting your review" prompt is gone from the admin's chat.
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.debug("notif delete failed for admin %s msg %s: %s",
                         admin_id_str, message_id, e)


class AdminGuard:
    """Enforce admin / payment-admin access on the admin router.

    * Full admins (``ADMIN_IDS`` env) — unrestricted access to every admin
      callback and the ``/admin`` command.
    * Payment admins (``payment_admin_ids`` DB setting) — restricted to
      payment-related callbacks only: ``admin:main``, ``admin:pending_payments``,
      ``pay:*`` (view/approve/reject), and ``menu:main`` (back to user menu).
      Every other admin callback is rejected with an inline toast.
    """

    # Callback-data prefixes (raw event.data strings) that payment admins may
    # invoke. AdminCB packs as "admin:<action>:...", PaymentCB as "pay:...",
    # MenuCB as "menu:<action>".
    # PAY-HISTORY-REWORK: payment admins may now view their own approval
    # history (admin:my_history). The per-admin picker (admin:admin_payments)
    # and the all-receipts view (admin:payment_history) are FULL-admin-only —
    # they're stripped out at the handler level even if a payment admin
    # somehow crafts the callback data, because the handler re-checks
    # _is_full_admin before rendering.
    _PAYMENT_ALLOWED_PREFIXES = (
        "admin:main",
        "admin:pending_payments",
        "admin:payment_history",
        "admin:my_history",
        "pay:",
        "menu:main",
    )

    def __init__(self, db: "Database"):
        self._db = db

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user:
            return
        # Full admin — unrestricted.
        if user.id in ADMIN_IDS:
            return await handler(event, data)
        # Payment admin?
        pa_ids = await get_payment_admin_ids(self._db)
        if user.id not in pa_ids:
            if isinstance(event, CallbackQuery):
                await event.answer(t("admin_only", "en"), show_alert=True)
            elif isinstance(event, Message):
                await event.answer(t("admin_only", "en"))
            return
        # Payment admin — restrict to payment-related callbacks.
        if isinstance(event, CallbackQuery):
            raw = event.data or ""
            if not any(raw.startswith(p) for p in self._PAYMENT_ALLOWED_PREFIXES):
                await event.answer(
                    "⛔ Access denied — payment admins can only manage payments.",
                    show_alert=True,
                )
                return
        return await handler(event, data)


# ============================================================================
# SECTION 10: USER HANDLERS
# ============================================================================

def create_user_router(db: Database, api: PanelAPI, lb: LoadBalancer, bot: Bot) -> Router:
    """All customer-facing handlers: purchase, accounts, trial, balance,
    referral, gift, support, guide, language switching, QR codes, labels."""
    router = Router()

    def _lang(db_user: Optional[dict]) -> str:
        return L((db_user or {}).get("language", DEFAULT_LANGUAGE))

    async def _currency() -> str:
        return await db.get_setting("currency", DEFAULT_CURRENCY) or DEFAULT_CURRENCY

    async def _can_access_admin(tg_id: int) -> bool:
        """True if user is a full admin OR a payment-only admin (so the
        'Admin Panel' button shows in their main menu)."""
        if tg_id in ADMIN_IDS:
            return True
        return tg_id in await get_payment_admin_ids(db)

    # ---------------------------------------------------------------- /start
    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext, db_user: dict):
        await state.clear()
        # Check if user needs to select language first
        if not db_user.get("language_selected", 0) and message.from_user.id not in ADMIN_IDS:
            await state.set_state(UserStates.waiting_for_language_on_start)
            await track_prompt(await message.answer(
                "🌐 <b>Please select your language / لطفاً زبان خود را انتخاب کنید:</b>",
                reply_markup=kb_language("en"),
            ), state)
            return
        lang = _lang(db_user)
        me = await bot.get_me()
        await message.answer(
            t("welcome", lang, bot_name=f"@{me.username}"),
            reply_markup=kb_main_menu(await _can_access_admin(message.from_user.id), lang),
        )

    # ---------------------------------------------------------------- /help
    @router.message(Command("help"))
    async def cmd_help(message: Message, db_user: dict):
        lang = _lang(db_user)
        # GUIDES: /help now shows the usage guide (admin-configurable, falls
        # back to the rich default). This replaces the old short help_text.
        usage = await db.get_setting(f"guide_usage_{lang}", "")
        if not (usage and usage.strip()):
            usage = DEFAULT_GUIDE_USAGE_FA if lang == "fa" else DEFAULT_GUIDE_USAGE_EN
        await message.answer(usage,
                             reply_markup=kb_main_menu(await _can_access_admin(message.from_user.id), lang))

    # ------------------------------------------------------------ /language
    @router.message(Command("language"))
    async def cmd_language(message: Message, db_user: dict):
        await message.answer(t("lang_title", _lang(db_user)),
                             reply_markup=kb_language(_lang(db_user)))

    # ------------------------------------------------------------ /cancel
    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext, db_user: dict):
        await state.clear()
        lang = _lang(db_user)
        await message.answer(t("cancelled_action", lang),
                             reply_markup=kb_main_menu(await _can_access_admin(message.from_user.id), lang))

    # ---------------------------------------------------- main menu buttons
    @router.callback_query(MenuCB.filter(F.action == "main"))
    async def cb_main_menu(callback: CallbackQuery, state: FSMContext, db_user: dict):
        await state.clear()
        lang = _lang(db_user)
        await show_view(callback.message, text=t("menu_main", lang),
                         reply_markup=kb_main_menu(await _can_access_admin(callback.from_user.id), lang))
        await callback.answer()

    @router.callback_query(MenuCB.filter(F.action == "cancel"))
    async def cb_cancel(callback: CallbackQuery, state: FSMContext, db_user: dict):
        await state.clear()
        lang = _lang(db_user)
        await show_view(callback.message, text=t("action_cancelled", lang),
                         reply_markup=kb_main_menu(await _can_access_admin(callback.from_user.id), lang))
        await callback.answer()

    # ------------------------------------------------------- language picker
    @router.callback_query(MenuCB.filter(F.action == "language"))
    async def cb_language(callback: CallbackQuery, db_user: dict):
        await callback.message.edit_text(t("lang_title", _lang(db_user)),
                                         reply_markup=kb_language(_lang(db_user)))
        await callback.answer()

    # -------------------------------------------------- MENU-RESTRUCTURE
    @router.callback_query(MenuCB.filter(F.action == "help"))
    async def cb_help(callback: CallbackQuery, db_user: dict):
        """Merged Help & Support submenu (MENU-RESTRUCTURE).

        Replaces the old separate "Support" and "Guide" main-menu buttons
        with a single screen that offers both guides and ticket actions.
        """
        lang = _lang(db_user)
        is_admin = await _can_access_admin(callback.from_user.id)
        text = f"{t('help_title', lang)}\n\n{t('help_desc', lang)}"
        kb = InlineKeyboardBuilder()
        kb.button(text=t("guide_usage_btn", lang),
                  callback_data=MenuCB(action="guide_usage").pack(), style="primary")
        kb.button(text=t("guide_connection_btn", lang),
                  callback_data=MenuCB(action="guide_connection").pack(), style="primary")
        kb.button(text=t("new_ticket", lang), callback_data=MenuCB(action="new_ticket").pack(), style="success")
        kb.button(style="primary", text=t("my_tickets", lang), callback_data=MenuCB(action="my_tickets").pack())
        if is_admin and callback.from_user.id in ADMIN_IDS:
            open_count = await db.count_open_tickets()
            badge = f" ({open_count})" if open_count else ""
            kb.button(style="primary", text=f"🛡 Tickets{badge}", callback_data=AdminCB(action="tickets").pack())
        kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack(), style="primary")
        kb.adjust(2, 2, 1)
        await show_view(callback.message, text=text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(MenuCB.filter(F.action == "more_features"))
    async def cb_more_features(callback: CallbackQuery, db_user: dict):
        """More Features submenu (MENU-RESTRUCTURE).

        Holds secondary settings (Language, and Admin Panel for admins) so
        the main menu stays uncluttered.
        """
        lang = _lang(db_user)
        is_admin = await _can_access_admin(callback.from_user.id)
        await show_view(callback.message, text=t("more_features_title", lang),
                        reply_markup=kb_more_features(lang, is_admin=is_admin))
        await callback.answer()

    @router.callback_query(LangCB.filter())
    async def cb_set_language(callback: CallbackQuery, callback_data: LangCB, state: FSMContext, db_user: dict):
        lang = L(callback_data.code)
        await db.update_user_language(callback.from_user.id, lang)
        await db.update_language_selected(callback.from_user.id, True)
        await state.clear()
        me = await bot.get_me()

        # FORCE-JOIN RE-CHECK: after setting the language, verify the user has
        # joined all required channels.  If force-join is enabled and the user
        # hasn't joined yet, re-show the force-join prompt (in the newly-
        # selected language) instead of the main menu — otherwise language
        # selection would be a force-join bypass.  Admins skip this check.
        if callback.from_user.id not in ADMIN_IDS:
            fj_enabled = await db.get_setting_int("force_join_enabled", 0)
            if fj_enabled:
                channels = await db.get_setting_json("force_join_channels", [])
                if channels:
                    not_joined = []
                    for ch in channels:
                        chat_id = ch.get("chat_id")
                        if chat_id:
                            try:
                                member = await bot.get_chat_member(int(chat_id), callback.from_user.id)
                                if member.status not in ("member", "administrator", "creator"):
                                    not_joined.append(ch)
                            except (TelegramBadRequest, TelegramForbiddenError):
                                not_joined.append(ch)
                            except Exception as e:
                                logger.warning("lang-set force_join check failed for %s: %s", ch, e)
                                not_joined.append(ch)
                    if not_joined:
                        channels_text = ""
                        for ch in not_joined:
                            username = ch.get("username", "")
                            title = ch.get("title", "")
                            if username:
                                channels_text += f"• @{username}\n"
                            elif title:
                                channels_text += f"• {title}\n"
                        kb = InlineKeyboardBuilder()
                        for ch in not_joined:
                            username = ch.get("username", "")
                            if username:
                                kb.button(style="primary", text=f"📢 {username}", url=f"https://t.me/{username}")
                        kb.button(text=t("verify_join", lang), callback_data=ForceJoinCB(action="verify").pack(), style="success")
                        kb.button(style="primary", text=t("language", lang), callback_data=MenuCB(action="language").pack())
                        kb.adjust(1, 2)
                        await callback.message.edit_text(
                            t("lang_set", lang) + "\n\n" + t("force_join", lang, channels=channels_text),
                            reply_markup=kb.as_markup(),
                        )
                        await callback.answer()
                        return

        # Normal flow: show welcome + main menu
        await callback.message.edit_text(
            t("lang_set", lang) + "\n\n" + t("welcome", lang, bot_name=f"@{me.username}"),
            reply_markup=kb_main_menu(await _can_access_admin(callback.from_user.id), lang),
        )
        await callback.answer()

    # ---- Force join verification ----
    @router.callback_query(ForceJoinCB.filter(F.action == "verify"))
    async def cb_force_join_verify(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        fj_enabled = await db.get_setting_int("force_join_enabled", 0)
        if not fj_enabled:
            # Force join disabled, let user proceed
            await callback.message.edit_text(
                t("welcome", lang, bot_name=f"@{(await bot.get_me()).username}"),
                reply_markup=kb_main_menu(await _can_access_admin(callback.from_user.id), lang),
            )
            await callback.answer()
            return
        channels = await db.get_setting_json("force_join_channels", [])
        not_joined = []
        for ch in channels:
            chat_id = ch.get("chat_id")
            if chat_id:
                try:
                    member = await bot.get_chat_member(int(chat_id), callback.from_user.id)
                    if member.status not in ("member", "administrator", "creator"):
                        not_joined.append(ch)
                except TelegramBadRequest as e:
                    # L11 — Bot likely lost admin in this channel (or chat was
                    # deleted / never existed). Previously this branch was
                    # fail-CLOSED: the user was treated as "not joined" and
                    # blocked. That is hostile to legitimate users when the
                    # admin forgets to re-promote the bot after a channel
                    # migration. Fail OPEN instead: log + alert admins so
                    # they can re-add the bot, but let the user through.
                    logger.warning(
                        "force_join check failed for channel %s: %s — failing open",
                        ch, e,
                    )
                    for admin_id in ADMIN_IDS:
                        await safe_notify(
                            bot.send_message(
                                admin_id,
                                f"⚠️ <b>Force-join check failed</b>\n"
                                f"Channel: <code>{escape_html(str(ch))}</code>\n"
                                f"Error: {escape_html(str(e))[:200]}\n"
                                f"User was allowed through (fail-open). "
                                f"Please re-add the bot as admin.",
                            ),
                            context="force_join fail-open alert",
                        )
                except TelegramForbiddenError as e:
                    # Same rationale — fail OPEN and alert admins.
                    logger.warning(
                        "force_join check forbidden for channel %s: %s — failing open",
                        ch, e,
                    )
                    for admin_id in ADMIN_IDS:
                        await safe_notify(
                            bot.send_message(
                                admin_id,
                                f"⚠️ <b>Force-join check failed</b>\n"
                                f"Channel: <code>{escape_html(str(ch))}</code>\n"
                                f"Error: {escape_html(str(e))[:200]}\n"
                                f"User was allowed through (fail-open). "
                                f"Please re-add the bot as admin.",
                            ),
                            context="force_join fail-open alert",
                        )
                except Exception as e:
                    # Unexpected error (network blip, Telegram 5xx, …). Same
                    # fail-open policy — better to let a real user in than to
                    # lock out 100% of new users during a Telegram outage.
                    logger.error(
                        "force_join check error for %s: %s — failing open",
                        ch, e, exc_info=True,
                    )
        if not_joined:
            # FORCE-JOIN-RENDER: re-render the force-join prompt with the list
            # of channels the user STILL hasn't joined, so they can see exactly
            # what they need to do.  Previously this only showed a brief popup
            # alert ("❌ You haven't joined...") which disappeared quickly and
            # didn't list the specific channels — the user had no persistent
            # reference.  Now we show the full prompt (with channel links +
            # "✅ I Joined" button + language button) AND the popup alert.
            channels_text = ""
            for ch in not_joined:
                username = ch.get("username", "")
                title = ch.get("title", "")
                if username:
                    channels_text += f"• @{username}\n"
                elif title:
                    channels_text += f"• {title}\n"
            kb = InlineKeyboardBuilder()
            for ch in not_joined:
                username = ch.get("username", "")
                if username:
                    kb.button(style="primary", text=f"📢 {username}", url=f"https://t.me/{username}")
            kb.button(text=t("verify_join", lang), callback_data=ForceJoinCB(action="verify").pack(), style="success")
            kb.button(style="primary", text=t("language", lang), callback_data=MenuCB(action="language").pack())
            kb.adjust(1, 2)
            try:
                # FORCE-JOIN-FEEDBACK: use force_join_not_joined (starts with
                # ❌) instead of the original force_join prompt (starts with
                # 🔒).  The different header guarantees the message content
                # changes, so Telegram's edit_text won't hit "message is not
                # modified" (which show_view swallows silently) — the user
                # always sees a visible "you haven't joined" response.
                await show_view(callback.message,
                                text=t("force_join_not_joined", lang, channels=channels_text),
                                reply_markup=kb.as_markup())
            except Exception as e:
                logger.warning("force-join re-render failed: %s", e)
            await callback.answer(t("force_join_failed", lang), show_alert=True)
            return
        # All channels joined
        await callback.message.edit_text(
            t("force_join_success", lang) + "\n\n" + t("welcome", lang, bot_name=f"@{(await bot.get_me()).username}"),
            reply_markup=kb_main_menu(await _can_access_admin(callback.from_user.id), lang),
        )
        await callback.answer()

    # ============================================================ BUY FLOW
    @router.callback_query(MenuCB.filter(F.action == "buy"))
    async def cb_buy(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        plans = await db.get_plans(active_only=True)
        if not plans:
            await callback.message.edit_text(t("no_plans", lang), reply_markup=kb_back_to_menu(lang))
            await callback.answer()
            return
        await callback.message.edit_text(t("choose_plan", lang),
                                         reply_markup=kb_plans(plans, lang, await _currency()))
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "view"))
    async def cb_plan_view(callback: CallbackQuery, callback_data: PlanCB,
                            state: FSMContext, db_user: dict):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer(t("not_found", _lang(db_user)), show_alert=True)
            return
        # UX-SKIP-BUY-STEP: selecting a plan from the list used to land on
        # an intermediate "plan detail" page whose only real action was a
        # "Buy Service" button that then opened the unified review page.
        # That extra tap added friction without adding information (the
        # review page already shows the plan card, balance, and fair-use
        # warning). Now selecting a plan goes straight to the review page,
        # performing the same state setup as cb_buy_start so the rest of
        # the purchase flow (name / promo / confirm) works unchanged.
        # NOTE: cb_buy_start is still wired up because the post-payment
        # "resume" button (line ~10077) reuses BuyCB(action="start",
        # step="resume") to drop the user back on the review page after a
        # shortfall payment is approved.
        await state.clear()
        await state.update_data(plan_id=callback_data.plan_id, account_name="",
                                promo_code="", final_price=plan["price"])
        await _render_purchase_review(callback.message, state, db_user, plan)
        await callback.answer()

    # ====================================================== PURCHASE REVIEW
    # UI-REDESIGN: a single review page replaces the old two-path flow.
    # The user picks a plan → lands on the review page → can set a name,
    # add a promo, or confirm payment, all from one place. This fixes the
    # old bug where the promo path skipped the name step.

    async def _render_purchase_review(message: Message, state: FSMContext, db_user: dict,
                                       plan: dict) -> Message:
        """Render the unified purchase review page.

        Pulls plan_id, account_name (optional), promo_code + final_price
        (optional) from FSM state and shows everything in one place:
        plan card, name, promo, price breakdown, balance, and the
        collapsible fair-use warning.
        """
        lang = _lang(db_user)
        currency = await _currency()
        data = await state.get_data()
        account_name = data.get("account_name", "")
        promo_code = data.get("promo_code")
        final_price = data.get("final_price", plan["price"])
        discount = max(0.0, plan["price"] - final_price)
        balance = db_user.get("balance", 0)

        text = fmt_plan_card(plan, lang, currency)
        text += f"\n\n{t('review_purchase', lang)}\n"
        # Name line
        name_disp = escape_html(account_name) if account_name else t("name_auto", lang)
        text += f"{t('name_label', lang)}: {name_disp}\n"
        # Promo line
        if promo_code and discount > 0:
            text += f"{t('promo_label', lang)}: <code>{escape_html(promo_code)}</code>\n"
            text += f"{t('discount_label', lang)}: {fmt_price(discount, lang, currency)}\n"
            text += f"{t('final_price_label', lang)}: <b>{fmt_price(final_price, lang, currency)}</b>\n"
        else:
            text += f"{t('promo_label', lang)}: {t('promo_none', lang)}\n"
            text += f"{t('final_price_label', lang)}: <b>{fmt_price(final_price, lang, currency)}</b>\n"
        # Balance line
        text += f"{t('your_balance', lang, balance=fmt_price(balance, lang, currency))}\n"
        # Whether card payments are enabled — controls which top-up button(s)
        # appear below AND which hint we show. Fetched up-front so the hint
        # logic can be context-aware instead of showing a contradictory hint.
        payment_enabled = await db.get_setting_int("payment_enabled", 0)
        if balance >= final_price:
            text += t("sufficient", lang)
        else:
            text += t("insufficient", lang, diff=fmt_price(final_price - balance, lang, currency))
            # PURCHASE-UX-2 + UX-HINT-MATCHES-BUTTONS: the hint must match the
            # buttons that actually appear below. When card payments are ON,
            # the first/primary button is "Pay Exact Shortfall" — the hint
            # points at it explicitly (and mentions gift code as an alt).
            # When card payments are OFF, there's no shortfall button, so we
            # show the payment_disabled_gift_only notice instead — showing
            # "pay the shortfall" here would point at a non-existent button.
            if payment_enabled:
                text += f"\n{t('review_short_hint', lang)}"
            else:
                text += f"\n{t('payment_disabled_gift_only', lang)}"
        # Collapsible warning
        warning = PURCHASE_WARNING_FA if lang == "fa" else PURCHASE_WARNING_EN
        text += f"\n<blockquote expandable>{warning}</blockquote>"
        kb = kb_purchase_review(plan["id"], lang,
                                has_name=bool(account_name),
                                has_promo=bool(promo_code and discount > 0),
                                can_afford=balance >= final_price,
                                payment_enabled=bool(payment_enabled))
        await state.set_state(None)  # leave FSM but keep data
        return await show_view(message, text=text, reply_markup=kb)

    # Step 1 — enter the review page directly (no name ask first).
    @router.callback_query(BuyCB.filter(F.action == "start"))
    async def cb_buy_start(callback: CallbackQuery, callback_data: BuyCB, state: FSMContext, db_user: dict):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer(t("not_found", _lang(db_user)), show_alert=True)
            return
        # BUG-13 FIX: the "resume" path (post-shortfall-payment approval)
        # used to call state.clear() + reset final_price=plan["price"],
        # discarding any applied promo discount. If the user's post-approval
        # balance covered the DISCOUNTED price but not the FULL price, they'd
        # be back to "can't afford" and have to re-apply the promo — and if
        # the promo had expired / hit max_uses in the meantime, they'd be
        # stuck having paid the shortfall but unable to buy. Fix: when
        # step=="resume", preserve any existing promo_code + final_price
        # (and account_name) from the state instead of wiping them. If the
        # state is empty/stale (bot restarted, user hit /cancel), fall back
        # to the fresh-seed behavior.
        if callback_data.step == "resume":
            existing = await state.get_data()
            # Only trust the existing state if it's for the SAME plan and
            # actually has a final_price (not an empty placeholder).
            if (existing.get("plan_id") == callback_data.plan_id
                    and existing.get("final_price") is not None):
                # State is fresh and matches — keep promo_code, final_price,
                # account_name as-is. Just re-render.
                await _render_purchase_review(callback.message, state, db_user, plan)
                await callback.answer()
                return
            # else: state was cleared/stale — fall through to fresh seed.
        # Fresh seed for a new purchase (or a resume with stale state).
        await state.clear()
        await state.update_data(plan_id=callback_data.plan_id, account_name="",
                                promo_code="", final_price=plan["price"])
        await _render_purchase_review(callback.message, state, db_user, plan)
        await callback.answer()

    # Step 2a — optional: set a custom account name.
    @router.callback_query(BuyCB.filter(F.action == "set_name"))
    async def cb_buy_name(callback: CallbackQuery, state: FSMContext, callback_data: BuyCB, db_user: dict):
        lang = _lang(db_user)
        # Preserve existing state (plan_id, promo, etc.) — only set the FSM
        # state to waiting_for_account_name so ms_account_name can fire.
        await state.set_state(UserStates.waiting_for_account_name)
        await state.update_data(plan_id=callback_data.plan_id)
        await callback.message.edit_text(t("ask_account_name", lang), reply_markup=kb_cancel(lang))
        await track_prompt(callback.message, state)
        await callback.answer()

    @router.message(UserStates.waiting_for_account_name)
    async def ms_account_name(message: Message, state: FSMContext, db_user: dict):
        await del_inbound(message, state)
        lang = _lang(db_user)
        name = sanitize_name(message.text or "")
        if name is None:
            await message.answer(t("invalid_name", lang), reply_markup=kb_cancel(lang))
            return
        await state.update_data(account_name=name)
        data = await state.get_data()
        plan = await db.get_plan(data["plan_id"])
        if not plan:
            await state.clear()
            await message.answer(t("not_found", lang), reply_markup=kb_back_to_menu(lang))
            return
        await _render_purchase_review(message, state, db_user, plan)

    # Step 2b — optional: apply a promo code.
    @router.callback_query(BuyCB.filter(F.action == "promo"))
    async def cb_buy_promo(callback: CallbackQuery, state: FSMContext, callback_data: BuyCB, db_user: dict):
        lang = _lang(db_user)
        await state.set_state(UserStates.waiting_for_promo_code)
        await state.update_data(plan_id=callback_data.plan_id)
        await callback.message.edit_text(t("enter_promo", lang), reply_markup=kb_cancel(lang))
        await track_prompt(callback.message, state)
        await callback.answer()

    @router.message(UserStates.waiting_for_promo_code)
    async def ms_promo_code(message: Message, state: FSMContext, db_user: dict):
        await del_inbound(message, state)
        lang = _lang(db_user)
        code = (message.text or "").strip().upper()
        promo = await db.validate_promo_code(code)
        if not promo:
            await message.answer(t("promo_invalid", lang), reply_markup=kb_cancel(lang))
            return
        data = await state.get_data()
        plan = await db.get_plan(data.get("plan_id"))
        if not plan:
            await state.clear()
            await message.answer(t("not_found", lang), reply_markup=kb_back_to_menu(lang))
            return
        currency = await _currency()
        discount = 0.0
        if promo["discount_percent"] > 0:
            discount = plan["price"] * promo["discount_percent"] / 100
        elif promo["discount_amount"] > 0:
            discount = promo["discount_amount"]
        final_price = max(0.0, plan["price"] - discount)
        await state.update_data(promo_code=code, final_price=final_price)
        await _render_purchase_review(message, state, db_user, plan)

    # Step 2c — GIFT CODE (purchase context). UX-GIFT-IN-PURCHASE: a gift-code
    # button on the review page lets the user top up their wallet without
    # leaving the purchase. Only BALANCE-type gift codes are accepted here
    # (plan-type codes are rejected with a clear message — they're not a
    # wallet top-up). After a successful balance redemption the user is sent
    # straight back to the SAME plan's review page so they can continue.
    @router.callback_query(BuyCB.filter(F.action == "gift"))
    async def cb_buy_gift(callback: CallbackQuery, state: FSMContext,
                           callback_data: BuyCB, db_user: dict):
        lang = _lang(db_user)
        # Preserve existing state (plan_id, account_name, promo, final_price)
        # — only switch the FSM state so ms_purchase_gift_code can fire.
        await state.update_data(plan_id=callback_data.plan_id)
        await state.set_state(UserStates.waiting_for_purchase_gift_code)
        await callback.message.edit_text(
            t("gift_in_purchase_hint", lang), reply_markup=kb_cancel(lang))
        await track_prompt(callback.message, state)
        await callback.answer()

    @router.message(UserStates.waiting_for_purchase_gift_code)
    async def ms_purchase_gift_code(message: Message, state: FSMContext, db_user: dict):
        await del_inbound(message, state)
        lang = _lang(db_user)
        code = (message.text or "").strip().upper()
        gift = await db.get_gift_code(code)
        if not gift:
            await message.answer(t("gift_invalid", lang), reply_markup=kb_cancel(lang))
            return
        if gift["is_used"]:
            await message.answer(t("gift_used_code", lang), reply_markup=kb_cancel(lang))
            return
        # UX-GIFT-IN-PURCHASE: reject plan-type codes BEFORE claiming. This
        # section is a wallet top-up channel, so only balance-type codes
        # make sense. The code stays unused so the owner can redeem it from
        # the main-menu Wallet → Gift Code flow instead.
        if gift["type"] != "balance":
            await message.answer(
                t("gift_plan_not_allowed_in_purchase", lang),
                reply_markup=kb_cancel(lang))
            return
        # C3 — atomic claim. Try to mark the code as used BEFORE granting the
        # reward. If two parallel redemptions both pass the is_used check
        # above, only one wins the UPDATE row and proceeds.
        if not await db.use_gift_code(code, message.from_user.id):
            await message.answer(t("gift_used_code", lang), reply_markup=kb_cancel(lang))
            return
        # Grant balance. We do NOT call state.clear() here — the plan_id,
        # account_name, promo_code, final_price must survive so we can drop
        # the user back on the same review page.
        amount = float(gift["value"])
        # BUG-14 FIX: wrap claim + grant in a single transaction. If the grant
        # fails, the transaction rolls back the use_gift_code UPDATE too — so
        # the code stays unused and the user can retry. Without this, a
        # transient DB error would consume the code without crediting balance.
        try:
            async with db.transaction():
                await db.update_user_balance(message.from_user.id, amount, add=True)
                await db.add_transaction(message.from_user.id, amount, "gift_balance", f"Gift: {code}")
        except Exception as e:
            logger.error("purchase gift balance grant failed (%s) — rolled back, code %s remains unused", e, code)
            await message.answer(
                t("gift_invalid", lang),  # generic "try again" — code is still valid
                reply_markup=kb_cancel(lang))
            return
        currency = await _currency()
        # Re-fetch the user so the balance shown on the review page reflects
        # the top-up (db_user passed in was read at request start, before the
        # balance changed).
        fresh_user = await db.get_user(message.from_user.id) or db_user
        data = await state.get_data()
        plan = await db.get_plan(data.get("plan_id"))
        if not plan:
            # Plan vanished mid-flow — can't return to the review page, so
            # confirm the redemption and send the user to the main menu.
            await state.clear()
            await message.answer(
                t("gift_balance_ok", lang, amount=fmt_price(amount, lang, currency)),
                reply_markup=kb_back_to_menu(lang))
            return
        # Confirm the top-up, then drop them back on the review page for the
        # same plan. The review page re-evaluates can_afford with the fresh
        # balance, so if the gift was enough the "Confirm & Pay" button will
        # now appear.
        await message.answer(
            t("gift_balance_ok_back_to_purchase", lang,
              amount=fmt_price(amount, lang, currency)))
        await _render_purchase_review(message, state, fresh_user, plan)

    # Step 2d — SHORTFALL-REQUEST: user can't afford the plan, so they request
    # a payment for exactly the missing amount. After admin approval the user
    # gets a one-tap "Buy Now" button to resume this purchase without having
    # to navigate back to the plan list.
    @router.callback_query(BuyCB.filter(F.action == "shortfall"))
    async def cb_buy_shortfall(callback: CallbackQuery, callback_data: BuyCB,
                                state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        # Payment must be enabled for the shortfall flow to make sense.
        if not await db.get_setting_int("payment_enabled", 0):
            await callback.answer(t("payment_disabled", lang), show_alert=True)
            return
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        data = await state.get_data()
        final_price = data.get("final_price", plan["price"])
        balance = db_user.get("balance", 0)
        # Shortfall = amount the user still needs. Clamp to >= 1 so we don't
        # create a zero-amount payment (which the panel/admin would reject).
        shortfall = max(1, math.ceil(final_price - balance))
        # Small 3-digit suffix (100–999) — same collision-avoidance as the
        # normal charge-wallet flow (C6). Keeps the surcharge tiny so the
        # user never pays thousands extra on top of the shortfall.
        suffix = secrets.randbelow(PAYMENT_UNIQUE_SUFFIX_RANGE) + PAYMENT_UNIQUE_SUFFIX_MIN
        unique_amount = shortfall + suffix
        # Stash everything ms_receipt needs, plus the plan_id so we can tag
        # the payment record with resume_plan_id.
        await state.update_data(
            original_amount=shortfall,
            unique_amount=unique_amount,
            shortfall_plan_id=plan["id"],
            shortfall_plan_name=plan["name"],
        )
        # CARD-RTL: wrap with ltr() so the digit groups keep their left-to-right
        # order inside an RTL (Farsi) paragraph. Without this, "6037 9919 2616
        # 0239" is visually reversed to "0239 2616 9919 6037".
        card_number = ltr(await db.get_setting("payment_card_number", "-"))
        card_holder = await db.get_setting("payment_card_holder", "-")
        currency = await _currency()
        # Show the user a clear "you need to pay exactly this" message with
        # the plan name so they remember what the shortfall is for.
        if lang == "fa":
            extra = (
                f"\n\n💡 این مبلغ دقیقاً برای خرید پلن «{escape_html(plan['name'])}» است. "
                f"بعد از تأیید ادمین، موجودیت شارژ میشه و می‌تونی همین پلن رو با یه کلیک بخری."
            )
        else:
            extra = (
                f"\n\n💡 This amount is exactly what you need for the \"{escape_html(plan['name'])}\" plan. "
                f"After admin approval, your balance will be topped up and you can buy this plan in one tap."
            )
        await callback.message.edit_text(
            t("shortfall_payment_info", lang,
              plan_name=escape_html(plan["name"]),
              shortfall=fmt_price(shortfall, lang, currency),
              card_number=card_number, card_holder=card_holder,
              amount_block=_amount_block(unique_amount, currency, lang))
            + extra,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(style="success", text=t("send_receipt", lang),
                                      callback_data=PaymentCB(action="send_receipt", amount=0).pack())],
                [InlineKeyboardButton(style="danger", text=t("back", lang),
                                      callback_data=BuyCB(action="start", plan_id=plan["id"], step="review").pack())],
            ]),
        )
        await callback.answer()

    # Step 3 — execute the purchase.
    @router.callback_query(BuyCB.filter(F.action == "confirm"))
    async def cb_buy_confirm(callback: CallbackQuery, callback_data: BuyCB,
                             state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        data = await state.get_data()
        await state.clear()
        final_price = data.get("final_price", plan["price"])
        promo_code = data.get("promo_code")
        account_name = data.get("account_name", "")

        # Friendly pre-check for instant feedback (the authoritative check is
        # the atomic try_deduct_balance below — C8 — so this race-free even if
        # we lie here briefly).
        balance = db_user.get("balance", 0)
        if balance < final_price:
            await callback.answer(
                t("insufficient", lang, diff=fmt_price(final_price - balance, lang, await _currency())),
                show_alert=True,
            )
            return

        await callback.message.edit_text(t("creating_account", lang))
        currency = await _currency()

        # Choose server among those referenced by the plan's inbounds (or any)
        allowed_servers = lb.plan_server_ids(plan)
        server = await lb.select_best_server(allowed_servers or None)
        if not server:
            await callback.message.edit_text(t("no_servers", lang), reply_markup=kb_back_to_menu(lang))
            return

        inbound_ids = await lb.select_inbounds_for_plan(server, plan)
        if not inbound_ids:
            await callback.message.edit_text(t("no_inbounds_configured", lang), reply_markup=kb_back_to_menu(lang))
            return

        email = gen_email(callback.from_user.id, account_name)
        expiry_time = (int((datetime.now(timezone.utc) + timedelta(days=plan["duration_days"])).timestamp() * 1000)
                       if plan["duration_days"] > 0 else 0)

        # C8 — atomic check-and-deduct balance. This replaces the
        # read-then-write pattern (`if balance < price: return; await
        # update_user_balance(..., add=False)`) which had a TOCTOU race
        # window allowing two parallel purchases to both succeed.
        if not await db.try_deduct_balance(callback.from_user.id, final_price):
            await callback.message.edit_text(
                t("insufficient", lang, diff=fmt_price(final_price, lang, currency)),
                reply_markup=kb_back_to_menu(lang),
            )
            return

        sub_id = gen_sub_id()
        result = await api.create_client(
            panel_url=server["panel_url"], token=server["api_token"],
            email=email, inbound_ids=inbound_ids,
            total_gb=plan["traffic_gb"], expiry_time=expiry_time,
            limit_ip=plan.get("limit_ip", 0), tg_id=callback.from_user.id,
            sub_id=sub_id,
        )
        if not result.get("success"):
            # C7 — COMPENSATION: the panel call failed AFTER we deducted the
            # balance. We must credit it back so the user isn't charged for
            # an account they never received.
            await db.update_user_balance(callback.from_user.id, final_price, add=True)
            await callback.message.edit_text(
                t("purchase_failed", lang, msg=result.get("msg", "error")),
                reply_markup=kb_back_to_menu(lang),
            )
            return

        links = await api.get_client_links(server["panel_url"], server["api_token"], email)

        # C7 — wrap the multi-step DB writes in a single transaction. If any
        # step fails, all of them roll back (no partial state: e.g. an account
        # row without a transaction record, or vice versa).
        try:
            async with db.transaction():
                await db.add_account(
                    user_tg_id=callback.from_user.id, server_id=server["id"], email=email,
                    sub_id=sub_id, plan_id=plan["id"], traffic_gb=plan["traffic_gb"],
                    expiry_time=expiry_time, limit_ip=plan.get("limit_ip", 0),
                    inbound_ids=json.dumps(inbound_ids), is_trial=False, label=account_name,
                )
                if promo_code:
                    await db.use_promo_code(promo_code)
                await db.add_transaction(
                    user_tg_id=callback.from_user.id, amount=final_price, type_="purchase",
                    description=f"Plan: {plan['name']}", account_email=email, plan_id=plan["id"],
                )
                await db.clear_traffic_alerts(email)
                await db.clear_expiry_reminders(email)
        except Exception as e:
            # C7 — COMPENSATION: DB write failed AFTER the panel client was
            # already created. Delete the orphaned panel client and refund
            # the balance so the user isn't left with a charge for nothing.
            logger.error("purchase DB write failed (%s) — compensating", e)
            try:
                await api.delete_client(server["panel_url"], server["api_token"], email)
            except Exception as del_err:
                logger.error("compensation delete_client failed: %s", del_err)
            await db.update_user_balance(callback.from_user.id, final_price, add=True)
            await callback.message.edit_text(
                t("purchase_failed", lang, msg="Internal error, please try again."),
                reply_markup=kb_back_to_menu(lang),
            )
            return

        # C2 — Atomic referral eligibility claim. mark_referral_rewarded
        # returns True only if THIS call performed the transition; if a
        # parallel purchase already claimed, we skip entirely.
        #
        # REFERRAL-CLAIM (new logic): we NO LONGER auto-apply the bonus to
        # the referrer's account at purchase time. Instead, we just mark the
        # referred user as eligible (referral_rewarded=1). The referrer must
        # press "Claim Reward" in the referral section — and they must have
        # their own active paid account to receive it. This ensures the
        # referrer is also a paying customer, and gives them control over
        # which account receives the bonus.
        if db_user.get("referred_by") and not db_user.get("referral_rewarded"):
            won_claim = await db.mark_referral_rewarded(callback.from_user.id)
            referrer_id = db_user["referred_by"]
            ref_enabled = await db.get_setting_int("referral_enabled", 1)
            if won_claim and ref_enabled:
                bonus_days = await db.get_setting_int("referral_bonus_days", 0)
                bonus_gb = await db.get_setting_float("referral_bonus_gb", 0)
                # Notify the referrer they have a claimable reward — don't
                # apply it yet. They need to visit Referral → Claim Reward.
                # LOCALIZATION: send the notification in the REFERRER's
                # language (not the buyer's), since the referrer is the one
                # receiving this message.
                try:
                    referrer_row = await db.get_user(referrer_id)
                    ref_lang = L((referrer_row or {}).get("language", DEFAULT_LANGUAGE))
                    if ref_lang == "fa":
                        notify_text = (
                            f"🎉 <b>پاداش دعوت در انتظار شماست!</b>\n\n"
                            f"یک دوست با لینک شما پلن خرید کرد.\n"
                        )
                        if bonus_days > 0 or bonus_gb > 0:
                            notify_text += (
                                f"🎁 حالا می‌تونی <b>+{fmt_days(bonus_days, ref_lang)}</b> و "
                                f"<b>+{fmt_gb(bonus_gb, ref_lang)}</b> روی اکانتت دریافت کنی.\n\n"
                                f"باز کن <b>🔗 دعوت</b> → بزن <b>دریافت پاداش</b>."
                            )
                        else:
                            notify_text += "باز کن 🔗 دعوت برای دیدن آمار."
                    else:
                        notify_text = (
                            f"🎉 <b>Referral reward waiting!</b>\n\n"
                            f"A friend just bought a plan using your link.\n"
                        )
                        if bonus_days > 0 or bonus_gb > 0:
                            notify_text += (
                                f"🎁 You can now claim <b>+{fmt_days(bonus_days, ref_lang)}</b> and "
                                f"<b>+{fmt_gb(bonus_gb, ref_lang)}</b> on your account.\n\n"
                                f"Open <b>🔗 Referral</b> → tap <b>Claim Reward</b>."
                            )
                        else:
                            notify_text += "Open 🔗 Referral to see your stats."
                    await bot.send_message(referrer_id, notify_text)
                except TelegramBadRequest:
                    pass
                except Exception as e:
                    logger.warning("referral-claimable notify failed: %s", e)

        # Delivery message
        delivery = f"{t('purchase_success', lang)}\n\n"
        delivery += fmt_account_card(
            {"email": email, "traffic_gb": plan["traffic_gb"], "expiry_time": expiry_time,
             "is_active": True, "is_trial": False, "label": account_name},
            lang=lang, server_alias=server["alias"], plan_name=plan["name"], currency=currency,
        )
        sub_url = build_sub_url(server, sub_id)
        if sub_url:
            delivery += f"\n\n{t('sub_url', lang)}\n<code>{escape_html(sub_url)}</code>\n"
        delivery += f"\n{t('how_to_use', lang)}"
        kb = InlineKeyboardBuilder()
        kb.button(text=t("how_to_use", lang), callback_data=AccountCB(action="guide", email=email).pack(), style="primary")
        kb.button(style="primary", text=t("get_link", lang), callback_data=AccountCB(action="links", email=email).pack())
        kb.button(style="primary", text=t("my_accounts", lang), callback_data=MenuCB(action="my_accounts").pack())
        kb.button(style="danger", text=t("back_menu", lang), callback_data=MenuCB(action="main").pack())
        kb.adjust(2, 2)
        await callback.message.edit_text(delivery, reply_markup=kb.as_markup(), disable_web_page_preview=True)
        await callback.answer("✅")

    # ====================================================== MY ACCOUNTS
    @router.callback_query(MenuCB.filter(F.action == "my_accounts"))
    async def cb_my_accounts(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        accounts = await db.get_user_accounts(callback.from_user.id)
        if not accounts:
            await callback.message.edit_text(t("no_accounts", lang), reply_markup=kb_back_to_menu(lang))
            await callback.answer()
            return
        await callback.message.edit_text(
            f"{t('my_accounts_title', lang)}\n\n{t('select_account', lang)}",
            reply_markup=kb_accounts_list(accounts, lang),
        )
        await callback.answer()

    @router.callback_query(AccountCB.filter(F.action == "view"))
    async def cb_account_view(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        lang = _lang(db_user)
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        plan = await db.get_plan(account["plan_id"]) if account.get("plan_id") else None
        traffic = None
        if server:
            traffic = await api.get_client_traffic(server["panel_url"], server["api_token"], account["email"])
        text = fmt_account_card(
            account, lang=lang, traffic_data=traffic,
            server_alias=server["alias"] if server else "-",
            plan_name=plan["name"] if plan else ("Trial" if account.get("is_trial") else "-"),
            currency=await _currency(),
        )
        # Use show_view (not edit_text) so the back-from-QR case works: when
        # the user is on a QR photo message, edit_text would fail because a
        # photo cannot be turned into text. show_view falls back to delete+send.
        await show_view(callback.message, text=text,
            reply_markup=kb_account_details(account["email"], account["is_active"], lang,
                                            account.get("is_trial", False),
                                            topup_enabled=bool(await db.get_setting_int("topup_enabled", 1)),
                                            is_unlimited=(account.get("traffic_gb") == 0)))
        await callback.answer()

    @router.callback_query(AccountCB.filter(F.action == "links"))
    async def cb_account_links(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        lang = _lang(db_user)
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        # Per user request: the Links section shows ONLY the subscription URL
        # (a single self-updating link), not the raw per-inbound V2Ray configs.
        # A QR-code button is provided here AND on the main account page.
        sub_url = build_sub_url(server, account.get("sub_id", ""))
        text = f"{t('links_sub_only', lang)}\n\n📱 <code>{escape_html(account['email'])}</code>\n"
        if sub_url:
            text += f"\n{t('sub_url', lang)}\n<code>{escape_html(sub_url)}</code>"
        else:
            text += f"\n❌ {t('not_found', lang)}"
        kb = InlineKeyboardBuilder()
        if sub_url:
            kb.button(style="primary", text=t("qr_sub", lang),
                      callback_data=AccountCB(action="qr", email=account["email"]).pack())
        kb.button(text=t("back", lang), callback_data=AccountCB(action="view", email=account["email"]).pack(), style="primary")
        kb.adjust(1)
        await show_view(callback.message, text=text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
        await callback.answer()

    @router.callback_query(AccountCB.filter(F.action == "guide"))
    async def cb_account_guide(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        lang = _lang(db_user)
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        sub_url = build_sub_url(server, account.get("sub_id", ""))
        # GUIDES: use the admin-configurable connection guide (with the
        # subscription URL prepended so the user has everything in one place).
        text = f"{t('guide_connection_title', lang)}\n\n"
        if sub_url:
            text += f"🌐 {t('sub_url', lang)}\n<code>{escape_html(sub_url)}</code>\n\n"
        conn_guide = await db.get_setting(f"guide_connection_{lang}", "")
        if not (conn_guide and conn_guide.strip()):
            conn_guide = DEFAULT_GUIDE_CONNECTION_FA if lang == "fa" else DEFAULT_GUIDE_CONNECTION_EN
        text += conn_guide
        kb = InlineKeyboardBuilder()
        kb.button(style="primary", text=t("get_link", lang), callback_data=AccountCB(action="links", email=account["email"]).pack())
        kb.button(text=t("back", lang), callback_data=AccountCB(action="view", email=account["email"]).pack(), style="primary")
        kb.adjust(2)
        await show_view(callback.message, text=text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
        await callback.answer()

    @router.callback_query(AccountCB.filter(F.action == "traffic"))
    async def cb_account_traffic(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        lang = _lang(db_user)
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        traffic = await api.get_client_traffic(server["panel_url"], server["api_token"], account["email"])
        if not traffic:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        up = traffic.get("up", 0)
        down = traffic.get("down", 0)
        total = traffic.get("total", 0)
        used = up + down
        text = f"{t('traffic_title', lang)}\n<code>{escape_html(account['email'])}</code>\n\n"
        # CLEAR-LABELS: each line now says what it is (uploaded / downloaded /
        # used / total) instead of bare emoji+number.  Mirrors the account
        # card so the two screens read consistently.
        text += f"{t('card_uploaded', lang)} ⬆️ {fmt_bytes(up, lang)}\n"
        text += f"{t('card_downloaded', lang)} ⬇️ {fmt_bytes(down, lang)}\n"
        if total > 0:
            remaining = max(0, total - used)
            text += f"{t('card_used', lang)} 📊 {fmt_bytes(used, lang)} / {fmt_bytes(total, lang)}\n"
            text += f"<code>{fmt_progress_bar((used/total)*100, lang=lang)}</code>\n"
            # Remaining — fmt_gb for the friendly "گیگابایت" wording (matches
            # the account card).
            text += f"{t('card_remaining_traffic', lang)} ✅ {fmt_gb(remaining / GB, lang)}"
        else:
            text += f"{t('card_used', lang)} 📊 {fmt_bytes(used, lang)} ({t('unlimited', lang)})"
        online = await api.get_online_clients(server["panel_url"], server["api_token"])
        is_online = account["email"] in online if isinstance(online, list) else False
        # Account status line — labelled so the user knows what 🔵 refers to.
        status_word = t('online', lang) if is_online else t('offline', lang)
        text += f"\n{t('card_account_status', lang)} : 🔵 {status_word}"
        try:
            ips = await api.get_client_ips(server["panel_url"], server["api_token"], account["email"])
            text += f"\n🌐 {t('active_ips', lang)}: {fmt_num(len(ips), lang)}"
        except Exception as e:
            # Panel-API call path — never swallow silently (M2). A failed
            # get_client_ips just means the IP line is omitted from the card.
            logger.warning("get_client_ips failed for %s: %s", account['email'], e, exc_info=True)
        kb = InlineKeyboardBuilder()
        kb.button(text=t("back", lang), callback_data=AccountCB(action="view", email=account["email"]).pack(), style="primary")
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    # ---- QR code -------------------------------------------------------
    @router.callback_query(AccountCB.filter(F.action == "qr"))
    async def cb_account_qr(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        lang = _lang(db_user)
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        links = await api.get_client_links(server["panel_url"], server["api_token"], account["email"])
        sub_url = build_sub_url(server, account.get("sub_id", ""))
        payload = sub_url or (links[0] if links else "")
        if not payload:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        png = make_qr_png(payload)
        kb = InlineKeyboardBuilder()
        kb.button(text=t("back", lang), callback_data=AccountCB(action="view", email=account["email"]).pack(), style="primary")
        if png:
            await callback.message.answer_photo(
                BufferedInputFile(png, filename="qr.png"),
                caption=f"📱 QR — <code>{escape_html(account['email'])}</code>\n"
                        f"{t('qr_caption_sub', lang) if sub_url else t('qr_caption_link', lang)}",
                reply_markup=kb.as_markup(),
            )
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass  # message already deleted / not found — expected
            except Exception as e:
                logger.warning("qr message delete failed: %s", e, exc_info=True)
        else:
            await callback.message.answer(
                f"📱 <code>{escape_html(payload)}</code>",
                reply_markup=kb.as_markup(),
                disable_web_page_preview=True,
            )
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass  # message already deleted / not found — expected
            except Exception as e:
                logger.warning("qr message delete failed: %s", e, exc_info=True)
        await callback.answer()

    # ---- set label -----------------------------------------------------
    @router.callback_query(AccountCB.filter(F.action == "label"))
    async def cb_account_label(callback: CallbackQuery, callback_data: AccountCB, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        await state.set_state(UserStates.waiting_for_label)
        await state.update_data(email=callback_data.email)
        await callback.message.edit_text(t("ask_label", lang), reply_markup=kb_cancel(lang))
        await track_prompt(callback.message, state)
        await callback.answer()

    @router.message(UserStates.waiting_for_label)
    async def ms_label(message: Message, state: FSMContext, db_user: dict):
        await del_inbound(message, state)
        lang = _lang(db_user)
        text = (message.text or "").strip()[:30]
        data = await state.get_data()
        await state.clear()
        email = data.get("email")
        if not email:
            await message.answer(t("not_found", lang), reply_markup=kb_back_to_menu(lang))
            return
        acc = await db.get_account(email)
        is_active = acc["is_active"] if acc else True
        is_trial = bool(acc.get("is_trial", False)) if acc else False
        is_unlimited = (acc.get("traffic_gb") == 0) if acc else False
        topup_enabled = bool(await db.get_setting_int("topup_enabled", 1))
        if text == "-":
            await db.update_account(email, label=None)
            await message.answer(t("label_cleared", lang),
                                 reply_markup=kb_account_details(email, is_active, lang, is_trial,
                                                                 topup_enabled=topup_enabled,
                                                                 is_unlimited=is_unlimited))
        else:
            await db.update_account(email, label=text)
            await message.answer(t("label_set", lang, label=escape_html(text)),
                                 reply_markup=kb_account_details(email, is_active, lang, is_trial,
                                                                 topup_enabled=topup_enabled,
                                                                 is_unlimited=is_unlimited))

    # ---- renew ---------------------------------------------------------
    @router.callback_query(AccountCB.filter(F.action == "renew"))
    async def cb_account_renew(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        lang = _lang(db_user)
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        if account.get("is_trial"):
            await callback.answer(t("trial_no_renew", lang), show_alert=True)
            return
        plan = await db.get_plan(account["plan_id"]) if account.get("plan_id") else None
        if not plan:
            await callback.answer(t("plan_not_found_buy", lang), show_alert=True)
            return
        currency = await _currency()
        balance = db_user.get("balance", 0)
        text = fmt_plan_card(plan, lang, currency)
        text += f"\n\n🔄 <code>{escape_html(account['email'])}</code>\n"
        text += t("your_balance", lang, balance=fmt_price(balance, lang, currency)) + "\n"
        if balance < plan["price"]:
            text += t("insufficient", lang, diff=fmt_price(plan["price"] - balance, lang, currency))
            # BUG-7 FIX: use renew_short_hint (not review_short_hint) — this
            # page shows a "Charge Wallet" button, NOT the purchase-review's
            # "Pay Exact Shortfall" button. The review_short_hint was a
            # regression from Task 16 that pointed at a non-existent button.
            text += f"\n{t('renew_short_hint', lang)}"
        else:
            # RENEW-EXPLAIN: when the user CAN afford the renewal, show a
            # clear explanation of what renewal does (remaining days/traffic
            # are added, price is deducted) plus the computed post-renewal
            # values, then ask for explicit confirmation.  This mirrors the
            # exact logic in cb_renew_confirm so the numbers match what
            # actually happens.
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            exp_ms = account.get("expiry_time") or 0
            is_expired = exp_ms <= now_ms
            is_unlimited = account.get("traffic_gb") == 0
            # Post-renewal duration (in days from now).
            if is_expired:
                new_total_days = plan["duration_days"]
            else:
                remaining_days = (exp_ms - now_ms) / MS_PER_DAY
                new_total_days = plan["duration_days"] + remaining_days
            # Post-renewal traffic.
            if is_unlimited:
                new_traffic_display = t("unlimited", lang)
            else:
                new_traffic_gb = (account.get("traffic_gb") or 0) + (plan.get("traffic_gb") or 0)
                new_traffic_display = fmt_gb(new_traffic_gb, lang)
            after_balance = balance - plan["price"]
            text += "\n" + t("renew_how_title", lang) + "\n"
            text += t("renew_how_deduct", lang) + "\n"
            if is_expired:
                text += t("renew_how_expired", lang) + "\n"
            else:
                text += t("renew_how_days_add", lang) + "\n"
            if is_unlimited:
                text += t("renew_how_unlimited", lang) + "\n"
            else:
                text += t("renew_how_traffic_add", lang) + "\n"
            text += "\n" + t("renew_after_title", lang) + "\n"
            if plan["duration_days"] > 0:
                text += t("renew_after_days", lang,
                          days=fmt_days(int(new_total_days), lang)) + "\n"
            text += t("renew_after_traffic", lang, traffic=new_traffic_display) + "\n"
            text += t("renew_after_balance", lang,
                      balance=fmt_price(after_balance, lang, currency)) + "\n"
            text += "\n" + t("renew_sure", lang)
        kb = InlineKeyboardBuilder()
        if balance >= plan["price"]:
            # RENEW-EXPLAIN: distinct confirm button label so the user knows
            # this is the final commitment (the old label was identical to
            # the entry "🔄 Renew" button, which was confusing).
            kb.button(text=t("renew_confirm_btn", lang),
                      callback_data=AccountCB(action="renew_confirm", email=account["email"]).pack(),
                      style="success")
        else:
            # PURCHASE-UX-2: can't afford — offer a direct top-up shortcut so
            # the user isn't sent back empty-handed.
            kb.button(text=t("charge_wallet_btn", lang),
                      callback_data=MenuCB(action="charge_wallet").pack(), style="primary")
        kb.button(text=t("back", lang), callback_data=AccountCB(action="view", email=account["email"]).pack(), style="danger")
        kb.adjust(1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AccountCB.filter(F.action == "renew_confirm"))
    async def cb_renew_confirm(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        lang = _lang(db_user)
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        plan = await db.get_plan(account["plan_id"]) if account.get("plan_id") else None
        if not plan:
            await callback.answer(t("plan_not_found", lang), show_alert=True)
            return
        if account.get("is_trial"):
            await callback.answer(t("trial_no_renew", lang), show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        # C8 — atomic balance deduction (replaces the read-then-write race).
        if not await db.try_deduct_balance(callback.from_user.id, plan["price"]):
            # M10 — show the ACTUAL shortfall (price - balance), not the full
            # price. The user already knows the plan price; what they need to
            # know is how much MORE they have to deposit.
            cur_balance = db_user.get("balance", 0)
            diff = max(0, plan["price"] - cur_balance)
            await callback.answer(
                t("insufficient", lang, diff=fmt_price(diff, lang, await _currency())),
                show_alert=True,
            )
            return
        # H2 — preserve unlimited accounts. traffic_gb=0 means UNLIMITED;
        # adding plan GB would silently cap it. Keep it 0 (unlimited) and
        # skip the bytes top-up entirely.
        if account.get("traffic_gb") == 0:
            new_traffic = 0
            add_bytes = 0
        else:
            new_traffic = (account["traffic_gb"] or 0) + (plan["traffic_gb"] or 0)
            # int() — bulk_adjust's addBytes is an integer byte count.
            add_bytes = int(plan["traffic_gb"] * GB) if plan["traffic_gb"] and plan["traffic_gb"] > 0 else 0
        result = await api.bulk_adjust(
            server["panel_url"], server["api_token"], [account["email"]],
            add_days=plan["duration_days"], add_bytes=add_bytes,
        )
        if not result.get("success"):
            # C7 — compensation: refund the deducted balance.
            await db.update_user_balance(callback.from_user.id, plan["price"], add=True)
            await callback.answer(t("renew_failed", lang, msg=result.get("msg", "")), show_alert=True)
            return
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        base = account["expiry_time"] if account["expiry_time"] and account["expiry_time"] > now_ms else now_ms
        new_expiry = base + plan["duration_days"] * MS_PER_DAY if plan["duration_days"] > 0 else 0
        # (new_traffic was computed above in the H2 block — preserves unlimited.)
        # C7 — transaction for the multi-step DB writes.
        try:
            async with db.transaction():
                await db.update_account(account["email"], expiry_time=new_expiry, traffic_gb=new_traffic,
                                        is_active=True, renewed_at=datetime.now(timezone.utc).isoformat())
                await db.clear_traffic_alerts(account["email"])
                await db.clear_expiry_reminders(account["email"])
                await db.add_transaction(
                    user_tg_id=callback.from_user.id, amount=plan["price"], type_="renewal",
                    description=f"Renewed: {plan['name']}", account_email=account["email"], plan_id=plan["id"],
                )
        except Exception as e:
            # C7 — compensation: panel succeeded but DB failed. Best-effort
            # log; the panel client is already extended. Don't refund because
            # the user DID get the renewal on the panel side.
            logger.error("renew DB write failed (%s) — panel already extended for %s",
                         e, account["email"])
            await callback.answer("⚠️ Renewal applied but DB sync failed. Contact support.",
                                  show_alert=True)
            return
        currency = await _currency()
        text = f"{t('renew_success', lang)}\n\n<code>{escape_html(account['email'])}</code>\n"
        text += f"📅 {fmt_ts(new_expiry, lang)}\n💾 {fmt_gb(new_traffic, lang)}\n"
        text += f"💵 {fmt_price(plan['price'], lang, currency)}"
        kb = InlineKeyboardBuilder()
        kb.button(text=t("back", lang), callback_data=AccountCB(action="view", email=account["email"]).pack(), style="primary")
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer("✅")

    # ---- traffic top-up (separate from renewal) ------------------------
    @router.callback_query(AccountCB.filter(F.action == "topup"))
    async def cb_account_topup(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        lang = _lang(db_user)
        # TOPUP-TOGGLE: guard at the entry point so a stale topup button (still
        # sitting in a user's old message from before the admin disabled it)
        # can't open the package picker.
        if not await db.get_setting_int("topup_enabled", 1):
            await callback.answer(t("topup_disabled", lang), show_alert=True)
            return
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        if account.get("is_trial"):
            await callback.answer(t("trial_no_renew", lang), show_alert=True)
            return
        # UNLIMITED-TOGGLE: top-up is meaningless for unlimited accounts
        # (traffic_gb=0).  The panel ignores add_bytes when total=0, and the
        # DB keeps traffic_gb=0 (H2 block below).  Reject stale taps from
        # old messages rendered before the button was hidden.
        if account.get("traffic_gb") == 0:
            await callback.answer(t("topup_unlimited_noop", lang), show_alert=True)
            return
        packages = await db.get_setting_json("topup_packages", [5, 10, 20, 50])
        await callback.message.edit_text(
            t("topup_title", lang),
            reply_markup=kb_topup_packages(account["email"], packages, lang, await _currency()),
        )
        await callback.answer()

    @router.callback_query(TopupCB.filter(F.action == "buy"))
    async def cb_topup_buy(callback: CallbackQuery, callback_data: TopupCB, db_user: dict):
        lang = _lang(db_user)
        # TOPUP-TOGGLE: double-guard on the actual purchase path too, in case
        # the user opened the picker before the admin disabled top-ups and is
        # now tapping a package button.
        if not await db.get_setting_int("topup_enabled", 1):
            await callback.answer(t("topup_disabled", lang), show_alert=True)
            return
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        if account.get("is_trial"):
            await callback.answer(t("trial_no_renew", lang), show_alert=True)
            return
        # UNLIMITED-TOGGLE: double-guard on the purchase path too (the user
        # may have opened the picker before the button was hidden).
        if account.get("traffic_gb") == 0:
            await callback.answer(t("topup_unlimited_noop", lang), show_alert=True)
            return
        gb = callback_data.gb
        price_per_gb = await db.get_setting_float("topup_price_per_gb", TOPUP_DEFAULT_PRICE_PER_GB)
        price = gb * price_per_gb
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        # C8 — atomic balance deduction.
        if not await db.try_deduct_balance(callback.from_user.id, price):
            # M11 — show the ACTUAL shortfall, not the full price.
            cur_balance = db_user.get("balance", 0)
            diff = max(0, price - cur_balance)
            await callback.answer(
                t("insufficient", lang, diff=fmt_price(diff, lang, await _currency())),
                show_alert=True,
            )
            return
        result = await api.bulk_adjust(
            server["panel_url"], server["api_token"], [account["email"]],
            add_bytes=int(gb * GB),
        )
        if not result.get("success"):
            # C7 — compensation: refund the deducted balance.
            await db.update_user_balance(callback.from_user.id, price, add=True)
            await callback.answer(t("action_failed", lang, msg=result.get('msg', '')), show_alert=True)
            return
        # H2 — preserve unlimited accounts on top-up too. traffic_gb=0
        # means UNLIMITED; adding GB would cap it. Keep it unlimited.
        if account.get("traffic_gb") == 0:
            new_traffic = 0
        else:
            new_traffic = (account["traffic_gb"] or 0) + gb
        # C7 — transaction.
        try:
            async with db.transaction():
                await db.update_account(account["email"], traffic_gb=new_traffic, is_active=True)
                await db.clear_traffic_alerts(account["email"])
                await db.add_transaction(
                    user_tg_id=callback.from_user.id, amount=price, type_="topup",
                    description=f"Top-up +{gb}GB", account_email=account["email"],
                )
        except Exception as e:
            logger.error("topup DB write failed (%s) — panel already extended for %s",
                         e, account["email"])
            await callback.answer("⚠️ Top-up applied but DB sync failed. Contact support.",
                                  show_alert=True)
            return
        currency = await _currency()
        await callback.answer("✅")
        text = t("topup_success", lang, gb=gb, email=account["email"]) + "\n"
        text += f"💵 {fmt_price(price, lang, currency)}"
        kb = InlineKeyboardBuilder()
        kb.button(text=t("back", lang), callback_data=AccountCB(action="view", email=account["email"]).pack(), style="primary")
        await callback.message.edit_text(text, reply_markup=kb.as_markup())

    # ---- enable / disable ---------------------------------------------
    @router.callback_query(AccountCB.filter(F.action == "disable"))
    async def cb_disable(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        lang = _lang(db_user)
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        res = await api.disable_client(server["panel_url"], server["api_token"], account["email"])
        if res.get("success"):
            await db.update_account(account["email"], is_active=False)
            await callback.answer("✅")
            await callback.message.edit_text(
                t("acc_disabled", lang, email=account["email"]),
                reply_markup=kb_account_details(account["email"], False, lang,
                                                topup_enabled=bool(await db.get_setting_int("topup_enabled", 1)),
                                                is_unlimited=(account.get("traffic_gb") == 0)),
            )
        else:
            await callback.answer(f"Failed: {res.get('msg')}", show_alert=True)

    @router.callback_query(AccountCB.filter(F.action == "enable"))
    async def cb_enable(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        lang = _lang(db_user)
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        res = await api.enable_client(server["panel_url"], server["api_token"], account["email"])
        if res.get("success"):
            await db.update_account(account["email"], is_active=True)
            await callback.answer("✅")
            await callback.message.edit_text(
                t("acc_enabled", lang, email=account["email"]),
                reply_markup=kb_account_details(account["email"], True, lang,
                                                topup_enabled=bool(await db.get_setting_int("topup_enabled", 1)),
                                                is_unlimited=(account.get("traffic_gb") == 0)),
            )
        else:
            await callback.answer(f"Failed: {res.get('msg')}", show_alert=True)

    # ---- delete account (with confirm) --------------------------------
    @router.callback_query(AccountCB.filter(F.action == "delete_ask"))
    async def cb_delete_ask(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        lang = _lang(db_user)
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        kb.button(text=t("confirm_delete", lang),
                  callback_data=AccountCB(action="delete", email=account["email"]).pack(), style="danger")
        kb.button(text=t("back", lang), callback_data=AccountCB(action="view", email=account["email"]).pack(), style="primary")
        kb.adjust(1, 1)
        await callback.message.edit_text(
            t("delete_confirm", lang, email=account["email"]),
            reply_markup=kb.as_markup(),
        )
        await callback.answer()

    @router.callback_query(AccountCB.filter(F.action == "delete"))
    async def cb_delete(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        lang = _lang(db_user)
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        if server:
            res = await api.delete_client(server["panel_url"], server["api_token"], account["email"])
            # H5 — only remove the DB row if the panel delete succeeded.
            # Otherwise the panel client lives on but the bot can no longer
            # manage it — an orphan the user can never delete/renew again.
            if not res.get("success"):
                logger.warning("user-side delete_client failed for %s: %s",
                               account["email"], res.get("msg"))
                await callback.answer(
                    t("delete_failed", lang, msg=res.get("msg", "")) if "delete_failed" in MESSAGES.get(lang, {}) else
                    f"⚠️ Delete failed on panel: {res.get('msg', 'unknown error')}",
                    show_alert=True,
                )
                return
        await db.delete_account(account["email"])
        await callback.answer("✅")
        accounts = await db.get_user_accounts(callback.from_user.id)
        if accounts:
            await callback.message.edit_text(
                f"{t('acc_deleted', lang, email=account['email'])}\n\n{t('my_accounts_title', lang)}",
                reply_markup=kb_accounts_list(accounts, lang),
            )
        else:
            await callback.message.edit_text(
                f"{t('acc_deleted', lang, email=account['email'])}",
                reply_markup=kb_back_to_menu(lang),
            )

    # ====================================================== FREE TRIAL
    @router.callback_query(MenuCB.filter(F.action == "trial"))
    async def cb_trial(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        if not (await db.get_setting_int("trial_enabled", 0)):
            await callback.message.edit_text(t("trial_disabled", lang), reply_markup=kb_back_to_menu(lang))
            await callback.answer()
            return
        if await db.has_used_trial(callback.from_user.id):
            await callback.message.edit_text(t("trial_used", lang), reply_markup=kb_back_to_menu(lang))
            await callback.answer()
            return
        days = await db.get_setting_int("trial_days", TRIAL_DEFAULT_DAYS)
        gb = await db.get_setting_float("trial_gb", TRIAL_DEFAULT_GB)
        text = (
            f"{t('trial_offer', lang)}\n\n"
            f"📅 {fmt_days(days, lang)}\n💾 {fmt_gb(gb, lang)}\n💵 {fmt_price(0, lang, await _currency())}\n\n"
            f"⚠️ {'One free account per user — you cannot get another trial.' if lang == 'en' else 'هر کاربر فقط یک‌بار می‌تواند اکانت رایگان دریافت کند.'}\n"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text=t("get_trial", lang), callback_data=MenuCB(action="trial_activate").pack(), style="success")
        kb.button(text=t("back", lang), callback_data=MenuCB(action="main").pack(), style="danger")
        kb.adjust(1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(MenuCB.filter(F.action == "trial_activate"))
    async def cb_trial_activate(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        if await db.has_used_trial(callback.from_user.id):
            await callback.answer(t("trial_used", lang), show_alert=True)
            return
        # C4 — atomic claim. Try to mark trial_used_at BEFORE creating the
        # panel client. If two parallel requests both reach this handler, only
        # one wins the row and proceeds; the other aborts with no side effects
        # (no orphaned panel client, no wasted API call).
        if not await db.mark_trial_used(callback.from_user.id):
            await callback.answer(t("trial_used", lang), show_alert=True)
            return
        await callback.message.edit_text(t("creating_account", lang))
        days = await db.get_setting_int("trial_days", TRIAL_DEFAULT_DAYS)
        gb = await db.get_setting_float("trial_gb", TRIAL_DEFAULT_GB)
        limit_ip = await db.get_setting_int("trial_limit_ip", 1)
        trial_inbounds = await db.get_setting_json("trial_inbounds", [])
        # BUG-6 FIX: restrict to servers referenced by trial_inbounds. The OLD
        # code used `int(x.split("_",1)[0])` with NO isdigit() guard — a
        # malformed entry like "abc_def" (manual DB edit, migration bug)
        # would raise ValueError and crash the WHOLE trial activation,
        # leaving the trial claim consumed with no account created (the
        # try/except that calls _unmark_trial_used is further down, after
        # this line). Mirror the H18-fixed LoadBalancer.select_trial_inbounds
        # pattern: validate sid_s.isdigit() before int().
        allowed = set()
        for x in trial_inbounds:
            if "_" not in x:
                continue
            sid_s = x.split("_", 1)[0]
            if sid_s.isdigit():
                allowed.add(int(sid_s))
        allowed = list(allowed)
        server = await lb.select_best_server(allowed or None)
        if not server:
            # C4 — undo the trial claim so the user can retry when a server
            # comes back online. (Otherwise they'd be permanently blocked.)
            await db._unmark_trial_used(callback.from_user.id)
            await callback.message.edit_text(t("no_servers", lang), reply_markup=kb_back_to_menu(lang))
            return
        inbound_ids = await lb.select_trial_inbounds(server, trial_inbounds)
        if not inbound_ids:
            await db._unmark_trial_used(callback.from_user.id)
            await callback.message.edit_text(t("no_inbounds", lang), reply_markup=kb_back_to_menu(lang))
            return
        email = gen_email(callback.from_user.id, "trial")
        expiry_time = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp() * 1000)
        sub_id = gen_sub_id()
        result = await api.create_client(
            panel_url=server["panel_url"], token=server["api_token"],
            email=email, inbound_ids=inbound_ids, total_gb=gb,
            expiry_time=expiry_time, limit_ip=limit_ip, tg_id=callback.from_user.id,
            sub_id=sub_id,
        )
        if not result.get("success"):
            # C4/C7 — undo the trial claim so the user can retry later.
            await db._unmark_trial_used(callback.from_user.id)
            await callback.message.edit_text(t("trial_failed", lang, msg=result.get("msg", "")),
                                             reply_markup=kb_back_to_menu(lang))
            return
        links = await api.get_client_links(server["panel_url"], server["api_token"], email)
        # C7 — wrap the DB writes in a single transaction.
        try:
            async with db.transaction():
                await db.add_account(
                    user_tg_id=callback.from_user.id, server_id=server["id"], email=email, sub_id=sub_id,
                    plan_id=None, traffic_gb=gb, expiry_time=expiry_time, limit_ip=limit_ip,
                    inbound_ids=json.dumps(inbound_ids), is_trial=True, label="",
                )
                await db.add_transaction(
                    user_tg_id=callback.from_user.id, amount=0, type_="trial",
                    description=f"Free Trial {days}d/{gb}GB", account_email=email,
                )
        except Exception as e:
            # C7 — compensation: panel client was created but DB failed.
            logger.error("trial DB write failed (%s) — compensating", e)
            try:
                await api.delete_client(server["panel_url"], server["api_token"], email)
            except Exception as del_err:
                logger.error("compensation delete_client failed: %s", del_err)
            await db._unmark_trial_used(callback.from_user.id)
            await callback.message.edit_text(
                t("trial_failed", lang, msg="Internal error, please try again."),
                reply_markup=kb_back_to_menu(lang),
            )
            return
        text = f"{t('trial_created', lang)}\n\n"
        text += fmt_account_card(
            {"email": email, "traffic_gb": gb, "expiry_time": expiry_time, "is_active": True, "is_trial": True, "label": "Trial"},
            lang=lang, server_alias=server["alias"], plan_name="Trial", currency=await _currency(),
        )
        sub_url = build_sub_url(server, sub_id)
        if sub_url:
            text += f"\n\n{t('sub_url', lang)}\n<code>{escape_html(sub_url)}</code>\n"
        text += f"\n{t('how_to_use', lang)}"
        kb = InlineKeyboardBuilder()
        kb.button(text=t("how_to_use", lang), callback_data=AccountCB(action="guide", email=email).pack(), style="primary")
        kb.button(style="primary", text=t("get_link", lang), callback_data=AccountCB(action="links", email=email).pack())
        kb.button(text=t("buy", lang), callback_data=MenuCB(action="buy").pack(), style="success")
        kb.button(style="danger", text=t("back_menu", lang), callback_data=MenuCB(action="main").pack())
        kb.adjust(2, 2)
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
        await callback.answer("✅")

    # ====================================================== WALLET (was BALANCE)
    # MENU-RESTRUCTURE: this handler now serves as the "Wallet" hub — the
    # single entry point from the main menu for balance info, charging, and
    # gift codes. The title changed from "Your Balance" to "Wallet".
    #
    # WALLET-TABLE v3: renders the wallet as a real bordered/striped Rich
    # Message table (summary kv-grid + transactions grid), matching the
    # style used throughout the admin panel.  The previous plain-HTML
    # version was a regression introduced in UI-OVERHAUL-2 because Rich
    # Message headings were leaking literal <b> tags — that happened only
    # because HTML was being passed into heading() text.  The dedicated
    # rich_tables.wallet_rich() builder uses PLAIN text (no <b> tags) so
    # nothing leaks, and the table layout is restored.  Farsi requests
    # is_rtl=True so the table lays out right-to-left.
    @router.callback_query(MenuCB.filter(F.action == "balance"))
    async def cb_balance(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        currency = await _currency()
        balance = db_user.get("balance", 0)
        txs = await db.get_user_transactions(callback.from_user.id, limit=10)
        rich = rich_tables.wallet_rich(
            balance=balance,
            total_orders=db_user.get("total_orders", 0),
            total_spent=db_user.get("total_spent", 0),
            txs=txs,
            lang=lang,
            currency=currency,
            fmt=fmt_price,
        )
        kb = InlineKeyboardBuilder()
        kb.button(text=t("charge_wallet_btn", lang), callback_data=MenuCB(action="charge_wallet").pack(), style="primary")
        kb.button(style="success", text=t("gift", lang), callback_data=MenuCB(action="gift").pack())
        kb.button(style="danger", text=t("back_menu", lang), callback_data=MenuCB(action="main").pack())
        kb.adjust(2, 1)
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())
        await callback.answer()

    # ====================================================== CHARGE WALLET
    @router.callback_query(MenuCB.filter(F.action == "charge_wallet"))
    async def cb_charge_wallet(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        payment_enabled = await db.get_setting_int("payment_enabled", 0)
        if not payment_enabled:
            await callback.message.edit_text(
                t("payment_disabled", lang),
                reply_markup=kb_back_to_menu(lang),
            )
            await callback.answer()
            return
        card_number = await db.get_setting("payment_card_number", "")
        card_holder = await db.get_setting("payment_card_holder", "")
        presets = await db.get_setting_json("payment_presets", [50000, 100000, 200000, 500000])
        currency = await _currency()
        kb = InlineKeyboardBuilder()
        for amt in presets:
            kb.button(style="primary", 
                text=fmt_price(amt, lang, currency),
                callback_data=PaymentCB(action="select_amount", amount=amt).pack(),
            )
        kb.button(text=t("custom_amount", lang), callback_data=PaymentCB(action="custom_amount").pack(), style="primary")
        kb.button(style="success", text=t("gift", lang), callback_data=MenuCB(action="gift").pack())
        # MENU-RESTRUCTURE: back goes to Wallet (balance) instead of main menu,
        # since charge-wallet is reached from the Wallet hub.
        kb.button(text=t("back", lang), callback_data=MenuCB(action="balance").pack(), style="danger")
        kb.adjust(2, 1, 2)
        await callback.message.edit_text(t("choose_amount", lang), reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(PaymentCB.filter(F.action == "select_amount"))
    async def cb_payment_select_amount(callback: CallbackQuery, callback_data: PaymentCB, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        original_amount = callback_data.amount
        # Small 3-digit suffix (100–999, 900 values) — keeps the surcharge
        # tiny (max +999 toman) while still distinguishing same-base payments
        # from different users.
        suffix = secrets.randbelow(PAYMENT_UNIQUE_SUFFIX_RANGE) + PAYMENT_UNIQUE_SUFFIX_MIN
        unique_amount = original_amount + suffix
        await state.update_data(original_amount=original_amount, unique_amount=unique_amount)
        # CARD-RTL: wrap with ltr() so the digit groups keep their left-to-right
        # order inside an RTL (Farsi) paragraph. Without this, "6037 9919 2616
        # 0239" is visually reversed to "0239 2616 9919 6037".
        card_number = ltr(await db.get_setting("payment_card_number", "-"))
        card_holder = await db.get_setting("payment_card_holder", "-")
        currency = await _currency()
        # Amount block: copyable toman + rial (or USD) amounts in <code> tags
        # with raw ASCII digits — see _amount_block().
        await callback.message.edit_text(
            t("payment_info", lang, card_number=card_number, card_holder=card_holder,
              amount_block=_amount_block(unique_amount, currency, lang)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(style="success", text=t("send_receipt", lang), callback_data=PaymentCB(action="send_receipt", amount=0).pack())],
                [InlineKeyboardButton(style="danger", text=t("back_menu", lang), callback_data=MenuCB(action="charge_wallet").pack())],
            ]),
        )
        await callback.answer()

    @router.callback_query(PaymentCB.filter(F.action == "custom_amount"))
    async def cb_payment_custom_amount(callback: CallbackQuery, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        await state.set_state(UserStates.waiting_for_custom_amount)
        # M12 — use admin-configurable min amount instead of hardcoded 10000.
        min_amount = await db.get_setting_int("payment_min_amount", DEFAULT_PAYMENT_MIN_AMOUNT)
        await callback.message.edit_text(
            t("enter_custom_amount", lang, min=fmt_price(min_amount, lang, await _currency())),
            reply_markup=kb_cancel(lang),
        )
        await track_prompt(callback.message, state)
        await callback.answer()

    @router.message(UserStates.waiting_for_custom_amount)
    async def ms_custom_amount(message: Message, state: FSMContext, db_user: dict):
        await del_inbound(message, state)
        lang = _lang(db_user)
        # H8 — guard against non-text input (photo/sticker/voice). The FSM
        # filter StateFilter fires on ANY message type; without this guard,
        # a photo sends message.text=None and None.strip() raises
        # AttributeError (NOT caught by except ValueError), crashing the
        # handler and leaving the user stuck in the state.
        raw = (message.text or "").strip()
        try:
            amount = int(raw)
        except (ValueError, TypeError):
            await message.answer(t("invalid_number", lang), reply_markup=kb_cancel(lang))
            return
        # Use the admin-configurable minimum amount (M12 — was hardcoded 10000).
        min_amount = await db.get_setting_int("payment_min_amount", DEFAULT_PAYMENT_MIN_AMOUNT)
        if amount < min_amount:
            await message.answer(
                t("invalid_number", lang),
                reply_markup=kb_cancel(lang),
            )
            return
        # Small 3-digit suffix (100–999, 900 values) — C6.
        suffix = secrets.randbelow(PAYMENT_UNIQUE_SUFFIX_RANGE) + PAYMENT_UNIQUE_SUFFIX_MIN
        unique_amount = amount + suffix
        await state.update_data(original_amount=amount, unique_amount=unique_amount)
        # CARD-RTL: wrap with ltr() so the digit groups keep their left-to-right
        # order inside an RTL (Farsi) paragraph. Without this, "6037 9919 2616
        # 0239" is visually reversed to "0239 2616 9919 6037".
        card_number = ltr(await db.get_setting("payment_card_number", "-"))
        card_holder = await db.get_setting("payment_card_holder", "-")
        currency = await _currency()
        # Amount block: copyable toman + rial (or USD) amounts in <code> tags.
        await message.answer(
            t("payment_info", lang, card_number=card_number, card_holder=card_holder,
              amount_block=_amount_block(unique_amount, currency, lang)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(style="success", text=t("send_receipt", lang), callback_data=PaymentCB(action="send_receipt", amount=0).pack())],
                [InlineKeyboardButton(style="danger", text=t("back_menu", lang), callback_data=MenuCB(action="charge_wallet").pack())],
            ]),
        )

    @router.callback_query(PaymentCB.filter(F.action == "send_receipt"))
    async def cb_payment_send_receipt(callback: CallbackQuery, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        await state.set_state(UserStates.waiting_for_receipt)
        await callback.message.edit_text(
            t("enter_receipt_text", lang),
            reply_markup=kb_cancel(lang),
        )
        await track_prompt(callback.message, state)
        await callback.answer()

    @router.message(UserStates.waiting_for_receipt)
    async def ms_receipt(message: Message, state: FSMContext, db_user: dict):
        await del_inbound(message, state)
        lang = _lang(db_user)
        data = await state.get_data()
        original_amount = data.get("original_amount", 0)
        unique_amount = data.get("unique_amount", 0)
        # SHORTFALL-REQUEST: if this receipt is for a shortfall payment, the
        # state holds shortfall_plan_id. We tag the payment record so the
        # approval handler can offer the user a one-tap "Buy Now" button.
        resume_plan_id = data.get("shortfall_plan_id")
        shortfall_plan_name = data.get("shortfall_plan_name", "")

        receipt_type = ""
        receipt_file_id = ""
        receipt_text = ""

        if message.photo:
            # message.photo is a list of PhotoSize, largest is the last one
            receipt_type = "photo"
            receipt_file_id = message.photo[-1].file_id
            receipt_text = (message.caption or "").strip()
        elif message.document:
            receipt_type = "document"
            receipt_file_id = message.document.file_id
            receipt_text = (message.caption or "").strip()
        elif message.text:
            receipt_type = "text"
            receipt_text = message.text.strip()
        else:
            # Unsupported content (sticker, voice, video note, etc.) — ask again
            await message.answer(t("receipt_empty", lang), reply_markup=kb_cancel(lang))
            return

        # Require at least a file or some text
        if not receipt_file_id and not receipt_text:
            await message.answer(t("receipt_empty", lang), reply_markup=kb_cancel(lang))
            return

        await state.clear()
        payment_id = await db.add_payment(
            user_tg_id=message.from_user.id,
            amount=original_amount,
            unique_amount=unique_amount,
            receipt_type=receipt_type,
            receipt_file_id=receipt_file_id,
            receipt_text=receipt_text,
            resume_plan_id=resume_plan_id,
        )
        # User-facing confirmation: if this was a shortfall payment, hint
        # that they'll get a Buy Now button after approval.
        if resume_plan_id and shortfall_plan_name:
            if lang == "fa":
                extra = (
                    f"\n\n💡 بعد از تأیید ادمین، موجودیت شارژ میشه و دکمهٔ «خرید پلن {escape_html(shortfall_plan_name)}» "
                    f"برات ظاهر میشه تا با یه کلیک خرید رو کامل کنی."
                )
            else:
                extra = (
                    f"\n\n💡 After admin approval, your balance will be topped up and a "
                    f"\"Buy {escape_html(shortfall_plan_name)}\" button will appear so you can finish the purchase in one tap."
                )
            await message.answer(
                t("receipt_received", lang, amount=fmt_price(unique_amount, lang, await _currency())) + extra,
                reply_markup=kb_back_to_menu(lang),
            )
        else:
            await message.answer(
                t("receipt_received", lang, amount=fmt_price(unique_amount, lang, await _currency())),
                reply_markup=kb_back_to_menu(lang),
            )
        # Notify every admin that a new payment needs their review.
        # Includes payment-only admins (who can approve/reject) in addition
        # to full ADMIN_IDS.
        currency = await _currency()
        notify_targets = set(ADMIN_IDS) | await get_payment_admin_ids(db)
        # RECEIPT-CROSS-ADMIN-CLEANUP: record each admin's notification
        # message_id so that when ANY admin approves/rejects, we can edit
        # (or delete) every other admin's notification — otherwise they'd
        # still see "awaiting your review" and might try to approve an
        # already-processed receipt.
        notif_map: Dict[str, dict] = {}
        for admin_id in notify_targets:
            try:
                notify_kb = InlineKeyboardBuilder()
                notify_kb.button(style="success", text="👁 Review",
                                 callback_data=PaymentCB(action="view", payment_id=payment_id).pack())
                notify_kb.button(style="primary", text="💰 Pending Pay",
                                 callback_data=AdminCB(action="pending_payments").pack())
                notify_kb.adjust(1)
                admin_text = (
                    f"💰 <b>New payment needs approval</b>\n\n"
                    f"👤 {escape_html(message.from_user.full_name)} (<code>{message.from_user.id}</code>)\n"
                    f"💵 {fmt_price(unique_amount, 'en', currency)} ({currency})\n"
                    f"🧾 Receipt: {receipt_type}\n"
                    f"⏳ Status: <b>awaiting your review</b>"
                )
                # If this is a shortfall payment, tell the admin which plan
                # the user wants to buy — helps them prioritise/understand.
                if resume_plan_id and shortfall_plan_name:
                    admin_text += f"\n🎯 Shortfall for plan: <b>{escape_html(shortfall_plan_name)}</b> (id #{resume_plan_id})"
                # Forward the receipt media to the admin if there is one, so
                # they can see it without even clicking through.
                sent_msg = None
                sent_kind = "text"
                if receipt_file_id and receipt_type == "photo":
                    sent_msg = await bot.send_photo(admin_id, receipt_file_id, caption=admin_text,
                                                    reply_markup=notify_kb.as_markup())
                    sent_kind = "photo"
                elif receipt_file_id and receipt_type == "document":
                    sent_msg = await bot.send_document(admin_id, receipt_file_id, caption=admin_text,
                                                       reply_markup=notify_kb.as_markup())
                    sent_kind = "document"
                else:
                    if receipt_text:
                        admin_text += f"\n📝 {escape_html(receipt_text[:300])}"
                    sent_msg = await bot.send_message(admin_id, admin_text, reply_markup=notify_kb.as_markup())
                    sent_kind = "text"
                # Record the (chat_id, message_id, type) for later cleanup.
                if sent_msg is not None:
                    notif_map[str(admin_id)] = {
                        "chat_id": admin_id,  # admins are DM'd, so chat_id == admin_id
                        "message_id": sent_msg.message_id,
                        "type": sent_kind,
                    }
            except TelegramForbiddenError:
                pass  # admin blocked the bot — expected
            except TelegramBadRequest as e:
                msg = str(e).lower()
                if "chat not found" not in msg and "blocked" not in msg:
                    logger.warning("payment-receipt admin notify failed: %s", e)
            except Exception as e:
                logger.warning("payment-receipt admin notify failed: %s", e, exc_info=True)
        # Persist the notification map so the approve/reject handler can
        # edit/delete each admin's notification later.  Best-effort: a failure
        # here doesn't break the receipt submission, just the cross-admin
        # cleanup feature.
        if notif_map:
            try:
                await db.update_payment_notif_ids(payment_id, json.dumps(notif_map))
            except Exception as e:
                logger.warning("update_payment_notif_ids failed for #%s: %s", payment_id, e)

    # ====================================================== REFERRAL
    @router.callback_query(MenuCB.filter(F.action == "referral"))
    async def cb_referral(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        # Respect the admin on/off toggle for the whole referral program.
        if not (await db.get_setting_int("referral_enabled", 1)):
            await callback.message.edit_text(
                f"{t('referral_title', lang)}\n\n{t('referral_disabled', lang)}",
                reply_markup=kb_back_to_menu(lang),
            )
            await callback.answer()
            return
        stats = await db.get_referral_stats(callback.from_user.id)
        bonus_days = await db.get_setting_int("referral_bonus_days", 0)
        bonus_gb = await db.get_setting_float("referral_bonus_gb", 0)
        # REFERRAL-CLAIM: count unclaimed rewards so we can show a Claim button.
        claimable = await db.get_claimable_referral_count(callback.from_user.id)
        me = await bot.get_me()
        ref_link = f"https://t.me/{me.username}?start={db_user.get('referral_code','')}"
        # Transparent "how it works" explanation — updated to mention the
        # claim requirement and the active-account requirement.
        how_text = (
            t('referral_how', lang, days=bonus_days, gb=bonus_gb)
            + "\n\n"
            + ("⚠️ <b>توجه:</b> برای دریافت پاداش، باید خودت یه اکانت فعال پرداختی داشته باشی. بعد از خرید دوستت، برو به بخش دعوت و پاداش رو دریافت کن."
               if lang == "fa"
               else "⚠️ <b>Note:</b> To claim your reward, you need your own active paid account. After your friend buys, come back here and tap Claim Reward.")
        )
        text = (
            f"{t('referral_title', lang)}\n\n"
            f"{how_text}\n\n"
            f"{t('referral_stats', lang)}\n"
            f"• {fmt_num(stats['total_referrals'], lang)} — {'Total invited' if lang == 'en' else 'کل دعوت‌شدگان'}\n"
            f"• {fmt_num(stats['completed_referrals'], lang)} — {'Bought (rewarded)' if lang == 'en' else 'خرید کرده (پاداش‌دار)'}\n"
            f"• {fmt_num(stats['pending_referrals'], lang)} — {'Pending (not bought yet)' if lang == 'en' else 'در انتظار (هنوز خرید نکرده)'}\n"
            # REWARD-TOTAL-FIX: use fmt_reward_days / fmt_reward_gb (NOT
            # fmt_days / fmt_gb) because the quota formatters treat 0 as
            # "Unlimited" — wrong for a SUM of earned bonuses where 0 means
            # "you haven't earned any bonus yet".  Previously a new user with
            # zero referrals saw "+نامحدود / +نامحدود" (Unlimited), misleading.
            f"• +{fmt_reward_days(stats['bonus_days_total'], lang)} / +{fmt_reward_gb(stats['bonus_gb_total'], lang)} — {'Total bonus earned' if lang == 'en' else 'کل پاداش کسب‌شده'}\n\n"
        )
        if claimable > 0:
            text += f"{t('ref_claimable', lang, count=claimable)}\n\n"
        text += f"{t('your_link', lang)}\n<code>{ref_link}</code>"
        # Recent referrals history (last 5)
        history = await db.get_referral_history(callback.from_user.id, limit=5)
        if history:
            text += f"\n\n{t('referral_history', lang)}\n"
            for h in history:
                name = escape_html(h.get("first_name") or h.get("username") or str(h["tg_id"]))
                if h.get("referral_rewarded"):
                    status = t("ref_status_bought", lang)
                else:
                    status = t("ref_status_pending", lang)
                text += f"• {name} — {status}\n"
        else:
            text += f"\n\n{t('referral_no_history', lang)}"
        # REFERRAL-TEXT-CFG: admin-defined extra note appended to the bottom
        # of the referral section (e.g. a promo or custom instructions).
        # Empty by default so existing bots are unaffected. {days}/{gb}
        # placeholders are filled too, for flexibility.
        _extra_raw = await db.get_setting(f"referral_extra_text_{lang}", "")
        if _extra_raw and _extra_raw.strip():
            try:
                _extra = _extra_raw.strip().format(days=bonus_days, gb=bonus_gb)
            except Exception:
                _extra = _extra_raw.strip()
            text += f"\n\n{_extra}"
        kb = InlineKeyboardBuilder()
        # Claim button — only shown when there are unclaimed rewards.
        if claimable > 0:
            kb.button(style="success", text=t("ref_claim_btn", lang),
                      callback_data=MenuCB(action="referral_claim").pack())
        # SHARE-WITH-TEXT: t.me/share/url accepts a `text` param so the
        # forwarded message includes a persuasive, localised pitch instead of
        # a bare link.  The text is built from the admin-configured bonus
        # amounts (bonus_days / bonus_gb) so it always reflects the real
        # reward.  Persian users get a Persian pitch, English users English.
        # REFERRAL-TEXT-CFG: use the admin-customised share text if set,
        # otherwise fall back to the locale default. {days}/{gb} placeholders
        # are filled with the current bonus amounts so a custom pitch always
        # reflects the real reward.
        _custom_share = await db.get_setting(f"referral_share_text_{lang}", "")
        _share_raw = (_custom_share.strip()
                      if (_custom_share and _custom_share.strip())
                      else t('referral_share_text', lang))
        try:
            share_pitch = _share_raw.format(days=bonus_days, gb=bonus_gb)
        except Exception:
            share_pitch = _share_raw
        share_url = f"https://t.me/share/url?url={quote(ref_link, safe='')}&text={quote(share_pitch, safe='')}"
        kb.button(style="primary", text=t("share_link", lang), url=share_url)
        # REFERRAL-INVITEES: button that opens the full invitees list
        # (tg_id + name + status + join date) so the customer can see exactly
        # who they've invited.  Always shown — even with zero invitees the
        # list view says "No invitees yet" which is informative.
        kb.button(style="primary", text=t("ref_invitees_btn", lang),
                  callback_data=MenuCB(action="referral_invitees").pack())
        kb.button(text=t("back", lang), callback_data=MenuCB(action="main").pack(), style="danger")
        kb.adjust(1, 1, 1, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
        await callback.answer()

    @router.callback_query(MenuCB.filter(F.action == "referral_invitees"))
    async def cb_referral_invitees(callback: CallbackQuery, db_user: dict):
        """REFERRAL-INVITEES: full list of users invited by this customer.

        Shows each invitee's tg_id, display name, reward status (bought /
        pending), and join date as a grid_table.  The user explicitly asked
        for the customer to see the IDs of everyone they've invited.
        """
        lang = _lang(db_user)
        invitees = await db.get_referral_invitees(callback.from_user.id, limit=50)
        blocks: list = [rich_tables.heading(t("ref_invitees_title", lang))]
        if invitees:
            rows = []
            for inv in invitees:
                status = t("ref_status_bought", lang) if inv.get("referral_rewarded") else t("ref_status_pending", lang)
                status_emoji = "✅" if inv.get("referral_rewarded") else "⏳"
                name = (inv.get("first_name") or inv.get("username") or str(inv["tg_id"]))[:16]
                disp_date = fmt_iso(inv.get("created_at"), "%Y-%m-%d") or "-"
                rows.append((inv["tg_id"], name, f"{status_emoji} {status}", disp_date))
            blocks.append(rich_tables.grid_table(
                t("ref_invitees_header", lang).split(" • "), rows,
                aligns=["right", "left", "center", "center"],
            ))
        else:
            blocks.append(rich_tables.paragraph(t("ref_invitees_none", lang)))
        rich = rich_tables.rich_message(*blocks, is_rtl=(lang == "fa"))
        kb = InlineKeyboardBuilder()
        kb.button(text=t("back", lang), callback_data=MenuCB(action="referral").pack(), style="danger")
        kb.adjust(1)
        # Rich messages can't be edited in place (aiogram 3.30 has no
        # edit_rich).  show_view handles delete + answer_rich for us.
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(MenuCB.filter(F.action == "referral_claim"))
    async def cb_referral_claim(callback: CallbackQuery, db_user: dict):
        """REFERRAL-CLAIM: let the referrer claim their pending reward.

        Requirements:
        1. The referrer must have at least one active, non-trial account
           (this ensures they're a paying customer — the whole point of the
           new logic).
        2. There must be at least one claimable referral (a referred user who
           bought but hasn't been rewarded yet).

        If the referrer has multiple active accounts, they pick which one
        receives the bonus. If they have exactly one, the bonus is applied
        immediately.
        """
        lang = _lang(db_user)
        claimable = await db.get_claimable_referral_count(callback.from_user.id)
        if claimable == 0:
            await callback.answer(t("ref_claim_none", lang), show_alert=True)
            return
        # Check for active non-trial accounts.
        accounts = await db.get_user_accounts(callback.from_user.id)
        active_paid = [a for a in accounts if a["is_active"] and not a["is_trial"]]
        if not active_paid:
            await callback.message.edit_text(
                t("ref_claim_no_account", lang),
                reply_markup=kb_back_to_menu(lang),
            )
            await callback.answer()
            return
        bonus_days = await db.get_setting_int("referral_bonus_days", 0)
        bonus_gb = await db.get_setting_float("referral_bonus_gb", 0)
        # If only one active account, apply directly.
        if len(active_paid) == 1:
            acc = active_paid[0]
            srv = await db.get_server(acc["server_id"])
            if not srv:
                await callback.answer(t("not_found", lang), show_alert=True)
                return
            # H3 — multiply the per-referral bonus by the claimable count.
            # Previously bulk_adjust was called once (1× bonus) but N reward
            # rows were recorded, so the referrer only got 1/N of their earned
            # bonus and could never claim the rest (the rows blocked re-claim).
            claimables = await db.get_claimable_referrals(callback.from_user.id)
            count = len(claimables)
            total_days = bonus_days * count
            total_gb = bonus_gb * count
            bonus_bytes = int(total_gb * GB) if total_gb > 0 else 0
            result = await api.bulk_adjust(
                srv["panel_url"], srv["api_token"],
                [acc["email"]], add_days=total_days, add_bytes=bonus_bytes,
            )
            # REFERRAL-CLAIM-CHECK: verify the panel actually applied the bonus
            # BEFORE marking referrals as rewarded. Without this guard, a panel
            # failure would still record reward rows (consuming the user's
            # pending bonus) while the account was never extended — an
            # unrecoverable loss for the user.
            if not result or not result.get("success"):
                logger.warning("referral-claim bulk_adjust failed for %s: %s",
                               acc["email"], (result or {}).get("msg"))
                await show_view(callback.message,
                    text=t("ref_claim_failed", lang, msg=(result or {}).get("msg", "unknown error")),
                    reply_markup=kb_back_to_menu(lang))
                await callback.answer()
                return
            # Record a reward row for EACH claimable referral (now N rows, with
            # the panel bonus applied N× above). H7 — add_referral_reward uses
            # INSERT OR IGNORE so a double-tap won't duplicate rows.
            for ref in claimables:
                await db.add_referral_reward(
                    referrer_tg_id=callback.from_user.id,
                    referred_tg_id=ref["tg_id"],
                    account_email=acc["email"],
                    bonus_days=bonus_days, bonus_gb=bonus_gb,
                )
            await show_view(callback.message,
                text=t("ref_claim_success", lang, days=total_days, gb=total_gb,
                       email=acc["email"]),
                reply_markup=kb_back_to_menu(lang),
            )
            await callback.answer("✅")
            return
        # Multiple active accounts — let the user pick which one.
        text = f"{t('ref_claim_pick', lang)}\n\n"
        kb = InlineKeyboardBuilder()
        for acc in active_paid:
            label = acc.get("label") or acc["email"][:24]
            kb.button(style="primary", text=f"📱 {label[:28]}",
                      callback_data=AccountCB(action="ref_claim_apply", email=acc["email"]).pack())
        kb.button(text=t("back", lang), callback_data=MenuCB(action="referral").pack(), style="danger")
        kb.adjust(1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AccountCB.filter(F.action == "ref_claim_apply"))
    async def cb_ref_claim_apply(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        """Apply the referral reward to the chosen account."""
        lang = _lang(db_user)
        acc = await db.get_account(callback_data.email)
        if not acc or acc["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        srv = await db.get_server(acc["server_id"])
        if not srv:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        claimable = await db.get_claimable_referral_count(callback.from_user.id)
        if claimable == 0:
            await callback.answer(t("ref_claim_none", lang), show_alert=True)
            return
        bonus_days = await db.get_setting_int("referral_bonus_days", 0)
        bonus_gb = await db.get_setting_float("referral_bonus_gb", 0)
        # H3 — multiply by the claimable count (see cb_referral_claim).
        claimables = await db.get_claimable_referrals(callback.from_user.id)
        count = len(claimables)
        total_days = bonus_days * count
        total_gb = bonus_gb * count
        bonus_bytes = int(total_gb * GB) if total_gb > 0 else 0
        result = await api.bulk_adjust(
            srv["panel_url"], srv["api_token"],
            [acc["email"]], add_days=total_days, add_bytes=bonus_bytes,
        )
        # REFERRAL-CLAIM-CHECK (mirrors cb_referral_claim): bail out without
        # recording rewards if the panel rejected the adjustment, so the
        # user's pending bonus is preserved for a later retry.
        if not result or not result.get("success"):
            logger.warning("ref-claim-apply bulk_adjust failed for %s: %s",
                           acc["email"], (result or {}).get("msg"))
            await show_view(callback.message,
                text=t("ref_claim_failed", lang, msg=(result or {}).get("msg", "unknown error")),
                reply_markup=kb_back_to_menu(lang))
            await callback.answer()
            return
        for ref in claimables:
            await db.add_referral_reward(
                referrer_tg_id=callback.from_user.id,
                referred_tg_id=ref["tg_id"],
                account_email=acc["email"],
                bonus_days=bonus_days, bonus_gb=bonus_gb,
            )
        await show_view(callback.message,
            text=t("ref_claim_success", lang, days=total_days, gb=total_gb, email=acc["email"]),
            reply_markup=kb_back_to_menu(lang))
        await callback.answer("✅")

    # ====================================================== GIFT CODE
    @router.callback_query(MenuCB.filter(F.action == "gift"))
    async def cb_gift(callback: CallbackQuery, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        await state.set_state(UserStates.waiting_for_gift_code)
        await callback.message.edit_text(t("enter_gift", lang), reply_markup=kb_cancel(lang))
        await track_prompt(callback.message, state)
        await callback.answer()

    @router.message(UserStates.waiting_for_gift_code)
    async def ms_gift_code(message: Message, state: FSMContext, db_user: dict):
        await del_inbound(message, state)
        lang = _lang(db_user)
        code = (message.text or "").strip().upper()
        gift = await db.get_gift_code(code)
        if not gift:
            await message.answer(t("gift_invalid", lang), reply_markup=kb_cancel(lang))
            return
        if gift["is_used"]:
            await state.clear()
            await message.answer(t("gift_used_code", lang), reply_markup=kb_back_to_menu(lang))
            return
        # C3 — atomic claim. Try to mark the code as used BEFORE granting the
        # reward. If two parallel redemptions both pass the is_used check
        # above, only one wins the UPDATE row and proceeds; the other aborts.
        if not await db.use_gift_code(code, message.from_user.id):
            await state.clear()
            await message.answer(t("gift_used_code", lang), reply_markup=kb_back_to_menu(lang))
            return
        await state.clear()
        currency = await _currency()

        if gift["type"] == "balance":
            amount = float(gift["value"])
            # BUG-14 FIX: wrap the claim + grant in a single transaction so a
            # failure between use_gift_code and update_user_balance rolls back
            # the claim (code stays unused → user can retry). Without this, a
            # transient DB error would consume the code without crediting the
            # balance. (use_gift_code's internal _auto_commit detects the
            # active transaction and skips its own commit, so the rollback
            # works correctly.)
            try:
                async with db.transaction():
                    await db.update_user_balance(message.from_user.id, amount, add=True)
                    await db.add_transaction(message.from_user.id, amount, "gift_balance", f"Gift: {code}")
            except Exception as e:
                # Transaction rolled back — the gift code is still unused.
                logger.error("gift balance grant failed (%s) — rolled back, code %s remains unused", e, code)
                await message.answer(
                    t("gift_invalid", lang),  # generic "try again" — code is still valid
                    reply_markup=kb_back_to_menu(lang))
                return
            await message.answer(
                t("gift_balance_ok", lang, amount=fmt_price(amount, lang, currency)),
                reply_markup=kb_back_to_menu(lang),
            )
        elif gift["type"] == "plan":
            plan = await db.get_plan(int(gift["value"]))
            if not plan:
                await message.answer(t("plan_not_found", lang), reply_markup=kb_back_to_menu(lang))
                return
            server = await lb.select_best_server(lb.plan_server_ids(plan) or None)
            if not server:
                await message.answer(t("no_servers", lang), reply_markup=kb_back_to_menu(lang))
                return
            inbound_ids = await lb.select_inbounds_for_plan(server, plan)
            if not inbound_ids:
                await message.answer(t("no_inbounds", lang), reply_markup=kb_back_to_menu(lang))
                return
            email = gen_email(message.from_user.id, "gift")
            expiry_time = (int((datetime.now(timezone.utc) + timedelta(days=plan["duration_days"])).timestamp() * 1000)
                           if plan["duration_days"] > 0 else 0)
            sub_id = gen_sub_id()
            res = await api.create_client(
                panel_url=server["panel_url"], token=server["api_token"], email=email,
                inbound_ids=inbound_ids, total_gb=plan["traffic_gb"], expiry_time=expiry_time,
                tg_id=message.from_user.id, sub_id=sub_id,
            )
            if not res.get("success"):
                # C7 — COMPENSATION: gift was claimed but panel failed. We've
                # already marked the code as used, so we can't un-claim it
                # without risking a double-grant. Instead, log the failure and
                # ask the user to contact support (admin can grant manually).
                logger.error("gift plan panel create failed for code %s user %s: %s",
                             code, message.from_user.id, res.get("msg"))
                await message.answer(
                    t("gift_plan_create_failed", lang, msg=res.get('msg', ''), code=code),
                    reply_markup=kb_back_to_menu(lang),
                )
                return
            links = await api.get_client_links(server["panel_url"], server["api_token"], email)
            # C7 — wrap multi-step DB writes in a transaction.
            try:
                async with db.transaction():
                    await db.add_account(
                        user_tg_id=message.from_user.id, server_id=server["id"], email=email, sub_id=sub_id,
                        plan_id=plan["id"], traffic_gb=plan["traffic_gb"], expiry_time=expiry_time,
                        limit_ip=plan.get("limit_ip", 0), inbound_ids=json.dumps(inbound_ids), label="Gift",
                    )
                    await db.add_transaction(message.from_user.id, 0, "gift_plan", f"Gift: {code}",
                                             account_email=email, plan_id=plan["id"])
            except Exception as e:
                # C7 — compensation: panel client was created but DB failed.
                logger.error("gift plan DB write failed (%s) — compensating", e)
                try:
                    await api.delete_client(server["panel_url"], server["api_token"], email)
                except Exception as del_err:
                    logger.error("compensation delete_client failed: %s", del_err)
                await message.answer(
                    t("gift_plan_db_failed", lang, code=code),
                    reply_markup=kb_back_to_menu(lang),
                )
                return
            text = f"{t('gift_plan_ok', lang, plan=plan['name'])}\n\n"
            text += fmt_account_card(
                {"email": email, "traffic_gb": plan["traffic_gb"], "expiry_time": expiry_time,
                 "is_active": True, "is_trial": False, "label": "Gift"},
                lang=lang, server_alias=server["alias"], plan_name=plan["name"], currency=currency,
            )
            sub_url = build_sub_url(server, sub_id)
            if sub_url:
                text += f"\n\n{t('sub_url', lang)}\n<code>{escape_html(sub_url)}</code>\n"
            text += f"\n{t('how_to_use', lang)}"
            kb = InlineKeyboardBuilder()
            kb.button(text=t("how_to_use", lang), callback_data=AccountCB(action="guide", email=email).pack(), style="primary")
            kb.button(style="primary", text=t("get_link", lang), callback_data=AccountCB(action="links", email=email).pack())
            kb.button(style="danger", text=t("back_menu", lang), callback_data=MenuCB(action="main").pack())
            kb.adjust(2, 1)
            await message.answer(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)

    # ====================================================== SUPPORT
    @router.callback_query(MenuCB.filter(F.action == "new_ticket"))
    async def cb_new_ticket(callback: CallbackQuery, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        await state.set_state(UserStates.waiting_for_ticket_category)
        kb = InlineKeyboardBuilder()
        kb.button(style="primary", text=t("cat_technical", lang),
                  callback_data=MenuCB(action="pick_cat", data="technical").pack())
        kb.button(style="primary", text=t("cat_payment", lang),
                  callback_data=MenuCB(action="pick_cat", data="payment").pack())
        kb.button(style="primary", text=t("cat_account", lang),
                  callback_data=MenuCB(action="pick_cat", data="account").pack())
        kb.button(style="primary", text=t("cat_other", lang),
                  callback_data=MenuCB(action="pick_cat", data="other").pack())
        kb.button(text=t("back", lang), callback_data=MenuCB(action="help").pack(), style="danger")
        kb.adjust(2, 2, 1)
        # TICKET-MEDIA-1: use show_view (handles photo-message case).
        await show_view(callback.message, text=t("choose_category", lang), reply_markup=kb.as_markup(), state=state)
        await callback.answer()

    @router.callback_query(MenuCB.filter(F.action == "pick_cat"))
    async def cb_pick_category(callback: CallbackQuery, callback_data: MenuCB, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        category = callback_data.data or "other"
        await state.set_state(UserStates.waiting_for_ticket_subject)
        await state.update_data(category=category)
        cat_label = t(f"cat_{category}", lang) if category in ("technical", "payment", "account", "other") else category
        # TICKET-MEDIA-1: use show_view (handles photo-message case).
        await show_view(callback.message, text=t("ask_subject", lang, category=cat_label), reply_markup=kb_cancel(lang), state=state)
        await callback.answer()

    @router.message(UserStates.waiting_for_ticket_subject)
    async def ms_ticket_subject(message: Message, state: FSMContext, db_user: dict):
        await del_inbound(message, state)
        lang = _lang(db_user)
        subject = (message.text or "").strip()[:100]
        if not subject:
            await message.answer(t("ask_subject", lang, category=""), reply_markup=kb_cancel(lang))
            return
        await state.update_data(subject=subject)
        await state.set_state(UserStates.waiting_for_ticket_message)
        await track_prompt(await message.answer(t("ask_message", lang, subject=escape_html(subject)), reply_markup=kb_cancel(lang)), state)

    @router.message(UserStates.waiting_for_ticket_message)
    async def ms_ticket_message(message: Message, state: FSMContext, db_user: dict):
        await del_inbound(message, state)
        lang = _lang(db_user)
        # Accept either plain text OR a media attachment (with optional
        # caption) as the initial ticket body.  This mirrors ms_ticket_reply
        # so users can attach a screenshot/voice the moment they open the
        # ticket — not just on follow-up replies.
        media_type, media_file_id, caption, msg_text = extract_ticket_media(message)
        if not media_type and not msg_text:
            await message.answer(t("ask_message", lang, subject=""), reply_markup=kb_cancel(lang))
            return
        # For media messages the stored body is the caption (may be empty).
        if media_type:
            msg_text = caption[:TICKET_MESSAGE_MAX_CHARS]
        data = await state.get_data()
        subject = data.get("subject", "No subject")
        category = data.get("category", "other")
        await state.clear()
        ticket_id = await db.create_ticket(message.from_user.id, subject, category)
        await db.add_ticket_message(
            ticket_id, "user", msg_text,
            media_type=media_type,
            media_file_id=media_file_id,
            media_caption=caption,
        )
        cat_label = t(f"cat_{category}", lang) if category in ("technical", "payment", "account", "other") else category
        # Admin notification: send the text header first, then forward the
        # media attachment (if any) so the admin sees exactly what the user
        # attached.  Uses _send_ticket_reply_notify for media-aware delivery.
        admin_header = (
            f"🎫 <b>New Ticket #{ticket_id}</b>\n"
            f"👤 {escape_html(message.from_user.full_name)} (<code>{message.from_user.id}</code>)\n"
            f"🏷 {cat_label}\n"
            f"📝 {escape_html(subject)}\n💬 {escape_html(msg_text[:500])}"
        )
        admin_kb = kb_ticket_view(ticket_id, True, "en", "open",
                                  user_tg_id=message.from_user.id)
        for admin_id in ADMIN_IDS:
            if media_type:
                await _send_ticket_reply_notify(
                    bot, admin_id, admin_header,
                    media_type, media_file_id, admin_kb,
                    context="new-ticket admin notify",
                )
            else:
                await safe_notify(
                    bot.send_message(admin_id, admin_header, reply_markup=admin_kb),
                    context="new-ticket admin notify",
                )
        await message.answer(t("ticket_created", lang, id=ticket_id, subject=escape_html(subject), category=cat_label),
                             reply_markup=kb_back_to_menu(lang))

    @router.callback_query(MenuCB.filter(F.action == "my_tickets"))
    async def cb_my_tickets(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        tickets = await db.get_user_tickets(callback.from_user.id)
        if not tickets:
            # TICKET-MEDIA-1: use show_view — the "Back" button on a ticket
            # photo notification routes here, so callback.message may be a photo.
            await show_view(callback.message, text=t("no_tickets", lang), reply_markup=kb_back_to_menu(lang))
            await callback.answer()
            return
        kb = InlineKeyboardBuilder()
        for tk in tickets:
            badge = _ticket_status_badge(tk, lang)
            cat_emoji = _category_emoji(tk.get("category", "other"))
            kb.button(style="primary",
                      text=f"{badge} #{tk['id']} — {cat_emoji} {tk['subject'][:22]}",
                      callback_data=TicketCB(action="view", ticket_id=tk["id"]).pack())
        kb.button(text=t("back", lang), callback_data=MenuCB(action="help").pack(), style="danger")
        kb.adjust(1)
        # TICKET-MEDIA-1: use show_view (handles photo-message case).
        await show_view(callback.message, text=t("my_tickets", lang), reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(TicketCB.filter(F.action == "view"))
    async def cb_ticket_view(callback: CallbackQuery, callback_data: TicketCB, db_user: dict):
        lang = _lang(db_user)
        ticket = await db.get_ticket(callback_data.ticket_id)
        if not ticket:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        is_admin = callback.from_user.id in ADMIN_IDS
        if ticket["user_tg_id"] != callback.from_user.id and not is_admin:
            await callback.answer(t("access_denied", lang), show_alert=True)
            return
        messages = await db.get_ticket_messages(callback_data.ticket_id)
        cat_label = t(f"cat_{ticket.get('category', 'other')}", lang) if ticket.get("category") in ("technical", "payment", "account", "other") else ticket.get("category", "other")
        status_label = _ticket_status_label(ticket, lang)
        text = (
            f"🎫 <b>Ticket #{ticket['id']}</b>\n"
            f"🏷 {cat_label}\n"
            f"📝 {escape_html(ticket['subject'])}\n"
            f"📊 {status_label}\n"
            f"📅 {fmt_iso(ticket.get('created_at'))}\n\n"
        )
        # Feature 1 — admins see the ticket owner's numeric ID + @username + first_name
        owner_user = None
        if is_admin:
            owner_user = await db.get_user(ticket["user_tg_id"])
        if is_admin and owner_user:
            uname = (owner_user.get("username") or "").strip()
            first = (owner_user.get("first_name") or "").strip()
            if uname:
                uname_html = f'@<a href="https://t.me/{uname}">{uname}</a>'
            else:
                uname_html = "—"
            text += (
                f"👤 <b>User:</b> {escape_html(first)}\n"
                f"🆔 <b>ID:</b> <code>{owner_user.get('tg_id', '')}</code>\n"
                f"💬 <b>Username:</b> {uname_html}\n\n"
            )
        # Feature 3 — render each ticket message; if it has a media attachment,
        # show a "[📎 Photo]" line above the caption (or in place of it).
        media_label_map = _media_label_map(lang)
        for m in messages:
            who = "👤" if m["sender"] == "user" else "🛡"
            mtext = (m.get("message") or "")
            mt = m.get("media_type") or ""
            if mt:
                label = media_label_map.get(mt, "Media")
                text += f"<b>{who} {fmt_iso(m.get('created_at'))}</b>\n[📎 {label}]"
                if mtext:
                    text += f"\n{escape_html(mtext[:TICKET_MESSAGE_MAX_CHARS])}"
                text += "\n\n"
            else:
                text += f"<b>{who} {fmt_iso(m.get('created_at'))}</b>\n{escape_html(mtext[:TICKET_MESSAGE_MAX_CHARS])}\n\n"
        await show_view(
            callback.message,
            text=text,
            reply_markup=kb_ticket_view(ticket["id"], is_admin, lang, ticket["status"],
                                        user_tg_id=ticket["user_tg_id"], messages=messages),
        )
        await callback.answer()

    @router.callback_query(TicketMediaCB.filter())
    async def cb_ticket_view_media(callback: CallbackQuery, callback_data: TicketMediaCB, db_user: dict):
        """Re-send a ticket message's media attachment to the requesting user.

        Looks up the ticket-message by (ticket_id, message_id) and forwards the
        stored file_id via the appropriate bot.send_* method. Only the ticket
        owner or an admin may fetch a ticket's media. (TICKET-1 Feature 3)
        """
        lang = _lang(db_user)
        ticket = await db.get_ticket(callback_data.ticket_id)
        if not ticket:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        is_admin = callback.from_user.id in ADMIN_IDS
        if ticket["user_tg_id"] != callback.from_user.id and not is_admin:
            await callback.answer(t("access_denied", lang), show_alert=True)
            return
        messages = await db.get_ticket_messages(callback_data.ticket_id)
        msg = next((m for m in messages if m.get("id") == callback_data.message_id), None)
        if not msg:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        media_type = msg.get("media_type") or ""
        media_file_id = msg.get("media_file_id") or ""
        if not media_type or not media_file_id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        sender_label = "Admin" if msg.get("sender") == "admin" else "User"
        ts = fmt_iso(msg.get("created_at"))
        caption_text = msg.get("media_caption") or ""
        cap = f"📎 {sender_label} — {ts}"
        if caption_text:
            cap += f"\n{escape_html(caption_text[:900])}"
        try:
            if media_type == "photo":
                await callback.message.answer_photo(photo=media_file_id, caption=cap)
            elif media_type == "document":
                await callback.message.answer_document(document=media_file_id, caption=cap)
            elif media_type == "video":
                await callback.message.answer_video(video=media_file_id, caption=cap)
            elif media_type == "voice":
                await callback.message.answer_voice(voice=media_file_id, caption=cap)
            elif media_type == "audio":
                await callback.message.answer_audio(audio=media_file_id, caption=cap)
            elif media_type == "animation":
                await callback.message.answer_animation(animation=media_file_id, caption=cap)
            elif media_type == "video_note":
                # Round-video notes accept a short caption.
                await callback.message.answer_video_note(video_note=media_file_id,
                                                         caption=cap[:200])
            elif media_type == "sticker":
                # Stickers can't carry a caption — send sticker then a text msg.
                await callback.message.answer_sticker(sticker=media_file_id)
                if caption_text:
                    await callback.message.answer(cap)
            else:
                await callback.answer(t("not_found", lang), show_alert=True)
                return
        except TelegramBadRequest as e:
            logger.warning("ticket-view-media send failed: %s", e, exc_info=True)
            await callback.answer(f"⚠️ {e}", show_alert=True)
            return
        except Exception as e:
            logger.warning("ticket-view-media unexpected: %s", e, exc_info=True)
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        await callback.answer(t("media_sent", lang))

    @router.callback_query(TicketCB.filter(F.action == "reply"))
    async def cb_ticket_reply(callback: CallbackQuery, callback_data: TicketCB,
                              state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        ticket = await db.get_ticket(callback_data.ticket_id)
        if not ticket:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        await state.set_state(UserStates.waiting_for_ticket_reply)
        await state.update_data(ticket_id=callback_data.ticket_id)
        # TICKET-1 Feature 3 — prompt now mentions that media attachments
        # (photo / screenshot) are accepted as evidence.
        header = (f"💬 <b>Reply to ticket #{callback_data.ticket_id}</b>\n"
                  f"📝 {escape_html(ticket['subject'])}\n\n")
        # TICKET-MEDIA-1: use show_view instead of edit_text.  When the user
        # (or admin) taps "Reply" on a ticket-reply NOTIFICATION that was sent
        # as a photo message (because the reply carried a screenshot),
        # callback.message is a photo — and edit_text would fail with
        # "Bad Request: there is no text in the message to edit".  show_view
        # detects this and falls back to delete + answer, so the reply prompt
        # replaces the photo cleanly.
        await show_view(
            callback.message,
            text=header + t("ask_reply_with_media", lang),
            reply_markup=kb_cancel(lang),
            state=state,
        )
        await callback.answer()

    @router.message(UserStates.waiting_for_ticket_reply)
    async def ms_ticket_reply(message: Message, state: FSMContext, db_user: dict):
        """Accept text OR any media attachment (photo, document, video, voice,
        audio/music, animation/GIF, round-video note, sticker — with optional
        caption) as a ticket reply. Persist the message + media metadata, then
        notify the other party — sending the media too if any. (TICKET-1)"""
        await del_inbound(message, state)
        lang = _lang(db_user)
        # ---- Extract media + text from the incoming message ------------------
        media_type, media_file_id, caption, msg_text = extract_ticket_media(message)
        # If neither media nor text was sent (e.g. a location, contact, poll,
        # dice, or any other unsupported content type), re-prompt the user
        # instead of silently storing an empty ticket message.
        if not media_type and not msg_text:
            header = "💬 <b>Reply to ticket</b>\n\n"
            await message.answer(header + t("ask_reply_with_media", lang),
                                 reply_markup=kb_cancel(lang))
            return
        # For media replies, the stored `message` column is the caption (or an
        # empty string). The media-type marker is rendered at view time.
        if media_type:
            msg_text = caption[:TICKET_REPLY_MAX_CHARS]
        # ---- Persist ----------------------------------------------------------
        data = await state.get_data()
        ticket_id = data.get("ticket_id")
        await state.clear()
        ticket = await db.get_ticket(ticket_id)
        if not ticket:
            await message.answer(t("not_found", lang), reply_markup=kb_back_to_menu(lang))
            return
        is_admin = message.from_user.id in ADMIN_IDS
        # Users cannot reply to a closed ticket (they must reopen it first).
        if not is_admin and ticket["status"] == "closed":
            await message.answer(t("ticket_closed", lang, id=ticket_id),
                                 reply_markup=kb_back_to_menu(lang))
            return
        sender = "admin" if is_admin else "user"
        await db.add_ticket_message(
            ticket_id, sender, msg_text,
            media_type=media_type,
            media_file_id=media_file_id,
            media_caption=caption,
        )
        # ---- Build the notification body + send (with media if attached) ----
        # Body shown after the header — for text replies it's the message text;
        # for media replies it's the caption (or a placeholder marker).
        if media_type:
            body_for_notify = caption if caption else f"[{media_type}]"
        else:
            body_for_notify = msg_text
        if is_admin:
            owner = await db.get_user(ticket["user_tg_id"]) or {}
            ulang = L(owner.get("language", DEFAULT_LANGUAGE))
            notify_text = (
                f"💬 <b>Admin replied — Ticket #{ticket_id}</b>\n"
                f"📝 {escape_html(ticket['subject'])}\n"
                f"💬 {escape_html(body_for_notify[:500])}"
            )
            reply_kb = kb_ticket_view(ticket_id, False, ulang, "open",
                                      user_tg_id=ticket["user_tg_id"])
            await _send_ticket_reply_notify(
                bot, ticket["user_tg_id"], notify_text,
                media_type, media_file_id, reply_kb,
                context="ticket-reply user notify",
            )
            await message.answer(t("reply_sent_admin", lang), reply_markup=kb_back_to_menu(lang))
        else:
            for admin_id in ADMIN_IDS:
                notify_text = (
                    f"💬 <b>User replied — Ticket #{ticket_id}</b>\n"
                    f"👤 {escape_html(message.from_user.full_name)} (<code>{message.from_user.id}</code>)\n"
                    f"💬 {escape_html(body_for_notify[:500])}"
                )
                reply_kb = kb_ticket_view(ticket_id, True, "en", "open",
                                          user_tg_id=ticket["user_tg_id"])
                await _send_ticket_reply_notify(
                    bot, admin_id, notify_text,
                    media_type, media_file_id, reply_kb,
                    context="ticket-reply admin notify",
                )
            await message.answer(t("reply_sent_user", lang), reply_markup=kb_back_to_menu(lang))

    @router.callback_query(TicketCB.filter(F.action == "reopen"))
    async def cb_ticket_reopen(callback: CallbackQuery, callback_data: TicketCB, db_user: dict):
        lang = _lang(db_user)
        ticket = await db.get_ticket(callback_data.ticket_id)
        if not ticket:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        if ticket["user_tg_id"] != callback.from_user.id and callback.from_user.id not in ADMIN_IDS:
            await callback.answer(t("access_denied", lang), show_alert=True)
            return
        await db.reopen_ticket(callback_data.ticket_id)
        # Notify admins
        for admin_id in ADMIN_IDS:
            await safe_notify(
                bot.send_message(
                    admin_id,
                    f"🔓 <b>Ticket #{callback_data.ticket_id} reopened</b>\n"
                    f"👤 {escape_html(callback.from_user.full_name)}\n📝 {escape_html(ticket['subject'])}",
                    reply_markup=kb_ticket_view(callback_data.ticket_id, True, "en", "open",
                                                user_tg_id=ticket["user_tg_id"]),
                ),
                context="ticket-reopen admin notify",
            )
        await callback.answer(t("ticket_reopened", lang, id=callback_data.ticket_id), show_alert=True)
        # Re-render the ticket view
        messages = await db.get_ticket_messages(callback_data.ticket_id)
        cat_label = t(f"cat_{ticket.get('category', 'other')}", lang) if ticket.get("category") in ("technical", "payment", "account", "other") else ticket.get("category", "other")
        text = (
            f"🎫 <b>Ticket #{ticket['id']}</b>\n"
            f"🏷 {cat_label}\n📝 {escape_html(ticket['subject'])}\n"
            f"📊 {t('ticket_status_open', lang)}\n📅 {fmt_iso(ticket.get('created_at'))}\n\n"
        )
        # LOW — add media markers ([📎 Photo] etc.) just like cb_ticket_view,
        # so media-bearing messages aren't rendered as empty lines on reopen.
        media_label_map = _media_label_map(lang)
        for m in messages:
            who = "👤" if m["sender"] == "user" else "🛡"
            mtext = (m.get("message") or "")
            mt = m.get("media_type") or ""
            if mt:
                label = media_label_map.get(mt, "Media")
                text += f"<b>{who} {fmt_iso(m.get('created_at'))}</b>\n[📎 {label}]"
                if mtext:
                    text += f"\n{escape_html(mtext[:TICKET_MESSAGE_MAX_CHARS])}"
                text += "\n\n"
            else:
                text += f"<b>{who} {fmt_iso(m.get('created_at'))}</b>\n{escape_html(mtext[:TICKET_MESSAGE_MAX_CHARS])}\n\n"
        # TICKET-MEDIA-1: use show_view — the "Reopen" button may be on a
        # closed-ticket photo notification, so callback.message may be a photo.
        await show_view(callback.message, text=text,
                        reply_markup=kb_ticket_view(ticket["id"], False, lang, "open"))

    # ====================================================== GUIDE (GUIDES)
    # Dual guides: "Using the bot" + "How to connect". Both are editable by
    # the admin (Settings → Guides). If the admin hasn't set a custom text,
    # we fall back to the rich DEFAULT_GUIDE_* constants.
    async def _get_guide_text(kind: str, lang: str) -> str:
        """Fetch a guide text from settings, falling back to defaults."""
        key = f"guide_{kind}_{lang}"
        val = await db.get_setting(key, "")
        if val and val.strip():
            return val
        defaults = {
            ("usage", "en"): DEFAULT_GUIDE_USAGE_EN,
            ("usage", "fa"): DEFAULT_GUIDE_USAGE_FA,
            ("connection", "en"): DEFAULT_GUIDE_CONNECTION_EN,
            ("connection", "fa"): DEFAULT_GUIDE_CONNECTION_FA,
        }
        return defaults.get((kind, lang), DEFAULT_GUIDE_USAGE_EN)

    @router.callback_query(MenuCB.filter(F.action == "guide"))
    async def cb_guide(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        text = (
            f"{t('guide_title', lang)}\n\n"
            + ("دو راهنما برای شما آماده شده — یکی برای کار با خود ربات، یکی برای اتصال به VPN. هر کدام را نیاز داری باز کن."
               if lang == "fa"
               else "Two guides are here — one for using the bot, one for connecting to the VPN. Open whichever you need.")
        )
        kb = InlineKeyboardBuilder()
        kb.button(text=t("guide_usage_btn", lang),
                  callback_data=MenuCB(action="guide_usage").pack(), style="primary")
        kb.button(text=t("guide_connection_btn", lang),
                  callback_data=MenuCB(action="guide_connection").pack(), style="primary")
        kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack(), style="danger")
        kb.adjust(1, 1, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(MenuCB.filter(F.action == "guide_usage"))
    async def cb_guide_usage(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        text = await _get_guide_text("usage", lang)
        kb = InlineKeyboardBuilder()
        kb.button(text=t("guide_connection_btn", lang),
                  callback_data=MenuCB(action="guide_connection").pack(), style="primary")
        kb.button(text=t("back", lang), callback_data=MenuCB(action="help").pack(), style="primary")
        kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack(), style="danger")
        kb.adjust(1, 1, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(MenuCB.filter(F.action == "guide_connection"))
    async def cb_guide_connection(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        text = await _get_guide_text("connection", lang)
        kb = InlineKeyboardBuilder()
        kb.button(text=t("guide_usage_btn", lang),
                  callback_data=MenuCB(action="guide_usage").pack(), style="primary")
        kb.button(text=t("back", lang), callback_data=MenuCB(action="help").pack(), style="primary")
        kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack(), style="danger")
        kb.adjust(1, 1, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    return router


# ============================================================================
# SECTION 11: ADMIN HANDLERS
# ============================================================================

def create_admin_router(db: Database, api: PanelAPI, lb: LoadBalancer, bot: Bot) -> Router:
    """Admin panel: servers, plans (with inbound picker), users (manual balance
    + account management), finance, promos, gift codes, tickets, broadcast,
    editable settings, depleted cleanup, groups."""
    router = Router()
    guard = AdminGuard(db)

    @router.message.middleware()
    async def _msg_mw(handler, event, data):
        return await guard(handler, event, data)

    @router.callback_query.middleware()
    async def _cb_mw(handler, event, data):
        return await guard(handler, event, data)

    # H12 — FSM stale-state guard. Several admin callbacks set a "waiting for
    # text" state (e.g. waiting_for_reject_reason, waiting_for_broadcast_message,
    # waiting_for_add_balance, setting_edit_value). If the admin then taps an
    # OLD inline button (Telegram keeps them live), the stale state persists
    # and the next text the admin types goes to the WRONG target — e.g.
    # rejecting the wrong payment, or broadcasting a half-finished message to
    # thousands of users.
    #
    # This outer middleware clears FSM state on EVERY admin callback UNLESS the
    # callback's action is in the allowlist below (actions that explicitly SET a
    # new state as part of their flow). This is far more robust than adding
    # state.clear() to 30+ individual handlers.
    _FSM_SETTING_ACTIONS = frozenset({
        "add_balance", "deduct_balance", "acc_extend",       # user balance / extend
        "create_promo",                                     # promo creation flow
        "create_gift", "gift_amount",                       # gift code creation flow
        "broadcast",                                        # broadcast flow
        "plan_add", "plan_edit_field",                      # plan creation/edit flow
        "srv_add", "srv_edit",                              # server add/edit flow
        "import_set_tg",                                    # import flow
        "admin_ticket_reply",                               # ticket reply flow
        "setting_edit",                                     # settings edit flow
        "admin_account_create",                             # admin manual account creation
        "users",                                            # user search sets its own state
    })

    @router.callback_query.outer_middleware()
    async def _fsm_clear_mw(handler, event, data):
        # Inspect the callback_data to extract the action. We support both
        # AdminCB (action field) and other callback types (MenuCB/PlanCB/etc.)
        # — for non-AdminCB callbacks we always clear (they're pure navigation).
        from aiogram.fsm.context import FSMContext
        state: Optional[FSMContext] = data.get("state")
        if state is not None:
            action: Optional[str] = None
            # Try AdminCB first (most admin actions use it).
            try:
                from aiogram.filters.callback_data import CallbackData
                # event.data is the packed callback string like "admin:main"
                raw = getattr(event, "data", None) or ""
                if raw.startswith("admin:"):
                    action = raw.split(":", 1)[1].split(":", 1)[0]
            except Exception:
                pass
            if action is None or action not in _FSM_SETTING_ACTIONS:
                await state.clear()
        return await handler(event, data)

    async def _currency() -> str:
        return await db.get_setting("currency", DEFAULT_CURRENCY) or DEFAULT_CURRENCY

    async def admin_lang(tg_id: int) -> str:
        """Return the admin user's preferred language (from users.language).

        Used by admin handlers so callback alerts / future admin screens can be
        localised instead of hard-coded English. Falls back to DEFAULT_LANGUAGE
        if the admin row is missing or has no language set. (M11)
        """
        user = await db.get_user(tg_id)
        return L((user or {}).get("language", DEFAULT_LANGUAGE)) if user else DEFAULT_LANGUAGE

    async def _pa_lang(tg_id: int) -> str:
        """PA-LANG: Return the display language for payment-admin screens.

        Full admins (ADMIN_IDS) ALWAYS see English — the main admin panel is
        English-only per the user's request.  Payment-only admins see their
        selected language (users.language), defaulting to DEFAULT_LANGUAGE.
        This is used by every payment-admin handler so the entire payment
        section (menu, pending list, history, detail view, approve/reject
        flows, receipt captions) is localised for payment admins while the
        full-admin experience stays English.
        """
        if tg_id in ADMIN_IDS:
            return "en"
        user = await db.get_user(tg_id)
        return L((user or {}).get("language", DEFAULT_LANGUAGE)) if user else DEFAULT_LANGUAGE

    async def _is_full_admin(tg_id: int) -> bool:
        return tg_id in ADMIN_IDS

    # ------------------------------------------------------------- /admin
    @router.message(Command("admin"))
    async def cmd_admin(message: Message):
        if await _is_full_admin(message.from_user.id):
            await message.answer("⚙️ <b>Admin Panel</b>", reply_markup=kb_admin_menu())
        else:
            # Payment-only admin — limited menu. PA-LANG: localised.
            pal = await _pa_lang(message.from_user.id)
            await message.answer(
                f"{t('pa_menu_title', pal)}\n\n{t('pa_menu_desc', pal)}",
                reply_markup=kb_payment_admin_menu(pal),
            )

    @router.callback_query(AdminCB.filter(F.action == "main"))
    async def cb_admin_main(callback: CallbackQuery, state: FSMContext):
        # ADMIN-NAV-CLEAR: clear any stale FSM state (e.g. setting_edit_value)
        # so a mid-edit admin who taps a nav button can't accidentally fire
        # ms_setting_edit with the old edit_type/key/email on their next text.
        await state.clear()
        if await _is_full_admin(callback.from_user.id):
            await show_view(callback.message, text="⚙️ <b>Admin Panel</b>", reply_markup=kb_admin_menu())
        else:
            # PA-LANG: payment-admin menu is localised.
            pal = await _pa_lang(callback.from_user.id)
            await show_view(callback.message,
                text=f"{t('pa_menu_title', pal)}\n\n{t('pa_menu_desc', pal)}",
                reply_markup=kb_payment_admin_menu(pal))
        await callback.answer()

    # ------------------------------------------------------- submenus
    # ADMIN-MENU-REWORK: top-level admin panel split into category submenus
    # so the main panel stays clean (5 rows × 2 instead of 14 flat buttons).
    # Each submenu is a simple landing page with the category's buttons +
    # a back-to-admin button.  Full-admin-only — payment admins never see
    # these (AdminGuard blocks admin:payments_menu / promos_menu / support_menu).
    @router.callback_query(AdminCB.filter(F.action == "payments_menu"))
    async def cb_payments_menu(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await show_view(callback.message,
            text="<b>💳 Payments Management</b>\n\nApprove pending payments, review receipt history, or audit each payment admin's approvals.",
            reply_markup=kb_payments_menu())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "promos_menu"))
    async def cb_promos_menu(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await show_view(callback.message,
            text="<b>🎁 Promotions & Marketing</b>\n\nPromo codes, gift codes, and broadcasts.",
            reply_markup=kb_promos_menu())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "support_menu"))
    async def cb_support_menu(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await show_view(callback.message,
            text="<b>💬 Support & Tickets</b>\n\nView and reply to user support tickets.",
            reply_markup=kb_support_menu())
        await callback.answer()

    # ------------------------------------------------------- dashboard
    @router.callback_query(AdminCB.filter(F.action == "dashboard"))
    async def cb_dashboard(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        total_users = await db.count_users()
        active = await db.get_all_active_accounts()
        open_tickets = await db.count_open_tickets()
        servers = await db.get_servers(active_only=True)
        healthy = [s for s in servers if s["is_healthy"]]
        rev = await db.get_revenue_stats(days=30)
        # total accounts (incl. inactive) — count from DB directly
        async with db._db.execute("SELECT COUNT(*) AS cnt FROM accounts") as cur:
            total_accounts = (await cur.fetchone())["cnt"]

        stats = {
            "total_users": total_users,
            "active_accounts": len(active),
            "total_accounts": total_accounts,
            "open_tickets": open_tickets,
            "servers_online": len(healthy),
            "revenue_30d": rev["total_revenue"],
            "revenue_today": rev["today_revenue"],
            "revenue_all": rev["all_time_revenue"],
        }
        cur = await _currency()
        rich = rich_tables.dashboard_rich(stats, cur, rev.get("top_plans"), fmt_price)
        await show_view(callback.message, rich=rich, reply_markup=kb_admin_menu())
        await callback.answer()

    # ====================================================== SERVERS
    @router.callback_query(AdminCB.filter(F.action == "servers"))
    async def cb_servers(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        servers = await db.get_servers()
        await show_view(callback.message, text="🖥 <b>Servers</b>", reply_markup=kb_servers(servers))
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "add"))
    async def cb_server_add(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_server_alias)
        await show_view(callback.message, text=
            "➕ <b>Add Server</b>\n\nEnter alias (e.g. <code>DE-Frankfurt</code>):",
            reply_markup=kb_cancel("en"),
            state=state,
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_server_alias)
    async def ms_srv_alias(message: Message, state: FSMContext):
        await del_inbound(message, state)
        await state.update_data(alias=message.text.strip())
        await state.set_state(AdminStates.waiting_for_server_url)
        await track_prompt(await message.answer("🔗 Enter panel URL (e.g. <code>https://1.2.3.4:2053</code>):",
                             reply_markup=kb_cancel("en")), state)

    @router.message(AdminStates.waiting_for_server_url)
    async def ms_srv_url(message: Message, state: FSMContext):
        await del_inbound(message, state)
        url = (message.text or "").strip().rstrip("/")
        if not url.startswith("http"):
            await message.answer("❌ URL must start with http:// or https://", reply_markup=kb_cancel("en"))
            return
        await state.update_data(panel_url=url)
        await state.set_state(AdminStates.waiting_for_server_token)
        await track_prompt(await message.answer("🔐 Enter API token:", reply_markup=kb_cancel("en")), state)

    @router.message(AdminStates.waiting_for_server_token)
    async def ms_srv_token(message: Message, state: FSMContext):
        await del_inbound(message, state)
        token = (message.text or "").strip()
        data = await state.get_data()
        await state.set_state(AdminStates.waiting_for_server_capacity)
        await state.update_data(token=token)
        await track_prompt(await message.answer(
            "🔢 Enter max client capacity (0 = unlimited):",
            reply_markup=kb_cancel("en"),
        ), state)

    @router.message(AdminStates.waiting_for_server_capacity)
    async def ms_srv_capacity(message: Message, state: FSMContext):
        await del_inbound(message, state)
        try:
            cap = int((message.text or "0").strip())
        except ValueError:
            await message.answer("❌ Enter a number:", reply_markup=kb_cancel("en"))
            return
        await state.update_data(capacity=cap)
        await state.set_state(AdminStates.waiting_for_server_priority)
        await track_prompt(await message.answer("⭐ Enter priority (lower = preferred, default 10):",
                             reply_markup=kb_cancel("en")), state)

    @router.message(AdminStates.waiting_for_server_priority)
    async def ms_srv_priority(message: Message, state: FSMContext):
        await del_inbound(message, state)
        try:
            pri = int((message.text or "10").strip())
        except ValueError:
            await message.answer("❌ Enter a number:", reply_markup=kb_cancel("en"))
            return
        await state.update_data(priority=pri)
        await state.set_state(AdminStates.waiting_for_server_location)
        await track_prompt(await message.answer("🌍 Enter location (e.g. <code>Germany</code>) or <code>-</code> for none:",
                             reply_markup=kb_cancel("en")), state)

    @router.message(AdminStates.waiting_for_server_location)
    async def ms_srv_location(message: Message, state: FSMContext):
        await del_inbound(message, state)
        loc = (message.text or "").strip()
        if loc == "-":
            loc = ""
        data = await state.get_data()
        await state.clear()
        await message.answer("⏳ Testing connection...")
        ok, msg = await api.test_panel_connection(data["panel_url"], data["token"])
        if not ok:
            await message.answer(f"❌ Connection failed: {msg}", reply_markup=kb_admin_menu())
            return
        # Fetch sub URI from panel settings
        sub_uri = await api.fetch_sub_uri(data["panel_url"], data["token"])
        server_id = await db.add_server(
            data["alias"], data["panel_url"], data["token"],
            capacity=data["capacity"], priority=data["priority"], location=loc,
        )
        if sub_uri:
            await db.update_server(server_id, sub_uri=sub_uri)
        inbounds = await api.get_inbounds(data["panel_url"], data["token"])
        await db.sync_inbounds(server_id, inbounds)
        await message.answer(
            f"✅ <b>Server added</b>\n🖥 {escape_html(data['alias'])}\n"
            f"🔗 <code>{escape_html(data['panel_url'])}</code>\n"
            f"📡 Inbounds: {len(inbounds)}\n"
            f"📡 Sub URI: <code>{escape_html(sub_uri or '-')}</code>",
            reply_markup=kb_admin_menu(),
        )

    @router.callback_query(ServerCB.filter(F.action == "view"))
    async def cb_srv_view(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        online = await api.get_online_clients(srv["panel_url"], srv["api_token"])
        rich = rich_tables.server_health_rich(srv, len(online) if isinstance(online, list) else 0, fmt_bytes)
        await show_view(callback.message, rich=rich, reply_markup=kb_server_view(srv["id"]))
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "sync"))
    async def cb_srv_sync(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        inbounds = await api.get_inbounds(srv["panel_url"], srv["api_token"])
        await db.sync_inbounds(srv["id"], inbounds)
        sub_uri = await api.fetch_sub_uri(srv["panel_url"], srv["api_token"])
        if sub_uri:
            await db.update_server(srv["id"], sub_uri=sub_uri)
        await callback.answer(f"✅ Synced {len(inbounds)} inbounds", show_alert=True)

    @router.callback_query(ServerCB.filter(F.action == "sync_all"))
    async def cb_srv_sync_all(callback: CallbackQuery):
        servers = await db.get_servers(active_only=True)
        total = 0
        for srv in servers:
            inbounds = await api.get_inbounds(srv["panel_url"], srv["api_token"])
            await db.sync_inbounds(srv["id"], inbounds)
            sub_uri = await api.fetch_sub_uri(srv["panel_url"], srv["api_token"])
            if sub_uri:
                await db.update_server(srv["id"], sub_uri=sub_uri)
            total += len(inbounds)
        await callback.answer(f"✅ {total} inbounds across {len(servers)} servers", show_alert=True)

    @router.callback_query(ServerCB.filter(F.action == "test"))
    async def cb_srv_test(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        ok, msg = await api.test_panel_connection(srv["panel_url"], srv["api_token"])
        await db.update_server_health(srv["id"], ok, "" if ok else msg)
        await callback.answer("✅ OK" if ok else f"❌ {msg}", show_alert=True)

    @router.callback_query(ServerCB.filter(F.action == "restart_ask"))
    async def cb_srv_restart_ask(callback: CallbackQuery, callback_data: ServerCB):
        # SERVER-RESTART-CONFIRM: Restart kicks every client off the server
        # (brief disconnect for all users). Require explicit confirmation so
        # a misclick doesn't cause a mass-disconnect.
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Confirm Restart", callback_data=ServerCB(action="restart", server_id=srv["id"]).pack(), style="danger")
        kb.button(text="🔙 Back", callback_data=ServerCB(action="view", server_id=srv["id"]).pack(), style="primary")
        await show_view(callback.message, text=
            f"🔄 <b>Restart server {escape_html(srv['alias'])}?</b>\n\n"
            f"⚠️ This will restart the 3x-ui panel process. All connected clients will be briefly disconnected.",
            reply_markup=kb.as_markup(),
        )
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "restart"))
    async def cb_srv_restart(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        r = await api.restart_panel(srv["panel_url"], srv["api_token"])
        await callback.answer("✅ Restart initiated" if r.get("success") else f"❌ {r.get('msg')}", show_alert=True)

    @router.callback_query(ServerCB.filter(F.action == "backup"))
    async def cb_srv_backup(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        r = await api.backup_to_telegram(srv["panel_url"], srv["api_token"])
        await callback.answer("✅ Backup sent" if r.get("success") else f"❌ {r.get('msg')}", show_alert=True)

    @router.callback_query(ServerCB.filter(F.action == "stats"))
    async def cb_srv_stats(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        data = await api.get_clients_paged(srv["panel_url"], srv["api_token"], page=1, page_size=25)
        summary = data.get("summary", {})
        items = data.get("items", [])
        total = data.get("total", 0)
        online = await api.get_online_clients(srv["panel_url"], srv["api_token"])
        on_count = len(online) if isinstance(online, list) else 0
        blocks = [rich_tables.heading(f"📊 {srv['alias']}"),
                  rich_tables.kv_table([
                      ("Total", total), ("Active", summary.get("active", 0)),
                      ("Online", on_count),
                      ("Depleted", len(summary.get("depleted", []))),
                      ("Expiring", len(summary.get("expiring", []))),
                      ("Deactive", len(summary.get("deactive", []))),
                  ])]
        if items:
            rows = [((it.get("email") or "—"), "Active" if it.get("enable") else "Off",
                     fmt_ts(it.get("expiryTime", 0))[:10]) for it in items[:10]]
            blocks.append(rich_tables.divider())
            blocks.append(rich_tables.heading("📋 Clients", size=4))
            blocks.append(rich_tables.grid_table(["Email", "Status", "Expiry"], rows,
                                                 aligns=["left", "center", "center"]))
        rich = rich_tables.rich_message(*blocks)
        await show_view(callback.message, rich=rich, reply_markup=kb_server_view(srv["id"]))
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "inbounds"))
    async def cb_srv_inbounds(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        inbounds = await db.get_inbounds(srv["id"])
        rich = rich_tables.inbounds_rich(srv["alias"], inbounds)
        await show_view(callback.message, rich=rich, reply_markup=kb_server_view(srv["id"]))
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "edit"))
    async def cb_srv_edit(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        kb.button(style="primary", text="✏️ Alias", callback_data=AdminCB(action="srv_edit_field", data=f"{srv['id']}_alias").pack())
        kb.button(style="primary", text="⭐ Priority", callback_data=AdminCB(action="srv_edit_field", data=f"{srv['id']}_priority").pack())
        kb.button(style="primary", text="🔢 Capacity", callback_data=AdminCB(action="srv_edit_field", data=f"{srv['id']}_capacity").pack())
        kb.button(style="primary", text="🌍 Location", callback_data=AdminCB(action="srv_edit_field", data=f"{srv['id']}_location").pack())
        kb.button(style="primary", text="🔗 Sub URI", callback_data=AdminCB(action="srv_edit_field", data=f"{srv['id']}_sub_uri").pack())
        kb.button(style="primary", text="🔑 Token", callback_data=AdminCB(action="srv_edit_field", data=f"{srv['id']}_api_token").pack())
        toggle = "⚪ Activate" if not srv["is_active"] else "🔴 Disable"
        kb.button(text=toggle, callback_data=ServerCB(action="toggle", server_id=srv["id"]).pack(), style="primary")
        kb.button(text="🔙 Back", callback_data=ServerCB(action="view", server_id=srv["id"]).pack(), style="danger")
        kb.adjust(2, 2, 2, 1, 1)
        await show_view(callback.message, text="✏️ <b>Edit server — pick a field</b>", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "toggle"))
    async def cb_srv_toggle(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        await db.toggle_server(srv["id"], not srv["is_active"])
        await callback.answer(t("toggled", await admin_lang(callback.from_user.id)), show_alert=True)
        servers = await db.get_servers()
        await show_view(callback.message, text="🖥 <b>Servers</b>", reply_markup=kb_servers(servers))

    @router.callback_query(AdminCB.filter(F.action == "srv_edit_field"))
    async def cb_srv_edit_field(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        server_id, field = callback_data.data.split("_", 1)
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="server", server_id=int(server_id), field=field)
        labels = {"alias": "alias", "priority": "priority (number)", "capacity": "capacity (number)",
                  "location": "location", "sub_uri": "subscription base URI", "api_token": "API token"}
        await show_view(callback.message, text=
            f"✏️ Enter new <b>{labels.get(field, field)}</b>:",
            reply_markup=kb_cancel("en"),
            state=state,
        )
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "delete_ask"))
    async def cb_srv_delete_ask(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        kb.button(text="🗑 Confirm Delete", callback_data=ServerCB(action="delete", server_id=srv["id"]).pack(), style="danger")
        kb.button(text="🔙 Back", callback_data=ServerCB(action="view", server_id=srv["id"]).pack(), style="primary")
        await show_view(callback.message, text=
            f"🗑 <b>Delete server {escape_html(srv['alias'])}?</b>\n\nAccounts on this server will remain in DB but become unmanageable.",
            reply_markup=kb.as_markup(),
        )
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "delete"))
    async def cb_srv_delete(callback: CallbackQuery, callback_data: ServerCB):
        # H9 — wrap in try/except: even with ON DELETE SET NULL (added in the
        # accounts FK migration), a concurrent write or an unexpected constraint
        # could raise IntegrityError. Without this guard the callback would
        # never be answered (endless spinner).
        try:
            await db.delete_server(callback_data.server_id)
        except Exception as e:
            logger.error("cb_srv_delete failed: %s", e)
            await callback.answer(f"❌ {str(e)[:80]}", show_alert=True)
            return
        servers = await db.get_servers()
        await show_view(callback.message, text="✅ Server deleted.", reply_markup=kb_servers(servers))
        await callback.answer("Deleted")

    # ====================================================== PLANS
    @router.callback_query(AdminCB.filter(F.action == "plans"))
    async def cb_plans(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        plans = await db.get_plans(active_only=False)
        await show_view(callback.message, text="📦 <b>Plans</b>", reply_markup=kb_admin_plans(plans))
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "add"))
    async def cb_plan_add(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_plan_name)
        await show_view(callback.message, text="➕ <b>Add Plan</b>\n\nEnter name:", reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    @router.message(AdminStates.waiting_for_plan_name)
    async def ms_plan_name(message: Message, state: FSMContext):
        await del_inbound(message, state)
        await state.update_data(name=message.text.strip())
        await state.set_state(AdminStates.waiting_for_plan_desc)
        await track_prompt(await message.answer("📝 Description (or <code>-</code> for none):", reply_markup=kb_cancel("en")), state)

    @router.message(AdminStates.waiting_for_plan_desc)
    async def ms_plan_desc(message: Message, state: FSMContext):
        await del_inbound(message, state)
        desc = message.text.strip()
        if desc == "-":
            desc = ""
        await state.update_data(description=desc)
        await state.set_state(AdminStates.waiting_for_plan_traffic)
        await track_prompt(await message.answer("💾 Traffic in GB (0 = unlimited).\n💡 Decimal values OK, e.g. <code>0.2</code> for 200 MB, <code>0.5</code> for 500 MB:",
                             reply_markup=kb_cancel("en")), state)

    @router.message(AdminStates.waiting_for_plan_traffic)
    async def ms_plan_traffic(message: Message, state: FSMContext):
        await del_inbound(message, state)
        try:
            # Accept fractional GB (0.2 = 200 MB) — the panel API receives
            # bytes (total_gb * GB), so any decimal works.
            gb = float(message.text.strip())
        except (ValueError, TypeError):
            await message.answer("❌ Number please (e.g. 5 or 0.2):", reply_markup=kb_cancel("en"))
            return
        if gb < 0:
            await message.answer("❌ Traffic cannot be negative.", reply_markup=kb_cancel("en"))
            return
        await state.update_data(traffic_gb=gb)
        await state.set_state(AdminStates.waiting_for_plan_duration)
        await track_prompt(await message.answer("📅 Duration in days (0 = never):", reply_markup=kb_cancel("en")), state)

    @router.message(AdminStates.waiting_for_plan_duration)
    async def ms_plan_duration(message: Message, state: FSMContext):
        await del_inbound(message, state)
        try:
            days = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Number please:", reply_markup=kb_cancel("en"))
            return
        # BUG-4 FIX: reject negative duration — the buy/renew flow computes
        # `expiry_time = (...) if plan["duration_days"] > 0 else 0`, so a
        # negative value silently becomes 0 (never expires). Admin typo
        # (-30 instead of 30) would grant unlimited accounts.
        if days < 0:
            await message.answer("❌ Duration cannot be negative. Use 0 for never-expires.",
                                 reply_markup=kb_cancel("en"))
            return
        await state.update_data(duration_days=days)
        await state.set_state(AdminStates.waiting_for_plan_price)
        cur = await _currency()
        unit = "Toman" if cur == "toman" else "USD"
        await track_prompt(await message.answer(f"💵 Price in {unit}:", reply_markup=kb_cancel("en")), state)

    @router.message(AdminStates.waiting_for_plan_price)
    async def ms_plan_price(message: Message, state: FSMContext):
        await del_inbound(message, state)
        try:
            price = float(message.text.strip())
        except ValueError:
            await message.answer("❌ Number please:", reply_markup=kb_cancel("en"))
            return
        # BUG-1 FIX: reject negative prices — a negative price would make
        # try_deduct_balance add to the user's balance (balance - (-price)),
        # letting users inflate their wallet by "buying" the plan.
        if price < 0:
            await message.answer("❌ Price cannot be negative.", reply_markup=kb_cancel("en"))
            return
        await state.update_data(price=price)
        await state.set_state(AdminStates.waiting_for_plan_limit_ip)
        await track_prompt(await message.answer("🔢 Max simultaneous IPs (0 = unlimited):", reply_markup=kb_cancel("en")), state)

    @router.message(AdminStates.waiting_for_plan_limit_ip)
    async def ms_plan_limit_ip(message: Message, state: FSMContext):
        await del_inbound(message, state)
        try:
            limit_ip = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Number please:", reply_markup=kb_cancel("en"))
            return
        data = await state.get_data()
        await state.clear()
        plan_id = await db.add_plan(
            name=data["name"], description=data["description"], traffic_gb=data["traffic_gb"],
            duration_days=data["duration_days"], price=data["price"], limit_ip=limit_ip,
            inbound_ids=[],   # admin picks inbounds next
        )
        plan = await db.get_plan(plan_id)
        await message.answer(
            f"✅ <b>Plan created</b>\n{fmt_plan_card(plan, 'en', await _currency())}\n\n"
            f"🔗 Now select inbounds for this plan:",
            reply_markup=kb_inbound_picker("?", [], set(), plan_id) if False else await _inbound_kb(plan_id),
        )

    async def _inbound_kb(plan_id: int) -> InlineKeyboardMarkup:
        """Build a multi-server inbound picker for a plan."""
        plan = await db.get_plan(plan_id)
        selected = set()
        if plan and plan.get("inbound_ids"):
            try:
                selected = set(json.loads(plan["inbound_ids"]))
            except Exception:
                selected = set()
        # Build a flat keyboard across all active servers
        kb = InlineKeyboardBuilder()
        servers = await db.get_servers(active_only=True)
        for srv in servers:
            kb.button(style="primary", text=f"— {escape_html(srv['alias'])} —", callback_data=NoopCB().pack())
            inbounds = await db.get_inbounds(srv["id"], enabled_only=True)
            for ib in inbounds:
                key = f"{srv['id']}_{ib['inbound_id']}"
                mark = "✅" if key in selected else "⬜"
                proto = ib.get("protocol", "?")
                remark = ib.get("remark") or f"id{ib['inbound_id']}"
                kb.button(style="primary", 
                    text=f"{mark} {remark} ({proto})",
                    callback_data=InboundCB(action="toggle", key=key, plan_id=plan_id).pack(),
                )
        kb.button(text="💾 Save", callback_data=InboundCB(action="save", plan_id=plan_id).pack(), style="success")
        kb.button(text="⬜ Clear All", callback_data=InboundCB(action="clear", plan_id=plan_id).pack(), style="danger")
        kb.button(text="🔙 Back", callback_data=PlanCB(action="admin_view", plan_id=plan_id).pack(), style="danger")
        kb.adjust(1, 1)
        return kb.as_markup()

    @router.callback_query(InboundCB.filter(F.action == "toggle"))
    async def cb_ib_toggle(callback: CallbackQuery, callback_data: InboundCB):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            # L12 — admin-facing i18n (was English-only "Plan not found").
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        try:
            selected = set(json.loads(plan.get("inbound_ids") or "[]"))
        except Exception:
            selected = set()
        if callback_data.key in selected:
            selected.discard(callback_data.key)
        else:
            selected.add(callback_data.key)
        await db.update_plan(plan["id"], inbound_ids=json.dumps(list(selected)))
        await show_view(callback.message, text=
            f"🔗 <b>Inbounds for {escape_html(plan['name'])}</b>\nSelected: {len(selected)}",
            reply_markup=await _inbound_kb(plan["id"]),
        )
        await callback.answer()

    @router.callback_query(InboundCB.filter(F.action == "clear"))
    async def cb_ib_clear(callback: CallbackQuery, callback_data: InboundCB):
        await db.update_plan(callback_data.plan_id, inbound_ids="[]")
        await show_view(callback.message, text="Cleared.", reply_markup=await _inbound_kb(callback_data.plan_id))
        await callback.answer()

    @router.callback_query(InboundCB.filter(F.action == "save"))
    async def cb_ib_save(callback: CallbackQuery, callback_data: InboundCB):
        plan = await db.get_plan(callback_data.plan_id)
        await show_view(callback.message, text=
            f"✅ Saved.\n\n{fmt_plan_card(plan, 'en', await _currency())}",
            reply_markup=kb_admin_plan_view(plan["id"], plan["is_active"]),
        )
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "admin_view"))
    async def cb_plan_admin_view(callback: CallbackQuery, callback_data: PlanCB):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        text = fmt_plan_card(plan, "en", await _currency())
        inb = plan.get("inbound_ids") or "[]"
        try:
            n = len(json.loads(inb))
        except Exception:
            n = 0
        text += f"\n\n🔗 Inbounds: {n}\n📊 Status: {'✅' if plan['is_active'] else '❌'}"
        await show_view(callback.message, text=text, reply_markup=kb_admin_plan_view(plan["id"], plan["is_active"]))
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "inbounds"))
    async def cb_plan_inbounds(callback: CallbackQuery, callback_data: PlanCB):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        await show_view(callback.message, text=
            f"🔗 <b>Inbounds — {escape_html(plan['name'])}</b>\nToggle the inbounds this plan can use:",
            reply_markup=await _inbound_kb(plan["id"]),
        )
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "toggle"))
    async def cb_plan_toggle(callback: CallbackQuery, callback_data: PlanCB):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        await db.toggle_plan(plan["id"], not plan["is_active"])
        plans = await db.get_plans(active_only=False)
        await show_view(callback.message, text="📦 <b>Plans</b>", reply_markup=kb_admin_plans(plans))
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "delete_ask"))
    async def cb_plan_delete_ask(callback: CallbackQuery, callback_data: PlanCB):
        # H10 — require a confirmation tap before deleting a plan (one-tap
        # destructive action was too easy to trigger accidentally).
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        kb.button(text="🗑 Confirm Delete", callback_data=PlanCB(action="delete", plan_id=plan["id"]).pack(), style="danger")
        kb.button(text="🔙 Back", callback_data=PlanCB(action="admin_view", plan_id=plan["id"]).pack(), style="primary")
        await show_view(callback.message, text=
            f"🗑 <b>Delete plan {escape_html(plan['name'])}?</b>\n\n"
            f"Existing accounts on this plan will keep working but lose their plan reference.",
            reply_markup=kb.as_markup(),
        )
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "delete"))
    async def cb_plan_delete(callback: CallbackQuery, callback_data: PlanCB):
        try:
            await db.delete_plan(callback_data.plan_id)
        except Exception as e:
            logger.error("cb_plan_delete failed: %s", e)
            await callback.answer(f"❌ {str(e)[:80]}", show_alert=True)
            return
        plans = await db.get_plans(active_only=False)
        await show_view(callback.message, text="✅ Deleted.", reply_markup=kb_admin_plans(plans))
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "edit"))
    async def cb_plan_edit(callback: CallbackQuery, callback_data: PlanCB):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        for field, label in [("name", "📛 Name"), ("description", "📝 Description"),
                             ("traffic_gb", "💾 Traffic (GB)"), ("duration_days", "📅 Duration (days)"),
                             ["price", "💵 Price"], ["limit_ip", "🔢 Max IPs"]]:
            kb.button(style="primary", text=label, callback_data=AdminCB(action="plan_edit_field", data=f"{plan['id']}_{field}").pack())
        kb.button(text="🔙 Back", callback_data=PlanCB(action="admin_view", plan_id=plan["id"]).pack(), style="danger")
        kb.adjust(2, 2, 2, 1)
        await show_view(callback.message, text="✏️ <b>Edit plan — pick a field</b>", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "plan_edit_field"))
    async def cb_plan_edit_field(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        plan_id, field = callback_data.data.split("_", 1)
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="plan", plan_id=int(plan_id), field=field)
        await show_view(callback.message, text=f"✏️ Enter new <b>{field}</b>:", reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    # Generic value handler for ALL admin FSM edits (server / plan / setting_int / acc_extend)
    @router.message(AdminStates.setting_edit_value)
    async def ms_setting_edit(message: Message, state: FSMContext):
        await del_inbound(message, state)
        data = await state.get_data()
        raw = (message.text or "").strip()
        edit_type = data.get("edit_type")
        field = data.get("field")

        # ---- server field edit ----
        if edit_type == "server":
            server_id = data["server_id"]
            if field in ("priority", "capacity"):
                try:
                    val = int(raw)
                except ValueError:
                    await state.clear()
                    await message.answer("❌ Number please.", reply_markup=kb_admin_menu())
                    return
                await db.update_server(server_id, **{field: val})
            else:
                await db.update_server(server_id, **{field: raw if raw != "-" else None})
            await state.clear()
            srv = await db.get_server(server_id)
            online = await api.get_online_clients(srv["panel_url"], srv["api_token"])
            rich = rich_tables.server_health_rich(srv, len(online) if isinstance(online, list) else 0, fmt_bytes)
            await message.answer_rich(rich_message=rich, reply_markup=kb_server_view(server_id))

        # ---- plan field edit ----
        elif edit_type == "plan":
            plan_id = data["plan_id"]
            if field == "traffic_gb":
                # traffic_gb is a REAL value — fractional GB (e.g. 0.2 for
                # 200 MB) is supported.
                try:
                    val = float(raw)
                except ValueError:
                    await state.clear()
                    await message.answer("❌ Number please (e.g. 5 or 0.2).",
                                         reply_markup=kb_admin_menu())
                    return
                if val < 0:
                    await state.clear()
                    await message.answer("❌ Traffic cannot be negative.",
                                         reply_markup=kb_admin_menu())
                    return
                await db.update_plan(plan_id, **{field: val})
            elif field in ("duration_days", "limit_ip"):
                try:
                    val = int(raw)
                except ValueError:
                    await state.clear()
                    await message.answer("❌ Number please.", reply_markup=kb_admin_menu())
                    return
                await db.update_plan(plan_id, **{field: val})
            elif field == "price":
                try:
                    val = float(raw)
                except ValueError:
                    await state.clear()
                    await message.answer("❌ Number please.", reply_markup=kb_admin_menu())
                    return
                # BUG-3 FIX: reject negative price (same exploit as BUG-1 but
                # via plan-edit). Mirrors the negative-check already present in
                # the traffic_gb branch below.
                if val < 0:
                    await state.clear()
                    await message.answer("❌ Price cannot be negative.", reply_markup=kb_admin_menu())
                    return
                await db.update_plan(plan_id, **{field: val})
            else:
                await db.update_plan(plan_id, **{field: raw if raw != "-" else None})
            await state.clear()
            plan = await db.get_plan(plan_id)
            await message.answer(fmt_plan_card(plan, "en", await _currency()),
                                 reply_markup=kb_admin_plan_view(plan_id, plan["is_active"]))

        # ---- bot setting (integer) ----
        elif edit_type == "setting_int":
            key = data.get("key", "")
            # Some integer-typed settings legitimately need fractional values:
            #   - trial_gb / referral_bonus_gb : the panel API receives bytes,
            #     so 0.2 GB (= 200 MB) is a valid quota.
            #   - topup_price_per_gb : price could be fractional in some
            #     currencies (though typically toman is whole).
            # All other setting_int keys remain strict integers.
            _FLOAT_KEYS = {"trial_gb", "referral_bonus_gb", "topup_price_per_gb"}
            if key in _FLOAT_KEYS:
                try:
                    val = float(raw)
                except ValueError:
                    await message.answer(f"❌ Number please (e.g. 5 or 0.2):",
                                         reply_markup=kb_cancel("en"))
                    return
            else:
                try:
                    val = int(raw)
                except ValueError:
                    await message.answer("❌ Number please:", reply_markup=kb_cancel("en"))
                    return
            # M5 — validate known setting keys to prevent misconfiguration
            # (negative prices/days/GB, absurd backup counts, etc.).
            _INT_RANGES = {
                "trial_days": (0, 365), "trial_gb": (0, 10000), "trial_limit_ip": (0, 100),
                "referral_bonus_days": (0, 365), "referral_bonus_gb": (0, 10000),
                "topup_price_per_gb": (0, 10_000_000), "payment_min_amount": (0, 10_000_000),
                "backup_interval_min": (1, 10080), "backup_keep": (1, 200),
                "data_retention_days": (1, 3650),
            }
            if key in _INT_RANGES:
                lo, hi = _INT_RANGES[key]
                if not (lo <= val <= hi):
                    await state.clear()
                    await message.answer(
                        f"❌ {data.get('label', key)} must be between {lo} and {hi}.",
                        reply_markup=kb_admin_menu(),
                    )
                    return
            await state.clear()
            await db.set_setting(key, str(val))
            await message.answer(f"✅ {data.get('label','Setting')} = {val}", reply_markup=kb_admin_menu())

        # ---- bot setting (string or JSON list) ----
        elif edit_type == "setting_str":
            key = data["key"]
            label = data.get("label", "Setting")
            # List-type settings are stored as JSON arrays
            if key in ("topup_packages", "payment_presets"):
                try:
                    vals = [int(x.strip()) for x in raw.split(",") if x.strip()]
                except ValueError:
                    await state.clear()
                    await message.answer("❌ Enter comma-separated numbers (e.g. 5, 10, 20).",
                                         reply_markup=kb_admin_menu())
                    return
                # M4 — reject 0 and negative values. A "-5 GB" topup button
                # would call bulk_adjust(addBytes=-5*GB) and REDUCE the user's
                # quota — a money-losing misconfiguration.
                vals = [v for v in vals if v > 0]
                if not vals:
                    await state.clear()
                    await message.answer("❌ Enter positive numbers only (e.g. 5, 10, 20).",
                                         reply_markup=kb_admin_menu())
                    return
                await state.clear()
                await db.set_setting(key, json.dumps(vals))
                await message.answer(f"✅ {label} = {vals}", reply_markup=kb_admin_menu())
            else:
                # Plain string setting (card number, card holder, help text, ...)
                val = raw if raw != "-" else ""
                await state.clear()
                await db.set_setting(key, val)
                # REFERRAL-TEXT-CFG: after saving a referral share/extra text,
                # re-render the referral settings view so the admin immediately
                # sees the updated Custom/Default status and preview (instead
                # of just dumping them back at the admin menu).
                if key in ("referral_share_text_fa", "referral_share_text_en",
                           "referral_extra_text_fa", "referral_extra_text_en"):
                    await message.answer(f"✅ {label} updated.")
                    await _render_settings_referral_view(message)
                    return
                if key in ("payment_card_number", "payment_card_holder", "api_token"):
                    shown = "••••" if val else "(empty)"
                else:
                    shown = val[:80] + ("…" if len(val) > 80 else "")
                await message.answer(f"✅ {label} set: {shown}", reply_markup=kb_admin_menu())

        # ---- admin extends a user account (days GB) ----
        elif edit_type == "acc_extend":
            parts = raw.split()
            # days is always an integer (whole days); GB may be fractional
            # (e.g. "30 0.2" = 30 days + 200 MB).
            # BUG-5 FIX: use str.isdigit() (NOT lstrip("-").isdigit()) so
            # negative numbers like "-30" are rejected at parse time instead
            # of silently accepted. The old lstrip("-") let "-30" through,
            # which then sent add_days=-30 to the panel (shortening the
            # account) while the DB kept the original expiry — inconsistent
            # state between panel and DB.
            days = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
            gb = 0.0
            if len(parts) > 1:
                try:
                    gb = float(parts[1])
                except ValueError:
                    gb = 0.0
            # BUG-5 FIX (cont): reject negative GB too — negative GB reduces
            # the DB traffic_gb while sending add_bytes=0 to the panel (the
            # `if gb > 0` guard in add_bytes), causing the same panel-vs-DB
            # inconsistency. Also reject explicit "-N" forms that slipped
            # past the isdigit() check via the GB float() parse.
            if gb < 0:
                await message.answer("❌ GB cannot be negative.", reply_markup=kb_cancel("en"))
                return
            if days == 0 and gb == 0:
                await message.answer("❌ Send e.g. <code>30 10</code> (days GB) — GB can be decimal like <code>30 0.2</code>:",
                                     reply_markup=kb_cancel("en"))
                return
            await state.clear()
            email = data["email"]
            tg_id = data["tg_id"]
            account = await db.get_account(email)
            server = await db.get_server(account["server_id"]) if account else None
            if not server:
                await message.answer("❌ Server not found.", reply_markup=kb_admin_menu())
                return
            # H2 — preserve unlimited accounts. traffic_gb=0 means UNLIMITED;
            # adding GB would silently cap it. Keep it 0 and skip the bytes.
            if account.get("traffic_gb") == 0:
                new_traffic = 0
                add_bytes = 0
            else:
                new_traffic = (account["traffic_gb"] or 0) + gb
                add_bytes = int(gb * GB) if gb > 0 else 0
            r = await api.bulk_adjust(server["panel_url"], server["api_token"], [email],
                                      add_days=days, add_bytes=add_bytes)
            if not r.get("success"):
                await message.answer(f"❌ {r.get('msg')}", reply_markup=kb_admin_menu())
                return
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            base = account["expiry_time"] if account["expiry_time"] and account["expiry_time"] > now_ms else now_ms
            new_exp = base + days * MS_PER_DAY if days > 0 else account["expiry_time"]
            await db.update_account(email, expiry_time=new_exp, traffic_gb=new_traffic, is_active=True)
            await db.clear_traffic_alerts(email)
            await db.clear_expiry_reminders(email)
            await message.answer(
                f"✅ Extended <code>{escape_html(email)}</code>\n+{days}d +{fmt_gb(gb, 'en')}",
                reply_markup=kb_admin_menu(),
            )
            # Also re-fetch and show the account view so the admin can verify
            # the new expiry / traffic at a glance (admin-side, stays English
            # for consistency with the rest of the admin panel).
        else:
            await state.clear()
            await message.answer("⚠️ Unknown edit type.", reply_markup=kb_admin_menu())

    # ====================================================== USERS
    @router.callback_query(AdminCB.filter(F.action == "users"))
    async def cb_users(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_user_search)
        await show_view(callback.message, text=
            "👥 <b>Users</b>\n\nSearch by Telegram ID, username or email.\n"
            "Send <code>all</code> to list recent users.",
            reply_markup=kb_cancel("en"),
            state=state,
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_user_search)
    async def ms_user_search(message: Message, state: FSMContext):
        await del_inbound(message, state)
        await state.clear()
        query = (message.text or "").strip()
        if query.lower() == "all":
            users = (await db.get_all_users())[:20]
        else:
            users = await db.search_user(query)
        if not users:
            await message.answer("❌ No users found.", reply_markup=kb_admin_menu())
            return
        cur = await _currency()
        rich = rich_tables.user_search_rich(users, cur, fmt_price)
        kb = InlineKeyboardBuilder()
        for u in users[:10]:
            kb.button(style="primary", text=f"👤 {u['tg_id']} · {(u.get('username') or '-')[:15]}",
                      callback_data=AdminCB(action="user_view", data=str(u["tg_id"])).pack())
        kb.button(text="🔙 Admin", callback_data=AdminCB(action="main").pack(), style="danger")
        kb.adjust(1)
        await message.answer_rich(rich_message=rich, reply_markup=kb.as_markup())

    @router.callback_query(AdminCB.filter(F.action == "user_view"))
    async def cb_user_view(callback: CallbackQuery, callback_data: AdminCB):
        tg_id = int(callback_data.data)
        user = await db.get_user(tg_id)
        if not user:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        accounts = await db.get_user_accounts(tg_id)
        cur = await _currency()
        # REFERRAL-INVITEES: show this user's invitee count in the user-detail
        # card so the admin can see at a glance how many they've referred.
        invitees = await db.get_referral_invitees(tg_id, limit=50)
        # USER-ACCOUNTS-REWORK: accounts are no longer rendered inline here
        # (old code appended a divider + heading + grid_table and scattered
        # up to 6 per-account ⚙️ buttons in the same keyboard).  They now
        # live in a dedicated sub-section (admin:user_accounts) so this
        # overview card stays clean.  We keep just the count in the kv_table
        # for an at-a-glance summary.
        blocks = [rich_tables.heading("👤 User"),
                  rich_tables.kv_table([
                      ("TG ID", user["tg_id"]),
                      ("Username", (user.get("username") or "-")),
                      ("Balance", fmt_price(user.get("balance", 0), "en", cur)),
                      ("Orders", user.get("total_orders", 0)),
                      ("Spent", fmt_price(user.get("total_spent", 0), "en", cur)),
                      ("Banned", "Yes" if user.get("is_banned") else "No"),
                      ("Joined", fmt_iso(user.get("created_at"), "%Y-%m-%d %H:%M:%S")),
                      ("Referred by", user.get("referred_by") or "-"),
                      ("Invitees", len(invitees)),
                      ("Accounts", len(accounts)),
                  ])]
        rich = rich_tables.rich_message(*blocks)
        kb = InlineKeyboardBuilder()
        if user.get("is_banned"):
            kb.button(text="✅ Unban", callback_data=AdminCB(action="unban", data=str(tg_id)).pack(), style="success")
        else:
            kb.button(text="🚫 Ban", callback_data=AdminCB(action="ban", data=str(tg_id)).pack(), style="danger")
        kb.button(text="💰 Add Balance", callback_data=AdminCB(action="add_balance", data=str(tg_id)).pack(), style="primary")
        kb.button(text="➖ Deduct Balance", callback_data=AdminCB(action="deduct_balance", data=str(tg_id)).pack(), style="danger")
        kb.button(text="➕ Create Account", callback_data=AdminCB(action="create_account", data=str(tg_id)).pack(), style="success")
        # USER-ACCOUNTS-REWORK: dedicated Accounts sub-section (replaces the
        # old inline accounts table + scattered per-account buttons).
        kb.button(text=t("am_user_accounts_btn", "en"), callback_data=AdminCB(action="user_accounts", data=str(tg_id)).pack(), style="primary")
        # PAY-HISTORY-REWORK / REFERRAL-INVITEES: financial history + receipts
        # + invitees views.  These give the admin full visibility into a
        # user's money flow and referral activity per the user's request.
        kb.button(text="💼 Finance", callback_data=AdminCB(action="user_finance", data=str(tg_id)).pack(), style="primary")
        kb.button(text="🧾 Receipts", callback_data=AdminCB(action="user_receipts", data=str(tg_id)).pack(), style="primary")
        kb.button(text=t("admin_ref_invitees_btn", "en"), callback_data=AdminCB(action="user_invitees", data=str(tg_id)).pack(), style="primary")
        kb.button(text="🔙 Search", callback_data=AdminCB(action="users").pack(), style="danger")
        kb.adjust(2, 2, 2, 2, 1)
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())
        await callback.answer()

    # ---- USER-ACCOUNTS-REWORK: dedicated accounts sub-section ------------
    # Accounts used to be rendered inline in cb_user_view (a divider + heading
    # + grid_table, plus up to 6 per-account ⚙️ buttons scattered in the same
    # keyboard).  They now have their own view so the user-overview card stays
    # clean and ALL accounts (not just the first 6) are reachable from one
    # place.  Full-admin-only (not in AdminGuard._PAYMENT_ALLOWED_PREFIXES).
    @router.callback_query(AdminCB.filter(F.action == "user_accounts"))
    async def cb_user_accounts(callback: CallbackQuery, callback_data: AdminCB):
        tg_id = int(callback_data.data)
        user = await db.get_user(tg_id)
        if not user:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        accounts = await db.get_user_accounts(tg_id)
        uname = (user.get("first_name") or user.get("username") or str(tg_id))
        blocks: list = [rich_tables.heading(f"📱 Accounts — {uname} ({len(accounts)})")]
        if accounts:
            rows = [("🟢" if a["is_active"] else "🔴",
                     ("🎁 " if a.get("is_trial") else "") + a["email"],
                     a.get("label") or "") for a in accounts]
            blocks.append(rich_tables.grid_table(["Status", "Email", "Label"], rows,
                                                 aligns=["center", "left", "left"]))
        else:
            blocks.append(rich_tables.paragraph(t("am_user_accounts_none", "en")))
        rich = rich_tables.rich_message(*blocks)
        kb = InlineKeyboardBuilder()
        # One button per account (no 6-item cap — show them all).  Tapping
        # opens the per-account detail (cb_user_account) with extend / reset
        # traffic / enable / disable / delete actions.
        for a in accounts:
            label_acc = a.get("label") or a["email"][:16]
            status_dot = "🟢" if a["is_active"] else "🔴"
            kb.button(style="primary", text=f"{status_dot} {label_acc}",
                      callback_data=AdminCB(action="user_account", data=f"{tg_id}_{a['email']}").pack())
        kb.button(text="➕ Create Account", callback_data=AdminCB(action="create_account", data=str(tg_id)).pack(), style="success")
        kb.button(text="🔙 User", callback_data=AdminCB(action="user_view", data=str(tg_id)).pack(), style="danger")
        # 1 button per account row, then Create (1), then Back (1).
        kb.adjust(*[1 for _ in accounts], 1, 1)
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "ban"))
    async def cb_ban(callback: CallbackQuery, callback_data: AdminCB):
        await db.ban_user(int(callback_data.data), True)
        await callback.answer("✅ Banned", show_alert=True)

    @router.callback_query(AdminCB.filter(F.action == "unban"))
    async def cb_unban(callback: CallbackQuery, callback_data: AdminCB):
        await db.ban_user(int(callback_data.data), False)
        await callback.answer("✅ Unbanned", show_alert=True)

    # ---- USER-FINANCE: full per-user financial history ----------------
    # PAY-HISTORY-REWORK / USER-FINANCE: full-admin-only views showing a
    # user's complete money flow.  ``cb_user_finance`` shows every
    # transaction (purchases, renewals, top-ups, deposits, gift balances,
    # admin adjustments) as a grid_table.  ``cb_user_receipts`` shows every
    # payment receipt (photo/document/text) the user ever submitted, with
    # status + approver — tapping a row opens the full receipt detail.
    @router.callback_query(AdminCB.filter(F.action == "user_finance"))
    async def cb_user_finance(callback: CallbackQuery, callback_data: AdminCB):
        tg_id = int(callback_data.data)
        user = await db.get_user(tg_id)
        if not user:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        cur = await _currency()
        txs = await db.get_user_transactions(tg_id, limit=30)
        uname = (user.get("first_name") or user.get("username") or str(tg_id))
        blocks: list = [
            rich_tables.heading(f"💼 Finance — {uname}"),
            rich_tables.kv_table([
                ("TG ID", user["tg_id"]),
                ("Balance", fmt_price(user.get("balance", 0), "en", cur)),
                ("Total Spent", fmt_price(user.get("total_spent", 0), "en", cur)),
                ("Total Orders", user.get("total_orders", 0)),
            ]),
        ]
        if txs:
            blocks.append(rich_tables.divider())
            blocks.append(rich_tables.heading(f"📜 Transactions ({len(txs)})", size=4))
            # TX-SIGN-FIX (mirror of wallet_rich): display sign is type-based so
            # purchases/renewals/topups show as "-" (money leaving wallet) even
            # though they're stored as positive amounts.
            _DEBIT = {"purchase", "renewal", "topup"}
            _CREDIT = {"deposit", "gift_balance"}
            rows = []
            for tx in txs:
                ttype = tx.get("type", "")
                amt = float(tx.get("amount", 0) or 0)
                if ttype in _DEBIT:
                    sign = "-"
                elif ttype in _CREDIT:
                    sign = "+"
                elif ttype == "admin_adjust":
                    sign = "+" if amt >= 0 else "-"
                    amt = abs(amt)
                else:
                    # trial / gift_plan / unknown → no sign, just amount.
                    sign = ""
                disp_amt = f"{sign}{fmt_num(amt, 'en')}"
                disp_desc = (tx.get("description") or "")[:22]
                disp_date = fmt_iso(tx.get("created_at"), "%m-%d %H:%M") or "-"
                rows.append((f"#{tx['id']}", ttype, disp_amt, disp_date, disp_desc))
            blocks.append(rich_tables.grid_table(
                ["ID", "Type", "Amount", "Date", "Description"], rows,
                aligns=["center", "left", "right", "center", "left"],
            ))
        else:
            blocks.append(rich_tables.paragraph("No transactions."))
        rich = rich_tables.rich_message(*blocks)
        kb = InlineKeyboardBuilder()
        kb.button(text="🧾 Receipts", callback_data=AdminCB(action="user_receipts", data=str(tg_id)).pack(), style="primary")
        kb.button(text=t("admin_ref_invitees_btn", "en"), callback_data=AdminCB(action="user_invitees", data=str(tg_id)).pack(), style="primary")
        kb.button(text="🔙 User", callback_data=AdminCB(action="user_view", data=str(tg_id)).pack(), style="danger")
        kb.adjust(2, 1)
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "user_receipts"))
    async def cb_user_receipts(callback: CallbackQuery, callback_data: AdminCB):
        """All payment receipts the user ever submitted (any status)."""
        tg_id = int(callback_data.data)
        user = await db.get_user(tg_id)
        if not user:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        payments = await db.get_user_payments(tg_id, limit=30)
        uname = (user.get("first_name") or user.get("username") or str(tg_id))
        blocks: list = [rich_tables.heading(f"🧾 Receipts — {uname}")]
        if payments:
            rows = []
            admin_cache: dict = {}
            for p in payments:
                status = p.get("status", "pending")
                status_emoji = {"approved": "✅", "rejected": "❌", "pending": "⏳"}.get(status, "•")
                rtype = (p.get("receipt_type") or "").lower()
                receipt_icon = {"photo": "📸", "document": "📎", "text": "📝"}.get(rtype, "—")
                # Approver label.
                admin_label = p.get("admin_username") or ""
                admin_id = p.get("admin_id")
                if not admin_label and admin_id:
                    if admin_id not in admin_cache:
                        admin_cache[admin_id] = await db.get_user(admin_id)
                    admin_label = _admin_display(admin_cache[admin_id]) if admin_cache[admin_id] else ""
                approver = admin_label[:14] if admin_label and status != "pending" else "—"
                disp_date = fmt_iso(p.get("created_at"), "%m-%d %H:%M") or "-"
                rows.append((f"#{p['id']}", fmt_num(p["unique_amount"], "en"),
                             f"{status_emoji} {status}", receipt_icon, disp_date, approver))
            blocks.append(rich_tables.grid_table(
                ["ID", "Amount", "Status", "Receipt", "Date", "Approver"], rows,
                aligns=["center", "right", "center", "center", "center", "left"],
            ))
        else:
            blocks.append(rich_tables.paragraph("No receipts."))
        rich = rich_tables.rich_message(*blocks)
        kb = InlineKeyboardBuilder()
        # Each payment gets a button so the admin can open the full detail
        # card + receipt photo.
        for p in payments:
            status = p.get("status", "pending")
            status_emoji = {"approved": "✅", "rejected": "❌", "pending": "⏳"}.get(status, "•")
            kb.button(style="primary",
                text=f"#{p['id']} — {fmt_num(p['unique_amount'], 'en')}T {status_emoji}",
                callback_data=PaymentCB(action="view", payment_id=p["id"]).pack())
        kb.button(text="💼 Finance", callback_data=AdminCB(action="user_finance", data=str(tg_id)).pack(), style="primary")
        kb.button(text=t("admin_ref_invitees_btn", "en"), callback_data=AdminCB(action="user_invitees", data=str(tg_id)).pack(), style="primary")
        kb.button(text="🔙 User", callback_data=AdminCB(action="user_view", data=str(tg_id)).pack(), style="danger")
        kb.adjust(*[1 for _ in payments], 2, 1)
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "user_invitees"))
    async def cb_user_invitees(callback: CallbackQuery, callback_data: AdminCB):
        """REFERRAL-INVITEES (admin view): every user this person invited."""
        tg_id = int(callback_data.data)
        user = await db.get_user(tg_id)
        if not user:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        invitees = await db.get_referral_invitees(tg_id, limit=50)
        uname = (user.get("first_name") or user.get("username") or str(tg_id))
        blocks: list = [rich_tables.heading(f"👥 Invitees — {uname}")]
        if invitees:
            rows = []
            for inv in invitees:
                status = "✅" if inv.get("referral_rewarded") else "⏳"
                name = (inv.get("first_name") or inv.get("username") or str(inv["tg_id"]))[:16]
                disp_date = fmt_iso(inv.get("created_at"), "%Y-%m-%d") or "-"
                rows.append((inv["tg_id"], name, status, disp_date))
            blocks.append(rich_tables.grid_table(
                ["TG ID", "Name", "Status", "Joined"], rows,
                aligns=["right", "left", "center", "center"],
            ))
        else:
            blocks.append(rich_tables.paragraph("No invitees."))
        rich = rich_tables.rich_message(*blocks)
        kb = InlineKeyboardBuilder()
        kb.button(text="💼 Finance", callback_data=AdminCB(action="user_finance", data=str(tg_id)).pack(), style="primary")
        kb.button(text="🧾 Receipts", callback_data=AdminCB(action="user_receipts", data=str(tg_id)).pack(), style="primary")
        kb.button(text="🔙 User", callback_data=AdminCB(action="user_view", data=str(tg_id)).pack(), style="danger")
        kb.adjust(2, 1)
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "add_balance"))
    async def cb_add_balance(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        tg_id = int(callback_data.data)
        await state.set_state(AdminStates.waiting_for_add_balance)
        await state.update_data(tg_id=tg_id)
        await show_view(callback.message, text=
            f"💰 <b>Add balance</b> to user <code>{tg_id}</code>\n\nEnter amount in {await _currency()}:",
            reply_markup=kb_cancel("en"),
            state=state,
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_add_balance)
    async def ms_add_balance(message: Message, state: FSMContext):
        await del_inbound(message, state)
        try:
            amount = float((message.text or "").strip())
        except ValueError:
            await message.answer("❌ Number please:", reply_markup=kb_cancel("en"))
            return
        if amount == 0:
            await state.clear()
            await message.answer("Amount was 0 — nothing changed.", reply_markup=kb_admin_menu())
            return
        data = await state.get_data()
        await state.clear()
        tg_id = data["tg_id"]
        await db.update_user_balance(tg_id, abs(amount), add=True)
        await db.add_transaction(
            user_tg_id=tg_id, amount=abs(amount), type_="admin_adjust",
            description=f"Admin balance add by {message.from_user.id}", admin_id=message.from_user.id,
        )
        cur = await _currency()
        user = await db.get_user(tg_id)
        await safe_notify(
            bot.send_message(
                tg_id,
                f"💰 <b>Balance updated</b>\n\n➕ {fmt_price(abs(amount), 'en' if (user or {}).get('language','en')=='en' else 'fa', cur)} added.\n"
                f"💳 New balance: {fmt_price((user or {}).get('balance',0), 'en' if (user or {}).get('language','en')=='en' else 'fa', cur)}",
            ),
            context="balance-update user notify",
        )
        await message.answer(
            f"✅ Added {fmt_price(abs(amount), 'en', cur)} to <code>{tg_id}</code>",
            reply_markup=kb_admin_menu(),
        )

    @router.callback_query(AdminCB.filter(F.action == "deduct_balance"))
    async def cb_deduct_balance(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        tg_id = int(callback_data.data)
        await state.set_state(AdminStates.waiting_for_deduct_balance)
        await state.update_data(tg_id=tg_id)
        await show_view(callback.message, text=
            f"➖ <b>Deduct balance</b> from <code>{tg_id}</code>\n\nEnter amount:",
            reply_markup=kb_cancel("en"),
            state=state,
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_deduct_balance)
    async def ms_deduct_balance(message: Message, state: FSMContext):
        await del_inbound(message, state)
        try:
            amount = float((message.text or "").strip())
        except ValueError:
            await message.answer("❌ Number please:", reply_markup=kb_cancel("en"))
            return
        data = await state.get_data()
        await state.clear()
        tg_id = data["tg_id"]
        amount = abs(amount)
        user = await db.get_user(tg_id)
        if not user:
            await message.answer("User not found.", reply_markup=kb_admin_menu())
            return
        new_bal = max(0.0, user.get("balance", 0) - amount)
        await db.set_user_balance(tg_id, new_bal)
        await db.add_transaction(
            user_tg_id=tg_id, amount=-amount, type_="admin_adjust",
            description=f"Admin deduct by {message.from_user.id}", admin_id=message.from_user.id,
        )
        await message.answer(
            f"✅ Deducted {fmt_price(amount, 'en', await _currency())}. New balance: {fmt_price(new_bal, 'en', await _currency())}",
            reply_markup=kb_admin_menu(),
        )

    # ---- admin creates an account for a user --------------------------
    @router.callback_query(AdminCB.filter(F.action == "create_account"))
    async def cb_create_account(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        tg_id = int(callback_data.data)
        plans = await db.get_plans(active_only=True)
        if not plans:
            # L12 — admin-facing i18n (was English-only "No plans available.").
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        await state.set_state(AdminStates.waiting_for_admin_account_create)
        await state.update_data(tg_id=tg_id)
        kb = InlineKeyboardBuilder()
        for p in plans:
            kb.button(style="primary", text=f"{p['name']} — {fmt_price(p['price'], 'en', await _currency())}",
                      callback_data=AdminCB(action="create_account_pick", data=f"{tg_id}_{p['id']}").pack())
        kb.button(text="❌ Cancel", callback_data=AdminCB(action="user_view", data=str(tg_id)).pack(), style="danger")
        kb.adjust(1)
        await show_view(callback.message, text="➕ <b>Create account for user</b> — pick a plan:", reply_markup=kb.as_markup(), state=state)
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "create_account_pick"))
    async def cb_create_account_pick(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        tg_id_str, plan_id_str = callback_data.data.split("_", 1)
        tg_id, plan_id = int(tg_id_str), int(plan_id_str)
        plan = await db.get_plan(plan_id)
        if not plan:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        # LABEL-FIX: ask the admin for an optional account label before
        # creating the panel client. Previously the label was hardcoded to
        # "Admin" with no way to customise it.
        await state.set_state(AdminStates.waiting_for_admin_account_create)
        await state.update_data(tg_id=tg_id, plan_id=plan_id)
        await show_view(callback.message,
            text=(
                "🏷 <b>Name this account (optional)</b>\n\n"
                f"👤 User: <code>{tg_id}</code>\n"
                f"📦 Plan: {escape_html(plan['name'])}\n\n"
                "Send a short label (max 30 chars) or <code>-</code> for no label:"
            ),
            reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    @router.message(AdminStates.waiting_for_admin_account_create)
    async def ms_admin_account_create(message: Message, state: FSMContext):
        await del_inbound(message, state)
        data = await state.get_data()
        await state.clear()
        tg_id = data["tg_id"]
        plan_id = data["plan_id"]
        plan = await db.get_plan(plan_id)
        if not plan:
            await message.answer("❌ Plan not found.", reply_markup=kb_admin_menu())
            return
        raw_label = (message.text or "").strip()
        label = "" if raw_label in ("-", "") else raw_label[:30]
        await message.answer("⏳ Creating account...")
        server = await lb.select_best_server(lb.plan_server_ids(plan) or None)
        if not server:
            await message.answer("❌ No servers available.", reply_markup=kb_admin_menu())
            return
        inbound_ids = await lb.select_inbounds_for_plan(server, plan)
        if not inbound_ids:
            await message.answer("❌ No inbounds available.", reply_markup=kb_admin_menu())
            return
        email = gen_email(tg_id, "admin")
        expiry = int((datetime.now(timezone.utc) + timedelta(days=plan["duration_days"])).timestamp() * 1000) if plan["duration_days"] > 0 else 0
        sub_id = gen_sub_id()
        res = await api.create_client(
            panel_url=server["panel_url"], token=server["api_token"], email=email,
            inbound_ids=inbound_ids, total_gb=plan["traffic_gb"], expiry_time=expiry,
            limit_ip=plan.get("limit_ip", 0), tg_id=tg_id, sub_id=sub_id,
        )
        if not res.get("success"):
            await message.answer(f"❌ {res.get('msg')}", reply_markup=kb_admin_menu())
            return
        await db.add_account(
            user_tg_id=tg_id, server_id=server["id"], email=email, sub_id=sub_id,
            plan_id=plan["id"], traffic_gb=plan["traffic_gb"], expiry_time=expiry,
            limit_ip=plan.get("limit_ip", 0), inbound_ids=json.dumps(inbound_ids), label=label,
        )
        await db.add_transaction(tg_id, 0, "admin_adjust", f"Admin created account ({plan['name']})",
                                 account_email=email, plan_id=plan["id"], admin_id=message.from_user.id)
        label_line = f"\n🏷 {escape_html(label)}" if label else ""
        await safe_notify(
            bot.send_message(
                tg_id,
                f"🎁 <b>Admin created a VPN account for you!</b>\n\n📦 {escape_html(plan['name'])}\n📧 <code>{escape_html(email)}</code>{label_line}",
            ),
            context="manual-account-create user notify",
        )
        await message.answer(
            f"✅ Account created for <code>{tg_id}</code>\n📧 <code>{escape_html(email)}</code>{label_line}",
            reply_markup=kb_admin_menu(),
        )

    # ---- admin manages a specific user account ------------------------
    @router.callback_query(AdminCB.filter(F.action == "user_account"))
    async def cb_user_account(callback: CallbackQuery, callback_data: AdminCB):
        tg_id_str, email = callback_data.data.split("_", 1)
        tg_id = int(tg_id_str)
        account = await db.get_account(email)
        if not account or account["user_tg_id"] != tg_id:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        plan = await db.get_plan(account["plan_id"]) if account.get("plan_id") else None
        traffic = await api.get_client_traffic(server["panel_url"], server["api_token"], email) if server else None
        text = fmt_account_card(account, lang="en", traffic_data=traffic,
                                server_alias=server["alias"] if server else "-",
                                plan_name=plan["name"] if plan else "Trial" if account.get("is_trial") else "-",
                                currency=await _currency())
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Extend", callback_data=AdminCB(action="acc_extend", data=f"{tg_id}_{email}").pack(), style="success")
        kb.button(text="🔄 Reset Traffic", callback_data=AdminCB(action="acc_reset", data=f"{tg_id}_{email}").pack(), style="primary")
        if account["is_active"]:
            kb.button(text="⛔ Disable", callback_data=AdminCB(action="acc_disable", data=f"{tg_id}_{email}").pack(), style="danger")
        else:
            kb.button(text="✅ Enable", callback_data=AdminCB(action="acc_enable", data=f"{tg_id}_{email}").pack(), style="success")
        kb.button(text="🗑 Delete", callback_data=AdminCB(action="acc_delete", data=f"{tg_id}_{email}").pack(), style="danger")
        # USER-ACCOUNTS-REWORK: back returns to the Accounts list (not the
        # user overview) so the admin stays in the accounts context.
        kb.button(text="🔙 Accounts", callback_data=AdminCB(action="user_accounts", data=str(tg_id)).pack(), style="danger")
        kb.adjust(2, 2, 1)
        await show_view(callback.message, text=text, reply_markup=kb.as_markup())
        await callback.answer()

    async def _parse_email(callback_data: AdminCB) -> Tuple[int, str]:
        tg_id_str, email = callback_data.data.split("_", 1)
        return int(tg_id_str), email

    @router.callback_query(AdminCB.filter(F.action == "acc_extend"))
    async def cb_acc_extend(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        tg_id, email = await _parse_email(callback_data)
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="acc_extend", tg_id=tg_id, email=email)
        await show_view(callback.message, text=
            "➕ Extend account.\nSend days and GB, e.g. <code>30 10</code> (30 days, 10 GB). Use 0 to skip either.",
            reply_markup=kb_cancel("en"),
            state=state,
        )
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "acc_reset"))
    async def cb_acc_reset(callback: CallbackQuery, callback_data: AdminCB):
        tg_id, email = await _parse_email(callback_data)
        account = await db.get_account(email)
        if not account:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        # BUG-10 FIX: server_id can be NULL (ON DELETE SET NULL when an admin
        # deletes a server that has accounts). Sibling handlers (cb_acc_disable,
        # cb_acc_enable, cb_acc_delete) all guard with `if server:` — this one
        # was missing it, so `server["panel_url"]` would crash with
        # TypeError: 'NoneType' object is not subscriptable and leave the
        # callback unanswered (endless spinner).
        if not server:
            await callback.answer("❌ Server not found", show_alert=True)
            return
        r = await api.reset_client_traffic(server["panel_url"], server["api_token"], email)
        if r.get("success"):
            # H16 — clear traffic_alerts so the user gets re-warned when they
            # cross 80%/95% again after the reset. Without this, the alert
            # rows from before the reset suppress all future warnings.
            await db.clear_traffic_alerts(email)
            await db.clear_expiry_reminders(email)
        await callback.answer("✅ Reset" if r.get("success") else f"❌ {r.get('msg')}", show_alert=True)

    @router.callback_query(AdminCB.filter(F.action == "acc_disable"))
    async def cb_acc_disable(callback: CallbackQuery, callback_data: AdminCB):
        tg_id, email = await _parse_email(callback_data)
        account = await db.get_account(email)
        server = await db.get_server(account["server_id"]) if account else None
        if server:
            # H4 — only update the DB if the panel call succeeded. Without
            # this guard, a panel failure leaves the DB saying is_active=False
            # while the user's VPN keeps working — admin sees "✅ Disabled"
            # but nothing actually changed on the panel.
            r = await api.disable_client(server["panel_url"], server["api_token"], email)
            if r.get("success"):
                await db.update_account(email, is_active=False)
                await callback.answer("✅ Disabled", show_alert=True)
            else:
                await callback.answer(f"❌ {r.get('msg')}", show_alert=True)
        else:
            await callback.answer("❌ Server not found", show_alert=True)

    @router.callback_query(AdminCB.filter(F.action == "acc_enable"))
    async def cb_acc_enable(callback: CallbackQuery, callback_data: AdminCB):
        tg_id, email = await _parse_email(callback_data)
        account = await db.get_account(email)
        server = await db.get_server(account["server_id"]) if account else None
        if server:
            r = await api.enable_client(server["panel_url"], server["api_token"], email)
            if r.get("success"):
                await db.update_account(email, is_active=True)
                await callback.answer("✅ Enabled", show_alert=True)
            else:
                await callback.answer(f"❌ {r.get('msg')}", show_alert=True)
        else:
            await callback.answer("❌ Server not found", show_alert=True)

    @router.callback_query(AdminCB.filter(F.action == "acc_delete"))
    async def cb_acc_delete(callback: CallbackQuery, callback_data: AdminCB):
        tg_id, email = await _parse_email(callback_data)
        account = await db.get_account(email)
        server = await db.get_server(account["server_id"]) if account else None
        if server:
            # H4 — only remove the DB row if the panel delete succeeded.
            # Otherwise the panel client lives on as an orphan the bot can
            # never manage again.
            r = await api.delete_client(server["panel_url"], server["api_token"], email)
            if not r.get("success"):
                await callback.answer(f"❌ {r.get('msg')}", show_alert=True)
                return
        await db.delete_account(email)
        await callback.answer("✅ Deleted", show_alert=True)
        # USER-ACCOUNTS-REWORK: return to the Accounts list (not user_view)
        # so the admin stays in the accounts context — consistent with the
        # other per-account actions (extend/reset/enable/disable).
        await show_view(callback.message, text="✅ Account deleted.",
                                         reply_markup=InlineKeyboardBuilder()
                                         .button(text="🔙 Accounts", callback_data=AdminCB(action="user_accounts", data=str(tg_id)).pack(), style="primary")
                                         .as_markup())

    # ====================================================== FINANCE
    @router.callback_query(AdminCB.filter(F.action == "finance"))
    async def cb_finance(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        rev = await db.get_revenue_stats(days=30)
        cur = await _currency()
        rich = rich_tables.finance_rich(rev, cur, fmt_price)
        await show_view(callback.message, rich=rich, reply_markup=kb_admin_menu())
        await callback.answer()

    # ====================================================== PROMOS
    @router.callback_query(AdminCB.filter(F.action == "promos"))
    async def cb_promos(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        promos = await db.get_promo_codes()
        cur = await _currency()
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Create", callback_data=AdminCB(action="create_promo").pack(), style="success")
        # ADMIN-MENU-REWORK: back goes to the Promotions submenu (not the
        # top-level admin panel) so the admin stays in the promotions context.
        kb.button(text="🔙 Promotions", callback_data=AdminCB(action="promos_menu").pack(), style="danger")
        kb.adjust(1)
        if promos:
            rich = rich_tables.promos_rich(promos, cur, fmt_price)
        else:
            rich = rich_tables.rich_message(rich_tables.heading("🎫 Promo Codes"),
                                            rich_tables.paragraph("No promo codes yet."))
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "create_promo"))
    async def cb_create_promo(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_promo_code_str)
        await show_view(callback.message, text="🎫 <b>Create promo</b>\n\nCode (or <code>-</code> for random):",
                                         reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    @router.message(AdminStates.waiting_for_promo_code_str)
    async def ms_promo_code(message: Message, state: FSMContext):
        await del_inbound(message, state)
        code = (message.text or "").strip().upper()
        if code == "-":
            code = gen_gift_code().replace("-", "")[:10]
        await state.update_data(code=code)
        await state.set_state(AdminStates.waiting_for_promo_discount)
        await track_prompt(await message.answer("💰 Discount percent (0-100):", reply_markup=kb_cancel("en")), state)

    @router.message(AdminStates.waiting_for_promo_discount)
    async def ms_promo_disc(message: Message, state: FSMContext):
        await del_inbound(message, state)
        try:
            d = int((message.text or "").strip())
        except ValueError:
            await message.answer("❌ Number:", reply_markup=kb_cancel("en"))
            return
        # M3 — discount must be 0-100. >100 gives free plan + credit; <0
        # makes the user pay more than the plan price.
        if not 0 <= d <= 100:
            await message.answer("❌ Discount must be between 0 and 100:", reply_markup=kb_cancel("en"))
            return
        await state.update_data(disc=d)
        await state.set_state(AdminStates.waiting_for_promo_max_uses)
        await track_prompt(await message.answer("🔢 Max uses (0 = unlimited):", reply_markup=kb_cancel("en")), state)

    @router.message(AdminStates.waiting_for_promo_max_uses)
    async def ms_promo_max(message: Message, state: FSMContext):
        await del_inbound(message, state)
        try:
            mu = int((message.text or "").strip())
        except ValueError:
            await message.answer("❌ Number:", reply_markup=kb_cancel("en"))
            return
        # BUG-11 FIX: reject negative max_uses. A negative value makes the
        # promo validate OK (validate_promo_code's `max_uses > 0` check is
        # False, so it's treated as unlimited) but the atomic increment in
        # use_promo_code (`used_count < max_uses` = `0 < -5` = False) always
        # rejects the redemption — creating an unusable promo. 0 = unlimited.
        if mu < 0:
            await message.answer("❌ Max uses cannot be negative (0 = unlimited).",
                                 reply_markup=kb_cancel("en"))
            return
        data = await state.get_data()
        await state.clear()
        await db.add_promo_code(data["code"], discount_percent=data["disc"], max_uses=mu)
        await message.answer(
            f"✅ <b>Promo created</b>\n🎫 <code>{data['code']}</code>\n💰 {data['disc']}%\n🔢 {mu or '∞'}",
            reply_markup=kb_admin_menu(),
        )

    # ====================================================== GIFT CODES
    @router.callback_query(AdminCB.filter(F.action == "gift_codes"))
    async def cb_gift_codes(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        gifts = await db.get_gift_codes(unused_only=False)
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Gift (Balance)", callback_data=AdminCB(action="create_gift_balance").pack(), style="success")
        kb.button(text="➕ Gift (Plan)", callback_data=AdminCB(action="create_gift_plan").pack(), style="primary")
        # ADMIN-MENU-REWORK: back → Promotions submenu.
        kb.button(text="🔙 Promotions", callback_data=AdminCB(action="promos_menu").pack(), style="danger")
        kb.adjust(1, 1, 1)
        if gifts:
            rich = rich_tables.gift_codes_rich(gifts)
        else:
            rich = rich_tables.rich_message(rich_tables.heading("🎁 Gift Codes"),
                                            rich_tables.paragraph("No gift codes yet."))
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "create_gift_balance"))
    async def cb_gift_balance(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_gift_amount)
        await show_view(callback.message, text=
            f"💰 <b>Balance gift</b>\n\nEnter amount in {await _currency()}:",
            reply_markup=kb_cancel("en"),
            state=state,
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_gift_amount)
    async def ms_gift_amount(message: Message, state: FSMContext):
        await del_inbound(message, state)
        try:
            amount = float((message.text or "").strip())
        except ValueError:
            await message.answer("❌ Number:", reply_markup=kb_cancel("en"))
            return
        # BUG-2 FIX: reject non-positive amounts — a negative balance-type gift
        # code would DRAIN the redeemer's wallet (update_user_balance with
        # add=True + negative = subtraction). Zero is meaningless.
        if amount <= 0:
            await message.answer("❌ Amount must be positive.", reply_markup=kb_cancel("en"))
            return
        await state.clear()
        code = gen_gift_code()
        await db.create_gift_code(code, "balance", str(amount), created_by=message.from_user.id)
        await message.answer(f"✅ <b>Gift code</b>\n🎫 <code>{code}</code>\n💰 {fmt_price(amount, 'en', await _currency())}",
                             reply_markup=kb_admin_menu())

    @router.callback_query(AdminCB.filter(F.action == "create_gift_plan"))
    async def cb_gift_plan(callback: CallbackQuery, state: FSMContext):
        plans = await db.get_plans(active_only=True)
        if not plans:
            # L12 — admin-facing i18n.
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        await state.set_state(AdminStates.waiting_for_gift_plan)
        kb = InlineKeyboardBuilder()
        for p in plans:
            kb.button(style="primary", text=p["name"],
                      callback_data=AdminCB(action="gift_plan_pick", data=str(p["id"])).pack())
        kb.button(text="❌ Cancel", callback_data=AdminCB(action="gift_codes").pack(), style="danger")
        kb.adjust(1)
        await show_view(callback.message, text="🎁 Pick a plan for the gift code:", reply_markup=kb.as_markup(), state=state)
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "gift_plan_pick"))
    async def cb_gift_plan_pick(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        await state.clear()
        plan_id = int(callback_data.data)
        plan = await db.get_plan(plan_id)
        if not plan:
            # L12 — admin-facing i18n.
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        code = gen_gift_code()
        await db.create_gift_code(code, "plan", str(plan_id), plan_id=plan_id, created_by=callback.from_user.id)
        await show_view(callback.message, text=
            f"✅ <b>Gift code</b>\n🎫 <code>{code}</code>\n📦 {escape_html(plan['name'])}",
            reply_markup=kb_admin_menu(),
        )
        await callback.answer()

    # ====================================================== TICKETS
    @router.callback_query(AdminCB.filter(F.action == "tickets"))
    async def cb_tickets(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        await state.clear()
        # L4 — callback_data is required because the @router decorator above
        # uses ``AdminCB.filter(F.action == "tickets")``: aiogram will only
        # route to this handler when the filter matches, and in that case
        # ``callback_data`` is always injected. The previous ``= None``
        # default was dead code (it would only have been used by an
        # unfiltered decorator, which we do not register) and silently
        # masked the case where filter-mismatch somehow routed here.
        #
        # callback_data.data carries the filter: "open" (default) or "all".
        # When the user taps the "📋 Show all" toggle button, the action is
        # still "tickets" but data="all"; otherwise data is empty / "open".
        filter_mode = "all" if callback_data.data == "all" else "open"
        if filter_mode == "all":
            tickets = await db.get_all_tickets(limit=50)
            title = f"💬 <b>All Tickets ({len(tickets)})</b>"
        else:
            tickets = await db.get_open_tickets()
            title = f"💬 <b>Open Tickets ({len(tickets)})</b>"
        if not tickets:
            # ADMIN-MENU-REWORK: empty-state back → Support submenu.
            await show_view(callback.message, text=f"{title}\n\n✅ No tickets.",
                            reply_markup=kb_support_menu())
            await callback.answer()
            return
        kb = InlineKeyboardBuilder()
        for tk in tickets:
            badge = _ticket_status_badge(tk, "en")
            cat_emoji = _category_emoji(tk.get("category", "other"))
            user = await db.get_user(tk["user_tg_id"])
            # TICKET-1 Feature 4 — show first_name + @username (if present)
            # so admins can recognise the requester at a glance from the list.
            if user:
                first = (user.get("first_name") or "").strip()
                uname = (user.get("username") or "").strip()
                if first and uname:
                    display = f"{first} @{uname}"
                elif first:
                    display = first
                elif uname:
                    display = f"@{uname}"
                else:
                    display = str(tk["user_tg_id"])
            else:
                display = str(tk["user_tg_id"])
            display = display[:20]
            kb.button(style="primary",
                      text=f"{badge} #{tk['id']} {cat_emoji} {display} — {tk['subject'][:18]}",
                      callback_data=TicketCB(action="view", ticket_id=tk["id"]).pack())
        # Filter toggle
        if filter_mode == "open":
            kb.button(style="primary", text="📋 Show all", callback_data=AdminCB(action="tickets", data="all").pack())
        else:
            kb.button(style="primary", text="🟢 Open only", callback_data=AdminCB(action="tickets", data="open").pack())
        # ADMIN-MENU-REWORK: back → Support submenu.
        kb.button(text="🔙 Support", callback_data=AdminCB(action="support_menu").pack(), style="danger")
        kb.adjust(1, 1)
        await show_view(callback.message, text=title, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(TicketCB.filter(F.action == "close"))
    async def cb_ticket_close(callback: CallbackQuery, callback_data: TicketCB):
        ticket = await db.get_ticket(callback_data.ticket_id)
        if not ticket:
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        await db.close_ticket(callback_data.ticket_id)
        # Notify the user in their language
        user = await db.get_user(ticket["user_tg_id"])
        ulang = L((user or {}).get("language", DEFAULT_LANGUAGE))
        await safe_notify(
            bot.send_message(
                ticket["user_tg_id"],
                t("ticket_closed", ulang, id=callback_data.ticket_id),
                reply_markup=kb_ticket_view(callback_data.ticket_id, False, ulang, "closed"),
            ),
            context="ticket-close user notify",
        )
        await callback.answer("✅ Closed", show_alert=True)
        tickets = await db.get_open_tickets()
        if tickets:
            await show_view(callback.message, text=f"💬 <b>Open Tickets ({len(tickets)})</b>",
                                             reply_markup=kb_tickets(tickets))
        else:
            await show_view(callback.message, text="✅ All open tickets resolved.", reply_markup=kb_admin_menu())

    # ====================================================== BROADCAST
    @router.callback_query(AdminCB.filter(F.action == "broadcast"))
    async def cb_broadcast(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await show_view(callback.message, text="📣 <b>Broadcast</b> — choose target:",
                                         reply_markup=kb_broadcast_targets())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action.startswith("broadcast_")))
    async def cb_broadcast_target(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        target = callback_data.action.replace("broadcast_", "")
        await state.set_state(AdminStates.waiting_for_broadcast_message)
        await state.update_data(target=target)
        await show_view(callback.message, text=
            f"📣 <b>Broadcast → {target}</b>\n\nSend the message (text/HTML):",
            reply_markup=kb_cancel("en"),
            state=state,
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_broadcast_message)
    async def ms_broadcast(message: Message, state: FSMContext):
        await del_inbound(message, state)
        text = (message.text or "").strip()[:BROADCAST_MAX_TEXT_CHARS]
        # BROADCAST-EMPTY-GUARD: if the admin sent a photo / sticker / voice /
        # animation (or genuinely empty text), message.text is None → text="".
        # Without this guard the broadcast loop would send just the localized
        # header to every user in the target group. Reject it up front and
        # keep the FSM state so the admin can retry (or hit Cancel).
        if not text:
            await message.answer(
                "❌ Empty broadcast — send the message as <b>text / HTML</b> "
                "(photos and stickers can't be broadcast).",
                reply_markup=kb_cancel("en"),
            )
            return
        data = await state.get_data()
        target = data.get("target", "all")
        await state.clear()
        user_ids = await db.get_users_by_filter(target)
        if not user_ids:
            await message.answer("❌ No users in this group.", reply_markup=kb_admin_menu())
            return
        bid = await db.create_broadcast(message.from_user.id, text, target)
        status_msg = await message.answer(f"📤 Sending to {len(user_ids)} users... 0%")
        # H2 — batch-fetch all user languages in ONE query instead of N+1.
        langs = await db.get_user_languages_by_ids(user_ids)
        sent = failed = 0
        total = len(user_ids)
        # Stream sends with throttle. Update progress every 50 sends.
        for i, uid in enumerate(user_ids, 1):
            try:
                blang = L(langs.get(uid, DEFAULT_LANGUAGE))
                header = t("broadcast_header_en", blang) if blang == "en" else t("broadcast_header_fa", blang)
                formatted = header + text
                await bot.send_message(uid, formatted)
                sent += 1
                # Throttle to ~20 msgs/sec — Telegram allows 30 but we leave
                # headroom for the bot's other concurrent sends.
                await asyncio.sleep(BROADCAST_THROTTLE_SECONDS)
            except TelegramForbiddenError:
                # User blocked the bot — expected, count as failed silently.
                # M19 — flag the user so they're excluded from future
                # broadcasts instead of being retried every cycle.
                failed += 1
                try:
                    await db.mark_user_blocked(uid)
                except Exception:
                    pass
            except TelegramBadRequest:
                failed += 1
            except Exception as e:
                logger.warning("broadcast send to %s failed: %s", uid, e)
                failed += 1
            # Progress update every 50 sends (cheap edit_text).
            if i % 50 == 0 and i < total:
                try:
                    await status_msg.edit_text(
                        f"📤 Sending to {total} users... {i*100//total}% ({sent} sent, {failed} failed)"
                    )
                except Exception:
                    pass  # Don't let progress-update failure abort the broadcast.
        await db.update_broadcast_stats(bid, sent, failed)
        try:
            await status_msg.delete()
        except Exception:
            pass
        await message.answer(
            f"✅ <b>Broadcast complete</b>\n📤 Sent: {sent}\n❌ Failed: {failed}\n📊 Total: {total}",
            reply_markup=kb_admin_menu(),
        )

    # ====================================================== CLEANUP
    @router.callback_query(AdminCB.filter(F.action == "cleanup"))
    async def cb_cleanup(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        kb = InlineKeyboardBuilder()
        kb.button(text="🧹 Delete depleted (all servers)", callback_data=AdminCB(action="cleanup_depleted").pack(), style="danger")
        kb.button(text="🧹 Sync client counts", callback_data=AdminCB(action="cleanup_sync_counts").pack(), style="primary")
        # ADMIN-MENU-REWORK: Cleanup now lives in the Servers section, so
        # back → servers list (not the top-level admin panel).
        kb.button(text="🔙 Servers", callback_data=AdminCB(action="servers").pack(), style="danger")
        kb.adjust(1, 1)
        await show_view(callback.message, text="🧹 <b>Cleanup & maintenance</b>", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "cleanup_depleted"))
    async def cb_cleanup_depleted(callback: CallbackQuery):
        servers = await db.get_servers(active_only=True)
        total = 0
        for srv in servers:
            r = await api.delete_depleted(srv["panel_url"], srv["api_token"])
            if r.get("success"):
                total += 1
        await callback.answer(f"✅ Ran delDepleted on {total}/{len(servers)} servers", show_alert=True)

    @router.callback_query(AdminCB.filter(F.action == "cleanup_sync_counts"))
    async def cb_cleanup_sync_counts(callback: CallbackQuery):
        servers = await db.get_servers(active_only=True)
        for srv in servers:
            data = await api.get_clients_paged(srv["panel_url"], srv["api_token"], page=1, page_size=1)
            total = data.get("total", 0)
            await db.update_server(srv["id"], total_clients=total)
        await callback.answer("✅ Client counts synced", show_alert=True)

    # ====================================================== SETTINGS
    @router.callback_query(AdminCB.filter(F.action == "settings"))
    async def cb_settings(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        cur = await _currency()
        trial_en = await db.get_setting_int("trial_enabled", 0)
        ref_en = await db.get_setting_int("referral_enabled", 1)
        pay_en = await db.get_setting_int("payment_enabled", 0)
        fj_en = await db.get_setting_int("force_join_enabled", 0)
        rich = rich_tables.rich_message(
            rich_tables.heading("⚙️ Settings"),
            rich_tables.kv_table([
                ("Currency", cur),
                ("Trial", "✅" if trial_en else "❌"),
                ("Referral", "✅" if ref_en else "❌"),
                ("Payment", "✅" if pay_en else "❌"),
                ("Force Join", "✅" if fj_en else "❌"),
            ]),
            rich_tables.paragraph("Tap a category to configure:"),
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="🎉 Trial", callback_data=SettingsCatCB(category="trial").pack(), style="primary")
        kb.button(text="🔗 Referral", callback_data=SettingsCatCB(category="referral").pack(), style="primary")
        kb.button(text="💳 Payment", callback_data=SettingsCatCB(category="payment").pack(), style="primary")
        kb.button(text="📢 Force Join", callback_data=SettingsCatCB(category="force_join").pack(), style="primary")
        kb.button(text="➕ Topup", callback_data=SettingsCatCB(category="topup").pack(), style="primary")
        kb.button(text="📚 Guides", callback_data=SettingsCatCB(category="guides").pack(), style="primary")
        kb.button(text="💾 Backup", callback_data=SettingsCatCB(category="backup").pack(), style="primary")
        kb.button(style="primary", text="💵 Currency", callback_data=AdminCB(action="set_currency").pack())
        kb.button(style="primary", text="👥 Payment Admins", callback_data=AdminCB(action="payment_admins").pack())
        kb.button(text="🔄 Refresh servers", callback_data=AdminCB(action="refresh_servers").pack(), style="primary")
        kb.button(text="🔙 Admin", callback_data=AdminCB(action="main").pack(), style="danger")
        kb.adjust(2, 2, 2, 2, 2, 1)
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())
        await callback.answer()

    # ---- Backup settings (BACKUP-CFG) ----
    @router.callback_query(SettingsCatCB.filter(F.category == "backup"))
    async def cb_settings_backup(callback: CallbackQuery):
        bk_en = await db.get_setting_int("backup_enabled", 0)
        bk_interval = await db.get_setting_int("backup_interval_minutes", 1440)
        bk_keep = await db.get_setting_int("backup_keep", 3)
        # Human-friendly interval display.
        if bk_interval < 60:
            interval_disp = f"{bk_interval} min"
        elif bk_interval < 1440:
            interval_disp = f"{bk_interval // 60}h {bk_interval % 60}m" if bk_interval % 60 else f"{bk_interval // 60}h"
        else:
            days = bk_interval / 1440
            interval_disp = f"{days:.1f} days" if days != int(days) else f"{int(days)} days"
        rich = rich_tables.rich_message(
            rich_tables.heading("💾 Auto DB Backup"),
            rich_tables.kv_table([
                ("Enabled", "✅" if bk_en else "❌"),
                ("Interval", interval_disp),
                ("Keep on disk", f"{bk_keep} copies"),
                ("Manual", "Tap DB Backup below"),
            ]),
            rich_tables.paragraph(
                "The backup is sent to the main admin as a Telegram document. "
                "The SQLite Online Backup API is used so the live bot is not interrupted."
            ),
        )
        kb = InlineKeyboardBuilder()
        kb.button(style="primary", text=f"{'✅' if bk_en else '❌'} Toggle",
                  callback_data=AdminCB(action="toggle_backup").pack())
        kb.button(style="primary", text="⏱ Interval (min)",
                  callback_data=AdminCB(action="set_backup_interval").pack())
        kb.button(style="primary", text="📦 Keep count",
                  callback_data=AdminCB(action="set_backup_keep").pack())
        kb.button(style="success", text="💾 Backup Now",
                  callback_data=AdminCB(action="db_backup").pack())
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(2, 2, 1, 1)
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "toggle_backup"))
    async def cb_toggle_backup(callback: CallbackQuery):
        cur = await db.get_setting_int("backup_enabled", 0)
        await db.set_setting("backup_enabled", "0" if cur else "1")
        await callback.answer(f"Auto backup {'enabled' if not cur else 'disabled'}", show_alert=True)
        await cb_settings(callback)

    @router.callback_query(AdminCB.filter(F.action == "set_backup_interval"))
    async def cb_set_backup_interval(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_int", key="backup_interval_minutes",
                                label="Backup interval (minutes)")
        cur = await db.get_setting_int("backup_interval_minutes", 1440)
        await show_view(callback.message,
            text=f"⏱ <b>Backup interval</b>\n\nEnter the interval in <b>minutes</b>:\n\n"
                 f"Current: {cur} min ({cur // 60}h)\n\n"
                 f"Examples: 60 = hourly, 360 = every 6h, 1440 = daily, 10080 = weekly",
            reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_backup_keep"))
    async def cb_set_backup_keep(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_int", key="backup_keep",
                                label="Backup keep count")
        cur = await db.get_setting_int("backup_keep", 3)
        await show_view(callback.message,
            text=f"📦 <b>Backup retention</b>\n\nHow many on-disk snapshots to keep?\n\n"
                 f"Current: {cur}\n\nOlder snapshots are automatically deleted.",
            reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    # ---- Settings Category Pages ----
    @router.callback_query(SettingsCatCB.filter(F.category == "trial"))
    async def cb_settings_trial(callback: CallbackQuery):
        trial_en = await db.get_setting_int("trial_enabled", 0)
        trial_days = await db.get_setting_int("trial_days", TRIAL_DEFAULT_DAYS)
        trial_gb = await db.get_setting_float("trial_gb", TRIAL_DEFAULT_GB)
        rich = rich_tables.trial_settings_rich(trial_en, trial_days, trial_gb)
        kb = InlineKeyboardBuilder()
        kb.button(style="primary", text=f"{'✅' if trial_en else '❌'} Toggle Trial", callback_data=AdminCB(action="toggle_trial").pack())
        kb.button(style="primary", text="📅 Days", callback_data=AdminCB(action="set_trial_days").pack())
        kb.button(style="primary", text="💾 GB", callback_data=AdminCB(action="set_trial_gb").pack())
        kb.button(style="primary", text="🔗 Inbounds", callback_data=AdminCB(action="set_trial_inbounds").pack())
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(2, 2, 1)
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())
        await callback.answer()

    # L3 — _render_settings_referral_view is the rendering logic extracted from
    # cb_settings_referral so that cb_toggle_referral can refresh the view
    # without the fragile ``fake_cb = callback`` pattern (calling one callback
    # handler from another). Both the view handler and the toggle handler now
    # delegate to this helper, which takes the *message* to render onto.
    async def _render_settings_referral_view(message: Message):
        ref_en = await db.get_setting_int("referral_enabled", 1)
        ref_days = await db.get_setting_int("referral_bonus_days", 5)
        ref_gb = await db.get_setting_float("referral_bonus_gb", 2)
        share_fa = await db.get_setting("referral_share_text_fa", "")
        share_en = await db.get_setting("referral_share_text_en", "")
        extra_fa = await db.get_setting("referral_extra_text_fa", "")
        extra_en = await db.get_setting("referral_extra_text_en", "")
        rich = rich_tables.referral_settings_rich(ref_en, ref_days, ref_gb,
                                                  share_fa, share_en, extra_fa, extra_en)
        kb = InlineKeyboardBuilder()
        kb.button(style="primary", text=f"{'✅' if ref_en else '❌'} Toggle Referral",
                  callback_data=AdminCB(action="toggle_referral").pack())
        kb.button(style="success", text="🎁 Bonus Days", callback_data=AdminCB(action="set_ref_days").pack())
        kb.button(style="success", text="🎁 Bonus GB", callback_data=AdminCB(action="set_ref_gb").pack())
        kb.button(style="primary", text="📝 Share Text 🇮🇷", callback_data=AdminCB(action="set_ref_share_fa").pack())
        kb.button(style="primary", text="📝 Share Text 🇬🇧", callback_data=AdminCB(action="set_ref_share_en").pack())
        kb.button(style="primary", text="➕ Extra Note 🇮🇷", callback_data=AdminCB(action="set_ref_extra_fa").pack())
        kb.button(style="primary", text="➕ Extra Note 🇬🇧", callback_data=AdminCB(action="set_ref_extra_en").pack())
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(1, 2, 2, 2, 1)
        await show_view(message, rich=rich, reply_markup=kb.as_markup())

    @router.callback_query(SettingsCatCB.filter(F.category == "referral"))
    async def cb_settings_referral(callback: CallbackQuery):
        await _render_settings_referral_view(callback.message)
        await callback.answer()

    # L3 — _render_settings_payment_view: same extraction for the payment
    # settings page; called by both cb_settings_payment and cb_toggle_payment.
    async def _render_settings_payment_view(message: Message):
        pay_en = await db.get_setting_int("payment_enabled", 0)
        card = ltr(await db.get_setting("payment_card_number", "-"))
        holder = await db.get_setting("payment_card_holder", "-")
        min_amt = await db.get_setting_int("payment_min_amount", DEFAULT_PAYMENT_MIN_AMOUNT)
        rich = rich_tables.payment_settings_rich(pay_en, card, holder, min_amt)
        kb = InlineKeyboardBuilder()
        kb.button(style="primary", text=f"{'✅' if pay_en else '❌'} Toggle", callback_data=AdminCB(action="toggle_payment").pack())
        kb.button(style="primary", text="💳 Card Number", callback_data=AdminCB(action="set_card_number").pack())
        kb.button(style="primary", text="👤 Card Holder", callback_data=AdminCB(action="set_card_holder").pack())
        kb.button(style="primary", text="🔢 Min Amount", callback_data=AdminCB(action="set_payment_min").pack())
        kb.button(style="primary", text="📋 Presets", callback_data=AdminCB(action="set_payment_presets").pack())
        kb.button(text="💰 Pending Payments", callback_data=AdminCB(action="pending_payments").pack(), style="success")
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(2, 2, 2, 1)
        await show_view(message, rich=rich, reply_markup=kb.as_markup())

    @router.callback_query(SettingsCatCB.filter(F.category == "payment"))
    async def cb_settings_payment(callback: CallbackQuery):
        await _render_settings_payment_view(callback.message)
        await callback.answer()

    # L3 — _render_settings_force_join_view: same extraction for the force-join
    # settings page; called by cb_settings_force_join, cb_toggle_force_join
    # and cb_remove_force_join_channel (which re-renders the view after
    # removing a channel).
    async def _render_settings_force_join_view(message: Message):
        fj_en = await db.get_setting_int("force_join_enabled", 0)
        channels = await db.get_setting_json("force_join_channels", [])
        rich = rich_tables.force_join_settings_rich(fj_en, channels)
        kb = InlineKeyboardBuilder()
        kb.button(style="primary", text=f"{'✅' if fj_en else '❌'} Toggle", callback_data=AdminCB(action="toggle_force_join").pack())
        kb.button(text="➕ Add Channel", callback_data=AdminCB(action="add_force_join_channel").pack(), style="success")
        if channels:
            kb.button(text="🗑 Remove Channel", callback_data=AdminCB(action="remove_force_join_channel").pack(), style="danger")
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(2, 1, 1)
        await show_view(message, rich=rich, reply_markup=kb.as_markup())

    @router.callback_query(SettingsCatCB.filter(F.category == "force_join"))
    async def cb_settings_force_join(callback: CallbackQuery):
        await _render_settings_force_join_view(callback.message)
        await callback.answer()

    async def _render_settings_topup_view(message: Message):
        topup_price = await db.get_setting_float("topup_price_per_gb", TOPUP_DEFAULT_PRICE_PER_GB)
        packages = await db.get_setting_json("topup_packages", [5, 10, 20, 50])
        topup_enabled = await db.get_setting_int("topup_enabled", 1)
        rich = rich_tables.topup_settings_rich(topup_price, packages)
        kb = InlineKeyboardBuilder()
        # TOPUP-TOGGLE: label reflects the NEXT state (what tapping will do).
        toggle_label = ("❌ Disable Top-Ups" if topup_enabled else "✅ Enable Top-Ups")
        kb.button(style="primary", text=toggle_label,
                  callback_data=AdminCB(action="toggle_topup").pack())
        kb.button(style="success", text="➕ Price/GB", callback_data=AdminCB(action="set_topup_price").pack())
        kb.button(style="primary", text="📦 Packages", callback_data=AdminCB(action="set_topup_packages").pack())
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(1, 2, 1)
        await show_view(message, rich=rich, reply_markup=kb.as_markup())

    @router.callback_query(SettingsCatCB.filter(F.category == "topup"))
    async def cb_settings_topup(callback: CallbackQuery):
        await _render_settings_topup_view(callback.message)
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "toggle_topup"))
    async def cb_toggle_topup(callback: CallbackQuery):
        cur = await db.get_setting_int("topup_enabled", 1)
        await db.set_setting("topup_enabled", "0" if cur else "1")
        await callback.answer(f"Top-ups {'enabled' if not cur else 'disabled'}", show_alert=True)
        # Refresh the view so the toggle label updates.
        await _render_settings_topup_view(callback.message)

    @router.callback_query(SettingsCatCB.filter(F.category == "guides"))
    async def cb_settings_guides(callback: CallbackQuery):
        """GUIDES: dual guides (usage + connection), each editable per language.

        Replaces the old single help_text category. The admin sees a preview
        of each guide's first line and can edit any of the 4 fields. Empty
        values fall back to the rich DEFAULT_GUIDE_* constants.
        """
        usage_en = await db.get_setting("guide_usage_en", "")
        usage_fa = await db.get_setting("guide_usage_fa", "")
        conn_en = await db.get_setting("guide_connection_en", "")
        conn_fa = await db.get_setting("guide_connection_fa", "")

        def _preview(val: str, fallback: str) -> str:
            v = (val if val and val.strip() else fallback)
            v = v.replace("\n", " ").strip()
            return escape_html(v[:80] + ("…" if len(v) > 80 else ""))

        text = (
            "📚 <b>Guide Settings</b>\n\n"
            "Two guides, each editable in English and Farsi. Leave empty to use the built-in default.\n\n"
            f"📖 <b>Usage — EN:</b>\n<i>{_preview(usage_en, DEFAULT_GUIDE_USAGE_EN)}</i>\n\n"
            f"📖 <b>Usage — FA:</b>\n<i>{_preview(usage_fa, DEFAULT_GUIDE_USAGE_FA)}</i>\n\n"
            f"🔌 <b>Connection — EN:</b>\n<i>{_preview(conn_en, DEFAULT_GUIDE_CONNECTION_EN)}</i>\n\n"
            f"🔌 <b>Connection — FA:</b>\n<i>{_preview(conn_fa, DEFAULT_GUIDE_CONNECTION_FA)}</i>"
        )
        kb = InlineKeyboardBuilder()
        kb.button(style="primary", text="📖 Usage 🇬🇧", callback_data=AdminCB(action="edit_guide_usage_en").pack())
        kb.button(style="primary", text="📖 Usage 🇮🇷", callback_data=AdminCB(action="edit_guide_usage_fa").pack())
        kb.button(style="primary", text="🔌 Connection 🇬🇧", callback_data=AdminCB(action="edit_guide_conn_en").pack())
        kb.button(style="primary", text="🔌 Connection 🇮🇷", callback_data=AdminCB(action="edit_guide_conn_fa").pack())
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(2, 2, 1)
        await show_view(callback.message, text=text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "toggle_trial"))
    async def cb_toggle_trial(callback: CallbackQuery):
        cur = await db.get_setting_int("trial_enabled", 0)
        await db.set_setting("trial_enabled", "0" if cur else "1")
        await callback.answer(f"Trial {'enabled' if not cur else 'disabled'}", show_alert=True)
        await cb_settings(callback)

    @router.callback_query(AdminCB.filter(F.action == "toggle_referral"))
    async def cb_toggle_referral(callback: CallbackQuery):
        cur = await db.get_setting_int("referral_enabled", 1)
        await db.set_setting("referral_enabled", "0" if cur else "1")
        await callback.answer(f"Referral {'enabled' if not cur else 'disabled'}", show_alert=True)
        # L3 — refresh the view via the extracted render helper instead of
        # ``fake_cb = callback; await cb_settings_referral(fake_cb)``.
        await _render_settings_referral_view(callback.message)

    @router.callback_query(AdminCB.filter(F.action == "set_currency"))
    async def cb_set_currency(callback: CallbackQuery):
        kb = InlineKeyboardBuilder()
        kb.button(text="🇮🇷 Toman", callback_data=AdminCB(action="set_currency_val", data="toman").pack(), style="primary")
        kb.button(style="primary", text="💵 USD", callback_data=AdminCB(action="set_currency_val", data="usd").pack())
        kb.button(text="🔙 Back", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(2, 1)
        await show_view(callback.message, text="💵 <b>Currency</b>", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_currency_val"))
    async def cb_set_currency_val(callback: CallbackQuery, callback_data: AdminCB):
        await db.set_setting("currency", callback_data.data)
        await callback.answer("✅ Currency updated", show_alert=True)
        await cb_settings(callback)

    async def _set_int_setting(message: Message, state: FSMContext, key: str, label: str):
        try:
            val = int((message.text or "").strip())
        except ValueError:
            await message.answer("❌ Number please:", reply_markup=kb_cancel("en"))
            return
        await state.clear()
        await db.set_setting(key, str(val))
        await message.answer(f"✅ {label} set to {val}", reply_markup=kb_admin_menu())

    async def _set_str_setting(message: Message, state: FSMContext, key: str, label: str):
        val = (message.text or "").strip()
        await state.clear()
        await db.set_setting(key, val)
        await message.answer(f"✅ {label} updated", reply_markup=kb_admin_menu())

    @router.callback_query(AdminCB.filter(F.action == "set_trial_days"))
    async def cb_set_trial_days(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_int", key="trial_days", label="Trial days")
        await show_view(callback.message, text="📅 Enter trial days:", reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_trial_gb"))
    async def cb_set_trial_gb(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_int", key="trial_gb", label="Trial GB")
        await show_view(callback.message, text="💾 Enter trial GB:", reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_ref_days"))
    async def cb_set_ref_days(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_int", key="referral_bonus_days", label="Referral days")
        await show_view(callback.message, text="🎁 Enter referral bonus days:", reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_ref_gb"))
    async def cb_set_ref_gb(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_int", key="referral_bonus_gb", label="Referral GB")
        await show_view(callback.message, text="🎁 Enter referral bonus GB:", reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    # ---- Referral text customisation (REFERRAL-TEXT-CFG) ----
    # The main admin can override the share pitch (the message users forward
    # to friends) and add an extra note shown at the bottom of the referral
    # section. Both are per-language (fa/en). Sending "-" clears the field so
    # the locale default share text / no extra note is used again. The share
    # text supports {days} and {gb} placeholders that are filled automatically
    # with the current bonus amounts when the user taps Share.
    async def _start_referral_text_edit(callback: CallbackQuery, state: FSMContext,
                                         key: str, label: str, lang_name: str,
                                         is_share: bool):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_str", key=key, label=label)
        current = await db.get_setting(key, "")
        fallback = t('referral_share_text', lang_name) if is_share else ""
        preview = current if (current and current.strip()) else fallback
        if is_share:
            placeholder_note = (
                "\n\n💡 Placeholders: <code>{days}</code> = bonus days, "
                "<code>{gb}</code> = bonus GB (filled automatically when shared)."
            )
            clear_hint = "use built-in default"
        else:
            placeholder_note = (
                "\n\n💡 This note appears at the bottom of the referral section. "
                "Use it for promotions or custom instructions. HTML is supported."
            )
            clear_hint = "hide the note"
        await show_view(callback.message, text=
            f"📝 <b>{label}</b>\n\n"
            f"Current (or default):\n<i>{escape_html(preview[:400])}{'…' if len(preview) > 400 else ''}</i>"
            f"{placeholder_note}"
            f"\n\nSend the new text, or <code>-</code> to clear ({clear_hint}):",
            reply_markup=kb_cancel(lang_name),
            state=state,
        )
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_ref_share_fa"))
    async def cb_set_ref_share_fa(callback: CallbackQuery, state: FSMContext):
        await _start_referral_text_edit(callback, state, "referral_share_text_fa",
                                         "Referral share text (Farsi)", "fa", is_share=True)

    @router.callback_query(AdminCB.filter(F.action == "set_ref_share_en"))
    async def cb_set_ref_share_en(callback: CallbackQuery, state: FSMContext):
        await _start_referral_text_edit(callback, state, "referral_share_text_en",
                                         "Referral share text (English)", "en", is_share=True)

    @router.callback_query(AdminCB.filter(F.action == "set_ref_extra_fa"))
    async def cb_set_ref_extra_fa(callback: CallbackQuery, state: FSMContext):
        await _start_referral_text_edit(callback, state, "referral_extra_text_fa",
                                         "Referral extra note (Farsi)", "fa", is_share=False)

    @router.callback_query(AdminCB.filter(F.action == "set_ref_extra_en"))
    async def cb_set_ref_extra_en(callback: CallbackQuery, state: FSMContext):
        await _start_referral_text_edit(callback, state, "referral_extra_text_en",
                                         "Referral extra note (English)", "en", is_share=False)

    @router.callback_query(AdminCB.filter(F.action == "set_topup_price"))
    async def cb_set_topup_price(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_int", key="topup_price_per_gb", label="Topup price/GB")
        await show_view(callback.message, text="➕ Enter topup price per GB:", reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    # The generic setting_edit_value handler also covers setting_int — handle it:
    # (already defined above for server/plan; extend it here via an extra branch)

    @router.callback_query(AdminCB.filter(F.action == "set_trial_inbounds"))
    async def cb_set_trial_inbounds(callback: CallbackQuery):
        """Multi-select inbound picker for trial accounts."""
        selected = set()
        raw = await db.get_setting("trial_inbounds", "[]")
        try:
            selected = set(json.loads(raw or "[]"))
        except Exception:
            selected = set()
        kb = InlineKeyboardBuilder()
        servers = await db.get_servers(active_only=True)
        for srv in servers:
            kb.button(style="primary", text=f"— {escape_html(srv['alias'])} —", callback_data=NoopCB().pack())
            inbounds = await db.get_inbounds(srv["id"], enabled_only=True)
            for ib in inbounds:
                key = f"{srv['id']}_{ib['inbound_id']}"
                mark = "✅" if key in selected else "⬜"
                kb.button(style="primary", text=f"{mark} {ib.get('remark') or ib['inbound_id']} ({ib.get('protocol','?')})",
                          callback_data=AdminCB(action="trial_ib_toggle", data=key).pack())
        kb.button(text="💾 Save", callback_data=AdminCB(action="settings").pack(), style="success")
        kb.button(text="⬜ Clear (use all)", callback_data=AdminCB(action="trial_ib_clear").pack(), style="danger")
        kb.adjust(1, 1)
        await show_view(callback.message, text="🔗 <b>Trial inbounds</b> (empty = use all)", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "trial_ib_toggle"))
    async def cb_trial_ib_toggle(callback: CallbackQuery, callback_data: AdminCB):
        raw = await db.get_setting("trial_inbounds", "[]")
        try:
            selected = set(json.loads(raw or "[]"))
        except Exception:
            selected = set()
        key = callback_data.data
        if key in selected:
            selected.discard(key)
        else:
            selected.add(key)
        await db.set_setting("trial_inbounds", json.dumps(list(selected)))
        # re-render
        await cb_set_trial_inbounds(callback)

    @router.callback_query(AdminCB.filter(F.action == "trial_ib_clear"))
    async def cb_trial_ib_clear(callback: CallbackQuery):
        await db.set_setting("trial_inbounds", "[]")
        await callback.answer("Cleared — trial will use all inbounds", show_alert=True)
        await cb_set_trial_inbounds(callback)

    @router.callback_query(AdminCB.filter(F.action == "refresh_servers"))
    async def cb_refresh_servers(callback: CallbackQuery):
        servers = await db.get_servers(active_only=True)
        for srv in servers:
            inbounds = await api.get_inbounds(srv["panel_url"], srv["api_token"])
            await db.sync_inbounds(srv["id"], inbounds)
            sub_uri = await api.fetch_sub_uri(srv["panel_url"], srv["api_token"])
            if sub_uri:
                await db.update_server(srv["id"], sub_uri=sub_uri)
            ok, msg = await api.test_panel_connection(srv["panel_url"], srv["api_token"])
            await db.update_server_health(srv["id"], ok, "" if ok else msg)
        await callback.answer(f"✅ Refreshed {len(servers)} servers", show_alert=True)

    @router.callback_query(AdminCB.filter(F.action == "db_backup"))
    async def cb_db_backup(callback: CallbackQuery):
        # H15 — use the SQLite Online Backup API (same as task_db_backup)
        # instead of reading the live DB file directly. A raw file copy races
        # with WAL checkpointing and can produce a corrupt snapshot.
        import tempfile
        tmp_path = None
        try:
            ts = datetime.now(TEHRAN_TZ).strftime("%Y%m%dT%H%M%SZ")
            fd, tmp_path = tempfile.mkstemp(prefix="bot_backup_", suffix=f"_{ts}.db")
            os.close(fd)
            async with aiosqlite.connect(tmp_path) as dst:
                await db._db.backup(dst)
            size = os.path.getsize(tmp_path)
            logger.info("manual db_backup: snapshot written to %s (%d bytes)", tmp_path, size)
            await bot.send_document(callback.from_user.id,
                                    FSInputFile(tmp_path, filename="bot.db"),
                                    caption=f"💾 <b>Database backup</b>\n📦 {size:,} bytes\n🕒 {ts}",
                                    parse_mode="HTML")
            await callback.answer("✅ Sent to your PM", show_alert=True)
        except Exception as e:
            logger.error("manual db_backup failed: %s", e, exc_info=True)
            await callback.answer(f"❌ {str(e)[:50]}", show_alert=True)
        finally:
            if tmp_path:
                try:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                except OSError:
                    pass

    # ====================================================== IMPORT FROM PANEL (MIGRATE-1)
    # In-memory cache of the last-fetched panel client list per admin, so
    # paging through the import list does not re-hit the panel API on every
    # page click. Holds one (server_id, clients) tuple per admin; trivially
    # small for a single-admin bot.
    _import_cache = {}

    async def _import_client_row(srv: dict, client: dict):
        """Import a single (slim) panel client object into the bot DB.

        Uses only fields present in the paged-list response (email, tgId,
        subId, enable, totalGB, expiryTime, limitIp, inboundIds), so no extra
        ``/clients/get`` API call is needed. The client must already have
        ``tgId`` set. Returns ``(ok, msg)``."""
        email = client.get("email")
        tg_id = int(client.get("tgId") or 0)
        if not email:
            return False, "no email"
        if not tg_id:
            return False, "no Telegram ID set"
        await db.upsert_user_minimal(tg_id)
        total_gb_raw = int(client.get("totalGB") or 0)
        # Use float division so fractional GB (e.g. 200 MB = 0.2 GB) is
        # preserved instead of being floored to 0 by integer division.
        traffic_gb = total_gb_raw / GB if total_gb_raw > 0 else 0
        inbound_ids = json.dumps(client.get("inboundIds") or [])
        await db.upsert_account(
            user_tg_id=tg_id,
            server_id=srv["id"],
            email=email,
            sub_id=client.get("subId") or "",
            traffic_gb=traffic_gb,
            expiry_time=int(client.get("expiryTime") or 0),
            limit_ip=int(client.get("limitIp") or 0),
            inbound_ids=inbound_ids,
            is_active=bool(client.get("enable")),
            is_trial=False,
        )
        return True, "ok"

    @router.callback_query(AdminCB.filter(F.action == "import_main"))
    async def cb_import_main(callback: CallbackQuery):
        servers = await db.get_servers(active_only=True)
        if not servers:
            await show_view(callback.message,
                text="ℹ️ No active servers. Add a server first.",
                reply_markup=kb_admin_menu())
            await callback.answer()
            return
        await show_view(callback.message,
            text=("📥 <b>Import clients from panel</b>\n\n"
                  "Select a server to list its existing panel clients.\n"
                  "You can assign a Telegram numeric ID to each client so "
                  "the user controls their config from this bot."),
            reply_markup=kb_import_server_picker(servers))
        await callback.answer()

    @router.callback_query(ImportCB.filter(F.action == "server"))
    async def cb_import_server(callback: CallbackQuery, callback_data: ImportCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer("Server not found", show_alert=True)
            return
        await callback.answer("⏳ Fetching clients…")
        try:
            clients = await api.get_all_clients(srv["panel_url"], srv["api_token"])
        except Exception as e:
            await show_view(callback.message,
                text=f"❌ Panel error: {escape_html(str(e)[:120])}",
                reply_markup=kb_import_server_picker(await db.get_servers(active_only=True)))
            return
        _import_cache[callback.from_user.id] = (srv["id"], clients)
        if not clients:
            await show_view(callback.message,
                text=f"📥 <b>{escape_html(srv['alias'])}</b>\n\nNo clients found on panel.",
                reply_markup=kb_import_server_picker(await db.get_servers(active_only=True)))
            return
        with_tg = sum(1 for c in clients if c.get("tgId"))
        rich = rich_tables.rich_message(
            rich_tables.heading(f"📥 {srv['alias']}"),
            rich_tables.kv_table([
                ("Total clients", len(clients)),
                ("With Telegram ID", with_tg),
            ]),
            rich_tables.paragraph("Tap a client to import or set its Telegram ID."),
        )
        await show_view(callback.message, rich=rich,
            reply_markup=kb_import_client_list(srv["id"], clients, callback_data.page))

    @router.callback_query(ImportCB.filter(F.action == "page"))
    async def cb_import_page(callback: CallbackQuery, callback_data: ImportCB):
        cached = _import_cache.get(callback.from_user.id)
        if not cached or cached[0] != callback_data.server_id:
            srv = await db.get_server(callback_data.server_id)
            if not srv:
                await callback.answer("Server not found", show_alert=True)
                return
            await callback.answer("⏳ Fetching clients…")
            clients = await api.get_all_clients(srv["panel_url"], srv["api_token"])
            _import_cache[callback.from_user.id] = (srv["id"], clients)
        else:
            clients = cached[1]
            srv = await db.get_server(callback_data.server_id)
        await show_view(callback.message,
            text=f"📥 <b>{escape_html(srv['alias'])}</b> — {len(clients)} clients",
            reply_markup=kb_import_client_list(srv["id"], clients, callback_data.page))

    @router.callback_query(ImportCB.filter(F.action == "client"))
    async def cb_import_client(callback: CallbackQuery, callback_data: ImportCB):
        cached = _import_cache.get(callback.from_user.id)
        clients = cached[1] if cached and cached[0] == callback_data.server_id else []
        client = next((c for c in clients if c.get("email") == callback_data.email), None)
        if not client:
            await callback.answer("Client not in cache. Reopen the server.", show_alert=True)
            return
        existing = await db.get_account(callback_data.email)
        total_gb_raw = int(client.get("totalGB") or 0)
        # Use fmt_gb so fractional GB (e.g. 200 MB) renders as "0.2 GB"
        # instead of being floored to "0 GB" by integer division.
        gb_disp = fmt_gb(total_gb_raw / GB, "en") if total_gb_raw else "∞"
        rows = [
            ("Email", client.get("email", "—")),
            ("Telegram ID", client.get("tgId") or "— not set —"),
            ("Enabled", "✅" if client.get("enable") else "❌"),
            ("Traffic limit", gb_disp),
            ("Expiry", fmt_ts(client.get("expiryTime") or 0) or "∞"),
            ("Limit IP", client.get("limitIp") or "∞"),
            ("Inbounds", ", ".join(str(i) for i in (client.get("inboundIds") or [])) or "—"),
            ("In bot DB", "✅ yes" if existing else "❌ no"),
        ]
        rich = rich_tables.rich_message(
            rich_tables.heading("📥 Client detail"),
            rich_tables.kv_table(rows),
        )
        await show_view(callback.message, rich=rich,
            reply_markup=kb_import_client_view(callback_data.server_id,
                callback_data.email, bool(client.get("tgId")),
                bool(existing), callback_data.page))

    @router.callback_query(ImportCB.filter(F.action == "set_tgid"))
    async def cb_import_set_tgid(callback: CallbackQuery, callback_data: ImportCB, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_import_tg_id)
        await state.update_data(server_id=callback_data.server_id,
                                email=callback_data.email,
                                page=callback_data.page)
        await show_view(callback.message,
            text=(f"🔗 Enter the Telegram numeric ID for:\n"
                  f"<code>{escape_html(callback_data.email)}</code>\n\n"
                  f"Send <code>0</code> or <code>-</code> to clear."),
            reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    @router.message(AdminStates.waiting_for_import_tg_id)
    async def ms_import_tg_id(message: Message, state: FSMContext):
        await del_inbound(message, state)
        raw = (message.text or "").strip()
        data = await state.get_data()
        await state.clear()
        server_id = data.get("server_id")
        email = data.get("email")
        page = data.get("page", 1)
        srv = await db.get_server(server_id)
        if not srv:
            await message.answer("❌ Server not found.", reply_markup=kb_admin_menu())
            return
        tg_id = 0
        if raw not in ("0", "-", ""):
            try:
                tg_id = int(raw)
                if tg_id < 0:
                    raise ValueError
            except ValueError:
                await message.answer("❌ Invalid Telegram ID. Must be a positive integer.",
                                     reply_markup=kb_admin_menu())
                return
        r = await api.set_client_tg_id(srv["panel_url"], srv["api_token"], email, tg_id)
        if not r.get("success"):
            await message.answer(f"❌ Panel error: {escape_html(r.get('msg', 'unknown'))}",
                                 reply_markup=kb_admin_menu())
            return
        cached = _import_cache.get(message.from_user.id)
        clients = cached[1] if cached and cached[0] == server_id else []
        client = next((c for c in clients if c.get("email") == email), None)
        if client:
            client["tgId"] = tg_id
        if tg_id and client:
            ok, _msg = await _import_client_row(srv, client)
            note = "imported" if ok else "import failed"
            await message.answer(
                f"✅ Telegram ID <code>{tg_id}</code> set on panel & client {note}.\n"
                f"The user will see this config in <b>My Accounts</b>.",
                reply_markup=kb_import_client_list(server_id, clients, page))
        elif tg_id:
            await message.answer(
                f"✅ Telegram ID <code>{tg_id}</code> set on panel.\n"
                f"Reopen the server to import.",
                reply_markup=kb_import_server_picker(await db.get_servers(active_only=True)))
        else:
            await message.answer("✅ Telegram ID cleared on panel.",
                reply_markup=kb_import_client_list(server_id, clients, page))

    @router.callback_query(ImportCB.filter(F.action == "do"))
    async def cb_import_do(callback: CallbackQuery, callback_data: ImportCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer("Server not found", show_alert=True)
            return
        cached = _import_cache.get(callback.from_user.id)
        clients = cached[1] if cached and cached[0] == srv["id"] else []
        client = next((c for c in clients if c.get("email") == callback_data.email), None)
        if not client:
            await callback.answer("Client not in cache. Reopen the server.", show_alert=True)
            return
        await callback.answer("⏳ Importing…")
        ok, msg = await _import_client_row(srv, client)
        await show_view(callback.message,
            text=(f"✅ Imported <code>{escape_html(callback_data.email)}</code>." if ok
                  else f"❌ Could not import: {escape_html(msg)}"),
            reply_markup=kb_import_client_list(srv["id"], clients, callback_data.page))

    @router.callback_query(ImportCB.filter(F.action == "all"))
    async def cb_import_all(callback: CallbackQuery, callback_data: ImportCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer("Server not found", show_alert=True)
            return
        await callback.answer("⏳ Bulk import…")
        cached = _import_cache.get(callback.from_user.id)
        if cached and cached[0] == srv["id"]:
            clients = cached[1]
        else:
            clients = await api.get_all_clients(srv["panel_url"], srv["api_token"])
            _import_cache[callback.from_user.id] = (srv["id"], clients)
        with_tg = [c for c in clients if c.get("tgId")]
        ok_count = 0
        fail_count = 0
        for c in with_tg:
            try:
                ok, _ = await _import_client_row(srv, c)
                if ok:
                    ok_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error("import %s failed: %s", c.get("email"), e)
                fail_count += 1
        await show_view(callback.message,
            text=(f"⚡ <b>Bulk import complete</b>\n\n"
                  f"✅ Imported: {ok_count}\n"
                  f"❌ Failed: {fail_count}\n"
                  f"➖ Skipped (no TG ID): {len(clients) - len(with_tg)}"),
            reply_markup=kb_import_client_list(srv["id"], clients, callback_data.page))

    # ---- Payment settings handlers ----
    @router.callback_query(AdminCB.filter(F.action == "toggle_payment"))
    async def cb_toggle_payment(callback: CallbackQuery):
        cur = await db.get_setting_int("payment_enabled", 0)
        await db.set_setting("payment_enabled", "0" if cur else "1")
        await callback.answer(f"Payment {'enabled' if not cur else 'disabled'}", show_alert=True)
        # L3 — refresh via the extracted render helper (no more fake_cb).
        await _render_settings_payment_view(callback.message)

    @router.callback_query(AdminCB.filter(F.action == "set_card_number"))
    async def cb_set_card_number(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_str", key="payment_card_number", label="Card number")
        await show_view(callback.message, text="💳 Enter card number:", reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_card_holder"))
    async def cb_set_card_holder(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_str", key="payment_card_holder", label="Card holder")
        await show_view(callback.message, text="👤 Enter card holder name:", reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_payment_min"))
    async def cb_set_payment_min(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_int", key="payment_min_amount", label="Min payment amount")
        await show_view(callback.message, text="🔢 Enter minimum payment amount (Toman):", reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_payment_presets"))
    async def cb_set_payment_presets(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_str", key="payment_presets", label="Payment presets")
        cur = await db.get_setting_json("payment_presets", [50000, 100000, 200000, 500000])
        await show_view(callback.message, text=
            f"📋 Enter preset amounts as comma-separated numbers (Toman):\n\nCurrent: {cur}",
            reply_markup=kb_cancel("en"),
            state=state,
        )
        await callback.answer()

    # ---- Payment admins management ----
    @router.callback_query(AdminCB.filter(F.action == "payment_admins"))
    async def cb_payment_admins(callback: CallbackQuery):
        pa_ids = sorted(await get_payment_admin_ids(db))
        lines = ["👥 <b>Payment-Only Admins</b>\n"]
        lines.append("These users can ONLY approve/reject pending payments.")
        lines.append("They cannot access servers, plans, users, or settings.\n")
        if pa_ids:
            lines.append("<b>Current payment admins:</b>")
            for pid in pa_ids:
                u = await db.get_user(pid)
                if u:
                    uname = escape_html(u.get("first_name") or u.get("username") or "—")
                    handle = f" @{u['username']}" if u.get("username") else ""
                    lines.append(f"• <code>{pid}</code> — {uname}{handle}")
                else:
                    lines.append(f"• <code>{pid}</code> — (not yet started bot)")
        else:
            lines.append("<i>No payment admins configured.</i>")
        kb = InlineKeyboardBuilder()
        kb.button(style="success", text="➕ Add Payment Admin",
                  callback_data=AdminCB(action="add_payment_admin").pack())
        # Remove buttons (one per existing admin)
        for pid in pa_ids:
            kb.button(style="danger", text=f"🗑 Remove {pid}",
                      callback_data=AdminCB(action="remove_payment_admin", data=str(pid)).pack())
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(1, *[1 for _ in pa_ids], 1)
        await show_view(callback.message, text="\n".join(lines), reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "add_payment_admin"))
    async def cb_add_payment_admin(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_payment_admin_id)
        await show_view(callback.message,
            text=("➕ <b>Add Payment Admin</b>\n\n"
                  "Send the Telegram <b>numeric ID</b> of the user you want to "
                  "make a payment-only admin.\n\n"
                  "The user must have started the bot at least once (so we can "
                  "look them up). Payment admins can only approve/reject "
                  "pending payments — nothing else."),
            reply_markup=kb_cancel("en"), state=state)
        await callback.answer()

    @router.message(AdminStates.waiting_for_payment_admin_id)
    async def ms_payment_admin_id(message: Message, state: FSMContext):
        await del_inbound(message, state)
        raw = (message.text or "").strip()
        await state.clear()
        try:
            tg_id = int(raw)
            if tg_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            await message.answer("❌ Invalid ID. Must be a positive integer.",
                                 reply_markup=kb_admin_menu())
            return
        if tg_id in ADMIN_IDS:
            await message.answer("⚠️ This user is already a FULL admin — no need to add as payment admin.",
                                 reply_markup=kb_admin_menu())
            return
        pa_ids = await get_payment_admin_ids(db)
        if tg_id in pa_ids:
            await message.answer("⚠️ This user is already a payment admin.",
                                 reply_markup=kb_admin_menu())
            return
        pa_ids.add(tg_id)
        await db.set_setting("payment_admin_ids", json.dumps(sorted(pa_ids)))
        # Try to notify the new payment admin.
        # PA-LANG: the notification is sent to the NEW payment admin, so use
        # their selected language (not the adding admin's language).
        try:
            new_pa_lang = await _pa_lang(tg_id)
            await bot.send_message(tg_id,
                f"{t('pa_menu_title', new_pa_lang)}\n\n"
                f"{t('pa_menu_desc', new_pa_lang)}\n"
                f"{'Send /admin to open the payment panel.' if new_pa_lang == 'en' else 'برای باز کردن پنل، /admin را بفرستید.'}",
                reply_markup=kb_payment_admin_menu(new_pa_lang))
        except Exception:
            pass  # user hasn't started the bot yet — that's OK
        await message.answer(
            f"✅ Added <code>{tg_id}</code> as payment admin.\n"
            f"They can now use /admin to manage payments.",
            reply_markup=kb_admin_menu())

    @router.callback_query(AdminCB.filter(F.action == "remove_payment_admin"))
    async def cb_remove_payment_admin(callback: CallbackQuery, callback_data: AdminCB):
        try:
            tg_id = int(callback_data.data)
        except (TypeError, ValueError):
            await callback.answer("Invalid ID.", show_alert=True)
            return
        pa_ids = await get_payment_admin_ids(db)
        pa_ids.discard(tg_id)
        await db.set_setting("payment_admin_ids", json.dumps(sorted(pa_ids)))
        await callback.answer(f"✅ Removed {tg_id}", show_alert=False)
        # Re-render the list.
        remaining = sorted(pa_ids)
        lines = ["👥 <b>Payment-Only Admins</b>\n",
                 "These users can ONLY approve/reject pending payments.\n"]
        if remaining:
            lines.append("<b>Current payment admins:</b>")
            for pid in remaining:
                u = await db.get_user(pid)
                if u:
                    uname = escape_html(u.get("first_name") or u.get("username") or "—")
                    handle = f" @{u['username']}" if u.get("username") else ""
                    lines.append(f"• <code>{pid}</code> — {uname}{handle}")
                else:
                    lines.append(f"• <code>{pid}</code> — (not yet started bot)")
        else:
            lines.append("<i>No payment admins configured.</i>")
        kb = InlineKeyboardBuilder()
        kb.button(style="success", text="➕ Add Payment Admin",
                  callback_data=AdminCB(action="add_payment_admin").pack())
        for pid in remaining:
            kb.button(style="danger", text=f"🗑 Remove {pid}",
                      callback_data=AdminCB(action="remove_payment_admin", data=str(pid)).pack())
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(1, *[1 for _ in remaining], 1)
        await show_view(callback.message, text="\n".join(lines), reply_markup=kb.as_markup())

    # ---- Pending payments ----
    @router.callback_query(AdminCB.filter(F.action == "pending_payments"))
    async def cb_pending_payments(callback: CallbackQuery):
        # RECEIPT-FIRST: the admin complained that entering Pending Payments
        # showed only the amount/unique-amount text and they couldn't see the
        # receipt photo or receipt text without an extra tap. Now we jump
        # straight into the first pending payment's full detail view (which
        # ALSO sends the receipt photo as a follow-up message). If there are
        # more pending payments, a "Next pending" button walks through them.
        # PA-LANG: localised via _pa_lang (full admins → en, payment admins →
        # their selected language).
        pal = await _pa_lang(callback.from_user.id)
        payments = await db.get_pending_payments()
        if not payments:
            # ADMIN-MENU-REWORK: full admin → Payments submenu, payment admin → their panel.
            menu = kb_payments_menu() if await _is_full_admin(callback.from_user.id) else kb_payment_admin_menu(pal)
            await show_view(callback.message, text=t("pa_no_pending", pal), reply_markup=menu)
            await callback.answer()
            return
        # Pre-build a small index summary so the admin still sees the queue
        # length (e.g. "3 pending") above the first item's detail card.
        await _render_pending_view(callback, payments, 0, pal)
        await callback.answer()

    async def _render_pending_view(callback: CallbackQuery,
                                   payments: List[dict], index: int,
                                   pal: str = "en"):
        """Render ONE pending payment as a full detail card + receipt photo.

        Shared by ``cb_pending_payments`` (initial entry) and the per-item
        "Next pending" navigation button. ``payments`` is the full pending
        list (already fetched); ``index`` is the 0-based slot to render.
        ``pal`` is the payment-admin display language (PA-LANG).
        """
        payment = payments[index]
        user = await db.get_user(payment["user_tg_id"])
        uname = escape_html((user or {}).get("first_name") or (user or {}).get("username")
                            or str(payment["user_tg_id"]))
        amt_en = fmt_num(payment['unique_amount'], 'en')
        # Header: queue position + total queue length, so the admin knows how
        # many are left after this one.
        header = t("pa_pending_header", pal, i=index + 1, n=len(payments)) + "\n\n"
        text = header + t("pa_payment_title", pal, id=payment['id']) + "\n\n"
        text += t("pa_user", pal, name=uname, id=payment['user_tg_id']) + "\n"
        text += t("pa_base_amount", pal, amt=int(payment['amount'])) + "\n"
        text += t("pa_unique_amount", pal, amt=amt_en) + "\n"
        text += t("pa_card", pal, num=ltr(escape_html(payment.get('card_number') or '-'))) + "\n"
        text += t("pa_created", pal, date=fmt_iso(payment.get('created_at'), '%Y-%m-%d %H:%M:%S') or '-') + "\n"
        receipt_kind = (payment.get("receipt_type") or "").lower()
        kind_icon = {"photo": "📸", "document": "📎", "text": "📝"}.get(receipt_kind, "—")
        if payment.get("receipt_text"):
            text += t("pa_receipt_text", pal, icon=kind_icon, text=escape_html(payment['receipt_text'][:300])) + "\n"
        else:
            text += t("pa_receipt_kind", pal, icon=kind_icon, kind=receipt_kind or 'none') + "\n"
        text += "\n" + t("pa_status", pal, status=t("pa_status_pending", pal))

        kb = InlineKeyboardBuilder()
        kb.button(text=t("pa_approve_btn", pal), callback_data=PaymentCB(action="approve", payment_id=payment["id"]).pack(), style="success")
        kb.button(text=t("pa_reject_btn", pal), callback_data=PaymentCB(action="reject_ask", payment_id=payment["id"]).pack(), style="danger")
        # Navigation: only show "Next pending" when there's actually a next
        # one. The callback carries the index of the NEXT slot to render so
        # the handler can re-fetch the live queue (in case some got approved
        # in the meantime) and jump to that slot.
        if index + 1 < len(payments):
            kb.button(style="primary", text=t("pa_next_btn", pal, i=index + 2, n=len(payments)),
                      callback_data=PaymentCB(action="next_pending", payment_id=index + 1).pack())
        kb.button(style="primary", text=t("pa_full_history_btn", pal),
                  callback_data=AdminCB(action="payment_history").pack())
        # ADMIN-MENU-REWORK: back is role-aware — full admins go back to the
        # Payments submenu (since they entered from there), payment admins go
        # back to their main payment-admin panel.
        back_action = "payments_menu" if await _is_full_admin(callback.from_user.id) else "main"
        kb.button(text=t("pa_admin_back_btn", pal), callback_data=AdminCB(action=back_action).pack(), style="danger")
        kb.adjust(2, 1, 2)

        file_id = payment.get("receipt_file_id")
        rtype = receipt_kind
        user_receipt_note = (payment.get("receipt_text") or "").strip()
        await show_view(callback.message, text=text, reply_markup=kb.as_markup())
        # Send the receipt photo as a SEPARATE follow-up message so the admin
        # sees both the detail card and the receipt image side by side — and
        # includes the user's own caption (the "combination of photo + caption"
        # the admin needs to verify the payment).
        if file_id:
            cap_lines = [t("pa_receipt_caption", pal, id=payment['id'], name=uname)]
            if user_receipt_note:
                cap_lines.append("")
                cap_lines.append(escape_html(user_receipt_note[:900]))
            photo_caption = "\n".join(cap_lines)[:1024]
            try:
                if rtype == "document":
                    await bot.send_document(callback.from_user.id, file_id, caption=photo_caption)
                else:
                    await bot.send_photo(callback.from_user.id, file_id, caption=photo_caption)
            except Exception as e:
                logger.warning("pending-view receipt media send failed: %s", e, exc_info=True)

    @router.callback_query(PaymentCB.filter(F.action == "noop"))
    async def cb_payment_noop(callback: CallbackQuery):
        """No-op handler for the disabled "✅ Approved"/"❌ Rejected" button
        that replaces the action keyboard on cross-admin payment notifications
        after one admin has already processed the receipt.  The button is
        informational only — tapping it just closes the loading spinner."""
        await callback.answer()

    @router.callback_query(PaymentCB.filter(F.action == "next_pending"))
    async def cb_next_pending(callback: CallbackQuery, callback_data: PaymentCB):
        # Re-fetch the live queue — some items may have been approved/rejected
        # since the user first entered Pending Payments. Jump to the requested
        # index, clamped to the new queue length.
        pal = await _pa_lang(callback.from_user.id)
        payments = await db.get_pending_payments()
        if not payments:
            # ADMIN-MENU-REWORK: full admin → Payments submenu, payment admin → their panel.
            menu = kb_payments_menu() if await _is_full_admin(callback.from_user.id) else kb_payment_admin_menu(pal)
            await show_view(callback.message, text=t("pa_no_more_pending", pal), reply_markup=menu)
            await callback.answer()
            return
        index = max(0, min(callback_data.payment_id, len(payments) - 1))
        await _render_pending_view(callback, payments, index, pal)
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "payment_history"))
    async def cb_payment_history(callback: CallbackQuery):
        """PAY-HISTORY-REWORK: receipt history.

        * Payment admins are REDIRECTED to ``cb_my_history`` (their own
          approvals only) — they must not see other admins' or un-reviewed
          receipts.  This keeps the menu button simple ("Payment History")
          while enforcing the per-admin isolation at the handler level.
        * Full admins see ALL receipts (any status, any approver) as a
          grid_table, plus a "👥 By Admin" button that opens the per-admin
          picker so they can review each payment admin's approvals in turn.

        The view is rendered via ``_render_history_table`` (shared with the
        per-admin view) so the column layout stays consistent.
        PA-LANG: localised via _pa_lang.
        """
        pal = await _pa_lang(callback.from_user.id)
        # Payment admins: force-redirect to their own approvals.
        if not await _is_full_admin(callback.from_user.id):
            await cb_my_history(callback)
            return
        # Full admin: show ALL receipts.
        payments = await db.get_recent_payments(limit=30)
        is_full = True
        if not payments:
            # ADMIN-MENU-REWORK: full admin empty-state → Payments submenu
            # (not the top-level admin panel) so the admin stays in context.
            menu = kb_payments_menu() if is_full else kb_payment_admin_menu(pal)
            await show_view(callback.message, text=t("pa_no_payments", pal), reply_markup=menu)
            await callback.answer()
            return
        await _render_history_table(callback, payments, pal,
                                    title=t("pa_history_all_title", pal),
                                    is_full=is_full)
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "my_history"))
    async def cb_my_history(callback: CallbackQuery):
        """PAY-HISTORY-REWORK: payment admin's OWN approval history.

        Shows only receipts THIS admin reviewed (approved or rejected).
        Full admins tapping this also see their own approvals — useful for
        the main admin to audit their own actions too.
        PA-LANG: localised via _pa_lang.
        """
        pal = await _pa_lang(callback.from_user.id)
        payments = await db.get_payments_by_admin(callback.from_user.id, limit=30)
        is_full = await _is_full_admin(callback.from_user.id)
        if not payments:
            menu = kb_payments_menu() if is_full else kb_payment_admin_menu(pal)
            await show_view(callback.message, text=t("pa_no_own_approvals", pal), reply_markup=menu)
            await callback.answer()
            return
        await _render_history_table(callback, payments, pal,
                                    title=t("pa_history_my_title", pal),
                                    is_full=is_full)
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "admin_payments"))
    async def cb_admin_payments_picker(callback: CallbackQuery):
        """PAY-HISTORY-REWORK: full-admin-only picker listing every payment
        admin with their approval/rejection counts. Tapping one opens
        ``cb_admin_payments_view`` for that admin's approvals.

        Full-admin-only: payment admins never see this — the callback is not
        in AdminGuard._PAYMENT_ALLOWED_PREFIXES, and even if reached, the
        _is_full_admin check below bounces them back to the menu.
        """
        pal = await _pa_lang(callback.from_user.id)
        if not await _is_full_admin(callback.from_user.id):
            # Payment admin shouldn't be here — bounce to menu.
            await show_view(callback.message,
                text=f"{t('pa_menu_title', pal)}\n\n{t('pa_menu_desc', pal)}",
                reply_markup=kb_payment_admin_menu(pal))
            await callback.answer()
            return
        pa_ids = await get_payment_admin_ids(db)
        # Include full admins (ADMIN_IDS) too — they may have approved
        # payments and the user wants to be able to audit EVERY approver,
        # not just payment-only admins.
        all_admin_ids = set(ADMIN_IDS) | pa_ids
        if not all_admin_ids:
            # ADMIN-MENU-REWORK: empty-state → Payments submenu.
            await show_view(callback.message, text=t("pa_history_admins_none", pal),
                            reply_markup=kb_payments_menu())
            await callback.answer()
            return
        rows_data = await db.get_payment_admins_with_counts(all_admin_ids)
        # Filter out admins with zero activity (cleaner picker).
        active = [r for r in rows_data if r["total"] > 0]
        if not active:
            await show_view(callback.message, text=t("pa_history_admins_none", pal),
                            reply_markup=kb_payments_menu())
            await callback.answer()
            return
        # Build grid_table: Admin • Approved • Rejected • Total
        table_rows = []
        for r in active:
            admin_row = await db.get_user(r["tg_id"])
            label = _admin_display(admin_row) if admin_row else f"#{r['tg_id']}"
            role_tag = "🛡" if r["tg_id"] in ADMIN_IDS else "💰"
            table_rows.append((f"{role_tag} {label}", r["approved"], r["rejected"], r["total"]))
        rich = rich_tables.rich_message(
            rich_tables.heading(t("pa_history_admins_title", pal)),
            rich_tables.grid_table(
                t("pa_history_admins_header", pal).split(" • "),
                table_rows,
                aligns=["left", "center", "center", "center"],
            ),
            rich_tables.paragraph(t("pa_history_admins_pick", pal)),
        )
        kb = InlineKeyboardBuilder()
        for r in active:
            admin_row = await db.get_user(r["tg_id"])
            label = _admin_display(admin_row) if admin_row else f"#{r['tg_id']}"
            role_tag = "🛡" if r["tg_id"] in ADMIN_IDS else "💰"
            kb.button(style="primary",
                text=f"{role_tag} {label[:20]} — ✅{r['approved']} ❌{r['rejected']}",
                callback_data=AdminCB(action="admin_payments_view", data=str(r["tg_id"])).pack())
        # BY-ADMIN-MOVE: "By Admin" now lives inside Pay History, so this
        # picker is only ever reached from there — back returns to the Pay
        # History list (not the Payments submenu).  The old explicit "Pay
        # History" button is removed since back now goes there.
        kb.button(style="danger", text=t("pa_admin_back_btn", pal),
                  callback_data=AdminCB(action="payment_history").pack())
        kb.adjust(*[1 for _ in active], 1)
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "admin_payments_view"))
    async def cb_admin_payments_view(callback: CallbackQuery, callback_data: AdminCB):
        """PAY-HISTORY-REWORK: full-admin-only view of a specific admin's
        approvals.  Callback data carries the target admin's tg_id."""
        pal = await _pa_lang(callback.from_user.id)
        if not await _is_full_admin(callback.from_user.id):
            await show_view(callback.message,
                text=f"{t('pa_menu_title', pal)}\n\n{t('pa_menu_desc', pal)}",
                reply_markup=kb_payment_admin_menu(pal))
            await callback.answer()
            return
        target_id = int(callback_data.data)
        target_row = await db.get_user(target_id)
        target_label = _admin_display(target_row) if target_row else f"#{target_id}"
        payments = await db.get_payments_by_admin(target_id, limit=30)
        if not payments:
            # BY-ADMIN-MOVE: empty-state → back to the By Admin picker (the
            # view this was reached from), so the admin can pick another
            # admin rather than landing on the Payments submenu.
            kb_empty = InlineKeyboardBuilder()
            kb_empty.button(style="danger", text=t("pa_admin_back_btn", pal),
                            callback_data=AdminCB(action="admin_payments").pack())
            kb_empty.adjust(1)
            await show_view(callback.message,
                text=t("pa_history_admin_none", pal),
                reply_markup=kb_empty.as_markup())
            await callback.answer()
            return
        await _render_history_table(callback, payments, pal,
                                    title=t("pa_history_admin_title", pal, admin=target_label),
                                    is_full=True,
                                    back_to=("admin_payments", ""))
        await callback.answer()

    async def _render_history_table(callback: CallbackQuery, payments: list,
                                    pal: str, title: str, is_full: bool,
                                    back_to: Optional[tuple] = None):
        """PAY-HISTORY-REWORK: shared renderer for the payment-history grid_table.

        Renders a 5-column table (ID • User • Amount • Status • Approver) and
        a row of per-payment buttons (so the admin can still open the full
        detail card + receipt photo).  ``back_to`` is an optional
        ``(action, data)`` tuple for the back button — defaults to the
        payment-history list for full admins or the payment-admin menu for
        payment admins.
        """
        # Cache admin lookups so we don't N+1 the same admin row when several
        # payments were approved by the same person.
        admin_cache: dict = {}
        _status_label = {
            "approved": t("pa_status_approved", pal),
            "rejected": t("pa_status_rejected", pal),
            "pending": t("pa_status_pending", pal),
        }
        table_rows = []
        for p in payments:
            user = await db.get_user(p["user_tg_id"])
            uname_raw = (user or {}).get("first_name") or (user or {}).get("username") or str(p["user_tg_id"])
            uname = uname_raw[:16]
            amt_en = fmt_num(p['unique_amount'], 'en')
            status = p.get('status', 'pending')
            status_emoji = {"approved": "✅", "rejected": "❌", "pending": "⏳"}.get(status, "•")
            status_word = _status_label.get(status, status)
            # Approver label: prefer denormalized column, fall back to cache.
            admin_label = p.get("admin_username") or ""
            admin_id = p.get("admin_id")
            if not admin_label and admin_id:
                if admin_id not in admin_cache:
                    admin_cache[admin_id] = await db.get_user(admin_id)
                admin_label = _admin_display(admin_cache[admin_id]) if admin_cache[admin_id] else ""
            approver = admin_label if admin_label and status != "pending" else "—"
            table_rows.append((f"#{p['id']}", uname[:14], amt_en, f"{status_emoji} {status_word}", approver[:18]))
        rich = rich_tables.rich_message(
            rich_tables.heading(title),
            rich_tables.grid_table(
                ["ID", "User", "Amount", "Status", "Approver"],
                table_rows,
                aligns=["center", "left", "right", "center", "left"],
            ),
        )
        kb = InlineKeyboardBuilder()
        for p in payments:
            user = await db.get_user(p["user_tg_id"])
            uname_raw = (user or {}).get("first_name") or (user or {}).get("username") or str(p["user_tg_id"])
            amt_en = fmt_num(p['unique_amount'], 'en')
            status = p.get('status', 'pending')
            status_emoji = {"approved": "✅", "rejected": "❌", "pending": "⏳"}.get(status, "•")
            kb.button(style="primary",
                text=f"#{p['id']} {uname_raw[:12]} — {amt_en}T {status_emoji}",
                callback_data=PaymentCB(action="view", payment_id=p["id"]).pack())
        # Bottom nav: Pending, [By Admin if full], Back.
        kb.button(style="primary", text=t("pa_pending_btn", pal),
                  callback_data=AdminCB(action="pending_payments").pack())
        if is_full:
            kb.button(style="primary", text=t("pa_admins_history_btn", pal),
                      callback_data=AdminCB(action="admin_payments").pack())
        if back_to:
            kb.button(style="danger", text=t("pa_admin_back_btn", pal),
                      callback_data=AdminCB(action=back_to[0], data=back_to[1]).pack())
        else:
            # ADMIN-MENU-REWORK: default back is role-aware — full admins go
            # to the Payments submenu, payment admins go to their panel.
            back_action = "payments_menu" if is_full else "main"
            kb.button(style="danger", text=t("pa_admin_back_btn", pal),
                      callback_data=AdminCB(action=back_action).pack())
        # 1 button per row for payment rows, then the nav row(s):
        # full admin → 2 (Pending + By Admin) then 1 (Back);
        # payment admin → 1 (Pending) then 1 (Back).
        kb.adjust(*[1 for _ in payments], 2 if is_full else 1, 1)
        await show_view(callback.message, rich=rich, reply_markup=kb.as_markup())


    @router.callback_query(PaymentCB.filter(F.action == "view"))
    async def cb_payment_view(callback: CallbackQuery, callback_data: PaymentCB):
        payment = await db.get_payment(callback_data.payment_id)
        if not payment:
            await callback.answer(t("not_found", await _pa_lang(callback.from_user.id)), show_alert=True)
            return
        # PA-LANG: localise the entire detail card.
        pal = await _pa_lang(callback.from_user.id)
        # PAY-HISTORY-REWORK: payment admins may only view (a) pending
        # payments (so they can approve/reject) and (b) payments they
        # themselves reviewed.  They must NOT see other admins' reviewed
        # receipts — that data is full-admin-only.  Full admins see everything.
        if not await _is_full_admin(callback.from_user.id):
            status = payment.get("status", "pending")
            admin_id = payment.get("admin_id")
            if status != "pending" and admin_id != callback.from_user.id:
                await callback.answer(
                    t("admin_only", "en") if pal == "en" else "⛔ دسترسی ندارید.",
                    show_alert=True,
                )
                return
        user = await db.get_user(payment["user_tg_id"])
        uname = escape_html((user or {}).get("first_name") or (user or {}).get("username") or str(payment["user_tg_id"]))
        text = t("pa_payment_title", pal, id=payment['id']) + "\n\n"
        text += t("pa_user", pal, name=uname, id=payment['user_tg_id']) + "\n"
        text += t("pa_base_amount", pal, amt=int(payment['amount'])) + "\n"
        text += t("pa_unique_amount", pal, amt=int(payment['unique_amount'])) + "\n"
        text += t("pa_card", pal, num=ltr(escape_html(payment.get('card_number') or '-'))) + "\n"
        text += t("pa_created", pal, date=fmt_iso(payment.get('created_at'), '%Y-%m-%d %H:%M:%S') or '-') + "\n"
        if payment.get("receipt_text"):
            text += t("pa_receipt_text", pal, icon="📝", text=escape_html(payment['receipt_text'][:300])) + "\n"
        # RECEIPT-HISTORY: show which admin acted on this payment. We prefer
        # the denormalized admin_username column (set at action time, survives
        # even if the admin later blocks the bot). If missing (legacy rows
        # approved before the column existed), fall back to looking the admin
        # up in `users` by tg_id.
        status = payment.get('status', 'pending')
        admin_id = payment.get('admin_id')
        admin_label = payment.get('admin_username') or ""
        if not admin_label and admin_id:
            admin_row = await db.get_user(admin_id)
            admin_label = _admin_display(admin_row) if admin_row else ""
        if status != "pending" and admin_label:
            reviewed_at = fmt_iso(payment.get('reviewed_at'), '%Y-%m-%d %H:%M:%S') or '-'
            action_word = t("pa_action_approved", pal) if status == "approved" else t("pa_action_rejected", pal)
            text += t("pa_reviewed_by", pal, action=action_word, admin=escape_html(admin_label), id=admin_id) + "\n"
            text += t("pa_reviewed_at", pal, date=reviewed_at) + "\n"
            if status == "rejected" and payment.get("admin_note"):
                text += t("pa_reject_reason", pal, reason=escape_html(payment['admin_note'])) + "\n"
        status_word = {"approved": t("pa_status_approved", pal),
                       "rejected": t("pa_status_rejected", pal),
                       "pending": t("pa_status_pending", pal)}.get(status, status)
        text += "\n" + t("pa_status", pal, status=status_word)

        kb = InlineKeyboardBuilder()
        # Only show approve/reject for still-pending payments — once acted
        # upon, those buttons would just toast "already processed".
        if status == "pending":
            kb.button(text=t("pa_approve_btn", pal), callback_data=PaymentCB(action="approve", payment_id=payment["id"]).pack(), style="success")
            kb.button(text=t("pa_reject_btn", pal), callback_data=PaymentCB(action="reject_ask", payment_id=payment["id"]).pack(), style="danger")
        kb.button(style="primary", text=t("pa_history_btn2", pal), callback_data=AdminCB(action="payment_history").pack())
        kb.button(style="danger", text=t("pa_pending_back_btn", pal), callback_data=AdminCB(action="pending_payments").pack())
        # adjust: when pending → 2 (Approve/Reject) + 2 (History/Pending);
        # when not pending → 2 (History/Pending) only.
        if status == "pending":
            kb.adjust(2, 2)
        else:
            kb.adjust(2)

        # L9 — preserve admin context. Previously this handler DELETED the
        # pending-payments list message and sent the receipt photo with the
        # payment details as the caption. The admin then lost the list and had
        # no easy way back to the next payment. Now we:
        #   1. EDIT the existing list message in place to show the payment
        #      details + approve/reject buttons inline (the list itself is
        #      just one tap away via the 🔙 Pending button).
        #   2. Send the receipt photo as a SEPARATE follow-up message so the
        #      admin sees both the detail card and the receipt image side by
        #      side without losing context.
        #
        # RECEIPT-INLINE-CAPTION: the receipt photo's caption now carries the
        # user's own receipt_text (the note the user attached when sending
        # the screenshot). This is the "combination of photo + caption" the
        # admin needs to verify — previously the caption was a generic
        # "📎 Receipt for payment #X" and the user's note was only visible in
        # the detail card above (which the admin might miss when scrolling
        # down to look at the photo).
        file_id = payment.get("receipt_file_id")
        rtype = (payment.get("receipt_type") or "").lower()
        user_receipt_note = (payment.get("receipt_text") or "").strip()
        await show_view(callback.message, text=text, reply_markup=kb.as_markup())
        if file_id:
            # Caption = identifier line + the user's own caption (if any).
            # Cap at 900 chars so we stay under Telegram's 1024-char caption
            # limit for photos.
            cap_lines = [t("pa_receipt_caption", pal, id=payment['id'], name=uname)]
            if user_receipt_note:
                cap_lines.append("")
                cap_lines.append(escape_html(user_receipt_note[:900]))
            photo_caption = "\n".join(cap_lines)[:1024]
            try:
                if rtype == "document":
                    await bot.send_document(
                        callback.from_user.id, file_id,
                        caption=photo_caption,
                    )
                else:
                    await bot.send_photo(
                        callback.from_user.id, file_id,
                        caption=photo_caption,
                    )
            except Exception as e:
                logger.warning("payment-view receipt media send failed: %s", e, exc_info=True)
        await callback.answer()

    @router.callback_query(PaymentCB.filter(F.action == "approve"))
    async def cb_payment_approve(callback: CallbackQuery, callback_data: PaymentCB):
        payment = await db.get_payment(callback_data.payment_id)
        if not payment or payment["status"] != "pending":
            await callback.answer(t("not_pending", await _pa_lang(callback.from_user.id)), show_alert=True)
            return
        # C1 — atomic approve: only one admin can perform the pending→approved
        # transition. If two admins click simultaneously, the second call to
        # approve_payment returns False and we abort before double-crediting.
        # M1 — wrap approve+credit+log in a single transaction so a failure
        # mid-flow doesn't leave the payment marked approved but the user
        # uncredited (an unrecoverable state — the payment can't be re-approved).
        try:
            async with db.transaction():
                ok = await db.approve_payment(payment["id"], callback.from_user.id,
                                              admin_username=_admin_handle_from_callback(callback.from_user))
                if not ok:
                    raise RuntimeError("already_processed")
                await db.update_user_balance(payment["user_tg_id"], payment["amount"], add=True)
                await db.add_transaction(
                    user_tg_id=payment["user_tg_id"], amount=payment["amount"], type_="deposit",
                    description=f"Card payment #{payment['id']}", admin_id=callback.from_user.id,
                )
        except RuntimeError:
            await callback.answer(t("already_processed", await _pa_lang(callback.from_user.id)), show_alert=True)
            return
        except Exception as e:
            logger.error("payment approve transaction failed for #%s: %s", payment['id'], e, exc_info=True)
            pal_err = await _pa_lang(callback.from_user.id)
            await callback.answer(t("pa_approve_failed", pal_err, err=str(e)[:60]), show_alert=True)
            return
        # RECEIPT-CROSS-ADMIN-CLEANUP: now that this admin has approved, edit
        # (or delete) the "new payment" notification in EVERY admin's chat so
        # the other admins see "✅ Approved by …" and don't try to approve it
        # again.  Best-effort — must never block the approve flow.
        try:
            await _mark_payment_notifs_processed(
                payment, "approved",
                _admin_handle_from_callback(callback.from_user), bot,
                db=db,
            )
        except Exception as e:
            logger.warning("cross-admin notif cleanup failed for approve #%s: %s",
                           payment.get('id'), e)
        # Notify user
        user = await db.get_user(payment["user_tg_id"])
        lang = L((user or {}).get("language", DEFAULT_LANGUAGE))
        currency = await _currency()
        balance = (user or {}).get("balance", 0)
        # SHORTFALL-REQUEST: if this payment was a shortfall-for-plan, the
        # user wanted to buy a specific plan but couldn't afford it. Now
        # that their balance is topped up, send them a one-tap "Buy Now"
        # button for that plan instead of the generic "payment approved"
        # message. This is the "redirect to purchase flow" the user asked
        # for — they don't have to navigate back to the plan list.
        resume_plan_id = payment.get("resume_plan_id") if isinstance(payment, dict) else None
        if resume_plan_id:
            plan = await db.get_plan(resume_plan_id)
        else:
            plan = None
        try:
            if plan:
                plan_name = escape_html(plan["name"])
                plan_price = fmt_price(plan["price"], lang, currency)
                if lang == "fa":
                    user_msg = (
                        f"✅ پرداختت تأیید شد و موجودیت شارژ شد!\n\n"
                        f"💰 موجودی فعلی: <b>{fmt_price(balance, lang, currency)}</b>\n\n"
                        f"🎯 حالا می‌تونی پلن «<b>{plan_name}</b>» (با قیمت {plan_price}) رو که می‌خواستی بخری. "
                        f"روی دکمهٔ زیر بزن تا بری به صفحهٔ خرید:"
                    )
                else:
                    user_msg = (
                        f"✅ Your payment was approved and your balance is topped up!\n\n"
                        f"💰 Current balance: <b>{fmt_price(balance, lang, currency)}</b>\n\n"
                        f"🎯 You can now buy the \"<b>{plan_name}</b>\" plan ({plan_price}) you wanted. "
                        f"Tap the button below to go to the purchase page:"
                    )
                resume_kb = InlineKeyboardBuilder()
                resume_kb.button(style="success",
                                 text=(f"🛒 خرید {plan['name']}" if lang == "fa" else f"🛒 Buy {plan['name']}"),
                                 callback_data=BuyCB(action="start", plan_id=plan["id"], step="resume").pack())
                resume_kb.button(text=(t("back_menu", lang)),
                                 callback_data=MenuCB(action="main").pack(), style="danger")
                resume_kb.adjust(1, 1)
                await bot.send_message(
                    payment["user_tg_id"], user_msg,
                    reply_markup=resume_kb.as_markup(),
                )
            else:
                await bot.send_message(
                    payment["user_tg_id"],
                    t("payment_approved", lang, amount=fmt_price(payment["amount"], lang, currency), balance=fmt_price(balance, lang, currency)),
                )
        except TelegramBadRequest:
            pass
        except Exception as e:
            logger.warning("approve notify failed: %s", e)
        # RECEIPT-FIRST: after approving one payment, jump straight back into
        # the pending queue (which now shows the NEXT pending payment with its
        # full receipt) instead of dumping the admin back to the menu. This
        # saves a tap when there are multiple pending payments to review.
        # If the queue is now empty, get_pending_payments returns [] and
        # cb_pending_payments shows the "no pending" empty state.
        # PA-LANG: localise the admin-facing confirmation.
        pal = await _pa_lang(callback.from_user.id)
        payments = await db.get_pending_payments()
        if payments:
            await _render_pending_view(callback, payments, 0, pal)
        else:
            # M14 — show the right menu: full admins see the Payments submenu
            # (ADMIN-MENU-REWORK: they just approved a payment, so stay in the
            # Payments context), payment-only admins see the payment-admin menu.
            if await _is_full_admin(callback.from_user.id):
                _menu = kb_payments_menu()
            else:
                _menu = kb_payment_admin_menu(pal)
            await show_view(callback.message, text=
                t("pa_approved_msg", pal, id=payment['id'], amt=int(payment['amount']), uid=payment['user_tg_id']),
                reply_markup=_menu,
            )
        await callback.answer(t("pa_approved_toast", pal))

    @router.callback_query(PaymentCB.filter(F.action == "reject_ask"))
    async def cb_payment_reject_ask(callback: CallbackQuery, callback_data: PaymentCB, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_reject_reason)
        await state.update_data(payment_id=callback_data.payment_id)
        # PA-LANG: use _pa_lang so payment admins see the prompt in their
        # selected language.
        alang = await _pa_lang(callback.from_user.id)
        await show_view(callback.message, text=t("enter_reject_reason", alang), reply_markup=kb_cancel(alang), state=state)
        await callback.answer()

    @router.message(AdminStates.waiting_for_reject_reason)
    async def ms_reject_reason(message: Message, state: FSMContext):
        await del_inbound(message, state)
        reason = (message.text or "").strip()
        if reason == "-":
            reason = ""
        data = await state.get_data()
        await state.clear()
        payment = await db.get_payment(data.get("payment_id"))
        # PA-LANG: localise the admin-facing messages.
        pal = await _pa_lang(message.from_user.id)
        if not payment or payment["status"] != "pending":
            # ADMIN-MENU-REWORK: full admin → Payments submenu.
            if await _is_full_admin(message.from_user.id):
                _menu = kb_payments_menu()
            else:
                _menu = kb_payment_admin_menu(pal)
            await message.answer(t("pa_not_found_processed", pal), reply_markup=_menu)
            return
        # C1 — atomic reject: only one admin can transition pending→rejected.
        ok = await db.reject_payment(payment["id"], message.from_user.id, reason,
                                     admin_username=_admin_handle_from_callback(message.from_user))
        if not ok:
            if await _is_full_admin(message.from_user.id):
                _menu = kb_payments_menu()
            else:
                _menu = kb_payment_admin_menu(pal)
            await message.answer(t("pa_already_processed_msg", pal),
                                 reply_markup=_menu)
            return
        # RECEIPT-CROSS-ADMIN-CLEANUP: edit (or delete) the "new payment"
        # notification in every admin's chat so the other admins see
        # "❌ Rejected by …" and don't try to act on an already-processed
        # receipt.  Best-effort — must never block the reject flow.
        try:
            await _mark_payment_notifs_processed(
                payment, "rejected",
                _admin_handle_from_callback(message.from_user), bot,
                db=db,
            )
        except Exception as e:
            logger.warning("cross-admin notif cleanup failed for reject #%s: %s",
                           payment.get('id'), e)
        # Notify user
        user = await db.get_user(payment["user_tg_id"])
        lang = L((user or {}).get("language", DEFAULT_LANGUAGE))
        try:
            await bot.send_message(
                payment["user_tg_id"],
                t("payment_rejected", lang, reason=escape_html(reason) or "No reason given"),
            )
        except TelegramBadRequest:
            pass
        except Exception as e:
            logger.warning("reject notify failed: %s", e)
        # M14 — branch on admin type for the menu.
        # ADMIN-MENU-REWORK: full admin → Payments submenu.
        if await _is_full_admin(message.from_user.id):
            _menu = kb_payments_menu()
        else:
            _menu = kb_payment_admin_menu(pal)
        await message.answer(
            t("pa_rejected_msg", pal, id=payment['id']),
            reply_markup=_menu,
        )

    # ---- Force join settings handlers ----
    @router.callback_query(AdminCB.filter(F.action == "toggle_force_join"))
    async def cb_toggle_force_join(callback: CallbackQuery):
        cur = await db.get_setting_int("force_join_enabled", 0)
        await db.set_setting("force_join_enabled", "0" if cur else "1")
        await callback.answer(f"Force join {'enabled' if not cur else 'disabled'}", show_alert=True)
        # L3 — refresh via the extracted render helper (no more fake_cb).
        await _render_settings_force_join_view(callback.message)

    @router.callback_query(AdminCB.filter(F.action == "add_force_join_channel"))
    async def cb_add_force_join_channel(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_force_join_channel)
        await show_view(callback.message, text=
            "📢 <b>Add Force Join Channel</b>\n\n"
            "Enter channel username (e.g. <code>@mychannel</code>) or chat ID:\n"
            "The bot must be an admin in the channel!",
            reply_markup=kb_cancel("en"),
            state=state,
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_force_join_channel)
    async def ms_force_join_channel(message: Message, state: FSMContext):
        await del_inbound(message, state)
        input_text = (message.text or "").strip()
        chat_id = None
        username = ""
        title = ""

        if input_text.startswith("-") and input_text.lstrip("-").isdigit():
            chat_id = int(input_text)
        elif input_text.startswith("@"):
            username = input_text.lstrip("@")
        elif input_text.isdigit():
            chat_id = int(input_text)
        else:
            username = input_text

        try:
            if chat_id:
                chat = await bot.get_chat(chat_id)
                title = chat.title or ""
                username = chat.username or ""
            elif username:
                chat = await bot.get_chat(f"@{username}")
                chat_id = chat.id
                title = chat.title or ""
                username = chat.username or username
        except Exception as e:
            await message.answer(f"❌ Could not find channel: {str(e)[:80]}", reply_markup=kb_admin_menu())
            await state.clear()
            return

        channels = await db.get_setting_json("force_join_channels", [])
        # Check duplicate
        for ch in channels:
            if ch.get("chat_id") == chat_id or (username and ch.get("username") == username):
                await message.answer("❌ This channel is already in the list.", reply_markup=kb_admin_menu())
                await state.clear()
                return

        channels.append({"chat_id": chat_id, "username": username, "title": title})
        await db.set_setting("force_join_channels", json.dumps(channels))
        await state.clear()
        await message.answer(
            f"✅ <b>Channel added</b>\n• {title} (@{username}) — {chat_id}",
            reply_markup=kb_admin_menu(),
        )

    @router.callback_query(AdminCB.filter(F.action == "remove_force_join_channel"))
    async def cb_remove_force_join_channel(callback: CallbackQuery):
        channels = await db.get_setting_json("force_join_channels", [])
        if not channels:
            # L12 — admin-facing i18n (was English-only "No channels to remove").
            await callback.answer(t("not_found", await admin_lang(callback.from_user.id)), show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        for i, ch in enumerate(channels):
            kb.button(
                text=f"🗑 {ch.get('title', ch.get('username', '?'))}",
                callback_data=AdminCB(action="remove_fj_channel", data=str(i)).pack(),
                style="danger",
            )
        kb.button(text="🔙 Force Join", callback_data=SettingsCatCB(category="force_join").pack(), style="primary")
        kb.adjust(1, 1)
        await show_view(callback.message, text="🗑 <b>Select channel to remove:</b>", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "remove_fj_channel"))
    async def cb_remove_fj_channel(callback: CallbackQuery, callback_data: AdminCB):
        channels = await db.get_setting_json("force_join_channels", [])
        idx = int(callback_data.data)
        if 0 <= idx < len(channels):
            removed = channels.pop(idx)
            await db.set_setting("force_join_channels", json.dumps(channels))
            await callback.answer(f"✅ Removed {removed.get('title', '?')}", show_alert=True)
        # L3 — refresh via the extracted render helper. (This callsite used
        # ``await cb_settings_force_join(callback)`` directly, which worked but
        # also fired a second ``callback.answer()`` from inside the view
        # handler. Switching to the helper avoids the double-answer.)
        await _render_settings_force_join_view(callback.message)

    # ---- Guide settings handlers (GUIDES) — replaces old edit_help_en/fa.
    # Four editable fields: guide_usage_en, guide_usage_fa,
    # guide_connection_en, guide_connection_fa. Sending "-" clears the field
    # so the default is used again.
    async def _start_guide_edit(callback: CallbackQuery, state: FSMContext,
                                 key: str, label: str, fallback: str, lang_name: str):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_str", key=key, label=label)
        current = await db.get_setting(key, "")
        preview = current if (current and current.strip()) else fallback
        await show_view(callback.message, text=
            f"📝 <b>Edit {label}</b>\n\n"
            f"Current (or default):\n<i>{escape_html(preview[:300])}{'…' if len(preview) > 300 else ''}</i>\n\n"
            f"Send the new text, or <code>-</code> to clear (use built-in default):",
            reply_markup=kb_cancel(lang_name),
            state=state,
        )
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "edit_guide_usage_en"))
    async def cb_edit_guide_usage_en(callback: CallbackQuery, state: FSMContext):
        await _start_guide_edit(callback, state, "guide_usage_en",
                                "Usage guide (English)", DEFAULT_GUIDE_USAGE_EN, "en")

    @router.callback_query(AdminCB.filter(F.action == "edit_guide_usage_fa"))
    async def cb_edit_guide_usage_fa(callback: CallbackQuery, state: FSMContext):
        await _start_guide_edit(callback, state, "guide_usage_fa",
                                "Usage guide (Farsi)", DEFAULT_GUIDE_USAGE_FA, "fa")

    @router.callback_query(AdminCB.filter(F.action == "edit_guide_conn_en"))
    async def cb_edit_guide_conn_en(callback: CallbackQuery, state: FSMContext):
        await _start_guide_edit(callback, state, "guide_connection_en",
                                "Connection guide (English)", DEFAULT_GUIDE_CONNECTION_EN, "en")

    @router.callback_query(AdminCB.filter(F.action == "edit_guide_conn_fa"))
    async def cb_edit_guide_conn_fa(callback: CallbackQuery, state: FSMContext):
        await _start_guide_edit(callback, state, "guide_connection_fa",
                                "Connection guide (Farsi)", DEFAULT_GUIDE_CONNECTION_FA, "fa")

    # ---- Topup packages settings handler ----
    @router.callback_query(AdminCB.filter(F.action == "set_topup_packages"))
    async def cb_set_topup_packages(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_str", key="topup_packages", label="Topup packages")
        cur = await db.get_setting_json("topup_packages", [5, 10, 20, 50])
        await show_view(callback.message, text=
            f"📦 Enter topup packages as comma-separated GB values:\n\nCurrent: {cur}",
            reply_markup=kb_cancel("en"),
            state=state,
        )
        await callback.answer()

    # ---- catch-all for section-header "noop" buttons & stray callbacks ----
    # L5: NoopCB packs to a string starting with the "noop" prefix, so the
    # original ``F.data.startswith("noop")`` filter (kept for backward
    # compatibility with any in-flight legacy callbacks) still matches. The
    # typed NoopCB.filter() form would also work but the startswith filter is
    # more permissive — it tolerates both the old literal "noop_0" and the
    # new packed form ("noop:").
    @router.callback_query(F.data.startswith("noop"))
    async def cb_noop(callback: CallbackQuery):
        await callback.answer()

    return router


# ============================================================================
# SECTION 12: BACKGROUND TASKS
# ============================================================================

async def task_expiry_checker(bot: Bot, db: Database, api: PanelAPI):
    """Hourly: send expiry reminders, auto-disable expired accounts."""
    logger.info("Background task started: expiry_checker")
    while True:
        try:
            # M15 — batch-fetch all user languages ONCE per cycle instead of
            # N+1 (one get_user per expiring account). With 1000 expiring
            # accounts this cut from 1000 SELECTs to 1.
            all_active = await db.get_all_active_accounts()
            expiring_tg_ids = {acc["user_tg_id"] for acc in all_active}
            for days in EXPIRY_REMINDER_DAYS:
                for acc in await db.get_expiring_accounts(days):
                    if await db.has_expiry_reminder(acc["email"], days):
                        continue
                    user = await db.get_user(acc["user_tg_id"])
                    if not user:
                        continue
                    lang = L(user.get("language", DEFAULT_LANGUAGE))
                    try:
                        kb = InlineKeyboardBuilder()
                        # Trial accounts can't be renewed — only offer Buy.
                        if not acc.get("is_trial"):
                            kb.button(text=t("renew", lang),
                                      callback_data=AccountCB(action="renew", email=acc["email"]).pack(),
                                      style="success")
                        kb.button(style="success", text=t("buy", lang), callback_data=MenuCB(action="buy").pack())
                        kb.button(style="primary", text=t("my_accounts", lang),
                                  callback_data=MenuCB(action="my_accounts").pack())
                        kb.adjust(2 if not acc.get("is_trial") else 1, 1)
                        await bot.send_message(
                            acc["user_tg_id"],
                            f"⏰ <b>{t('expiry_reminder_subject', lang)}</b>\n\n"
                            f"📱 <code>{escape_html(acc['email'])}</code>\n"
                            f"📅 {fmt_remaining(acc['expiry_time'], lang)}\n"
                            f"🗓 {fmt_ts(acc['expiry_time'], lang)}",
                            reply_markup=kb.as_markup(),
                        )
                        await db.add_expiry_reminder(acc["email"], days)
                    except Exception as e:
                        logger.error("expiry reminder failed: %s", e)

            # Auto-disable fully expired accounts
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            # M15 — reuse the already-fetched all_active list + batch languages.
            expired_tg_ids = {acc["user_tg_id"] for acc in all_active
                              if acc["expiry_time"] > 0 and acc["expiry_time"] < now_ms}
            if expired_tg_ids:
                exp_langs = await db.get_user_languages_by_ids(list(expired_tg_ids))
            for acc in all_active:
                if acc["expiry_time"] > 0 and acc["expiry_time"] < now_ms:
                    server = await db.get_server(acc["server_id"])
                    if server:
                        await api.disable_client(server["panel_url"], server["api_token"], acc["email"])
                    await db.update_account(acc["email"], is_active=False)
                    lang = L(exp_langs.get(acc["user_tg_id"], DEFAULT_LANGUAGE))
                    logger.info("Auto-disabled expired: %s", acc["email"])
                    try:
                        kb = InlineKeyboardBuilder()
                        # Trial accounts can't be renewed — only offer Buy.
                        if not acc.get("is_trial"):
                            kb.button(text=t("renew", lang),
                                      callback_data=AccountCB(action="renew", email=acc["email"]).pack(), style="success")
                        kb.button(style="success", text=t("buy", lang), callback_data=MenuCB(action="buy").pack())
                        kb.adjust(2 if not acc.get("is_trial") else 1)
                        await bot.send_message(
                            acc["user_tg_id"],
                            f"🔴 <b>{t('account_expired_subject', lang)}</b>\n\n"
                            f"📱 <code>{escape_html(acc['email'])}</code>\n"
                            f"🗓 {fmt_ts(acc['expiry_time'], lang)}",
                            reply_markup=kb.as_markup(),
                        )
                    except TelegramForbiddenError:
                        pass  # user blocked the bot — expected
                    except TelegramBadRequest as e:
                        msg = str(e).lower()
                        if "chat not found" not in msg and "blocked" not in msg:
                            logger.warning("expiry auto-disable notify failed: %s", e)
                    except Exception as e:
                        logger.warning("expiry auto-disable notify failed: %s", e, exc_info=True)
        except Exception as e:
            logger.error("expiry checker error: %s", e)
        await asyncio.sleep(EXPIRY_CHECK_INTERVAL_SECONDS)


async def task_traffic_alerts(bot: Bot, db: Database, api: PanelAPI):
    """Every 10 min: traffic-threshold alerts + auto-disable depleted.

    Servers and the accounts on them are processed CONCURRENTLY via
    asyncio.gather; every panel API call is bounded by PANEL_API_SEMAPHORE
    (M15) so a single 1-core/1GB server cannot be flooded with 100s of
    simultaneous HTTP requests.
    """
    logger.info("Background task started: traffic_alerts")

    async def _check_account(acc: dict, server: dict, lang: str):
        """Per-account traffic check + alert + auto-disable. Bounded by the
        semaphore so panel API calls (get_client_traffic, disable_client)
        stay under PANEL_API_SEMAPHORE concurrency.
        M16 — lang is passed in (batch-fetched once per cycle) instead of
        calling db.get_user per account (N+1 query fix)."""
        async with PANEL_API_SEMAPHORE:
            traffic = await api.get_client_traffic(server["panel_url"], server["api_token"], acc["email"])
            if not traffic:
                return
            total = traffic.get("total", 0)
            used = traffic.get("up", 0) + traffic.get("down", 0)
            if total <= 0:
                return
            pct = (used / total) * 100
            for threshold in (TRAFFIC_ALERT_THRESHOLD_1, TRAFFIC_ALERT_THRESHOLD_2):
                if pct >= threshold and not await db.has_traffic_alert(acc["email"], threshold):
                    try:
                        emoji = "⚠️" if threshold < 90 else "🚨"
                        kb = InlineKeyboardBuilder()
                        # TOPUP-TOGGLE: only fetch topup_enabled when we're
                        # actually about to send an alert (rare event), so we
                        # don't add a query to the hot path of every check.
                        topup_on = bool(await db.get_setting_int("topup_enabled", 1))
                        # Trial accounts can't be topped up or renewed.
                        if not acc.get("is_trial"):
                            if topup_on:
                                kb.button(text=t("topup_traffic", lang),
                                          callback_data=AccountCB(action="topup", email=acc["email"]).pack(), style="primary")
                            kb.button(text=t("renew", lang),
                                      callback_data=AccountCB(action="renew", email=acc["email"]).pack(), style="success")
                            # 2 buttons when both shown, 1 when top-up hidden.
                            kb.adjust(2 if topup_on else 1)
                        else:
                            kb.button(style="success", text=t("buy", lang), callback_data=MenuCB(action="buy").pack())
                            kb.adjust(1)
                        await bot.send_message(
                            acc["user_tg_id"],
                            f"{emoji} <b>Traffic {threshold}%</b>\n"
                            f"📱 <code>{escape_html(acc['email'])}</code>\n"
                            f"📊 {fmt_bytes(used, lang)} / {fmt_bytes(total, lang)}\n"
                            f"✅ {fmt_bytes(total-used, lang)}",
                            reply_markup=kb.as_markup(),
                        )
                        await db.add_traffic_alert(acc["email"], threshold)
                    except Exception as e:
                        logger.error("traffic alert send failed: %s", e)
            if pct >= 100 and acc["is_active"]:
                await api.disable_client(server["panel_url"], server["api_token"], acc["email"])
                await db.update_account(acc["email"], is_active=False)
                logger.info("Auto-disabled depleted: %s", acc["email"])
                try:
                    kb = InlineKeyboardBuilder()
                    # BUG-8 FIX: trial accounts can't be renewed (cb_account_renew
                    # returns trial_no_renew). Showing a "Renew" button to a trial
                    # user whose traffic just hit 100% is a dead end — they tap it
                    # and get a "can't renew trial" alert. Mirror the 80/95% alert
                    # (line 10550-10557): show "Buy" for trial accounts, "Renew"
                    # for regular accounts.
                    if not acc.get("is_trial"):
                        kb.button(text=t("renew", lang),
                                  callback_data=AccountCB(action="renew", email=acc["email"]).pack(), style="success")
                    else:
                        kb.button(style="success", text=t("buy", lang),
                                  callback_data=MenuCB(action="buy").pack())
                    kb.adjust(1)
                    await bot.send_message(
                        acc["user_tg_id"],
                        f"🔴 <b>{t('traffic_depleted_subject', lang)}</b>\n"
                        f"📱 <code>{escape_html(acc['email'])}</code>",
                        reply_markup=kb.as_markup(),
                    )
                except TelegramForbiddenError:
                    pass  # user blocked the bot — expected
                except TelegramBadRequest as e:
                    msg = str(e).lower()
                    if "chat not found" not in msg and "blocked" not in msg:
                        logger.warning("traffic-depleted notify failed: %s", e)
                except Exception as e:
                    logger.warning("traffic-depleted notify failed: %s", e, exc_info=True)

    async def _process_server(srv: dict, accounts: List[dict], langs: Dict[int, str]):
        """Fan out per-account checks for one server. Per-account coroutines
        acquire PANEL_API_SEMAPHORE individually so a busy server with many
        accounts cannot starve other servers."""
        if not accounts:
            return
        results = await asyncio.gather(
            *(_check_account(acc, srv, L(langs.get(acc["user_tg_id"], DEFAULT_LANGUAGE))) for acc in accounts),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.error("traffic alerts account error on %s: %s",
                             srv.get("alias", srv.get("id")), r)

    while True:
        try:
            servers = await db.get_servers(active_only=True)
            accounts = await db.get_all_active_accounts()
            # M16 — batch-fetch all user languages in ONE query instead of
            # N+1 (one get_user per account per cycle).
            tg_ids = list({acc["user_tg_id"] for acc in accounts})
            langs = await db.get_user_languages_by_ids(tg_ids) if tg_ids else {}
            # Group accounts by server_id so each _process_server coroutine
            # handles only its own accounts (avoids N redundant get_server calls).
            by_srv: Dict[int, List[dict]] = {}
            for acc in accounts:
                by_srv.setdefault(acc["server_id"], []).append(acc)
            server_results = await asyncio.gather(
                *(_process_server(srv, by_srv.get(srv["id"], []), langs) for srv in servers),
                return_exceptions=True,
            )
            for r in server_results:
                if isinstance(r, Exception):
                    logger.error("traffic alerts server error: %s", r)
        except Exception as e:
            logger.error("traffic alerts error: %s", e)
        await asyncio.sleep(TRAFFIC_CHECK_INTERVAL_SECONDS)


async def task_server_health(bot: Bot, db: Database, api: PanelAPI):
    """Every 5 min: probe all servers, notify admins of state changes.

    All servers are probed CONCURRENTLY via asyncio.gather; each probe is
    bounded by PANEL_API_SEMAPHORE (M15). One slow/down server cannot block
    the rest because return_exceptions=True is used and any Exception is
    logged but does not abort the loop.
    """
    logger.info("Background task started: server_health")

    async def _check_server(srv: dict):
        async with PANEL_API_SEMAPHORE:
            ok, msg = await api.test_panel_connection(srv["panel_url"], srv["api_token"])
            was_healthy = bool(srv["is_healthy"])
            await db.update_server_health(srv["id"], ok, "" if ok else msg)
            if was_healthy and not ok:
                logger.warning("Server down: %s — %s", srv["alias"], msg)
                for admin_id in ADMIN_IDS:
                    await safe_notify(
                        bot.send_message(
                            admin_id,
                            f"🔴 <b>Server Down</b>\n🖥 {escape_html(srv['alias'])}\n"
                            f"🔗 <code>{escape_html(srv['panel_url'])}</code>\n❌ {escape_html(msg)}",
                        ),
                        context="server-down admin notify",
                    )
            elif not was_healthy and ok:
                logger.info("Server recovered: %s", srv["alias"])
                for admin_id in ADMIN_IDS:
                    await safe_notify(
                        bot.send_message(
                            admin_id,
                            f"🟢 <b>Server Recovered</b>\n🖥 {escape_html(srv['alias'])}",
                        ),
                        context="server-recovered admin notify",
                    )

    while True:
        try:
            servers = await db.get_servers(active_only=True)
            results = await asyncio.gather(
                *(_check_server(srv) for srv in servers),
                return_exceptions=True,
            )
            for srv, r in zip(servers, results):
                if isinstance(r, Exception):
                    logger.error("server health check failed for %s: %s",
                                 srv.get("alias", srv.get("id")), r)
        except Exception as e:
            logger.error("server health error: %s", e)
        await asyncio.sleep(SERVER_HEALTH_INTERVAL_SECONDS)


async def task_sync_client_counts(db: Database, api: PanelAPI):
    """Every 30 min: refresh cached client counts per server (for load balancing).

    All servers are synced CONCURRENTLY via asyncio.gather; each panel call is
    bounded by PANEL_API_SEMAPHORE (M15).
    """
    logger.info("Background task started: sync_client_counts")

    async def _sync_server(srv: dict):
        async with PANEL_API_SEMAPHORE:
            data = await api.get_clients_paged(srv["panel_url"], srv["api_token"], page=1, page_size=1)
            total = data.get("total", 0) if isinstance(data, dict) else 0
            await db.update_server(srv["id"], total_clients=total)

    while True:
        try:
            servers = await db.get_servers(active_only=True)
            results = await asyncio.gather(
                *(_sync_server(srv) for srv in servers),
                return_exceptions=True,
            )
            for srv, r in zip(servers, results):
                if isinstance(r, Exception):
                    logger.error("sync client counts failed for %s: %s",
                                 srv.get("alias", srv.get("id")), r)
        except Exception as e:
            logger.error("sync client counts error: %s", e)
        await asyncio.sleep(SYNC_COUNTS_INTERVAL_SECONDS)


async def task_data_retention(bot: Bot, db: Database):
    """Every 24h: purge old ticket_messages / payments / broadcasts (M9).

    Policy lives in ``Database.purge_old_data`` so it can be unit-tested and
    re-used by an admin "cleanup" button later.  This task just calls it on a
    fixed 86400-second cadence and logs the per-table purge counts.
    """
    logger.info("Background task started: data_retention")
    while True:
        try:
            counts = await db.purge_old_data()
            if any(counts.values()):
                logger.info(
                    "data_retention purged: ticket_messages=%d payments=%d broadcasts=%d",
                    counts["ticket_messages"], counts["payments"], counts["broadcasts"],
                )
            else:
                logger.info("data_retention: nothing to purge this cycle")
        except Exception as e:
            logger.error("data retention error: %s", e)
        await asyncio.sleep(DATA_RETENTION_INTERVAL_SECONDS)


async def task_db_backup(bot: Bot, db: Database):
    """Auto DB backup — cadence configurable from the bot (BACKUP-CFG).

    Settings (in the settings table, editable from Settings → Backup):
      backup_enabled          — 0/1, master toggle
      backup_interval_minutes — how often to back up (default 1440 = 24h)
      backup_keep             — on-disk retention count (default 3)

    Why the SQLite Online Backup API instead of VACUUM INTO or a file copy:
      ``aiosqlite.Connection.backup()`` wraps sqlite3's online backup API,
      which copies a transactionally-consistent snapshot into a fresh file
      without taking a write lock on the source DB and without failing when
      the shared connection has an open transaction. (``VACUUM INTO`` issued
      on the shared bot connection raises "cannot VACUUM - SQL statements in
      progress" whenever another handler has an active statement, because
      VACUUM cannot run alongside any other work on the same connection.)
      File-level ``cp bot.db bot.db.bak`` would race with concurrent writes
      and could yield a corrupt backup.

    The sleep interval is re-read from settings each cycle, so the admin can
    change the cadence (e.g. from 24h to 1h) without restarting the bot. We
    also poll every 60s so a newly-enabled backup starts within a minute
    rather than waiting for the full old interval to elapse.
    """
    logger.info("Background task started: db_backup (configurable)")
    while True:
        # H11 — initialise these BEFORE the try block so the except handler
        # can reference them safely even if the first iteration raises before
        # they're assigned (e.g. DB unavailable at startup).
        backup_path = None
        interval_sec = 60
        try:
            enabled = await db.get_setting_int("backup_enabled", 0)
            interval_min = await db.get_setting_int("backup_interval_minutes", 1440)
            keep = await db.get_setting_int("backup_keep",
                                             int(os.getenv("DB_BACKUP_KEEP", "3")))
            # Clamp interval to a sane range: 1 minute minimum, 30 days max.
            interval_min = max(1, min(interval_min, 30 * 24 * 60))
            interval_sec = interval_min * 60
            if not enabled:
                # Poll every 60s so we notice when the admin enables it.
                await asyncio.sleep(60)
                continue
            ts = datetime.now(TEHRAN_TZ).strftime("%Y%m%dT%H%M%SZ")
            # The Online Backup API writes to a timestamped path so we never
            # overwrite the live bot.db. When sending via Telegram we rename
            # the file to "bot.db" (per user request — they want the original
            # filename, not the ".bak.<ts>" form).
            backup_path = f"{DATABASE_PATH}.bak.{ts}"
            # Open a fresh destination connection and copy the live DB into it
            # via the online backup API. This produces a consistent snapshot
            # without the "cannot VACUUM - SQL statements in progress" error
            # that VACUUM INTO hits on the shared bot connection.
            async with aiosqlite.connect(backup_path) as dst:
                await db._db.backup(dst)
            size = os.path.getsize(backup_path) if os.path.exists(backup_path) else 0
            # M18 — verify backup integrity before declaring success.
            integrity_ok = False
            try:
                async with aiosqlite.connect(backup_path) as chk:
                    async with chk.execute("PRAGMA integrity_check") as cur:
                        row = await cur.fetchone()
                        integrity_ok = bool(row and row[0] == "ok")
            except Exception as chk_err:
                logger.warning("db_backup: integrity_check raised: %s", chk_err)
            if not integrity_ok:
                logger.error("db_backup: integrity_check FAILED for %s — discarding", backup_path)
                try:
                    os.unlink(backup_path)
                except OSError:
                    pass
                backup_path = None
            else:
                logger.info("db_backup: snapshot written to %s (%d bytes, integrity OK)", backup_path, size)
                for admin_id in ADMIN_IDS:
                    await safe_notify(
                        bot.send_document(
                            admin_id,
                            FSInputFile(backup_path, filename="bot.db"),
                            caption=(
                                f"💾 <b>Scheduled DB backup</b>\n"
                                f"📦 {size:,} bytes\n"
                                f"🕒 {ts}"
                            ),
                        ),
                        context="db_backup admin document",
                    )
                # Retention — keep only the latest `keep` on-disk snapshots.
                try:
                    import glob
                    backups = sorted(
                        glob.glob(f"{DATABASE_PATH}.bak.*"),
                        key=lambda p: os.path.getmtime(p),
                    )
                    for old in backups[:-keep]:
                        try:
                            os.unlink(old)
                            logger.info("db_backup: pruned old snapshot %s", old)
                        except OSError as exc:
                            logger.warning("db_backup: could not prune %s: %s", old, exc)
                except Exception as exc:
                    logger.warning("db_backup: retention sweep failed: %s", exc, exc_info=True)
        except Exception as e:
            logger.error("db_backup error: %s", e, exc_info=True)
            if backup_path:
                try:
                    if os.path.exists(backup_path):
                        os.unlink(backup_path)
                except OSError:
                    pass
        # Sleep for the configured interval, but poll every 60s so cadence
        # changes (and disable) take effect within a minute.
        waited = 0
        while waited < interval_sec:
            await asyncio.sleep(60)
            waited += 60


# ============================================================================
# SECTION 13: MAIN APPLICATION
# ============================================================================

async def _health_server(db: Database, api: PanelAPI, port: int = 9090):
    """Tiny aiohttp /health endpoint (M7).

    Runs alongside the bot so external monitors (Uptime Kuma, Kubernetes,
    systemd watchdog) can poll it. Returns 200 OK if the DB is reachable and
    the polling loop is alive. Returns 503 if anything is wrong.

    H13 — the heartbeat dict is updated by a polling middleware so the
    staleness check actually works (previously it was set once at startup and
    never updated, so /health always returned 503 after 60s).
    H14 — binds to 127.0.0.1 by default (HEALTH_BIND env var overrides) and
    supports an optional HEALTH_TOKEN bearer auth.
    """
    try:
        from aiohttp import web
    except ImportError:
        logger.info("aiohttp not installed — /health endpoint disabled (M7)")
        return None

    last_poll_heartbeat = {"ts": time.time()}
    health_token = os.getenv("HEALTH_TOKEN", "")

    async def health_handler(request):
        # H14 — optional bearer-token auth.
        if health_token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {health_token}":
                return web.json_response({"error": "unauthorized"}, status=401)
        try:
            # DB reachability check.
            async with db._db.execute("SELECT 1") as cur:
                await cur.fetchone()
            db_ok = True
        except Exception:
            db_ok = False
        # Panel API client is "alive" if the httpx session isn't closed.
        api_ok = not api.client.is_closed
        # Polling heartbeat: should update every few seconds.
        poll_stale = (time.time() - last_poll_heartbeat["ts"]) > 60
        status = "ok" if (db_ok and api_ok and not poll_stale) else "degraded"
        code = 200 if status == "ok" else 503
        return web.json_response(
            {"status": status, "db": db_ok, "api_client": api_ok,
             "poll_stale": poll_stale, "ts": time.time()},
            status=code,
        )

    app = web.Application()
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    # H14 — bind to 127.0.0.1 by default; HEALTH_BIND=0.0.0.0 to expose.
    bind_addr = os.getenv("HEALTH_BIND", "127.0.0.1")
    # If the primary port is already in use (typically because a previous
    # bot instance didn't shut down cleanly), try a few fallback ports so
    # /health monitoring keeps working instead of just failing silently.
    primary_port = port
    tried_ports = [primary_port + i for i in range(6)]  # 9090..9095
    bound_port = None
    site = None
    for p in tried_ports:
        try:
            site = web.TCPSite(runner, bind_addr, p)
            await site.start()
            bound_port = p
            break
        except OSError as be:
            if "address already in use" in str(be).lower() or be.errno == 98:
                logger.debug("/health: port %d in use, trying next", p)
                continue
            # Different OSError — re-raise so the outer handler logs it.
            raise
    if bound_port is None:
        # All candidate ports are in use — another bot instance is almost
        # certainly still running. Surface a clear, actionable message.
        logger.warning(
            "/health: could not bind %s:%d (or ports %d-%d) — another bot "
            "instance may still be running. /health disabled for this "
            "process (non-fatal; bot keeps working).",
            bind_addr, primary_port, tried_ports[0], tried_ports[-1],
        )
        await runner.cleanup()
        return None
    if bound_port != primary_port:
        logger.warning(
            "/health: primary port %d was busy — using port %d instead. "
            "If this is unexpected, check for a stale bot process still "
            "holding port %d.",
            primary_port, bound_port, primary_port,
        )
    logger.info("/health endpoint listening on %s:%d", bind_addr, bound_port)
    return runner, last_poll_heartbeat


async def main():
    """Initialise DB, API, bot, routers, middleware, background tasks — then poll."""
    logger.info("=" * 60)
    logger.info("3X-UI Telegram Sales Bot — starting up")
    logger.info("=" * 60)

    db = Database(DATABASE_PATH)
    await db.connect()

    api = PanelAPI()
    lb = LoadBalancer(db, api)

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    auth_mw = AuthMiddleware(db, bot)

    user_router = create_user_router(db, api, lb, bot)
    admin_router = create_admin_router(db, api, lb, bot)

    # Auth middleware on both routers (resolves the user, blocks banned users)
    for r in (user_router, admin_router):
        r.message.middleware()(auth_mw)
        r.callback_query.middleware()(auth_mw)

    dp.include_router(user_router)
    dp.include_router(admin_router)

    # Background tasks
    tasks = [
        asyncio.create_task(task_expiry_checker(bot, db, api)),
        asyncio.create_task(task_traffic_alerts(bot, db, api)),
        asyncio.create_task(task_server_health(bot, db, api)),
        asyncio.create_task(task_sync_client_counts(db, api)),
        asyncio.create_task(task_data_retention(bot, db)),
        asyncio.create_task(task_db_backup(bot, db)),
    ]
    logger.info("Background tasks: expiry, traffic, health, sync_counts, data_retention, db_backup")

    # /health endpoint (M7)
    health_runner = None
    health_heartbeat = None
    try:
        result = await _health_server(db, api, port=int(os.getenv("HEALTH_PORT", "9090")))
        if result:
            health_runner, health_heartbeat = result
    except Exception as e:
        logger.warning("/health endpoint failed to start (non-fatal): %s", e)

    # H13 — a lightweight outer middleware that bumps the heartbeat timestamp
    # on every update, so /health's staleness check actually reflects liveness.
    @dp.update.outer_middleware()
    async def heartbeat_middleware(handler, update, data):
        if health_heartbeat is not None:
            health_heartbeat["ts"] = time.time()
        return await handler(update, data)

    # M6 — graceful shutdown: SIGTERM (systemd) and SIGINT (Ctrl+C) both
    # trigger dp.stop_polling() so the `finally` block can clean up cleanly.
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received — draining...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, RuntimeError):
            # add_signal_handler is not available on Windows / some loops.
            pass

    me = await bot.get_me()
    logger.info("Bot online as @%s", me.username)
    for admin_id in ADMIN_IDS:
        await safe_notify(
            bot.send_message(
                admin_id,
                f"✅ <b>Bot Started</b>\n🤖 @{me.username}\n"
                f"⚙️ /admin — admin panel\n🔧 Background tasks: active",
            ),
            context="bot-start admin notify",
        )

    # Polling task — runs concurrently with the stop-event watcher so we can
    # react to SIGTERM/SIGINT cleanly (M6).
    polling_task = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    )
    stop_watcher = asyncio.create_task(stop_event.wait())
    logger.info("Starting polling...")
    try:
        # Wait for either polling to exit OR a stop signal to arrive.
        done, pending = await asyncio.wait(
            {polling_task, stop_watcher}, return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_watcher in done:
            # Signal received — stop polling gracefully.
            await dp.stop_polling()
            try:
                await asyncio.wait_for(polling_task, timeout=10)
            except asyncio.TimeoutError:
                polling_task.cancel()
    finally:
        if not stop_watcher.done():
            stop_watcher.cancel()
        for task in tasks:
            task.cancel()
        # Give cancelled tasks up to 30s to finish in-flight work (M6).
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=30,
        )
        # LOW — shutdown order: wrap each cleanup in its own try/except so a
        # failure in one doesn't skip the rest.
        if health_runner is not None:
            try:
                await health_runner.cleanup()
            except Exception as e:
                logger.warning("health_runner cleanup failed: %s", e)
        try:
            await api.close()
        except Exception as e:
            logger.warning("api.close() failed: %s", e)
        try:
            await db.disconnect()
        except Exception as e:
            logger.warning("db.disconnect() failed: %s", e)
        try:
            await bot.session.close()
        except Exception as e:
            logger.warning("bot.session.close() failed: %s", e)
        logger.info("Bot shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error("Fatal error: %s", e)
        sys.exit(1)
