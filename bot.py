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
import string
import random
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Dict, List, Tuple, Callable

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

# Optional QR-code support (graceful fallback if not installed)
try:
    import qrcode
    _HAS_QR = True
except Exception:  # pragma: no cover
    _HAS_QR = False

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

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("vpnbot")
logging.getLogger("aiogram").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Constants
GB = 1073741824            # 1 GB in bytes
MS_PER_DAY = 86_400_000    # milliseconds per day
SUPPORTED_LANGS = ("en", "fa")
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
            "👋 <b>Welcome to {bot_name}!</b>\n\n"
            "🔐 Premium VPN service with instant delivery.\n"
            "📱 Manage your accounts right here in Telegram.\n\n"
            "<b>What I can do:</b>\n"
            "• 🛒 Buy a VPN subscription instantly\n"
            "• 📱 View account status & traffic usage\n"
            "• 🔄 Renew & top-up subscriptions\n"
            "• 🎁 Get a free trial\n"
            "• 🔗 Earn rewards via referrals\n"
            "• 💬 Get support without leaving Telegram\n\n"
            "Pick an option below 👇"
        ),
        "menu_main": "🏠 <b>Main Menu</b>\n\nWhat would you like to do?",
        "buy": "🛒 Buy VPN",
        "my_accounts": "📱 My Accounts",
        "trial": "🎁 Free Trial",
        "balance": "💳 Balance",
        "referral": "🔗 Referral",
        "gift": "🎫 Gift Code",
        "support": "💬 Support",
        "guide": "📚 Guide",
        "language": "🌐 Language",
        "admin_panel": "⚙️ Admin Panel",
        # buy flow
        "choose_plan": "🛒 <b>Choose a Plan</b>\n\nSelect a subscription plan:",
        "no_plans": "😔 No plans available yet. Please check back later or contact support.",
        "your_balance": "💳 Your balance: <b>{balance}</b>",
        "sufficient": "✅ You have enough balance.",
        "insufficient": "⚠️ Insufficient balance. You need <b>{diff}</b> more.\n\nUse Charge Wallet or redeem a gift code.",
        "ask_account_name": (
            "✏️ <b>Name your account</b>\n\n"
            "Send a friendly name (e.g. <code>phone</code>, <code>laptop</code>) — only letters, numbers, <code>-</code> and <code>_</code>.\n"
            "Send <code>-</code> for an automatic name."
        ),
        "invalid_name": "❌ Invalid name. Use 2-24 characters: letters, digits, dash or underscore.",
        "review_purchase": "📋 <b>Review your order</b>",
        "confirm_pay": "✅ Confirm & Pay",
        "apply_promo": "🎟 Promo Code",
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
        "unlimited": "Unlimited",
        # top-up
        "topup_title": "➕ <b>Traffic Top-up</b>\n\nChoose a package to add traffic without changing your expiry date:",
        "topup_success": "✅ <b>Traffic added!</b>\n+{gb} GB added to <code>{email}</code>.",
        # trial
        "trial_disabled": "😔 Free trials are currently disabled.\n\nPlease check back later.",
        "trial_used": "🎁 <b>Free Trial</b>\n\nYou have already used your free trial.\nEach user is limited to one trial.\n\n🛒 Check out our affordable plans!",
        "trial_offer": "🎁 <b>Free Trial Offer</b>",
        "get_trial": "✅ Get Free Trial",
        "trial_created": "🎉 <b>Trial account created!</b>",
        "trial_failed": "❌ Failed to create trial: {msg}",
        # balance
        "balance_title": "💳 <b>Your Balance</b>",
        "recent_tx": "📋 <b>Recent transactions</b>",
        "topup_hint": "💡 Use Charge Wallet to add balance, or redeem a gift code.",
        # referral
        "referral_title": "🔗 <b>Referral Program</b>",
        "referral_desc": "Invite friends and earn rewards automatically when they buy their first plan!",
        "your_link": "📤 <b>Your referral link</b>",
        # gift
        "enter_gift": "🎫 <b>Redeem gift code</b>\n\nSend me your code:",
        "gift_invalid": "❌ Invalid gift code. Try again:",
        "gift_used_code": "❌ This code has already been used.",
        "gift_balance_ok": "✅ <b>Gift redeemed!</b>\n💰 <b>{amount}</b> added to your balance.",
        "gift_plan_ok": "✅ <b>Gift redeemed!</b>\n🎁 Plan: <b>{plan}</b>",
        # support
        "support_title": "💬 <b>Support Center</b>",
        "support_desc": "Need help? Open a ticket and our team will assist you.\n\n• 🎫 Create a ticket for any issue\n• ⏱ We usually reply within a few hours\n• 🔒 Your conversation is private",
        "new_ticket": "🎫 New Ticket",
        "my_tickets": "📋 My Tickets",
        "ask_subject": "🎫 <b>New support ticket</b>\n\nSend a short subject:",
        "ask_message": "📝 <b>Subject:</b> {subject}\n\nNow describe your issue in detail:",
        "ticket_created": "✅ <b>Ticket #{id} created!</b>\n\n📝 Subject: {subject}\n⏱ We will respond as soon as possible.",
        "reply": "💬 Reply",
        "close": "🔒 Close",
        "ask_reply": "💬 <b>Reply to ticket</b>\n\nType your message:",
        "reply_sent_admin": "✅ Reply sent to user.",
        "reply_sent_user": "✅ Reply sent to admin.",
        "ticket_closed": "🔒 <b>Ticket #{id} has been closed.</b>\n\nIf you need further help, open a new ticket.",
        "no_tickets": "📋 <b>My Tickets</b>\n\nYou have no tickets yet.",
        # guide
        "guide_title": "📚 <b>VPN Setup Guide</b>",
        # language
        "lang_title": "🌐 <b>Language / زبان</b>\n\nChoose your language:",
        "lang_set": "✅ Language set to English.",
        # delivery
        "conn_links": "🔗 <b>Connection links</b>",
        "sub_url": "📡 <b>Subscription URL</b> (auto-updates all servers)",
        "how_to_use": "📱 <b>How to connect</b>",
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
            "💰 <b>Pay EXACTLY this amount:</b> {unique_amount} Toman\n\n"
            "⚠️ The extra digits are for verification. Pay the exact amount shown above.\n\n"
            "After payment, send your receipt (photo or text) using the button below."
        ),
        "send_receipt": "📤 Send Receipt",
        "enter_receipt_text": "📝 Type your receipt details (transaction ID, time, etc):",
        "receipt_received": "✅ Receipt received! Your payment is pending admin review.\n\nAmount: {amount} Toman\nYou'll be notified once it's approved.",
        "payment_approved": "✅ <b>Payment Approved!</b>\n\n💰 {amount} Toman added to your balance.\n💳 New balance: {balance}",
        "payment_rejected": "❌ <b>Payment Rejected</b>\n\nReason: {reason}\n\nPlease contact support if you have questions.",
        "pending_payments": "💰 <b>Pending Payments</b>",
        "approve_payment": "✅ Approve",
        "reject_payment": "❌ Reject",
        "enter_reject_reason": "❌ Enter rejection reason (or send <code>-</code> for no reason):",
        "force_join": (
            "🔒 <b>Please join our channel first!</b>\n\n"
            "You must join the following channel(s) to use this bot:\n\n"
            "{channels}\n\n"
            "After joining, click ✅ below to continue."
        ),
        "verify_join": "✅ I Joined",
        "force_join_success": "✅ Membership verified! You can now use the bot.",
        "force_join_failed": "❌ You haven't joined all required channels yet. Please join first.",
        "no_inbounds_configured": "❌ This plan has no configured inbounds. Please contact admin.",
        "broadcast_header_en": "📢 <b>Public Announcement</b>\n\n",
        "charge_wallet_btn": "💳 Charge Wallet",
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
            "👋 <b>به ربات VPN خوش آمدید!</b>\n\n"
            "🔐 سرویس VPN پریمیوم با تحویل آنی.\n"
            "📱 مدیریت اکانت‌ها مستقیم از همین تلگرام.\n\n"
            "<b>کارهایی که می‌توانید انجام دهید:</b>\n"
            "• 🛒 خرید اشتراک VPN به‌صورت آنی\n"
            "• 📱 مشاهدهٔ وضعیت اکانت و مصرف حجم\n"
            "• 🔄 تمدید و افزایش حجم\n"
            "• 🎁 دریافت اکانت آزمایشی رایگان\n"
            "• 🔗 کسب پاداش با دعوت دوستان\n"
            "• 💬 پشتیبانی بدون خروج از تلگرام\n\n"
            "یک گزینه انتخاب کنید 👇"
        ),
        "menu_main": "🏠 <b>منوی اصلی</b>\n\nچه کاری می‌خواهید انجام دهید؟",
        "buy": "🛒 خرید VPN",
        "my_accounts": "📱 اکانت‌های من",
        "trial": "🎁 اکانت رایگان",
        "balance": "💳 موجودی",
        "referral": "🔗 دعوت دوستان",
        "gift": "🎫 کد هدیه",
        "support": "💬 پشتیبانی",
        "guide": "📚 راهنما",
        "language": "🌐 زبان",
        "admin_panel": "⚙️ پنل مدیریت",
        "choose_plan": "🛒 <b>انتخاب پلن</b>\n\nیک پلن انتخاب کنید:",
        "no_plans": "😔 هنوز پلنی تعریف نشده. بعداً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.",
        "your_balance": "💳 موجودی شما: <b>{balance}</b>",
        "sufficient": "✅ موجودی کافی است.",
        "insufficient": "⚠️ موجودی کافی نیست. <b>{diff}</b> دیگر نیاز دارید.\n\nاز شارژ کیف پول استفاده کنید یا کد هدیه دریافت کنید.",
        "ask_account_name": (
            "✏️ <b>نام اکانت</b>\n\n"
            "یک نام دلخواه بفرستید (مثلاً <code>phone</code> یا <code>laptop</code>) — فقط حروف انگلیسی، عدد، خط تیره و زیرخط.\n"
            "برای نام خودکار، <code>-</code> بفرستید."
        ),
        "invalid_name": "❌ نام نامعتبر است. ۲ تا ۲۴ کاراکتر: حروف، عدد، خط تیره یا زیرخط.",
        "review_purchase": "📋 <b>بررسی سفارش</b>",
        "confirm_pay": "✅ تأیید و پرداخت",
        "apply_promo": "🎟 کد تخفیف",
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
        "unlimited": "نامحدود",
        "topup_title": "➕ <b>افزایش حجم</b>\n\nیک بسته انتخاب کنید تا بدون تغییر تاریخ انقضا، حجم اکانت افزایش یابد:",
        "topup_success": "✅ <b>حجم اضافه شد!</b>\n+{gb} GB به <code>{email}</code> اضافه شد.",
        "trial_disabled": "😔 در حال حاضر اکانت رایگان غیرفعال است.\n\nبعداً دوباره تلاش کنید.",
        "trial_used": "🎁 <b>اکانت رایگان</b>\n\nشما قبلاً اکانت رایگان دریافت کرده‌اید.\nهر کاربر فقط یک‌بار می‌تواند استفاده کند.\n\n🛒 پلن‌های مقرون‌به‌صرفه ما را ببینید!",
        "trial_offer": "🎁 <b>پیشنهاد اکانت رایگان</b>",
        "get_trial": "✅ دریافت اکانت رایگان",
        "trial_created": "🎉 <b>اکانت آزمایشی ساخته شد!</b>",
        "trial_failed": "❌ ساخت اکانت آزمایشی ناموفق بود: {msg}",
        "balance_title": "💳 <b>موجودی شما</b>",
        "recent_tx": "📋 <b>تراکنش‌های اخیر</b>",
        "topup_hint": "💡 از شارژ کیف پول برای افزایش موجودی استفاده کنید یا کد هدیه دریافت کنید.",
        "referral_title": "🔗 <b>برنامهٔ دعوت دوستان</b>",
        "referral_desc": "دوستان خود را دعوت کنید و با اولین خریدشان، به‌طور خودکار پاداش بگیرید!",
        "your_link": "📤 <b>لینک دعوت شما</b>",
        "enter_gift": "🎫 <b>کد هدیه</b>\n\nکد خود را بفرستید:",
        "gift_invalid": "❌ کد هدیه نامعتبر است. دوباره تلاش کنید:",
        "gift_used_code": "❌ این کد قبلاً استفاده شده است.",
        "gift_balance_ok": "✅ <b>کد ثبت شد!</b>\n💰 <b>{amount}</b> به موجودی شما اضافه شد.",
        "gift_plan_ok": "✅ <b>کد ثبت شد!</b>\n🎁 پلن: <b>{plan}</b>",
        "support_title": "💬 <b>مرکز پشتیبانی</b>",
        "support_desc": "نیاز به کمک دارید؟ یک تیکت باز کنید تا تیم ما کمکتان کند.\n\n• 🎫 برای هر مشکلی تیکت بزنید\n• ⏱ معمولاً ظرف چند ساعت پاسخ می‌دهیم\n• 🔒 گفتگو کاملاً محرمانه است",
        "new_ticket": "🎫 تیکت جدید",
        "my_tickets": "📋 تیکت‌های من",
        "ask_subject": "🎫 <b>تیکت پشتیبانی جدید</b>\n\nموضوع کوتاهی بنویسید:",
        "ask_message": "📝 <b>موضوع:</b> {subject}\n\nحالا مشکل خود را شرح دهید:",
        "ticket_created": "✅ <b>تیکت #{id} ساخته شد!</b>\n\n📝 موضوع: {subject}\n⏱ به‌زودی پاسخ می‌دهیم.",
        "reply": "💬 پاسخ",
        "close": "🔒 بستن",
        "ask_reply": "💬 <b>پاسخ به تیکت</b>\n\nپیام خود را بنویسید:",
        "reply_sent_admin": "✅ پاسخ به کاربر ارسال شد.",
        "reply_sent_user": "✅ پاسخ به مدیریت ارسال شد.",
        "ticket_closed": "🔒 <b>تیکت #{id} بسته شد.</b>\n\nاگر کمک بیشتری نیاز دارید، تیکت جدیدی باز کنید.",
        "no_tickets": "📋 <b>تیکت‌های من</b>\n\nهنوز تیکتی ندارید.",
        "guide_title": "📚 <b>راهنمای راه‌اندازی VPN</b>",
        "lang_title": "🌐 <b>Language / زبان</b>\n\nزبان خود را انتخاب کنید:",
        "lang_set": "✅ زبان به فارسی تغییر یافت.",
        "conn_links": "🔗 <b>لینک‌های اتصال</b>",
        "sub_url": "📡 <b>لینک سابسکریپشن</b> (همهٔ سرورها را خودکار به‌روز می‌کند)",
        "how_to_use": "📱 <b>نحوهٔ اتصال</b>",
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
            "💰 <b>دقیقاً این مبلغ را پرداخت کنید:</b> {unique_amount} تومان\n\n"
            "⚠️ ارقام مازاد برای تأیید تراکنش هستند. دقیقاً همین مبلغ را پرداخت کنید.\n\n"
            "پس از پرداخت، رسید خود (عکس یا متن) را با دکمهٔ زیر بفرستید."
        ),
        "send_receipt": "📤 ارسال رسید",
        "enter_receipt_text": "📝 جزئیات رسید خود را بنویسید (شماره تراکنش، زمان و ...):",
        "receipt_received": "✅ رسید دریافت شد! پرداخت شما در انتظار بررسی مدیریت است.\n\nمبلغ: {amount} تومان\nپس از تأیید، مبلغ به کیف پول شما اضافه می‌شود.",
        "payment_approved": "✅ <b>پرداخت تأیید شد!</b>\n\n💰 {amount} تومان به موجودی شما اضافه شد.\n💳 موجودی جدید: {balance}",
        "payment_rejected": "❌ <b>پرداخت رد شد</b>\n\nدلیل: {reason}\n\nاگر سؤالی دارید، با پشتیبانی تماس بگیرید.",
        "pending_payments": "💰 <b>پرداخت‌های در انتظار</b>",
        "approve_payment": "✅ تأیید",
        "reject_payment": "❌ رد",
        "enter_reject_reason": "❌ دلیل رد را بنویسید (یا <code>-</code> برای بدون دلیل):",
        "force_join": (
            "🔒 <b>ابتدا عضو کانال ما شوید!</b>\n\n"
            "برای استفاده از ربات، باید عضو کانال‌های زیر باشید:\n\n"
            "{channels}\n\n"
            "پس از عضویت، دکمه ✅ زیر را بزنید."
        ),
        "verify_join": "✅ عضو شدم",
        "force_join_success": "✅ عضویت تأیید شد! حالا می‌توانید از ربات استفاده کنید.",
        "force_join_failed": "❌ شما هنوز عضو همهٔ کانال‌های مورد نیاز نشده‌اید. ابتدا عضو شوید.",
        "no_inbounds_configured": "❌ این پلن اینباند تنظیم‌شده ندارد. با مدیریت تماس بگیرید.",
        "broadcast_header_fa": "📢 <b>اطلاعیه همگانی</b>\n\n",
        "charge_wallet_btn": "💳 شارژ کیف پول",
    },
}


def t(key: str, lang: str = "en", **kwargs) -> str:
    """Translate a key for the given language with optional formatting."""
    table = MESSAGES.get(lang) or MESSAGES["en"]
    text = table.get(key) or MESSAGES["en"].get(key) or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
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
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()
        await self._migrate()
        await self._seed_settings()
        await self._db.commit()
        logger.info("Database initialised")

    async def disconnect(self):
        if self._db:
            await self._db.close()
            logger.info("Database connection closed")

    async def _create_tables(self):
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id            INTEGER PRIMARY KEY,
                username         TEXT,
                first_name       TEXT,
                language         TEXT DEFAULT 'fa',
                balance          REAL DEFAULT 0,
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
                traffic_gb    INTEGER NOT NULL,
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
                traffic_gb  INTEGER,
                expiry_time INTEGER,
                limit_ip    INTEGER DEFAULT 0,
                is_active   INTEGER DEFAULT 1,
                is_trial    INTEGER DEFAULT 0,
                inbound_ids TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                renewed_at  TEXT,
                FOREIGN KEY (user_tg_id) REFERENCES users(tg_id),
                FOREIGN KEY (server_id) REFERENCES servers(id),
                FOREIGN KEY (plan_id) REFERENCES plans(id)
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
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_tg_id INTEGER NOT NULL,
                subject    TEXT,
                status     TEXT DEFAULT 'open',
                priority   TEXT DEFAULT 'normal',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT,
                closed_at  TEXT,
                FOREIGN KEY (user_tg_id) REFERENCES users(tg_id)
            );

            CREATE TABLE IF NOT EXISTS ticket_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id  INTEGER NOT NULL,
                sender     TEXT NOT NULL,        -- user | admin
                message    TEXT,
                created_at TEXT DEFAULT (datetime('now')),
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
                created_at      TEXT DEFAULT (datetime('now'))
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
            "users": [("referral_rewarded", "INTEGER DEFAULT 0"), ("language_selected", "INTEGER DEFAULT 0")],
            "servers": [
                ("sub_uri", "TEXT"),
                ("capacity", "INTEGER DEFAULT 0"),
                ("priority", "INTEGER DEFAULT 10"),
                ("location", "TEXT"),
            ],
            "plans": [("inbound_ids", "TEXT")],
            "accounts": [("label", "TEXT")],
            "transactions": [("admin_id", "INTEGER")],
        }
        for table, cols in add_cols.items():
            async with self._db.execute(f"PRAGMA table_info({table})") as cur:
                existing = {row[1] for row in await cur.fetchall()}
            for col, decl in cols:
                if col not in existing:
                    await self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    logger.info("Migrated: added %s.%s", table, col)

    async def _seed_settings(self):
        defaults = {
            "trial_enabled": "1",
            "trial_days": str(int(os.getenv("TRIAL_DAYS", "3"))),
            "trial_gb": str(int(os.getenv("TRIAL_GB", "5"))),
            "trial_limit_ip": "1",
            "trial_inbounds": "[]",   # JSON list of "server_id_inbound_id"
            "referral_bonus_days": str(int(os.getenv("REFERRAL_BONUS_DAYS", "5"))),
            "referral_bonus_gb": str(int(os.getenv("REFERRAL_BONUS_GB", "2"))),
            "currency": DEFAULT_CURRENCY,
            "default_language": DEFAULT_LANGUAGE,
            "topup_packages": json.dumps([5, 10, 20, 50]),  # GB options
            "payment_enabled": "1",
            "payment_card_number": "",
            "payment_card_holder": "",
            "payment_presets": json.dumps([50000, 100000, 200000, 500000]),
            "payment_min_amount": "50000",
            "force_join_enabled": "0",
            "force_join_channels": json.dumps([]),
            "help_text_en": "",
            "help_text_fa": "",
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
                (datetime.now().isoformat(), username, first_name, tg_id),
            )
            await self._db.commit()
            return dict(row)

        ref_code_generated = self._gen_referral_code()
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
             datetime.now().isoformat()),
        )
        await self._db.commit()
        async with self._db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            return dict(await cur.fetchone())

    def _gen_referral_code(self) -> str:
        chars = string.ascii_uppercase + string.digits
        return "REF" + "".join(random.choices(chars, k=6))

    async def get_user(self, tg_id: int) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_user_language(self, tg_id: int, lang: str):
        await self._db.execute(
            "UPDATE users SET language = ? WHERE tg_id = ?", (L(lang), tg_id)
        )
        await self._db.commit()

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

    async def set_user_balance(self, tg_id: int, amount: float):
        await self._db.execute(
            "UPDATE users SET balance = ? WHERE tg_id = ?", (amount, tg_id)
        )
        await self._db.commit()

    async def mark_referral_rewarded(self, tg_id: int):
        await self._db.execute(
            "UPDATE users SET referral_rewarded = 1 WHERE tg_id = ?", (tg_id,)
        )
        await self._db.commit()

    async def ban_user(self, tg_id: int, banned: bool = True):
        await self._db.execute(
            "UPDATE users SET is_banned = ? WHERE tg_id = ?", (1 if banned else 0, tg_id)
        )
        await self._db.commit()

    async def get_all_users(self) -> List[dict]:
        async with self._db.execute("SELECT * FROM users ORDER BY created_at DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def count_users(self) -> int:
        async with self._db.execute("SELECT COUNT(*) AS cnt FROM users") as cur:
            return (await cur.fetchone())["cnt"]

    async def get_users_by_filter(self, filter_type: str) -> List[int]:
        if filter_type == "all":
            sql = "SELECT tg_id FROM users WHERE is_banned = 0"
        elif filter_type == "active":
            sql = """SELECT DISTINCT u.tg_id FROM users u
                     JOIN accounts a ON u.tg_id = a.user_tg_id
                     WHERE a.is_active = 1 AND u.is_banned = 0"""
        elif filter_type == "expired":
            sql = """SELECT DISTINCT u.tg_id FROM users u
                     JOIN accounts a ON u.tg_id = a.user_tg_id
                     WHERE a.is_active = 0 AND u.is_banned = 0"""
        elif filter_type == "trial":
            sql = """SELECT DISTINCT u.tg_id FROM users u
                     JOIN accounts a ON u.tg_id = a.user_tg_id
                     WHERE a.is_trial = 1 AND u.is_banned = 0"""
        elif filter_type == "banned":
            sql = "SELECT tg_id FROM users WHERE is_banned = 1"
        else:
            return []
        async with self._db.execute(sql) as cur:
            return [r["tg_id"] for r in await cur.fetchall()]

    async def search_user(self, query: str) -> List[dict]:
        results: List[dict] = []
        seen = set()
        if query.isdigit():
            async with self._db.execute("SELECT * FROM users WHERE tg_id = ?", (int(query),)) as cur:
                for r in await cur.fetchall():
                    results.append(dict(r))
                    seen.add(r["tg_id"])
        async with self._db.execute(
            "SELECT * FROM users WHERE username LIKE ? OR first_name LIKE ?",
            (f"%{query}%", f"%{query}%"),
        ) as cur:
            for r in await cur.fetchall():
                if r["tg_id"] not in seen:
                    results.append(dict(r))
                    seen.add(r["tg_id"])
        async with self._db.execute(
            """SELECT u.* FROM users u JOIN accounts a ON u.tg_id = a.user_tg_id
               WHERE a.email LIKE ?""", (f"%{query}%",),
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
        await self._db.commit()
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

    async def update_server(self, server_id: int, **kwargs):
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [server_id]
        await self._db.execute(f"UPDATE servers SET {sets} WHERE id = ?", vals)
        await self._db.commit()

    async def update_server_health(self, server_id: int, healthy: bool,
                                   error: str = "", total_clients: int = 0,
                                   total_traffic: int = 0):
        await self._db.execute(
            """UPDATE servers SET is_healthy = ?, last_check = ?, last_error = ?,
               total_clients = ?, total_traffic = ? WHERE id = ?""",
            (1 if healthy else 0, datetime.now().isoformat(),
             error, total_clients, total_traffic, server_id),
        )
        await self._db.commit()

    async def toggle_server(self, server_id: int, active: bool):
        await self._db.execute(
            "UPDATE servers SET is_active = ? WHERE id = ?",
            (1 if active else 0, server_id),
        )
        await self._db.commit()

    async def delete_server(self, server_id: int):
        await self._db.execute("DELETE FROM servers WHERE id = ?", (server_id,))
        await self._db.commit()

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
        await self._db.commit()

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
    async def add_plan(self, name: str, description: str, traffic_gb: int,
                       duration_days: int, price: float, limit_ip: int = 0,
                       inbound_ids: Optional[List[str]] = None) -> int:
        cur = await self._db.execute(
            """INSERT INTO plans
               (name, description, traffic_gb, duration_days, price, limit_ip, inbound_ids)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, description, traffic_gb, duration_days, price, limit_ip,
             json.dumps(inbound_ids or [])),
        )
        await self._db.commit()
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
        await self._db.commit()

    async def delete_plan(self, plan_id: int):
        await self._db.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        await self._db.commit()

    async def update_plan(self, plan_id: int, **kwargs):
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [plan_id]
        await self._db.execute(f"UPDATE plans SET {sets} WHERE id = ?", vals)
        await self._db.commit()

    # ------------------------------------------------------------ accounts
    async def add_account(self, user_tg_id: int, server_id: int, email: str,
                          sub_id: str, plan_id: Optional[int], traffic_gb: int,
                          expiry_time: int, limit_ip: int, inbound_ids: str,
                          is_trial: bool = False, label: str = "") -> int:
        cur = await self._db.execute(
            """INSERT INTO accounts
               (user_tg_id, server_id, email, sub_id, label, plan_id, traffic_gb,
                expiry_time, limit_ip, inbound_ids, is_trial)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_tg_id, server_id, email, sub_id, label, plan_id, traffic_gb,
             expiry_time, limit_ip, inbound_ids, 1 if is_trial else 0),
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
            "SELECT * FROM accounts WHERE user_tg_id = ? ORDER BY created_at DESC", (tg_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def update_account(self, email: str, **kwargs):
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [email]
        await self._db.execute(f"UPDATE accounts SET {sets} WHERE email = ?", vals)
        await self._db.commit()

    async def delete_account(self, email: str):
        await self._db.execute("DELETE FROM accounts WHERE email = ?", (email,))
        await self._db.commit()

    async def get_all_active_accounts(self) -> List[dict]:
        async with self._db.execute("SELECT * FROM accounts WHERE is_active = 1") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_expiring_accounts(self, days: int) -> List[dict]:
        future = int((datetime.now() + timedelta(days=days)).timestamp() * 1000)
        now = int(datetime.now().timestamp() * 1000)
        async with self._db.execute(
            """SELECT * FROM accounts WHERE is_active = 1
               AND expiry_time > 0 AND expiry_time <= ? AND expiry_time > ?""",
            (future, now),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def has_used_trial(self, tg_id: int) -> bool:
        async with self._db.execute(
            "SELECT COUNT(*) AS cnt FROM accounts WHERE user_tg_id = ? AND is_trial = 1",
            (tg_id,),
        ) as cur:
            return (await cur.fetchone())["cnt"] > 0

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
        await self._db.commit()
        return cur.lastrowid

    async def get_user_transactions(self, tg_id: int, limit: int = 10) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM transactions WHERE user_tg_id = ? ORDER BY created_at DESC LIMIT ?",
            (tg_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_revenue_stats(self, days: int = 30) -> dict:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        async with self._db.execute(
            """SELECT SUM(amount) AS total, COUNT(*) AS cnt
               FROM transactions
               WHERE type IN ('purchase','renewal','topup') AND created_at >= ?""",
            (since,),
        ) as cur:
            row = await cur.fetchone()

        today = datetime.now().strftime("%Y-%m-%d")
        async with self._db.execute(
            """SELECT SUM(amount) AS total FROM transactions
               WHERE type IN ('purchase','renewal','topup') AND created_at LIKE ?""",
            (f"{today}%",),
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
        await self._db.commit()
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
            if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) < datetime.now():
                return None
            return dict(row)

    async def use_promo_code(self, code: str):
        await self._db.execute(
            "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?", (code.upper(),)
        )
        await self._db.commit()

    async def get_promo_codes(self) -> List[dict]:
        async with self._db.execute("SELECT * FROM promo_codes ORDER BY created_at DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def delete_promo_code(self, code_id: int):
        await self._db.execute("DELETE FROM promo_codes WHERE id = ?", (code_id,))
        await self._db.commit()

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
        await self._db.commit()
        return cur.lastrowid

    async def get_gift_code(self, code: str) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM gift_codes WHERE code = ?", (code.upper(),)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def use_gift_code(self, code: str, user_tg_id: int):
        await self._db.execute(
            "UPDATE gift_codes SET is_used = 1, used_by = ?, used_at = ? WHERE code = ?",
            (user_tg_id, datetime.now().isoformat(), code.upper()),
        )
        await self._db.commit()

    async def get_gift_codes(self, unused_only: bool = False) -> List[dict]:
        q = "SELECT * FROM gift_codes"
        if unused_only:
            q += " WHERE is_used = 0"
        q += " ORDER BY created_at DESC"
        async with self._db.execute(q) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # -------------------------------------------------------------- tickets
    async def create_ticket(self, user_tg_id: int, subject: str) -> int:
        cur = await self._db.execute(
            "INSERT INTO tickets (user_tg_id, subject) VALUES (?, ?)", (user_tg_id, subject)
        )
        await self._db.commit()
        return cur.lastrowid

    async def add_ticket_message(self, ticket_id: int, sender: str, message: str):
        await self._db.execute(
            "INSERT INTO ticket_messages (ticket_id, sender, message) VALUES (?, ?, ?)",
            (ticket_id, sender, message),
        )
        await self._db.execute(
            "UPDATE tickets SET updated_at = ? WHERE id = ?", (datetime.now().isoformat(), ticket_id)
        )
        await self._db.commit()

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
            "SELECT * FROM tickets WHERE user_tg_id = ? ORDER BY created_at DESC", (tg_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_open_tickets(self) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM tickets WHERE status = 'open' ORDER BY created_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def close_ticket(self, ticket_id: int):
        await self._db.execute(
            "UPDATE tickets SET status = 'closed', closed_at = ? WHERE id = ?",
            (datetime.now().isoformat(), ticket_id),
        )
        await self._db.commit()

    async def count_open_tickets(self) -> int:
        async with self._db.execute("SELECT COUNT(*) AS cnt FROM tickets WHERE status = 'open'") as cur:
            return (await cur.fetchone())["cnt"]

    # ----------------------------------------------------------- broadcasts
    async def create_broadcast(self, admin_id: int, message: str, target: str = "all") -> int:
        cur = await self._db.execute(
            "INSERT INTO broadcasts (admin_id, message, target) VALUES (?, ?, ?)",
            (admin_id, message, target),
        )
        await self._db.commit()
        return cur.lastrowid

    async def update_broadcast_stats(self, broadcast_id: int, sent: int, failed: int,
                                     status: str = "completed"):
        await self._db.execute(
            "UPDATE broadcasts SET total_sent = ?, total_failed = ?, status = ? WHERE id = ?",
            (sent, failed, status, broadcast_id),
        )
        await self._db.commit()

    async def get_broadcasts(self, limit: int = 10) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM broadcasts ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

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
        await self._db.commit()

    async def clear_traffic_alerts(self, email: str):
        await self._db.execute("DELETE FROM traffic_alerts WHERE account_email = ?", (email,))
        await self._db.commit()

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
        await self._db.commit()

    async def clear_expiry_reminders(self, email: str):
        await self._db.execute("DELETE FROM expiry_reminders WHERE account_email = ?", (email,))
        await self._db.commit()

    # ------------------------------------------------------------- referrals
    async def add_referral_reward(self, referrer_tg_id: int, referred_tg_id: int,
                                  account_email: str, bonus_days: int, bonus_gb: int):
        await self._db.execute(
            """INSERT INTO referral_rewards
               (referrer_tg_id, referred_tg_id, account_email, bonus_days, bonus_gb)
               VALUES (?, ?, ?, ?, ?)""",
            (referrer_tg_id, referred_tg_id, account_email, bonus_days, bonus_gb),
        )
        await self._db.commit()

    async def get_referral_stats(self, tg_id: int) -> dict:
        async with self._db.execute(
            "SELECT COUNT(*) AS cnt FROM referral_rewards WHERE referrer_tg_id = ?", (tg_id,)
        ) as cur:
            completed = (await cur.fetchone())["cnt"]
        async with self._db.execute(
            "SELECT COUNT(*) AS cnt FROM users WHERE referred_by = ?", (tg_id,)
        ) as cur:
            total = (await cur.fetchone())["cnt"]
        return {"total_referrals": total, "completed_referrals": completed}

    # ------------------------------------------------------------- payments
    async def add_payment(self, user_tg_id: int, amount: float, unique_amount: float,
                          card_number: str = "", card_holder: str = "",
                          receipt_type: str = "", receipt_file_id: str = "",
                          receipt_text: str = "") -> int:
        cur = await self._db.execute(
            """INSERT INTO payments
               (user_tg_id, amount, unique_amount, card_number, card_holder,
                receipt_type, receipt_file_id, receipt_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_tg_id, amount, unique_amount, card_number, card_holder,
             receipt_type, receipt_file_id, receipt_text),
        )
        await self._db.commit()
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

    async def get_user_payments(self, tg_id: int, limit: int = 10) -> List[dict]:
        async with self._db.execute(
            "SELECT * FROM payments WHERE user_tg_id = ? ORDER BY created_at DESC LIMIT ?",
            (tg_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def approve_payment(self, payment_id: int, admin_id: int):
        await self._db.execute(
            "UPDATE payments SET status = 'approved', admin_id = ?, reviewed_at = ? WHERE id = ?",
            (admin_id, datetime.now().isoformat(), payment_id),
        )
        await self._db.commit()

    async def reject_payment(self, payment_id: int, admin_id: int, note: str = ""):
        await self._db.execute(
            "UPDATE payments SET status = 'rejected', admin_id = ?, admin_note = ?, reviewed_at = ? WHERE id = ?",
            (admin_id, note, datetime.now().isoformat(), payment_id),
        )
        await self._db.commit()

    async def update_language_selected(self, tg_id: int, selected: bool = True):
        await self._db.execute(
            "UPDATE users SET language_selected = ? WHERE tg_id = ?",
            (1 if selected else 0, tg_id),
        )
        await self._db.commit()

    # ------------------------------------------------------------- settings
    async def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        async with self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row["value"] if row else default

    async def get_setting_int(self, key: str, default: int = 0) -> int:
        v = await self.get_setting(key)
        try:
            return int(v) if v is not None else default
        except Exception:
            return default

    async def get_setting_json(self, key: str, default: Any = None) -> Any:
        v = await self.get_setting(key)
        if v is None:
            return default
        try:
            return json.loads(v)
        except Exception:
            return default

    async def set_setting(self, key: str, value: str):
        await self._db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        await self._db.commit()


# ============================================================================
# SECTION 3: 3X-UI PANEL API CLIENT
# ============================================================================

class PanelAPI:
    """Async client for the 3X-UI panel API with multi-panel support."""

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            verify=False,            # panels often use self-signed certs
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
                            inbound_ids: List[int], total_gb: int = 0,
                            expiry_time: int = 0, limit_ip: int = 0,
                            tg_id: int = 0, flow: str = "", sub_id: str = "") -> dict:
        client: Dict[str, Any] = {"email": email, "enable": True}
        if total_gb > 0:
            # 3x-ui API expects totalGB in gigabytes (not bytes)
            client["totalGB"] = total_gb
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
        r = await self._request("GET", panel_url, token, f"/panel/api/clients/get/{email}")
        return r.get("obj") if r.get("success") else None

    async def get_client_traffic(self, panel_url: str, token: str, email: str) -> Optional[dict]:
        r = await self._request("GET", panel_url, token, f"/panel/api/clients/traffic/{email}")
        return r.get("obj") if r.get("success") else None

    async def get_client_links(self, panel_url: str, token: str, email: str) -> List[str]:
        r = await self._request("GET", panel_url, token, f"/panel/api/clients/links/{email}")
        return r.get("obj", []) if r.get("success") else []

    async def get_sub_links(self, panel_url: str, token: str, sub_id: str) -> List[str]:
        r = await self._request("GET", panel_url, token, f"/panel/api/clients/subLinks/{sub_id}")
        return r.get("obj", []) if r.get("success") else []

    async def update_client(self, panel_url: str, token: str, email: str,
                            client_data: dict) -> dict:
        return await self._request(
            "POST", panel_url, token, f"/panel/api/clients/update/{email}", json=client_data
        )

    async def delete_client(self, panel_url: str, token: str, email: str,
                            keep_traffic: bool = False) -> dict:
        return await self._request(
            "POST", panel_url, token,
            f"/panel/api/clients/del/{email}?keepTraffic={'1' if keep_traffic else '0'}",
        )

    async def reset_client_traffic(self, panel_url: str, token: str, email: str) -> dict:
        return await self._request("POST", panel_url, token, f"/panel/api/clients/resetTraffic/{email}")

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
        r = await self._request("POST", panel_url, token, f"/panel/api/clients/ips/{email}")
        return r.get("obj", []) if r.get("success") else []

    async def clear_client_ips(self, panel_url: str, token: str, email: str) -> dict:
        return await self._request("POST", panel_url, token, f"/panel/api/clients/clearIps/{email}")

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

    async def attach_client(self, panel_url: str, token: str, email: str,
                            inbound_ids: List[int]) -> dict:
        return await self._request("POST", panel_url, token,
                                   f"/panel/api/clients/{email}/attach", json={"inboundIds": inbound_ids})

    async def detach_client(self, panel_url: str, token: str, email: str,
                            inbound_ids: List[int]) -> dict:
        return await self._request("POST", panel_url, token,
                                   f"/panel/api/clients/{email}/detach", json={"inboundIds": inbound_ids})

    async def set_external_links(self, panel_url: str, token: str, email: str,
                                 links: List[dict]) -> dict:
        return await self._request("POST", panel_url, token,
                                   f"/panel/api/clients/{email}/externalLinks",
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
        for srv in healthy:
            local_count = srv.get("total_clients", 0)
            capacity = srv.get("capacity", 0) or 0
            # Capacity check: skip servers that are full
            if capacity > 0 and local_count >= capacity:
                continue
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
        # wanted entries look like "server_id_inbound_id"
        allowed = {int(x.split("_", 1)[1]) for x in wanted
                   if "_" in x and int(x.split("_", 1)[0]) == server["id"]}
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
        allowed = {int(x.split("_", 1)[1]) for x in trial_inbounds
                   if "_" in x and int(x.split("_", 1)[0]) == server["id"]}
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
        return list({int(x.split("_", 1)[0]) for x in entries if "_" in x})


# ============================================================================
# SECTION 5: FORMATTERS & UTILITIES
# ============================================================================

def fmt_bytes(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "0 B"
    n = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} EB"


def fmt_gb(gb: int, lang: str = "en") -> str:
    if gb == 0:
        return t("unlimited", lang)
    if gb >= 1024:
        s = f"{gb/1024:.1f} TB"
    else:
        s = f"{gb} GB"
    return to_fa_digits(s) if lang == "fa" else s


def fmt_days(days: int, lang: str = "en") -> str:
    if days == 0:
        return "∞" if lang == "en" else "نامحدود"
    if days >= 365:
        s = f"{days/365:.1f}y"
    elif days >= 30:
        s = f"{days/30:.1f}mo"
    else:
        s = f"{days}d"
    return to_fa_digits(s) if lang == "fa" else s


def fmt_ts(ts_ms: int, lang: str = "en") -> str:
    if ts_ms == 0:
        return "∞" if lang == "en" else "نامحدود"
    dt = datetime.fromtimestamp(ts_ms / 1000)
    s = dt.strftime("%Y-%m-%d %H:%M")
    return to_fa_digits(s) if lang == "fa" else s


def fmt_remaining(expiry_ms: int, lang: str = "en") -> str:
    if expiry_ms == 0:
        return "∞" if lang == "en" else "نامحدود"
    now_ms = int(datetime.now().timestamp() * 1000)
    diff = expiry_ms - now_ms
    if diff <= 0:
        return "Expired" if lang == "en" else "منقضی"
    days = diff // MS_PER_DAY
    hours = (diff % MS_PER_DAY) // 3_600_000
    if days > 0:
        s = f"{days}d {hours}h"
    else:
        minutes = (diff % 3_600_000) // 60_000
        s = f"{hours}h {minutes}m"
    return to_fa_digits(s) if lang == "fa" else s


def fmt_progress_bar(pct: float, width: int = 10) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(width * pct / 100)
    return "█" * filled + "░" * (width - filled) + f" {pct:.0f}%"


def sanitize_name(name: str) -> Optional[str]:
    """Validate a user-supplied account name.  Returns cleaned name or None."""
    name = name.strip()
    if not name or name == "-":
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,24}", name):
        return None
    return name


def gen_email(tg_id: int, name: str = "") -> str:
    """Generate a panel-unique email (used as client ID)."""
    suffix = secrets.token_hex(4)
    short_id = str(tg_id)[-6:]
    if name:
        return f"{name}_{short_id}_{suffix}"
    ts = int(time.time()) % 1000000
    return f"tg{short_id}_{ts}_{suffix}"


def gen_gift_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return "-".join("".join(random.choices(chars, k=4)) for _ in range(4))


def gen_sub_id() -> str:
    """Generate a subscription ID in UUID format (compatible with 3x-ui panel)."""
    return str(uuid.uuid4())


def escape_html(text: str) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---- Localised account / plan cards ----------------------------------------

def fmt_account_card(account: dict, lang: str = "en", traffic_data: Optional[dict] = None,
                     server_alias: str = "", plan_name: str = "",
                     currency: str = "toman") -> str:
    """Render an account status card.  Soft format — works in LTR & RTL."""
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
        lines.append(f"📦 {escape_html(plan_name)}")
    lines.append(f"💾 {fmt_gb(account.get('traffic_gb',0), lang)}")
    lines.append(f"📅 {fmt_remaining(account.get('expiry_time',0), lang)}")

    if traffic_data:
        up = traffic_data.get("up", 0)
        down = traffic_data.get("down", 0)
        total = traffic_data.get("total", 0)
        used = up + down
        if total > 0:
            remaining = max(0, total - used)
            pct = (used / total) * 100
            lines.append(f"📈 {fmt_bytes(used)} / {fmt_bytes(total)}")
            lines.append(f"<code>{fmt_progress_bar(pct)}</code>")
            lines.append(f"✅ {fmt_bytes(remaining)}")
        else:
            lines.append(f"📈 {fmt_bytes(used)} ({t('unlimited', lang)})")
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


def fmt_dashboard(stats: dict, lang: str = "en", currency: str = "toman") -> str:
    """Admin dashboard — English box table for alignment."""
    today = datetime.now().strftime("%Y-%m-%d")
    html = "<b>📊 Admin Dashboard</b>\n"
    html += f"<pre>┌──────────────────────────────┐\n"
    html += f"│ Date:          {today:<12} │\n"
    html += f"├──────────────────────────────┤\n"
    html += f"│ Total Users:   {stats.get('total_users',0):>12} │\n"
    html += f"│ Active Accts:  {stats.get('active_accounts',0):>12} │\n"
    html += f"│ Total Accts:   {stats.get('total_accounts',0):>12} │\n"
    html += f"│ Open Tickets:  {stats.get('open_tickets',0):>12} │\n"
    html += f"│ Servers Online:{stats.get('servers_online',0):>12} │\n"
    html += f"├──────────────────────────────┤\n"
    html += f"│ Revenue 30d:   {fmt_price(stats.get('revenue_30d',0),'en',currency):>18} │\n"
    html += f"│ Revenue Today: {fmt_price(stats.get('revenue_today',0),'en',currency):>18} │\n"
    html += f"│ Revenue All:   {fmt_price(stats.get('revenue_all',0),'en',currency):>18} │\n"
    html += f"└──────────────────────────────┘</pre>"
    return html


def fmt_server_health(server: dict, online_count: int = 0) -> str:
    status = "🟢 Healthy" if server["is_healthy"] else "🔴 Unhealthy"
    if not server["is_active"]:
        status = "⚪ Disabled"
    cap = server.get("capacity", 0)
    cap_s = f"{server.get('total_clients',0)}/{cap}" if cap > 0 else f"{server.get('total_clients',0)}/∞"
    html = f"<b>🖥 Server: {escape_html(server['alias'])}</b>\n"
    html += f"<pre>┌──────────────────────────────┐\n"
    html += f"│ Status:   {status:<19} │\n"
    html += f"│ URL:      {server['panel_url'][:19]:<19} │\n"
    html += f"│ Location: {escape_html(server.get('location') or '-'):<19} │\n"
    html += f"│ Priority: {str(server.get('priority',10)):<19} │\n"
    html += f"│ Clients:  {cap_s:<19} │\n"
    html += f"│ Online:   {str(online_count):<19} │\n"
    html += f"│ Traffic:  {fmt_bytes(server.get('total_traffic',0)):<19} │\n"
    if server.get("last_check"):
        dt = datetime.fromisoformat(server["last_check"])
        html += f"│ Check:    {dt.strftime('%Y-%m-%d %H:%M'):<19} │\n"
    if server.get("last_error"):
        html += f"│ Error:    {server['last_error'][:19]:<19} │\n"
    html += f"└──────────────────────────────┘</pre>"
    return html


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


# ============================================================================
# SECTION 7: KEYBOARDS
# ============================================================================

def kb_main_menu(is_admin: bool, lang: str = "en") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t("buy", lang), callback_data=MenuCB(action="buy").pack(), style="primary")
    kb.button(text=t("my_accounts", lang), callback_data=MenuCB(action="my_accounts").pack())
    kb.button(text=t("trial", lang), callback_data=MenuCB(action="trial").pack(), style="success")
    kb.button(text=t("charge_wallet_btn", lang), callback_data=MenuCB(action="charge_wallet").pack())
    kb.button(text=t("balance", lang), callback_data=MenuCB(action="balance").pack())
    kb.button(text=t("referral", lang), callback_data=MenuCB(action="referral").pack())
    kb.button(text=t("gift", lang), callback_data=MenuCB(action="gift").pack())
    kb.button(text=t("support", lang), callback_data=MenuCB(action="support").pack())
    kb.button(text=t("guide", lang), callback_data=MenuCB(action="guide").pack())
    kb.button(text=t("language", lang), callback_data=MenuCB(action="language").pack())
    if is_admin:
        kb.button(text=t("admin_panel", lang), callback_data=AdminCB(action="dashboard").pack(), style="danger")
    kb.adjust(2, 2, 2, 2, 2, 2, 1 if is_admin else 0)
    return kb.as_markup()


def kb_admin_menu(lang: str = "en") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Dashboard", callback_data=AdminCB(action="dashboard").pack(), style="primary")
    kb.button(text="🖥 Servers", callback_data=AdminCB(action="servers").pack())
    kb.button(text="📦 Plans", callback_data=AdminCB(action="plans").pack())
    kb.button(text="👥 Users", callback_data=AdminCB(action="users").pack())
    kb.button(text="💰 Finance", callback_data=AdminCB(action="finance").pack())
    kb.button(text="💰 Pending Pay", callback_data=AdminCB(action="pending_payments").pack())
    kb.button(text="🎫 Promos", callback_data=AdminCB(action="promos").pack())
    kb.button(text="🎁 Gift Codes", callback_data=AdminCB(action="gift_codes").pack())
    kb.button(text="💬 Tickets", callback_data=AdminCB(action="tickets").pack())
    kb.button(text="📣 Broadcast", callback_data=AdminCB(action="broadcast").pack())
    kb.button(text="🧹 Cleanup", callback_data=AdminCB(action="cleanup").pack())
    kb.button(text="⚙️ Settings", callback_data=AdminCB(action="settings").pack())
    kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack(), style="danger")
    kb.adjust(2, 2, 2, 2, 2, 2, 1)
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
    kb = InlineKeyboardBuilder()
    kb.button(text=t("confirm_pay", lang), callback_data=BuyCB(action="start", plan_id=plan_id, step="name").pack(), style="success")
    kb.button(text=t("apply_promo", lang), callback_data=BuyCB(action="promo", plan_id=plan_id, step="enter").pack(), style="primary")
    kb.button(text=t("back", lang), callback_data=MenuCB(action="buy").pack(), style="danger")
    kb.adjust(1, 2)
    return kb.as_markup()


def kb_account_details(email: str, is_active: bool, lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t("renew", lang), callback_data=AccountCB(action="renew", email=email).pack(), style="success")
    kb.button(text=t("topup_traffic", lang), callback_data=AccountCB(action="topup", email=email).pack(), style="primary")
    kb.button(text=t("traffic", lang), callback_data=AccountCB(action="traffic", email=email).pack())
    kb.button(text=t("get_link", lang), callback_data=AccountCB(action="links", email=email).pack())
    kb.button(text=t("qr", lang), callback_data=AccountCB(action="qr", email=email).pack())
    kb.button(text=t("set_label", lang), callback_data=AccountCB(action="label", email=email).pack())
    if is_active:
        kb.button(text=t("disable", lang), callback_data=AccountCB(action="disable", email=email).pack(), style="danger")
    else:
        kb.button(text=t("enable", lang), callback_data=AccountCB(action="enable", email=email).pack(), style="success")
    kb.button(text=t("delete", lang), callback_data=AccountCB(action="delete_ask", email=email).pack(), style="danger")
    kb.button(text=t("back", lang), callback_data=MenuCB(action="my_accounts").pack(), style="primary")
    kb.adjust(2, 2, 2, 2, 1)
    return kb.as_markup()


def kb_accounts_list(accounts: List[dict], lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for acc in accounts:
        status = "🟢" if acc["is_active"] else "🔴"
        trial = "🎁" if acc["is_trial"] else ""
        label = acc.get("label") or acc["email"][:20]
        kb.button(
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
        kb.button(text=f"{status} {srv['alias']}", callback_data=ServerCB(action="view", server_id=srv["id"]).pack())
    kb.button(text="➕ Add Server", callback_data=ServerCB(action="add").pack(), style="success")
    kb.button(text="🔄 Sync All", callback_data=ServerCB(action="sync_all").pack(), style="primary")
    kb.button(text="🔙 Admin", callback_data=AdminCB(action="main").pack(), style="danger")
    kb.adjust(1, 2, 1)
    return kb.as_markup()


def kb_server_view(server_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Stats", callback_data=ServerCB(action="stats", server_id=server_id).pack(), style="primary")
    kb.button(text="📡 Inbounds", callback_data=ServerCB(action="inbounds", server_id=server_id).pack())
    kb.button(text="🔄 Sync", callback_data=ServerCB(action="sync", server_id=server_id).pack())
    kb.button(text="✏️ Edit", callback_data=ServerCB(action="edit", server_id=server_id).pack())
    kb.button(text="📶 Test", callback_data=ServerCB(action="test", server_id=server_id).pack())
    kb.button(text="💾 Backup", callback_data=ServerCB(action="backup", server_id=server_id).pack())
    kb.button(text="🔄 Restart", callback_data=ServerCB(action="restart", server_id=server_id).pack(), style="danger")
    kb.button(text="🗑 Delete", callback_data=ServerCB(action="delete_ask", server_id=server_id).pack(), style="danger")
    kb.button(text="🔙 Servers", callback_data=AdminCB(action="servers").pack(), style="danger")
    kb.adjust(2, 2, 2, 2, 1)
    return kb.as_markup()


def kb_admin_plans(plans: List[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for plan in plans:
        status = "✅" if plan["is_active"] else "❌"
        kb.button(text=f"{status} {plan['name']}", callback_data=PlanCB(action="admin_view", plan_id=plan["id"]).pack())
    kb.button(text="➕ Add Plan", callback_data=PlanCB(action="add", plan_id=0).pack(), style="success")
    kb.button(text="🔙 Admin", callback_data=AdminCB(action="main").pack(), style="danger")
    kb.adjust(1, 2, 1)
    return kb.as_markup()


def kb_admin_plan_view(plan_id: int, is_active: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Edit", callback_data=PlanCB(action="edit", plan_id=plan_id).pack(), style="primary")
    kb.button(text="🔗 Inbounds", callback_data=PlanCB(action="inbounds", plan_id=plan_id).pack())
    if is_active:
        kb.button(text="❌ Disable", callback_data=PlanCB(action="toggle", plan_id=plan_id).pack(), style="danger")
    else:
        kb.button(text="✅ Enable", callback_data=PlanCB(action="toggle", plan_id=plan_id).pack(), style="success")
    kb.button(text="🗑 Delete", callback_data=PlanCB(action="delete", plan_id=plan_id).pack(), style="danger")
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
        kb.button(
            text=f"{mark} {server_alias} · {remark} ({proto})",
            callback_data=InboundCB(action="toggle", key=key, plan_id=plan_id).pack(),
        )
    kb.button(text="💾 Save", callback_data=InboundCB(action="save", plan_id=plan_id).pack(), style="success")
    kb.button(text="🔙 Back", callback_data=PlanCB(action="admin_view", plan_id=plan_id).pack(), style="danger")
    kb.adjust(1, 2)
    return kb.as_markup()


def kb_tickets(tickets: List[dict], back_cb: str = "admin") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for tk in tickets:
        status = "🟢" if tk["status"] == "open" else "🔴"
        kb.button(text=f"{status} #{tk['id']} - {tk['subject'][:20]}",
                  callback_data=TicketCB(action="view", ticket_id=tk["id"]).pack())
    kb.button(text="🔙 Back", callback_data=AdminCB(action=back_cb).pack(), style="danger")
    kb.adjust(1)
    return kb.as_markup()


def kb_ticket_view(ticket_id: int, is_admin: bool, lang: str = "en") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t("reply", lang), callback_data=TicketCB(action="reply", ticket_id=ticket_id).pack(), style="primary")
    if is_admin:
        kb.button(text=t("close", lang), callback_data=TicketCB(action="close", ticket_id=ticket_id).pack(), style="danger")
        kb.button(text="🔙 Tickets", callback_data=AdminCB(action="tickets").pack(), style="danger")
    else:
        kb.button(text=t("back", lang), callback_data=MenuCB(action="my_tickets").pack(), style="danger")
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
    kb.button(text="🎁 Trial", callback_data=AdminCB(action="broadcast_trial").pack())
    kb.button(text="🔙 Admin", callback_data=AdminCB(action="main").pack(), style="danger")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def kb_topup_packages(email: str, packages: List[int], lang: str, currency: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for gb in packages:
        # Price: 1 GB = 2000 Toman (configurable via setting topup_price_per_gb)
        kb.button(text=f"➕ {gb} GB", callback_data=TopupCB(action="buy", email=email, gb=gb).pack(), style="primary")
    kb.button(text=t("back", lang), callback_data=AccountCB(action="view", email=email).pack(), style="danger")
    kb.adjust(2)
    return kb.as_markup()


def kb_language(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🇬🇧 English", callback_data=LangCB(code="en").pack())
    kb.button(text="🇮🇷 فارسی", callback_data=LangCB(code="fa").pack())
    kb.button(text=t("back", lang), callback_data=MenuCB(action="main").pack(), style="danger")
    kb.adjust(2, 1)
    return kb.as_markup()


# ============================================================================
# SECTION 8: FSM STATES
# ============================================================================

class UserStates(StatesGroup):
    waiting_for_promo_code = State()
    waiting_for_gift_code = State()
    waiting_for_account_name = State()
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
            except Exception:
                pass
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
                            except Exception:
                                not_joined.append(ch)
                    if not_joined:
                        # Allow language selection callbacks to pass through
                        if isinstance(event, CallbackQuery):
                            cb_data = data.get("callback_data")
                            if isinstance(cb_data, LangCB):
                                return await handler(event, data)
                            if isinstance(cb_data, ForceJoinCB):
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
                                kb.button(text=f"📢 {username}", url=f"https://t.me/{username}")
                        kb.button(text=t("verify_join", lang), callback_data=ForceJoinCB(action="verify").pack(), style="success")
                        kb.button(text=t("language", lang), callback_data=MenuCB(action="language").pack())
                        kb.adjust(1, 2)
                        try:
                            if isinstance(event, Message):
                                await event.answer(t("force_join", lang, channels=channels_text),
                                                   reply_markup=kb.as_markup())
                            elif isinstance(event, CallbackQuery):
                                await event.message.edit_text(t("force_join", lang, channels=channels_text),
                                                              reply_markup=kb.as_markup())
                                await event.answer(t("force_join_failed", lang), show_alert=True)
                        except Exception:
                            pass
                        return

        data["db_user"] = db_user
        return await handler(event, data)


class AdminGuard:
    """Reject non-admins from admin-only routers."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.id not in ADMIN_IDS:
            if isinstance(event, CallbackQuery):
                await event.answer(t("admin_only", "en"), show_alert=True)
            elif isinstance(event, Message):
                await event.answer(t("admin_only", "en"))
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

    # ---------------------------------------------------------------- /start
    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext, db_user: dict):
        await state.clear()
        # Check if user needs to select language first
        if not db_user.get("language_selected", 0) and message.from_user.id not in ADMIN_IDS:
            await state.set_state(UserStates.waiting_for_language_on_start)
            await message.answer(
                "🌐 <b>Please select your language / لطفاً زبان خود را انتخاب کنید:</b>",
                reply_markup=kb_language("en"),
            )
            return
        lang = _lang(db_user)
        me = await bot.get_me()
        await message.answer(
            t("welcome", lang, bot_name=f"@{me.username}"),
            reply_markup=kb_main_menu(message.from_user.id in ADMIN_IDS, lang),
        )

    # ---------------------------------------------------------------- /help
    @router.message(Command("help"))
    async def cmd_help(message: Message, db_user: dict):
        lang = _lang(db_user)
        help_text = await db.get_setting(f"help_text_{lang}") or t("help_text", lang)
        await message.answer(help_text,
                             reply_markup=kb_main_menu(message.from_user.id in ADMIN_IDS, lang))

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
                             reply_markup=kb_main_menu(message.from_user.id in ADMIN_IDS, lang))

    # ---------------------------------------------------- main menu buttons
    @router.callback_query(MenuCB.filter(F.action == "main"))
    async def cb_main_menu(callback: CallbackQuery, state: FSMContext, db_user: dict):
        await state.clear()
        lang = _lang(db_user)
        await callback.message.edit_text(t("menu_main", lang),
                                         reply_markup=kb_main_menu(callback.from_user.id in ADMIN_IDS, lang))
        await callback.answer()

    @router.callback_query(MenuCB.filter(F.action == "cancel"))
    async def cb_cancel(callback: CallbackQuery, state: FSMContext, db_user: dict):
        await state.clear()
        lang = _lang(db_user)
        await callback.message.edit_text(t("action_cancelled", lang),
                                         reply_markup=kb_main_menu(callback.from_user.id in ADMIN_IDS, lang))
        await callback.answer()

    # ------------------------------------------------------- language picker
    @router.callback_query(MenuCB.filter(F.action == "language"))
    async def cb_language(callback: CallbackQuery, db_user: dict):
        await callback.message.edit_text(t("lang_title", _lang(db_user)),
                                         reply_markup=kb_language(_lang(db_user)))
        await callback.answer()

    @router.callback_query(LangCB.filter())
    async def cb_set_language(callback: CallbackQuery, callback_data: LangCB, state: FSMContext, db_user: dict):
        lang = L(callback_data.code)
        await db.update_user_language(callback.from_user.id, lang)
        await db.update_language_selected(callback.from_user.id, True)
        await state.clear()
        me = await bot.get_me()
        await callback.message.edit_text(
            t("lang_set", lang) + "\n\n" + t("welcome", lang, bot_name=f"@{me.username}"),
            reply_markup=kb_main_menu(callback.from_user.id in ADMIN_IDS, lang),
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
                reply_markup=kb_main_menu(callback.from_user.id in ADMIN_IDS, lang),
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
                except Exception:
                    not_joined.append(ch)
        if not_joined:
            await callback.answer(t("force_join_failed", lang), show_alert=True)
            return
        # All channels joined
        await callback.message.edit_text(
            t("force_join_success", lang) + "\n\n" + t("welcome", lang, bot_name=f"@{(await bot.get_me()).username}"),
            reply_markup=kb_main_menu(callback.from_user.id in ADMIN_IDS, lang),
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
    async def cb_plan_view(callback: CallbackQuery, callback_data: PlanCB, db_user: dict):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer(t("not_found", _lang(db_user)), show_alert=True)
            return
        lang = _lang(db_user)
        await callback.message.edit_text(fmt_plan_card(plan, lang, await _currency()),
                                         reply_markup=kb_plan_view(plan["id"], lang))
        await callback.answer()

    # Step 1 — ask for a custom account name
    @router.callback_query(BuyCB.filter(F.action == "start"))
    async def cb_buy_start(callback: CallbackQuery, callback_data: BuyCB, state: FSMContext, db_user: dict):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer(t("not_found", _lang(db_user)), show_alert=True)
            return
        lang = _lang(db_user)
        await state.set_state(UserStates.waiting_for_account_name)
        await state.update_data(plan_id=callback_data.plan_id)
        await callback.message.edit_text(t("ask_account_name", lang), reply_markup=kb_cancel(lang))
        await callback.answer()

    @router.message(UserStates.waiting_for_account_name)
    async def ms_account_name(message: Message, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        name = sanitize_name(message.text or "")
        if name is None:
            await message.answer(t("invalid_name", lang), reply_markup=kb_cancel(lang))
            return
        await state.update_data(account_name=name)
        data = await state.get_data()
        plan = await db.get_plan(data["plan_id"])
        currency = await _currency()
        balance = db_user.get("balance", 0)

        text = fmt_plan_card(plan, lang, currency)
        text += f"\n\n{t('review_purchase', lang)}\n"
        if name:
            text += f"🏷 {escape_html(name)}\n"
        text += f"{t('your_balance', lang, balance=fmt_price(balance, lang, currency))}\n"
        if balance >= plan["price"]:
            text += t("sufficient", lang)
        else:
            text += t("insufficient", lang, diff=fmt_price(plan["price"] - balance, lang, currency))

        await state.set_state(None)  # leave FSM but keep data
        await message.answer(text, reply_markup=kb_confirm_purchase(plan["id"], lang))

    # Promo-code entry during purchase
    @router.callback_query(BuyCB.filter(F.action == "promo"))
    async def cb_buy_promo(callback: CallbackQuery, state: FSMContext, callback_data: BuyCB, db_user: dict):
        lang = _lang(db_user)
        await state.set_state(UserStates.waiting_for_promo_code)
        await state.update_data(plan_id=callback_data.plan_id)
        await callback.message.edit_text(t("enter_promo", lang), reply_markup=kb_cancel(lang))
        await callback.answer()

    @router.message(UserStates.waiting_for_promo_code)
    async def ms_promo_code(message: Message, state: FSMContext, db_user: dict):
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
        await state.set_state(None)

        balance = db_user.get("balance", 0)
        text = fmt_plan_card(plan, lang, currency)
        text += f"\n\n{t('promo_applied', lang, code=code, discount=fmt_price(discount, lang, currency))}\n"
        text += f"💵 {fmt_price(final_price, lang, currency)}\n"
        text += t("your_balance", lang, balance=fmt_price(balance, lang, currency)) + "\n"
        if balance >= final_price:
            text += t("sufficient", lang)
        else:
            text += t("insufficient", lang, diff=fmt_price(final_price - balance, lang, currency))

        kb = InlineKeyboardBuilder()
        kb.button(text=t("confirm_pay", lang),
                  callback_data=BuyCB(action="confirm", plan_id=plan["id"], step="execute").pack(),
                  style="success")
        kb.button(text=t("cancel", lang), callback_data=MenuCB(action="buy").pack(), style="danger")
        kb.adjust(1, 1)
        await message.answer(text, reply_markup=kb.as_markup())

    # Execute the purchase
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
        expiry_time = (int((datetime.now() + timedelta(days=plan["duration_days"])).timestamp() * 1000)
                       if plan["duration_days"] > 0 else 0)

        sub_id = gen_sub_id()
        result = await api.create_client(
            panel_url=server["panel_url"], token=server["api_token"],
            email=email, inbound_ids=inbound_ids,
            total_gb=plan["traffic_gb"], expiry_time=expiry_time,
            limit_ip=plan.get("limit_ip", 0), tg_id=callback.from_user.id,
            sub_id=sub_id,
        )
        if not result.get("success"):
            await callback.message.edit_text(
                t("purchase_failed", lang, msg=result.get("msg", "error")),
                reply_markup=kb_back_to_menu(lang),
            )
            return

        links = await api.get_client_links(server["panel_url"], server["api_token"], email)

        await db.add_account(
            user_tg_id=callback.from_user.id, server_id=server["id"], email=email,
            sub_id=sub_id, plan_id=plan["id"], traffic_gb=plan["traffic_gb"],
            expiry_time=expiry_time, limit_ip=plan.get("limit_ip", 0),
            inbound_ids=json.dumps(inbound_ids), is_trial=False, label=account_name,
        )
        await db.update_user_balance(callback.from_user.id, final_price, add=False)
        if promo_code:
            await db.use_promo_code(promo_code)
        await db.add_transaction(
            user_tg_id=callback.from_user.id, amount=final_price, type_="purchase",
            description=f"Plan: {plan['name']}", account_email=email, plan_id=plan["id"],
        )
        await db.clear_traffic_alerts(email)
        await db.clear_expiry_reminders(email)

        # Referral bonus — fires only ONCE per user (on first paid purchase)
        if db_user.get("referred_by") and not db_user.get("referral_rewarded"):
            referrer_id = db_user["referred_by"]
            bonus_days = await db.get_setting_int("referral_bonus_days", 0)
            bonus_gb = await db.get_setting_int("referral_bonus_gb", 0)
            if bonus_days > 0 or bonus_gb > 0:
                ref_accounts = await db.get_user_accounts(referrer_id)
                active_refs = [a for a in ref_accounts if a["is_active"] and not a["is_trial"]]
                if active_refs:
                    ref_acc = active_refs[0]
                    ref_server = await db.get_server(ref_acc["server_id"])
                    if ref_server:
                        bonus_bytes = bonus_gb * GB if bonus_gb > 0 else 0
                        await api.bulk_adjust(
                            ref_server["panel_url"], ref_server["api_token"],
                            [ref_acc["email"]], add_days=bonus_days, add_bytes=bonus_bytes,
                        )
                        await db.add_referral_reward(
                            referrer_tg_id=referrer_id, referred_tg_id=callback.from_user.id,
                            account_email=ref_acc["email"], bonus_days=bonus_days, bonus_gb=bonus_gb,
                        )
                        try:
                            await bot.send_message(
                                referrer_id,
                                f"🎉 <b>Referral Bonus!</b>\n\n"
                                f"A friend just bought a plan thanks to you!\n"
                                f"🎁 +{bonus_days}d +{bonus_gb} GB applied to <code>{escape_html(ref_acc['email'])}</code>",
                            )
                        except Exception:
                            pass
            await db.mark_referral_rewarded(callback.from_user.id)

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
        kb.button(text=t("get_link", lang), callback_data=AccountCB(action="links", email=email).pack())
        kb.button(text=t("my_accounts", lang), callback_data=MenuCB(action="my_accounts").pack())
        kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack())
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
        await callback.message.edit_text(text, reply_markup=kb_account_details(account["email"], account["is_active"], lang))
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
        links = await api.get_client_links(server["panel_url"], server["api_token"], account["email"])
        text = f"{t('conn_links', lang)}\n<code>{escape_html(account['email'])}</code>\n\n"
        for i, link in enumerate(links, 1):
            text += f"<code>{escape_html(link)}</code>\n\n"
        sub_url = build_sub_url(server, account.get("sub_id", ""))
        if sub_url:
            text += f"{t('sub_url', lang)}\n<code>{escape_html(sub_url)}</code>"
        kb = InlineKeyboardBuilder()
        kb.button(text=t("back", lang), callback_data=AccountCB(action="view", email=account["email"]).pack(), style="primary")
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
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
        text = f"{t('guide_title', lang)}\n\n"
        if sub_url:
            text += f"🌐 {t('sub_url', lang)}\n<code>{escape_html(sub_url)}</code>\n\n"
        text += (
            "📱 <b>v2rayNG (Android)</b>\n"
            "1. Install v2rayNG\n2. Copy subscription URL → Subscription → add → paste → update\n3. Pick a server, tap V\n\n"
            "📱 <b>Streisand (iOS)</b>\n"
            "1. Install app\n2. Add subscription URL\n3. Select server → toggle on\n\n"
            "🖥 <b>v2rayN (Windows)</b>\n"
            "1. Install v2rayN\n2. Add subscription URL\n3. Select server → connect\n"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text=t("get_link", lang), callback_data=AccountCB(action="links", email=account["email"]).pack())
        kb.button(text=t("back", lang), callback_data=AccountCB(action="view", email=account["email"]).pack(), style="primary")
        kb.adjust(2)
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
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
        text += f"⬆️ {fmt_bytes(up)}\n⬇️ {fmt_bytes(down)}\n📊 {fmt_bytes(used)}"
        if total > 0:
            text += f" / {fmt_bytes(total)}\n<code>{fmt_progress_bar((used/total)*100)}</code>\n"
            text += f"✅ {fmt_bytes(total-used)}"
        else:
            text += f" ({t('unlimited', lang)})"
        online = await api.get_online_clients(server["panel_url"], server["api_token"])
        is_online = account["email"] in online if isinstance(online, list) else False
        text += f"\n🔵 {t('online', lang) if is_online else t('offline', lang)}"
        try:
            ips = await api.get_client_ips(server["panel_url"], server["api_token"], account["email"])
            text += f"\n🌐 {t('active_ips', lang)}: {fmt_num(len(ips), lang)}"
        except Exception:
            pass
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
                        f"{'📡 Subscription' if sub_url else '🔗 Connection link'}",
                reply_markup=kb.as_markup(),
            )
            try:
                await callback.message.delete()
            except Exception:
                pass
        else:
            await callback.message.answer(
                f"📱 <code>{escape_html(payload)}</code>",
                reply_markup=kb.as_markup(),
                disable_web_page_preview=True,
            )
            try:
                await callback.message.delete()
            except Exception:
                pass
        await callback.answer()

    # ---- set label -----------------------------------------------------
    @router.callback_query(AccountCB.filter(F.action == "label"))
    async def cb_account_label(callback: CallbackQuery, callback_data: AccountCB, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        await state.set_state(UserStates.waiting_for_label)
        await state.update_data(email=callback_data.email)
        await callback.message.edit_text(t("ask_label", lang), reply_markup=kb_cancel(lang))
        await callback.answer()

    @router.message(UserStates.waiting_for_label)
    async def ms_label(message: Message, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        text = (message.text or "").strip()[:30]
        data = await state.get_data()
        await state.clear()
        email = data.get("email")
        if not email:
            await message.answer(t("not_found", lang), reply_markup=kb_back_to_menu(lang))
            return
        if text == "-":
            await db.update_account(email, label=None)
            await message.answer(t("label_cleared", lang),
                                 reply_markup=kb_account_details(email, True, lang))
        else:
            await db.update_account(email, label=text)
            await message.answer(t("label_set", lang, label=escape_html(text)),
                                 reply_markup=kb_account_details(email, True, lang))

    # ---- renew ---------------------------------------------------------
    @router.callback_query(AccountCB.filter(F.action == "renew"))
    async def cb_account_renew(callback: CallbackQuery, callback_data: AccountCB, db_user: dict):
        lang = _lang(db_user)
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        plan = await db.get_plan(account["plan_id"]) if account.get("plan_id") else None
        if not plan:
            await callback.answer("Plan not found — buy a new plan.", show_alert=True)
            return
        currency = await _currency()
        balance = db_user.get("balance", 0)
        text = fmt_plan_card(plan, lang, currency)
        text += f"\n\n🔄 <code>{escape_html(account['email'])}</code>\n"
        text += t("your_balance", lang, balance=fmt_price(balance, lang, currency)) + "\n"
        if balance < plan["price"]:
            text += t("insufficient", lang, diff=fmt_price(plan["price"] - balance, lang, currency))
        kb = InlineKeyboardBuilder()
        if balance >= plan["price"]:
            kb.button(text=t("renew", lang),
                      callback_data=AccountCB(action="renew_confirm", email=account["email"]).pack(),
                      style="success")
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
            await callback.answer("Plan not found.", show_alert=True)
            return
        if db_user.get("balance", 0) < plan["price"]:
            await callback.answer(t("insufficient", lang, diff=""), show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        add_bytes = plan["traffic_gb"] * GB if plan["traffic_gb"] > 0 else 0
        result = await api.bulk_adjust(
            server["panel_url"], server["api_token"], [account["email"]],
            add_days=plan["duration_days"], add_bytes=add_bytes,
        )
        if not result.get("success"):
            await callback.answer(t("renew_failed", lang, msg=result.get("msg", "")), show_alert=True)
            return
        now_ms = int(datetime.now().timestamp() * 1000)
        base = account["expiry_time"] if account["expiry_time"] and account["expiry_time"] > now_ms else now_ms
        new_expiry = base + plan["duration_days"] * MS_PER_DAY if plan["duration_days"] > 0 else 0
        new_traffic = (account["traffic_gb"] + plan["traffic_gb"]) if account["traffic_gb"] and plan["traffic_gb"] else (account["traffic_gb"] or plan["traffic_gb"])
        await db.update_account(account["email"], expiry_time=new_expiry, traffic_gb=new_traffic,
                                is_active=True, renewed_at=datetime.now().isoformat())
        await db.clear_traffic_alerts(account["email"])
        await db.clear_expiry_reminders(account["email"])
        await db.update_user_balance(callback.from_user.id, plan["price"], add=False)
        await db.add_transaction(
            user_tg_id=callback.from_user.id, amount=plan["price"], type_="renewal",
            description=f"Renewed: {plan['name']}", account_email=account["email"], plan_id=plan["id"],
        )
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
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
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
        account = await db.get_account(callback_data.email)
        if not account or account["user_tg_id"] != callback.from_user.id:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        gb = callback_data.gb
        price_per_gb = await db.get_setting_int("topup_price_per_gb", 2000)
        price = gb * price_per_gb
        if db_user.get("balance", 0) < price:
            await callback.answer(t("insufficient", lang, diff=fmt_price(price - db_user.get("balance", 0), lang, await _currency())), show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        if not server:
            await callback.answer(t("not_found", lang), show_alert=True)
            return
        result = await api.bulk_adjust(
            server["panel_url"], server["api_token"], [account["email"]],
            add_bytes=gb * GB,
        )
        if not result.get("success"):
            await callback.answer(f"Failed: {result.get('msg')}", show_alert=True)
            return
        new_traffic = (account["traffic_gb"] or 0) + gb
        await db.update_account(account["email"], traffic_gb=new_traffic, is_active=True)
        await db.clear_traffic_alerts(account["email"])
        await db.update_user_balance(callback.from_user.id, price, add=False)
        await db.add_transaction(
            user_tg_id=callback.from_user.id, amount=price, type_="topup",
            description=f"Top-up +{gb}GB", account_email=account["email"],
        )
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
                reply_markup=kb_account_details(account["email"], False, lang),
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
                reply_markup=kb_account_details(account["email"], True, lang),
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
            await api.delete_client(server["panel_url"], server["api_token"], account["email"])
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
        days = await db.get_setting_int("trial_days", 3)
        gb = await db.get_setting_int("trial_gb", 5)
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
        await callback.message.edit_text(t("creating_account", lang))
        days = await db.get_setting_int("trial_days", 3)
        gb = await db.get_setting_int("trial_gb", 5)
        limit_ip = await db.get_setting_int("trial_limit_ip", 1)
        trial_inbounds = await db.get_setting_json("trial_inbounds", [])
        # Restrict to servers referenced by trial_inbounds (if any)
        allowed = list({int(x.split("_", 1)[0]) for x in trial_inbounds if "_" in x})
        server = await lb.select_best_server(allowed or None)
        if not server:
            await callback.message.edit_text(t("no_servers", lang), reply_markup=kb_back_to_menu(lang))
            return
        inbound_ids = await lb.select_trial_inbounds(server, trial_inbounds)
        if not inbound_ids:
            await callback.message.edit_text(t("no_inbounds", lang), reply_markup=kb_back_to_menu(lang))
            return
        email = gen_email(callback.from_user.id, "trial")
        expiry_time = int((datetime.now() + timedelta(days=days)).timestamp() * 1000)
        sub_id = gen_sub_id()
        result = await api.create_client(
            panel_url=server["panel_url"], token=server["api_token"],
            email=email, inbound_ids=inbound_ids, total_gb=gb,
            expiry_time=expiry_time, limit_ip=limit_ip, tg_id=callback.from_user.id,
            sub_id=sub_id,
        )
        if not result.get("success"):
            await callback.message.edit_text(t("trial_failed", lang, msg=result.get("msg", "")),
                                             reply_markup=kb_back_to_menu(lang))
            return
        links = await api.get_client_links(server["panel_url"], server["api_token"], email)
        await db.add_account(
            user_tg_id=callback.from_user.id, server_id=server["id"], email=email, sub_id=sub_id,
            plan_id=None, traffic_gb=gb, expiry_time=expiry_time, limit_ip=limit_ip,
            inbound_ids=json.dumps(inbound_ids), is_trial=True, label="Trial",
        )
        await db.add_transaction(
            user_tg_id=callback.from_user.id, amount=0, type_="trial",
            description=f"Free Trial {days}d/{gb}GB", account_email=email,
        )
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
        kb.button(text=t("get_link", lang), callback_data=AccountCB(action="links", email=email).pack())
        kb.button(text=t("buy", lang), callback_data=MenuCB(action="buy").pack(), style="success")
        kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack())
        kb.adjust(2, 2)
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
        await callback.answer("✅")

    # ====================================================== BALANCE
    @router.callback_query(MenuCB.filter(F.action == "balance"))
    async def cb_balance(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        currency = await _currency()
        balance = db_user.get("balance", 0)
        text = f"{t('balance_title', lang)}\n\n💵 {fmt_price(balance, lang, currency)}\n"
        text += f"🛒 {fmt_num(db_user.get('total_orders', 0), lang)}\n"
        text += f"💸 {fmt_price(db_user.get('total_spent', 0), lang, currency)}\n"
        txs = await db.get_user_transactions(callback.from_user.id, limit=5)
        if txs:
            text += f"\n{t('recent_tx', lang)}\n"
            for tx in txs:
                icon = {"purchase": "🛒", "renewal": "🔄", "topup": "➕", "deposit": "💰",
                        "gift_balance": "🎁", "gift_plan": "🎁", "trial": "🆓",
                        "admin_adjust": "⚙️"}.get(tx["type"], "•")
                text += f"{icon} {tx['created_at'][:16]} · {fmt_price(tx['amount'], lang, currency)} · {escape_html((tx.get('description') or '')[:24])}\n"
        text += f"\n{t('topup_hint', lang)}"
        kb = InlineKeyboardBuilder()
        kb.button(text=t("charge_wallet_btn", lang), callback_data=MenuCB(action="charge_wallet").pack(), style="primary")
        kb.button(text=t("gift", lang), callback_data=MenuCB(action="gift").pack())
        kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack())
        kb.adjust(2, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
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
            kb.button(
                text=fmt_price(amt, lang, currency),
                callback_data=PaymentCB(action="select_amount", amount=amt).pack(),
            )
        kb.button(text=t("custom_amount", lang), callback_data=PaymentCB(action="custom_amount").pack(), style="primary")
        kb.button(text=t("gift", lang), callback_data=MenuCB(action="gift").pack())
        kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack(), style="danger")
        kb.adjust(2, 1, 2)
        await callback.message.edit_text(t("choose_amount", lang), reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(PaymentCB.filter(F.action == "select_amount"))
    async def cb_payment_select_amount(callback: CallbackQuery, callback_data: PaymentCB, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        original_amount = callback_data.amount
        suffix = random.randint(100, 999)
        unique_amount = original_amount + suffix
        await state.update_data(original_amount=original_amount, unique_amount=unique_amount)
        card_number = await db.get_setting("payment_card_number", "-")
        card_holder = await db.get_setting("payment_card_holder", "-")
        await callback.message.edit_text(
            t("payment_info", lang, card_number=card_number, card_holder=card_holder, unique_amount=fmt_price(unique_amount, lang, await _currency())),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t("send_receipt", lang), callback_data=PaymentCB(action="send_receipt", amount=0).pack())],
                [InlineKeyboardButton(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack())],
            ]),
        )
        await callback.answer()

    @router.callback_query(PaymentCB.filter(F.action == "custom_amount"))
    async def cb_payment_custom_amount(callback: CallbackQuery, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        await state.set_state(UserStates.waiting_for_custom_amount)
        min_amount = 10000
        await callback.message.edit_text(
            t("enter_custom_amount", lang, min=fmt_price(min_amount, lang, await _currency())),
            reply_markup=kb_cancel(lang),
        )
        await callback.answer()

    @router.message(UserStates.waiting_for_custom_amount)
    async def ms_custom_amount(message: Message, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        try:
            amount = int(message.text.strip())
        except ValueError:
            await message.answer(t("invalid_number", lang), reply_markup=kb_cancel(lang))
            return
        if amount < 10000:
            await message.answer(t("invalid_number", lang), reply_markup=kb_cancel(lang))
            return
        suffix = random.randint(100, 999)
        unique_amount = amount + suffix
        await state.update_data(original_amount=amount, unique_amount=unique_amount)
        card_number = await db.get_setting("payment_card_number", "-")
        card_holder = await db.get_setting("payment_card_holder", "-")
        await message.answer(
            t("payment_info", lang, card_number=card_number, card_holder=card_holder, unique_amount=fmt_price(unique_amount, lang, await _currency())),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t("send_receipt", lang), callback_data=PaymentCB(action="send_receipt", amount=0).pack())],
                [InlineKeyboardButton(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack())],
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
        await callback.answer()

    @router.message(UserStates.waiting_for_receipt)
    async def ms_receipt(message: Message, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        data = await state.get_data()
        await state.clear()
        original_amount = data.get("original_amount", 0)
        unique_amount = data.get("unique_amount", 0)
        receipt_text = (message.text or "").strip()
        payment_id = await db.add_payment(
            user_tg_id=message.from_user.id,
            amount=original_amount,
            unique_amount=unique_amount,
            receipt_type="text",
            receipt_text=receipt_text,
        )
        await message.answer(
            t("receipt_received", lang, amount=fmt_price(unique_amount, lang, await _currency())),
            reply_markup=kb_back_to_menu(lang),
        )

    # ====================================================== REFERRAL
    @router.callback_query(MenuCB.filter(F.action == "referral"))
    async def cb_referral(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        stats = await db.get_referral_stats(callback.from_user.id)
        bonus_days = await db.get_setting_int("referral_bonus_days", 0)
        bonus_gb = await db.get_setting_int("referral_bonus_gb", 0)
        me = await bot.get_me()
        ref_link = f"https://t.me/{me.username}?start={db_user.get('referral_code','')}"
        text = (
            f"{t('referral_title', lang)}\n\n{t('referral_desc', lang)}\n\n"
            f"👥 {fmt_num(stats['total_referrals'], lang)}\n"
            f"✅ {fmt_num(stats['completed_referrals'], lang)}\n"
            f"🎁 +{bonus_days}d +{bonus_gb} GB\n\n"
            f"{t('your_link', lang)}\n<code>{ref_link}</code>"
        )
        await callback.message.edit_text(text, reply_markup=kb_back_to_menu(lang))
        await callback.answer()

    # ====================================================== GIFT CODE
    @router.callback_query(MenuCB.filter(F.action == "gift"))
    async def cb_gift(callback: CallbackQuery, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        await state.set_state(UserStates.waiting_for_gift_code)
        await callback.message.edit_text(t("enter_gift", lang), reply_markup=kb_cancel(lang))
        await callback.answer()

    @router.message(UserStates.waiting_for_gift_code)
    async def ms_gift_code(message: Message, state: FSMContext, db_user: dict):
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
        await state.clear()
        currency = await _currency()

        if gift["type"] == "balance":
            amount = float(gift["value"])
            await db.update_user_balance(message.from_user.id, amount, add=True)
            await db.add_transaction(message.from_user.id, amount, "gift_balance", f"Gift: {code}")
            await db.use_gift_code(code, message.from_user.id)
            await message.answer(
                t("gift_balance_ok", lang, amount=fmt_price(amount, lang, currency)),
                reply_markup=kb_back_to_menu(lang),
            )
        elif gift["type"] == "plan":
            plan = await db.get_plan(int(gift["value"]))
            if not plan:
                await message.answer("Plan not found.", reply_markup=kb_back_to_menu(lang))
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
            expiry_time = (int((datetime.now() + timedelta(days=plan["duration_days"])).timestamp() * 1000)
                           if plan["duration_days"] > 0 else 0)
            sub_id = gen_sub_id()
            res = await api.create_client(
                panel_url=server["panel_url"], token=server["api_token"], email=email,
                inbound_ids=inbound_ids, total_gb=plan["traffic_gb"], expiry_time=expiry_time,
                tg_id=message.from_user.id, sub_id=sub_id,
            )
            if not res.get("success"):
                await message.answer(f"❌ {res.get('msg')}", reply_markup=kb_back_to_menu(lang))
                return
            links = await api.get_client_links(server["panel_url"], server["api_token"], email)
            await db.add_account(
                user_tg_id=message.from_user.id, server_id=server["id"], email=email, sub_id=sub_id,
                plan_id=plan["id"], traffic_gb=plan["traffic_gb"], expiry_time=expiry_time,
                limit_ip=plan.get("limit_ip", 0), inbound_ids=json.dumps(inbound_ids), label="Gift",
            )
            await db.add_transaction(message.from_user.id, 0, "gift_plan", f"Gift: {code}", account_email=email, plan_id=plan["id"])
            await db.use_gift_code(code, message.from_user.id)
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
            kb.button(text=t("get_link", lang), callback_data=AccountCB(action="links", email=email).pack())
            kb.button(text=t("back_menu", lang), callback_data=MenuCB(action="main").pack())
            kb.adjust(2, 1)
            await message.answer(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)

    # ====================================================== SUPPORT
    @router.callback_query(MenuCB.filter(F.action == "support"))
    async def cb_support(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        kb = InlineKeyboardBuilder()
        kb.button(text=t("new_ticket", lang), callback_data=MenuCB(action="new_ticket").pack(), style="success")
        kb.button(text=t("my_tickets", lang), callback_data=MenuCB(action="my_tickets").pack())
        kb.button(text=t("back", lang), callback_data=MenuCB(action="main").pack(), style="danger")
        kb.adjust(2, 1)
        await callback.message.edit_text(f"{t('support_title', lang)}\n\n{t('support_desc', lang)}",
                                         reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(MenuCB.filter(F.action == "new_ticket"))
    async def cb_new_ticket(callback: CallbackQuery, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        await state.set_state(UserStates.waiting_for_ticket_subject)
        await callback.message.edit_text(t("ask_subject", lang), reply_markup=kb_cancel(lang))
        await callback.answer()

    @router.message(UserStates.waiting_for_ticket_subject)
    async def ms_ticket_subject(message: Message, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        subject = (message.text or "").strip()[:100]
        await state.update_data(subject=subject)
        await state.set_state(UserStates.waiting_for_ticket_message)
        await message.answer(t("ask_message", lang, subject=escape_html(subject)), reply_markup=kb_cancel(lang))

    @router.message(UserStates.waiting_for_ticket_message)
    async def ms_ticket_message(message: Message, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        msg_text = (message.text or "").strip()[:2000]
        data = await state.get_data()
        subject = data.get("subject", "No subject")
        await state.clear()
        ticket_id = await db.create_ticket(message.from_user.id, subject)
        await db.add_ticket_message(ticket_id, "user", msg_text)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🎫 <b>New Ticket #{ticket_id}</b>\n"
                    f"👤 {message.from_user.full_name} (<code>{message.from_user.id}</code>)\n"
                    f"📝 {escape_html(subject)}\n💬 {escape_html(msg_text[:500])}",
                    reply_markup=kb_ticket_view(ticket_id, True),
                )
            except Exception:
                pass
        await message.answer(t("ticket_created", lang, id=ticket_id, subject=escape_html(subject)),
                             reply_markup=kb_back_to_menu(lang))

    @router.callback_query(MenuCB.filter(F.action == "my_tickets"))
    async def cb_my_tickets(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        tickets = await db.get_user_tickets(callback.from_user.id)
        if not tickets:
            await callback.message.edit_text(t("no_tickets", lang), reply_markup=kb_back_to_menu(lang))
            await callback.answer()
            return
        kb = InlineKeyboardBuilder()
        for tk in tickets:
            status = "🟢" if tk["status"] == "open" else "🔴"
            kb.button(text=f"{status} #{tk['id']} - {tk['subject'][:25]}",
                      callback_data=TicketCB(action="view", ticket_id=tk["id"]).pack())
        kb.button(text=t("back", lang), callback_data=MenuCB(action="support").pack(), style="danger")
        kb.adjust(1)
        await callback.message.edit_text(t("no_tickets", lang).split('\n')[0], reply_markup=kb.as_markup())
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
        text = (f"🎫 <b>Ticket #{ticket['id']}</b>\n"
                f"📝 {escape_html(ticket['subject'])}\n"
                f"📊 {'🟢 Open' if ticket['status']=='open' else '🔴 Closed'}\n"
                f"📅 {ticket['created_at'][:16]}\n\n")
        for m in messages:
            who = "👤" if m["sender"] == "user" else "🛡"
            text += f"\n<b>{who} {m['created_at'][:16]}</b>\n{escape_html(m['message'][:600])}\n"
        await callback.message.edit_text(text, reply_markup=kb_ticket_view(ticket["id"], is_admin, lang))
        await callback.answer()

    @router.callback_query(TicketCB.filter(F.action == "reply"))
    async def cb_ticket_reply(callback: CallbackQuery, callback_data: TicketCB,
                              state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        await state.set_state(UserStates.waiting_for_ticket_reply)
        await state.update_data(ticket_id=callback_data.ticket_id)
        await callback.message.edit_text(t("ask_reply", lang), reply_markup=kb_cancel(lang))
        await callback.answer()

    @router.message(UserStates.waiting_for_ticket_reply)
    async def ms_ticket_reply(message: Message, state: FSMContext, db_user: dict):
        lang = _lang(db_user)
        msg_text = (message.text or "").strip()[:2000]
        data = await state.get_data()
        ticket_id = data.get("ticket_id")
        await state.clear()
        ticket = await db.get_ticket(ticket_id)
        if not ticket:
            await message.answer(t("not_found", lang), reply_markup=kb_back_to_menu(lang))
            return
        is_admin = message.from_user.id in ADMIN_IDS
        sender = "admin" if is_admin else "user"
        await db.add_ticket_message(ticket_id, sender, msg_text)
        if is_admin:
            try:
                await bot.send_message(
                    ticket["user_tg_id"],
                    f"💬 <b>Admin replied — Ticket #{ticket_id}</b>\n📝 {escape_html(ticket['subject'])}\n💬 {escape_html(msg_text[:500])}",
                    reply_markup=kb_ticket_view(ticket_id, False, lang),
                )
            except Exception:
                pass
            await message.answer(t("reply_sent_admin", lang), reply_markup=kb_back_to_menu(lang))
        else:
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"💬 <b>User replied — Ticket #{ticket_id}</b>\n👤 {message.from_user.full_name}\n💬 {escape_html(msg_text[:500])}",
                        reply_markup=kb_ticket_view(ticket_id, True),
                    )
                except Exception:
                    pass
            await message.answer(t("reply_sent_user", lang), reply_markup=kb_back_to_menu(lang))

    # ====================================================== GUIDE
    @router.callback_query(MenuCB.filter(F.action == "guide"))
    async def cb_guide(callback: CallbackQuery, db_user: dict):
        lang = _lang(db_user)
        text = (
            f"{t('guide_title', lang)}\n\n"
            "<b>🤖 Android (v2rayNG)</b>\n"
            "1. Install v2rayNG\n2. Copy subscription URL\n3. Subscription → add → paste → update\n4. Pick a server, tap V\n\n"
            "<b>📱 iOS (Streisand / V2Box)</b>\n"
            "1. Install Streisand\n2. Settings → Subscriptions → add URL\n3. Pick server → connect\n\n"
            "<b>💻 Windows (v2rayN)</b>\n"
            "1. Install v2rayN\n2. Subscription → add → paste URL → update\n3. Right-click server → enable\n\n"
            "<b>🍎 macOS (V2RayU / Foxray)</b>\n"
            "1. Install app\n2. Add subscription URL\n3. Select server → toggle on\n\n"
            f"<b>🌐</b> {t('sub_url', lang)}"
        )
        await callback.message.edit_text(text, reply_markup=kb_back_to_menu(lang))
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
    guard = AdminGuard()

    @router.message.middleware()
    async def _msg_mw(handler, event, data):
        return await guard(handler, event, data)

    @router.callback_query.middleware()
    async def _cb_mw(handler, event, data):
        return await guard(handler, event, data)

    async def _currency() -> str:
        return await db.get_setting("currency", DEFAULT_CURRENCY) or DEFAULT_CURRENCY

    # ------------------------------------------------------------- /admin
    @router.message(Command("admin"))
    async def cmd_admin(message: Message):
        await message.answer("⚙️ <b>Admin Panel</b>", reply_markup=kb_admin_menu())

    @router.callback_query(AdminCB.filter(F.action == "main"))
    async def cb_admin_main(callback: CallbackQuery):
        await callback.message.edit_text("⚙️ <b>Admin Panel</b>", reply_markup=kb_admin_menu())
        await callback.answer()

    # ------------------------------------------------------- dashboard
    @router.callback_query(AdminCB.filter(F.action == "dashboard"))
    async def cb_dashboard(callback: CallbackQuery):
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
        text = fmt_dashboard(stats, currency=await _currency())
        if rev["top_plans"]:
            text += "\n<b>🏆 Top Plans (30d)</b>\n<pre>"
            text += f"{'Plan':<15} | {'#':>3} | {'Revenue':>10}\n"
            text += "─" * 35 + "\n"
            for p in rev["top_plans"]:
                name = (p.get("name") or "—")[:15]
                text += f"{name:<15} | {p['cnt']:>3} | {fmt_price(p.get('revenue') or 0, 'en', await _currency()):>10}\n"
            text += "</pre>"
        await callback.message.edit_text(text, reply_markup=kb_admin_menu())
        await callback.answer()

    # ====================================================== SERVERS
    @router.callback_query(AdminCB.filter(F.action == "servers"))
    async def cb_servers(callback: CallbackQuery):
        servers = await db.get_servers()
        await callback.message.edit_text("🖥 <b>Servers</b>", reply_markup=kb_servers(servers))
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "add"))
    async def cb_server_add(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_server_alias)
        await callback.message.edit_text(
            "➕ <b>Add Server</b>\n\nEnter alias (e.g. <code>DE-Frankfurt</code>):",
            reply_markup=kb_cancel("en"),
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_server_alias)
    async def ms_srv_alias(message: Message, state: FSMContext):
        await state.update_data(alias=message.text.strip())
        await state.set_state(AdminStates.waiting_for_server_url)
        await message.answer("🔗 Enter panel URL (e.g. <code>https://1.2.3.4:2053</code>):",
                             reply_markup=kb_cancel("en"))

    @router.message(AdminStates.waiting_for_server_url)
    async def ms_srv_url(message: Message, state: FSMContext):
        url = (message.text or "").strip().rstrip("/")
        if not url.startswith("http"):
            await message.answer("❌ URL must start with http:// or https://", reply_markup=kb_cancel("en"))
            return
        await state.update_data(panel_url=url)
        await state.set_state(AdminStates.waiting_for_server_token)
        await message.answer("🔐 Enter API token:", reply_markup=kb_cancel("en"))

    @router.message(AdminStates.waiting_for_server_token)
    async def ms_srv_token(message: Message, state: FSMContext):
        token = (message.text or "").strip()
        data = await state.get_data()
        await state.set_state(AdminStates.waiting_for_server_capacity)
        await state.update_data(token=token)
        await message.answer(
            "🔢 Enter max client capacity (0 = unlimited):",
            reply_markup=kb_cancel("en"),
        )

    @router.message(AdminStates.waiting_for_server_capacity)
    async def ms_srv_capacity(message: Message, state: FSMContext):
        try:
            cap = int((message.text or "0").strip())
        except ValueError:
            await message.answer("❌ Enter a number:", reply_markup=kb_cancel("en"))
            return
        await state.update_data(capacity=cap)
        await state.set_state(AdminStates.waiting_for_server_priority)
        await message.answer("⭐ Enter priority (lower = preferred, default 10):",
                             reply_markup=kb_cancel("en"))

    @router.message(AdminStates.waiting_for_server_priority)
    async def ms_srv_priority(message: Message, state: FSMContext):
        try:
            pri = int((message.text or "10").strip())
        except ValueError:
            await message.answer("❌ Enter a number:", reply_markup=kb_cancel("en"))
            return
        await state.update_data(priority=pri)
        await state.set_state(AdminStates.waiting_for_server_location)
        await message.answer("🌍 Enter location (e.g. <code>Germany</code>) or <code>-</code> for none:",
                             reply_markup=kb_cancel("en"))

    @router.message(AdminStates.waiting_for_server_location)
    async def ms_srv_location(message: Message, state: FSMContext):
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
            await callback.answer("Not found", show_alert=True)
            return
        online = await api.get_online_clients(srv["panel_url"], srv["api_token"])
        await callback.message.edit_text(fmt_server_health(srv, len(online) if isinstance(online, list) else 0),
                                         reply_markup=kb_server_view(srv["id"]))
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "sync"))
    async def cb_srv_sync(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer("Not found", show_alert=True)
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
            await callback.answer("Not found", show_alert=True)
            return
        ok, msg = await api.test_panel_connection(srv["panel_url"], srv["api_token"])
        await db.update_server_health(srv["id"], ok, "" if ok else msg)
        await callback.answer("✅ OK" if ok else f"❌ {msg}", show_alert=True)

    @router.callback_query(ServerCB.filter(F.action == "restart"))
    async def cb_srv_restart(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer("Not found", show_alert=True)
            return
        r = await api.restart_panel(srv["panel_url"], srv["api_token"])
        await callback.answer("✅ Restart initiated" if r.get("success") else f"❌ {r.get('msg')}", show_alert=True)

    @router.callback_query(ServerCB.filter(F.action == "backup"))
    async def cb_srv_backup(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer("Not found", show_alert=True)
            return
        r = await api.backup_to_telegram(srv["panel_url"], srv["api_token"])
        await callback.answer("✅ Backup sent" if r.get("success") else f"❌ {r.get('msg')}", show_alert=True)

    @router.callback_query(ServerCB.filter(F.action == "stats"))
    async def cb_srv_stats(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer("Not found", show_alert=True)
            return
        data = await api.get_clients_paged(srv["panel_url"], srv["api_token"], page=1, page_size=25)
        summary = data.get("summary", {})
        items = data.get("items", [])
        total = data.get("total", 0)
        online = await api.get_online_clients(srv["panel_url"], srv["api_token"])
        on_count = len(online) if isinstance(online, list) else 0
        text = f"📊 <b>{escape_html(srv['alias'])}</b>\n<pre>"
        text += f"┌──────────────────────────────┐\n"
        text += f"│ Total:    {total:>19} │\n"
        text += f"│ Active:   {summary.get('active',0):>19} │\n"
        text += f"│ Online:   {on_count:>19} │\n"
        text += f"│ Depleted: {len(summary.get('depleted',[])):>19} │\n"
        text += f"│ Expiring: {len(summary.get('expiring',[])):>19} │\n"
        text += f"│ Deactive: {len(summary.get('deactive',[])):>19} │\n"
        text += f"└──────────────────────────────┘</pre>"
        if items:
            text += "\n<pre>"
            text += f"{'Email':<26} | {'Status':<8} | {'Expiry':<12}\n"
            text += "─" * 52 + "\n"
            for it in items[:10]:
                em = (it.get("email") or "—")[:26]
                st = "Active" if it.get("enable") else "Off"
                ex = fmt_ts(it.get("expiryTime", 0))[:12]
                text += f"{em:<26} | {st:<8} | {ex:<12}\n"
            text += "</pre>"
        await callback.message.edit_text(text, reply_markup=kb_server_view(srv["id"]))
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "inbounds"))
    async def cb_srv_inbounds(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer("Not found", show_alert=True)
            return
        inbounds = await db.get_inbounds(srv["id"])
        text = f"📡 <b>Inbounds — {escape_html(srv['alias'])}</b>\n<pre>"
        text += f"{'ID':>4} | {'Proto':<7} | {'Port':<6} | {'Remark':<20}\n"
        text += "─" * 45 + "\n"
        for ib in inbounds:
            text += f"{ib['inbound_id']:>4} | {ib.get('protocol','?'):<7} | {str(ib.get('port','')):<6} | {(ib.get('remark') or '')[:20]:<20}\n"
        text += "</pre>"
        await callback.message.edit_text(text, reply_markup=kb_server_view(srv["id"]))
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "edit"))
    async def cb_srv_edit(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer("Not found", show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        kb.button(text="✏️ Alias", callback_data=AdminCB(action="srv_edit_field", data=f"{srv['id']}_alias").pack())
        kb.button(text="⭐ Priority", callback_data=AdminCB(action="srv_edit_field", data=f"{srv['id']}_priority").pack())
        kb.button(text="🔢 Capacity", callback_data=AdminCB(action="srv_edit_field", data=f"{srv['id']}_capacity").pack())
        kb.button(text="🌍 Location", callback_data=AdminCB(action="srv_edit_field", data=f"{srv['id']}_location").pack())
        kb.button(text="🔗 Sub URI", callback_data=AdminCB(action="srv_edit_field", data=f"{srv['id']}_sub_uri").pack())
        kb.button(text="🔑 Token", callback_data=AdminCB(action="srv_edit_field", data=f"{srv['id']}_api_token").pack())
        toggle = "⚪ Activate" if not srv["is_active"] else "🔴 Disable"
        kb.button(text=toggle, callback_data=ServerCB(action="toggle", server_id=srv["id"]).pack(), style="primary")
        kb.button(text="🔙 Back", callback_data=ServerCB(action="view", server_id=srv["id"]).pack(), style="danger")
        kb.adjust(2, 2, 2, 1, 1)
        await callback.message.edit_text("✏️ <b>Edit server — pick a field</b>", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "toggle"))
    async def cb_srv_toggle(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer("Not found", show_alert=True)
            return
        await db.toggle_server(srv["id"], not srv["is_active"])
        await callback.answer("✅ Toggled", show_alert=True)
        servers = await db.get_servers()
        await callback.message.edit_text("🖥 <b>Servers</b>", reply_markup=kb_servers(servers))

    @router.callback_query(AdminCB.filter(F.action == "srv_edit_field"))
    async def cb_srv_edit_field(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        server_id, field = callback_data.data.split("_", 1)
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="server", server_id=int(server_id), field=field)
        labels = {"alias": "alias", "priority": "priority (number)", "capacity": "capacity (number)",
                  "location": "location", "sub_uri": "subscription base URI", "api_token": "API token"}
        await callback.message.edit_text(
            f"✏️ Enter new <b>{labels.get(field, field)}</b>:",
            reply_markup=kb_cancel("en"),
        )
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "delete_ask"))
    async def cb_srv_delete_ask(callback: CallbackQuery, callback_data: ServerCB):
        srv = await db.get_server(callback_data.server_id)
        if not srv:
            await callback.answer("Not found", show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        kb.button(text="🗑 Confirm Delete", callback_data=ServerCB(action="delete", server_id=srv["id"]).pack(), style="danger")
        kb.button(text="🔙 Back", callback_data=ServerCB(action="view", server_id=srv["id"]).pack(), style="primary")
        await callback.message.edit_text(
            f"🗑 <b>Delete server {escape_html(srv['alias'])}?</b>\n\nAccounts on this server will remain in DB but become unmanageable.",
            reply_markup=kb.as_markup(),
        )
        await callback.answer()

    @router.callback_query(ServerCB.filter(F.action == "delete"))
    async def cb_srv_delete(callback: CallbackQuery, callback_data: ServerCB):
        await db.delete_server(callback_data.server_id)
        servers = await db.get_servers()
        await callback.message.edit_text("✅ Server deleted.", reply_markup=kb_servers(servers))
        await callback.answer("Deleted")

    # ====================================================== PLANS
    @router.callback_query(AdminCB.filter(F.action == "plans"))
    async def cb_plans(callback: CallbackQuery):
        plans = await db.get_plans(active_only=False)
        await callback.message.edit_text("📦 <b>Plans</b>", reply_markup=kb_admin_plans(plans))
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "add"))
    async def cb_plan_add(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_plan_name)
        await callback.message.edit_text("➕ <b>Add Plan</b>\n\nEnter name:", reply_markup=kb_cancel("en"))
        await callback.answer()

    @router.message(AdminStates.waiting_for_plan_name)
    async def ms_plan_name(message: Message, state: FSMContext):
        await state.update_data(name=message.text.strip())
        await state.set_state(AdminStates.waiting_for_plan_desc)
        await message.answer("📝 Description (or <code>-</code> for none):", reply_markup=kb_cancel("en"))

    @router.message(AdminStates.waiting_for_plan_desc)
    async def ms_plan_desc(message: Message, state: FSMContext):
        desc = message.text.strip()
        if desc == "-":
            desc = ""
        await state.update_data(description=desc)
        await state.set_state(AdminStates.waiting_for_plan_traffic)
        await message.answer("💾 Traffic in GB (0 = unlimited):", reply_markup=kb_cancel("en"))

    @router.message(AdminStates.waiting_for_plan_traffic)
    async def ms_plan_traffic(message: Message, state: FSMContext):
        try:
            gb = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Number please:", reply_markup=kb_cancel("en"))
            return
        await state.update_data(traffic_gb=gb)
        await state.set_state(AdminStates.waiting_for_plan_duration)
        await message.answer("📅 Duration in days (0 = never):", reply_markup=kb_cancel("en"))

    @router.message(AdminStates.waiting_for_plan_duration)
    async def ms_plan_duration(message: Message, state: FSMContext):
        try:
            days = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Number please:", reply_markup=kb_cancel("en"))
            return
        await state.update_data(duration_days=days)
        await state.set_state(AdminStates.waiting_for_plan_price)
        cur = await _currency()
        unit = "Toman" if cur == "toman" else "USD"
        await message.answer(f"💵 Price in {unit}:", reply_markup=kb_cancel("en"))

    @router.message(AdminStates.waiting_for_plan_price)
    async def ms_plan_price(message: Message, state: FSMContext):
        try:
            price = float(message.text.strip())
        except ValueError:
            await message.answer("❌ Number please:", reply_markup=kb_cancel("en"))
            return
        await state.update_data(price=price)
        await state.set_state(AdminStates.waiting_for_plan_limit_ip)
        await message.answer("🔢 Max simultaneous IPs (0 = unlimited):", reply_markup=kb_cancel("en"))

    @router.message(AdminStates.waiting_for_plan_limit_ip)
    async def ms_plan_limit_ip(message: Message, state: FSMContext):
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
            kb.button(text=f"— {escape_html(srv['alias'])} —", callback_data="noop_0")
            inbounds = await db.get_inbounds(srv["id"], enabled_only=True)
            for ib in inbounds:
                key = f"{srv['id']}_{ib['inbound_id']}"
                mark = "✅" if key in selected else "⬜"
                proto = ib.get("protocol", "?")
                remark = ib.get("remark") or f"id{ib['inbound_id']}"
                kb.button(
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
            await callback.answer("Plan not found", show_alert=True)
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
        await callback.message.edit_text(
            f"🔗 <b>Inbounds for {escape_html(plan['name'])}</b>\nSelected: {len(selected)}",
            reply_markup=await _inbound_kb(plan["id"]),
        )
        await callback.answer()

    @router.callback_query(InboundCB.filter(F.action == "clear"))
    async def cb_ib_clear(callback: CallbackQuery, callback_data: InboundCB):
        await db.update_plan(callback_data.plan_id, inbound_ids="[]")
        await callback.message.edit_text("Cleared.", reply_markup=await _inbound_kb(callback_data.plan_id))
        await callback.answer()

    @router.callback_query(InboundCB.filter(F.action == "save"))
    async def cb_ib_save(callback: CallbackQuery, callback_data: InboundCB):
        plan = await db.get_plan(callback_data.plan_id)
        await callback.message.edit_text(
            f"✅ Saved.\n\n{fmt_plan_card(plan, 'en', await _currency())}",
            reply_markup=kb_admin_plan_view(plan["id"], plan["is_active"]),
        )
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "admin_view"))
    async def cb_plan_admin_view(callback: CallbackQuery, callback_data: PlanCB):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer("Not found", show_alert=True)
            return
        text = fmt_plan_card(plan, "en", await _currency())
        inb = plan.get("inbound_ids") or "[]"
        try:
            n = len(json.loads(inb))
        except Exception:
            n = 0
        text += f"\n\n🔗 Inbounds: {n}\n📊 Status: {'✅' if plan['is_active'] else '❌'}"
        await callback.message.edit_text(text, reply_markup=kb_admin_plan_view(plan["id"], plan["is_active"]))
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "inbounds"))
    async def cb_plan_inbounds(callback: CallbackQuery, callback_data: PlanCB):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer("Not found", show_alert=True)
            return
        await callback.message.edit_text(
            f"🔗 <b>Inbounds — {escape_html(plan['name'])}</b>\nToggle the inbounds this plan can use:",
            reply_markup=await _inbound_kb(plan["id"]),
        )
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "toggle"))
    async def cb_plan_toggle(callback: CallbackQuery, callback_data: PlanCB):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer("Not found", show_alert=True)
            return
        await db.toggle_plan(plan["id"], not plan["is_active"])
        plans = await db.get_plans(active_only=False)
        await callback.message.edit_text("📦 <b>Plans</b>", reply_markup=kb_admin_plans(plans))
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "delete"))
    async def cb_plan_delete(callback: CallbackQuery, callback_data: PlanCB):
        await db.delete_plan(callback_data.plan_id)
        plans = await db.get_plans(active_only=False)
        await callback.message.edit_text("✅ Deleted.", reply_markup=kb_admin_plans(plans))
        await callback.answer()

    @router.callback_query(PlanCB.filter(F.action == "edit"))
    async def cb_plan_edit(callback: CallbackQuery, callback_data: PlanCB):
        plan = await db.get_plan(callback_data.plan_id)
        if not plan:
            await callback.answer("Not found", show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        for field, label in [("name", "📛 Name"), ("description", "📝 Description"),
                             ("traffic_gb", "💾 Traffic (GB)"), ("duration_days", "📅 Duration (days)"),
                             ["price", "💵 Price"], ["limit_ip", "🔢 Max IPs"]]:
            kb.button(text=label, callback_data=AdminCB(action="plan_edit_field", data=f"{plan['id']}_{field}").pack())
        kb.button(text="🔙 Back", callback_data=PlanCB(action="admin_view", plan_id=plan["id"]).pack(), style="danger")
        kb.adjust(2, 2, 2, 1)
        await callback.message.edit_text("✏️ <b>Edit plan — pick a field</b>", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "plan_edit_field"))
    async def cb_plan_edit_field(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        plan_id, field = callback_data.data.split("_", 1)
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="plan", plan_id=int(plan_id), field=field)
        await callback.message.edit_text(f"✏️ Enter new <b>{field}</b>:", reply_markup=kb_cancel("en"))
        await callback.answer()

    # Generic value handler for ALL admin FSM edits (server / plan / setting_int / acc_extend)
    @router.message(AdminStates.setting_edit_value)
    async def ms_setting_edit(message: Message, state: FSMContext):
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
            await message.answer(fmt_server_health(srv, len(online) if isinstance(online, list) else 0),
                                 reply_markup=kb_server_view(server_id))

        # ---- plan field edit ----
        elif edit_type == "plan":
            plan_id = data["plan_id"]
            if field in ("traffic_gb", "duration_days", "limit_ip"):
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
                await db.update_plan(plan_id, **{field: val})
            else:
                await db.update_plan(plan_id, **{field: raw if raw != "-" else None})
            await state.clear()
            plan = await db.get_plan(plan_id)
            await message.answer(fmt_plan_card(plan, "en", await _currency()),
                                 reply_markup=kb_admin_plan_view(plan_id, plan["is_active"]))

        # ---- bot setting (integer) ----
        elif edit_type == "setting_int":
            try:
                val = int(raw)
            except ValueError:
                await message.answer("❌ Number please:", reply_markup=kb_cancel("en"))
                return
            await state.clear()
            await db.set_setting(data["key"], str(val))
            await message.answer(f"✅ {data.get('label','Setting')} = {val}", reply_markup=kb_admin_menu())

        # ---- admin extends a user account (days GB) ----
        elif edit_type == "acc_extend":
            parts = raw.split()
            days = int(parts[0]) if len(parts) > 0 and parts[0].lstrip("-").isdigit() else 0
            gb = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 0
            if days == 0 and gb == 0:
                await message.answer("❌ Send e.g. <code>30 10</code> (days GB):", reply_markup=kb_cancel("en"))
                return
            await state.clear()
            email = data["email"]
            tg_id = data["tg_id"]
            account = await db.get_account(email)
            server = await db.get_server(account["server_id"]) if account else None
            if not server:
                await message.answer("❌ Server not found.", reply_markup=kb_admin_menu())
                return
            add_bytes = gb * GB if gb > 0 else 0
            r = await api.bulk_adjust(server["panel_url"], server["api_token"], [email],
                                      add_days=days, add_bytes=add_bytes)
            if not r.get("success"):
                await message.answer(f"❌ {r.get('msg')}", reply_markup=kb_admin_menu())
                return
            now_ms = int(datetime.now().timestamp() * 1000)
            base = account["expiry_time"] if account["expiry_time"] and account["expiry_time"] > now_ms else now_ms
            new_exp = base + days * MS_PER_DAY if days > 0 else account["expiry_time"]
            new_traffic = (account["traffic_gb"] + gb) if account["traffic_gb"] and gb else (account["traffic_gb"] or gb)
            await db.update_account(email, expiry_time=new_exp, traffic_gb=new_traffic, is_active=True)
            await db.clear_traffic_alerts(email)
            await db.clear_expiry_reminders(email)
            await message.answer(
                f"✅ Extended <code>{escape_html(email)}</code>\n+{days}d +{gb}GB",
                reply_markup=kb_admin_menu(),
            )
        else:
            await state.clear()
            await message.answer("⚠️ Unknown edit type.", reply_markup=kb_admin_menu())

    # ====================================================== USERS
    @router.callback_query(AdminCB.filter(F.action == "users"))
    async def cb_users(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_user_search)
        await callback.message.edit_text(
            "👥 <b>Users</b>\n\nSearch by Telegram ID, username or email.\n"
            "Send <code>all</code> to list recent users.",
            reply_markup=kb_cancel("en"),
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_user_search)
    async def ms_user_search(message: Message, state: FSMContext):
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
        text = f"👥 <b>Results ({len(users)})</b>\n<pre>"
        text += f"{'TG ID':<12} | {'Username':<18} | {'Balance':>10} | {'Orders':>6}\n"
        text += "─" * 54 + "\n"
        for u in users[:20]:
            text += f"{u['tg_id']:<12} | {(u.get('username') or '-')[:18]:<18} | {fmt_price(u.get('balance',0),'en',cur):>10} | {u.get('total_orders',0):>6}\n"
        text += "</pre>"
        kb = InlineKeyboardBuilder()
        for u in users[:10]:
            kb.button(text=f"👤 {u['tg_id']} · {(u.get('username') or '-')[:15]}",
                      callback_data=AdminCB(action="user_view", data=str(u["tg_id"])).pack())
        kb.button(text="🔙 Admin", callback_data=AdminCB(action="main").pack(), style="danger")
        kb.adjust(1)
        await message.answer(text, reply_markup=kb.as_markup())

    @router.callback_query(AdminCB.filter(F.action == "user_view"))
    async def cb_user_view(callback: CallbackQuery, callback_data: AdminCB):
        tg_id = int(callback_data.data)
        user = await db.get_user(tg_id)
        if not user:
            await callback.answer("Not found", show_alert=True)
            return
        accounts = await db.get_user_accounts(tg_id)
        cur = await _currency()
        text = "👤 <b>User</b>\n<pre>"
        text += f"┌──────────────────────────────┐\n"
        text += f"│ TG ID:    {user['tg_id']:<18} │\n"
        text += f"│ Username: {(user.get('username') or '-')[:18]:<18} │\n"
        text += f"│ Balance:  {fmt_price(user.get('balance',0),'en',cur):>18} │\n"
        text += f"│ Orders:   {user.get('total_orders',0):<18} │\n"
        text += f"│ Spent:    {fmt_price(user.get('total_spent',0),'en',cur):>18} │\n"
        text += f"│ Banned:   {'Yes' if user.get('is_banned') else 'No':<18} │\n"
        text += f"│ Joined:   {user['created_at'][:18]:<18} │\n"
        text += f"└──────────────────────────────┘</pre>"
        if accounts:
            text += f"\n📱 <b>Accounts ({len(accounts)})</b>\n"
            for a in accounts:
                st = "🟢" if a["is_active"] else "🔴"
                tr = " 🎁" if a["is_trial"] else ""
                lbl = a.get("label") or ""
                text += f"{st}<code>{escape_html(a['email'])}</code>{tr} {escape_html(lbl)}\n"
        kb = InlineKeyboardBuilder()
        if user.get("is_banned"):
            kb.button(text="✅ Unban", callback_data=AdminCB(action="unban", data=str(tg_id)).pack(), style="success")
        else:
            kb.button(text="🚫 Ban", callback_data=AdminCB(action="ban", data=str(tg_id)).pack(), style="danger")
        kb.button(text="💰 Add Balance", callback_data=AdminCB(action="add_balance", data=str(tg_id)).pack(), style="primary")
        kb.button(text="➖ Deduct", callback_data=AdminCB(action="deduct_balance", data=str(tg_id)).pack(), style="danger")
        kb.button(text="➕ Create Account", callback_data=AdminCB(action="create_account", data=str(tg_id)).pack(), style="success")
        # per-account actions
        for a in accounts[:6]:
            label_acc = a.get("label") or a["email"][:16]
            kb.button(text=f"⚙️ {label_acc}",
                      callback_data=AdminCB(action="user_account", data=f"{tg_id}_{a['email']}").pack())
        kb.button(text="🔙 Search", callback_data=AdminCB(action="users").pack(), style="danger")
        kb.adjust(2, 2, 1, 1, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "ban"))
    async def cb_ban(callback: CallbackQuery, callback_data: AdminCB):
        await db.ban_user(int(callback_data.data), True)
        await callback.answer("✅ Banned", show_alert=True)

    @router.callback_query(AdminCB.filter(F.action == "unban"))
    async def cb_unban(callback: CallbackQuery, callback_data: AdminCB):
        await db.ban_user(int(callback_data.data), False)
        await callback.answer("✅ Unbanned", show_alert=True)

    @router.callback_query(AdminCB.filter(F.action == "add_balance"))
    async def cb_add_balance(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        tg_id = int(callback_data.data)
        await state.set_state(AdminStates.waiting_for_add_balance)
        await state.update_data(tg_id=tg_id)
        await callback.message.edit_text(
            f"💰 <b>Add balance</b> to user <code>{tg_id}</code>\n\nEnter amount in {await _currency()}:",
            reply_markup=kb_cancel("en"),
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_add_balance)
    async def ms_add_balance(message: Message, state: FSMContext):
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
        try:
            await bot.send_message(
                tg_id,
                f"💰 <b>Balance updated</b>\n\n➕ {fmt_price(abs(amount), 'en' if (user or {}).get('language','en')=='en' else 'fa', cur)} added.\n"
                f"💳 New balance: {fmt_price((user or {}).get('balance',0), 'en' if (user or {}).get('language','en')=='en' else 'fa', cur)}",
            )
        except Exception:
            pass
        await message.answer(
            f"✅ Added {fmt_price(abs(amount), 'en', cur)} to <code>{tg_id}</code>",
            reply_markup=kb_admin_menu(),
        )

    @router.callback_query(AdminCB.filter(F.action == "deduct_balance"))
    async def cb_deduct_balance(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        tg_id = int(callback_data.data)
        await state.set_state(AdminStates.waiting_for_deduct_balance)
        await state.update_data(tg_id=tg_id)
        await callback.message.edit_text(
            f"➖ <b>Deduct balance</b> from <code>{tg_id}</code>\n\nEnter amount:",
            reply_markup=kb_cancel("en"),
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_deduct_balance)
    async def ms_deduct_balance(message: Message, state: FSMContext):
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
            await callback.answer("No plans available.", show_alert=True)
            return
        await state.set_state(AdminStates.waiting_for_admin_account_create)
        await state.update_data(tg_id=tg_id)
        kb = InlineKeyboardBuilder()
        for p in plans:
            kb.button(text=f"{p['name']} — {fmt_price(p['price'], 'en', await _currency())}",
                      callback_data=AdminCB(action="create_account_pick", data=f"{tg_id}_{p['id']}").pack())
        kb.button(text="❌ Cancel", callback_data=AdminCB(action="user_view", data=str(tg_id)).pack(), style="danger")
        kb.adjust(1)
        await callback.message.edit_text("➕ <b>Create account for user</b> — pick a plan:", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "create_account_pick"))
    async def cb_create_account_pick(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        tg_id_str, plan_id_str = callback_data.data.split("_", 1)
        tg_id, plan_id = int(tg_id_str), int(plan_id_str)
        plan = await db.get_plan(plan_id)
        if not plan:
            await callback.answer("Plan not found", show_alert=True)
            return
        await callback.message.edit_text("⏳ Creating account...")
        server = await lb.select_best_server(lb.plan_server_ids(plan) or None)
        if not server:
            await callback.message.edit_text("❌ No servers available.", reply_markup=kb_admin_menu())
            return
        inbound_ids = await lb.select_inbounds_for_plan(server, plan)
        if not inbound_ids:
            await callback.message.edit_text("❌ No inbounds available.", reply_markup=kb_admin_menu())
            return
        email = gen_email(tg_id, "admin")
        expiry = int((datetime.now() + timedelta(days=plan["duration_days"])).timestamp() * 1000) if plan["duration_days"] > 0 else 0
        sub_id = gen_sub_id()
        res = await api.create_client(
            panel_url=server["panel_url"], token=server["api_token"], email=email,
            inbound_ids=inbound_ids, total_gb=plan["traffic_gb"], expiry_time=expiry,
            limit_ip=plan.get("limit_ip", 0), tg_id=tg_id, sub_id=sub_id,
        )
        if not res.get("success"):
            await callback.message.edit_text(f"❌ {res.get('msg')}", reply_markup=kb_admin_menu())
            return
        await db.add_account(
            user_tg_id=tg_id, server_id=server["id"], email=email, sub_id=sub_id,
            plan_id=plan["id"], traffic_gb=plan["traffic_gb"], expiry_time=expiry,
            limit_ip=plan.get("limit_ip", 0), inbound_ids=json.dumps(inbound_ids), label="Admin",
        )
        await db.add_transaction(tg_id, 0, "admin_adjust", f"Admin created account ({plan['name']})",
                                 account_email=email, plan_id=plan["id"], admin_id=callback.from_user.id)
        try:
            await bot.send_message(
                tg_id,
                f"🎁 <b>Admin created a VPN account for you!</b>\n\n📦 {escape_html(plan['name'])}\n📧 <code>{escape_html(email)}</code>",
            )
        except Exception:
            pass
        await callback.message.edit_text(
            f"✅ Account created for <code>{tg_id}</code>\n📧 <code>{escape_html(email)}</code>",
            reply_markup=kb_admin_menu(),
        )
        await callback.answer()

    # ---- admin manages a specific user account ------------------------
    @router.callback_query(AdminCB.filter(F.action == "user_account"))
    async def cb_user_account(callback: CallbackQuery, callback_data: AdminCB):
        tg_id_str, email = callback_data.data.split("_", 1)
        tg_id = int(tg_id_str)
        account = await db.get_account(email)
        if not account or account["user_tg_id"] != tg_id:
            await callback.answer("Not found", show_alert=True)
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
        kb.button(text="🔙 User", callback_data=AdminCB(action="user_view", data=str(tg_id)).pack(), style="danger")
        kb.adjust(2, 2, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    async def _parse_email(callback_data: AdminCB) -> Tuple[int, str]:
        tg_id_str, email = callback_data.data.split("_", 1)
        return int(tg_id_str), email

    @router.callback_query(AdminCB.filter(F.action == "acc_extend"))
    async def cb_acc_extend(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        tg_id, email = await _parse_email(callback_data)
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="acc_extend", tg_id=tg_id, email=email)
        await callback.message.edit_text(
            "➕ Extend account.\nSend days and GB, e.g. <code>30 10</code> (30 days, 10 GB). Use 0 to skip either.",
            reply_markup=kb_cancel("en"),
        )
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "acc_reset"))
    async def cb_acc_reset(callback: CallbackQuery, callback_data: AdminCB):
        tg_id, email = await _parse_email(callback_data)
        account = await db.get_account(email)
        if not account:
            await callback.answer("Not found", show_alert=True)
            return
        server = await db.get_server(account["server_id"])
        r = await api.reset_client_traffic(server["panel_url"], server["api_token"], email)
        await callback.answer("✅ Reset" if r.get("success") else f"❌ {r.get('msg')}", show_alert=True)

    @router.callback_query(AdminCB.filter(F.action == "acc_disable"))
    async def cb_acc_disable(callback: CallbackQuery, callback_data: AdminCB):
        tg_id, email = await _parse_email(callback_data)
        account = await db.get_account(email)
        server = await db.get_server(account["server_id"]) if account else None
        if server:
            await api.disable_client(server["panel_url"], server["api_token"], email)
            await db.update_account(email, is_active=False)
        await callback.answer("✅ Disabled", show_alert=True)

    @router.callback_query(AdminCB.filter(F.action == "acc_enable"))
    async def cb_acc_enable(callback: CallbackQuery, callback_data: AdminCB):
        tg_id, email = await _parse_email(callback_data)
        account = await db.get_account(email)
        server = await db.get_server(account["server_id"]) if account else None
        if server:
            await api.enable_client(server["panel_url"], server["api_token"], email)
            await db.update_account(email, is_active=True)
        await callback.answer("✅ Enabled", show_alert=True)

    @router.callback_query(AdminCB.filter(F.action == "acc_delete"))
    async def cb_acc_delete(callback: CallbackQuery, callback_data: AdminCB):
        tg_id, email = await _parse_email(callback_data)
        account = await db.get_account(email)
        server = await db.get_server(account["server_id"]) if account else None
        if server:
            await api.delete_client(server["panel_url"], server["api_token"], email)
        await db.delete_account(email)
        await callback.answer("✅ Deleted", show_alert=True)
        # refresh user view
        await callback.message.edit_text("✅ Account deleted.",
                                         reply_markup=InlineKeyboardBuilder()
                                         .button(text="🔙 User", callback_data=AdminCB(action="user_view", data=str(tg_id)).pack(), style="primary")
                                         .as_markup())

    # ====================================================== FINANCE
    @router.callback_query(AdminCB.filter(F.action == "finance"))
    async def cb_finance(callback: CallbackQuery):
        rev = await db.get_revenue_stats(days=30)
        cur = await _currency()
        text = "💰 <b>Finance (30d)</b>\n<pre>"
        text += f"┌──────────────────────────────┐\n"
        text += f"│ Revenue 30d:  {fmt_price(rev['total_revenue'],'en',cur):>16} │\n"
        text += f"│ Today:        {fmt_price(rev['today_revenue'],'en',cur):>16} │\n"
        text += f"│ All-time:     {fmt_price(rev['all_time_revenue'],'en',cur):>16} │\n"
        text += f"│ Transactions: {rev['transaction_count']:>16} │\n"
        avg = rev["total_revenue"] / max(rev["transaction_count"], 1)
        text += f"│ Avg order:    {fmt_price(avg,'en',cur):>16} │\n"
        text += f"└──────────────────────────────┘</pre>"
        if rev["top_plans"]:
            text += "\n<b>🏆 Top Plans</b>\n<pre>"
            text += f"{'Plan':<15} | {'#':>4} | {'Revenue':>12}\n" + "─" * 36 + "\n"
            for p in rev["top_plans"]:
                text += f"{(p.get('name') or '—')[:15]:<15} | {p['cnt']:>4} | {fmt_price(p.get('revenue') or 0,'en',cur):>12}\n"
            text += "</pre>"
        await callback.message.edit_text(text, reply_markup=kb_admin_menu())
        await callback.answer()

    # ====================================================== PROMOS
    @router.callback_query(AdminCB.filter(F.action == "promos"))
    async def cb_promos(callback: CallbackQuery):
        promos = await db.get_promo_codes()
        cur = await _currency()
        text = "🎫 <b>Promo Codes</b>\n\n"
        if promos:
            text += "<pre>"
            text += f"{'Code':<14} | {'Disc':>6} | {'Used':>5} | {'Max':>5}\n" + "─" * 38 + "\n"
            for p in promos:
                disc = f"{p['discount_percent']}%" if p["discount_percent"] > 0 else fmt_price(p["discount_amount"], "en", cur)
                text += f"{p['code']:<14} | {disc:>6} | {p['used_count']:>5} | {p['max_uses'] or '∞':>5}\n"
            text += "</pre>"
        else:
            text += "No promo codes yet."
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Create", callback_data=AdminCB(action="create_promo").pack(), style="success")
        kb.button(text="🔙 Admin", callback_data=AdminCB(action="main").pack(), style="danger")
        kb.adjust(1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "create_promo"))
    async def cb_create_promo(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_promo_code_str)
        await callback.message.edit_text("🎫 <b>Create promo</b>\n\nCode (or <code>-</code> for random):",
                                         reply_markup=kb_cancel("en"))
        await callback.answer()

    @router.message(AdminStates.waiting_for_promo_code_str)
    async def ms_promo_code(message: Message, state: FSMContext):
        code = (message.text or "").strip().upper()
        if code == "-":
            code = gen_gift_code().replace("-", "")[:10]
        await state.update_data(code=code)
        await state.set_state(AdminStates.waiting_for_promo_discount)
        await message.answer("💰 Discount percent (0-100):", reply_markup=kb_cancel("en"))

    @router.message(AdminStates.waiting_for_promo_discount)
    async def ms_promo_disc(message: Message, state: FSMContext):
        try:
            d = int((message.text or "").strip())
        except ValueError:
            await message.answer("❌ Number:", reply_markup=kb_cancel("en"))
            return
        await state.update_data(disc=d)
        await state.set_state(AdminStates.waiting_for_promo_max_uses)
        await message.answer("🔢 Max uses (0 = unlimited):", reply_markup=kb_cancel("en"))

    @router.message(AdminStates.waiting_for_promo_max_uses)
    async def ms_promo_max(message: Message, state: FSMContext):
        try:
            mu = int((message.text or "").strip())
        except ValueError:
            await message.answer("❌ Number:", reply_markup=kb_cancel("en"))
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
    async def cb_gift_codes(callback: CallbackQuery):
        gifts = await db.get_gift_codes(unused_only=False)
        cur = await _currency()
        text = "🎁 <b>Gift Codes</b>\n\n"
        if gifts:
            text += "<pre>"
            text += f"{'Code':<20} | {'Type':<8} | {'Value':<10} | {'Used':<4}\n" + "─" * 50 + "\n"
            for g in gifts[:25]:
                val = g["value"][:10]
                text += f"{g['code']:<20} | {g['type']:<8} | {val:<10} | {'Yes' if g['is_used'] else 'No':<4}\n"
            text += "</pre>"
        else:
            text += "No gift codes yet."
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Gift (Balance)", callback_data=AdminCB(action="create_gift_balance").pack(), style="success")
        kb.button(text="➕ Gift (Plan)", callback_data=AdminCB(action="create_gift_plan").pack(), style="primary")
        kb.button(text="🔙 Admin", callback_data=AdminCB(action="main").pack(), style="danger")
        kb.adjust(1, 1, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "create_gift_balance"))
    async def cb_gift_balance(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_gift_amount)
        await callback.message.edit_text(
            f"💰 <b>Balance gift</b>\n\nEnter amount in {await _currency()}:",
            reply_markup=kb_cancel("en"),
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_gift_amount)
    async def ms_gift_amount(message: Message, state: FSMContext):
        try:
            amount = float((message.text or "").strip())
        except ValueError:
            await message.answer("❌ Number:", reply_markup=kb_cancel("en"))
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
            await callback.answer("No plans", show_alert=True)
            return
        await state.set_state(AdminStates.waiting_for_gift_plan)
        kb = InlineKeyboardBuilder()
        for p in plans:
            kb.button(text=p["name"],
                      callback_data=AdminCB(action="gift_plan_pick", data=str(p["id"])).pack())
        kb.button(text="❌ Cancel", callback_data=AdminCB(action="gift_codes").pack(), style="danger")
        kb.adjust(1)
        await callback.message.edit_text("🎁 Pick a plan for the gift code:", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "gift_plan_pick"))
    async def cb_gift_plan_pick(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        await state.clear()
        plan_id = int(callback_data.data)
        plan = await db.get_plan(plan_id)
        if not plan:
            await callback.answer("Plan not found", show_alert=True)
            return
        code = gen_gift_code()
        await db.create_gift_code(code, "plan", str(plan_id), plan_id=plan_id, created_by=callback.from_user.id)
        await callback.message.edit_text(
            f"✅ <b>Gift code</b>\n🎫 <code>{code}</code>\n📦 {escape_html(plan['name'])}",
            reply_markup=kb_admin_menu(),
        )
        await callback.answer()

    # ====================================================== TICKETS
    @router.callback_query(AdminCB.filter(F.action == "tickets"))
    async def cb_tickets(callback: CallbackQuery):
        tickets = await db.get_open_tickets()
        if not tickets:
            await callback.message.edit_text("💬 <b>Tickets</b>\n\n✅ No open tickets.", reply_markup=kb_admin_menu())
            await callback.answer()
            return
        await callback.message.edit_text(f"💬 <b>Open Tickets ({len(tickets)})</b>",
                                         reply_markup=kb_tickets(tickets))
        await callback.answer()

    @router.callback_query(TicketCB.filter(F.action == "close"))
    async def cb_ticket_close(callback: CallbackQuery, callback_data: TicketCB):
        ticket = await db.get_ticket(callback_data.ticket_id)
        if not ticket:
            await callback.answer("Not found", show_alert=True)
            return
        await db.close_ticket(callback_data.ticket_id)
        try:
            await bot.send_message(ticket["user_tg_id"],
                                   f"🔒 <b>Ticket #{callback_data.ticket_id} closed.</b>")
        except Exception:
            pass
        await callback.answer("✅ Closed", show_alert=True)
        tickets = await db.get_open_tickets()
        if tickets:
            await callback.message.edit_text(f"💬 <b>Open Tickets ({len(tickets)})</b>",
                                             reply_markup=kb_tickets(tickets))
        else:
            await callback.message.edit_text("✅ All tickets resolved.", reply_markup=kb_admin_menu())

    # ====================================================== BROADCAST
    @router.callback_query(AdminCB.filter(F.action == "broadcast"))
    async def cb_broadcast(callback: CallbackQuery):
        await callback.message.edit_text("📣 <b>Broadcast</b> — choose target:",
                                         reply_markup=kb_broadcast_targets())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action.startswith("broadcast_")))
    async def cb_broadcast_target(callback: CallbackQuery, callback_data: AdminCB, state: FSMContext):
        target = callback_data.action.replace("broadcast_", "")
        await state.set_state(AdminStates.waiting_for_broadcast_message)
        await state.update_data(target=target)
        await callback.message.edit_text(
            f"📣 <b>Broadcast → {target}</b>\n\nSend the message (text/HTML):",
            reply_markup=kb_cancel("en"),
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_broadcast_message)
    async def ms_broadcast(message: Message, state: FSMContext):
        text = (message.text or "").strip()[:4000]
        data = await state.get_data()
        target = data.get("target", "all")
        await state.clear()
        user_ids = await db.get_users_by_filter(target)
        if not user_ids:
            await message.answer("❌ No users in this group.", reply_markup=kb_admin_menu())
            return
        bid = await db.create_broadcast(message.from_user.id, text, target)
        await message.answer(f"📤 Sending to {len(user_ids)} users...")
        sent = failed = 0
        for uid in user_ids:
            try:
                # Get user language and format broadcast with proper header
                buser = await db.get_user(uid)
                blang = L((buser or {}).get("language", DEFAULT_LANGUAGE))
                header = t("broadcast_header_en", blang) if blang == "en" else t("broadcast_header_fa", blang)
                formatted = header + text
                await bot.send_message(uid, formatted)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1
        await db.update_broadcast_stats(bid, sent, failed)
        await message.answer(
            f"✅ <b>Broadcast complete</b>\n📤 Sent: {sent}\n❌ Failed: {failed}\n📊 Total: {len(user_ids)}",
            reply_markup=kb_admin_menu(),
        )

    # ====================================================== CLEANUP
    @router.callback_query(AdminCB.filter(F.action == "cleanup"))
    async def cb_cleanup(callback: CallbackQuery):
        kb = InlineKeyboardBuilder()
        kb.button(text="🧹 Delete depleted (all servers)", callback_data=AdminCB(action="cleanup_depleted").pack(), style="danger")
        kb.button(text="🧹 Sync client counts", callback_data=AdminCB(action="cleanup_sync_counts").pack(), style="primary")
        kb.button(text="🔙 Admin", callback_data=AdminCB(action="main").pack(), style="danger")
        kb.adjust(1, 1)
        await callback.message.edit_text("🧹 <b>Cleanup & maintenance</b>", reply_markup=kb.as_markup())
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
    async def cb_settings(callback: CallbackQuery):
        cur = await _currency()
        trial_en = await db.get_setting_int("trial_enabled", 0)
        pay_en = await db.get_setting_int("payment_enabled", 0)
        fj_en = await db.get_setting_int("force_join_enabled", 0)
        text = (
            "⚙️ <b>Settings</b>\n<pre>"
            f"┌──────────────────────────────┐\n"
            f"│ Currency:        {cur:>11} │\n"
            f"│ Trial:           {'✅' if trial_en else '❌':>11} │\n"
            f"│ Payment:         {'✅' if pay_en else '❌':>11} │\n"
            f"│ Force Join:      {'✅' if fj_en else '❌':>11} │\n"
            f"└──────────────────────────────┘</pre>"
            "Tap a category to configure:"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="🎉 Trial", callback_data=SettingsCatCB(category="trial").pack(), style="primary")
        kb.button(text="🔗 Referral", callback_data=SettingsCatCB(category="referral").pack(), style="primary")
        kb.button(text="💳 Payment", callback_data=SettingsCatCB(category="payment").pack(), style="primary")
        kb.button(text="📢 Force Join", callback_data=SettingsCatCB(category="force_join").pack(), style="primary")
        kb.button(text="➕ Topup", callback_data=SettingsCatCB(category="topup").pack(), style="primary")
        kb.button(text="📚 Help Text", callback_data=SettingsCatCB(category="help_text").pack(), style="primary")
        kb.button(text="💵 Currency", callback_data=AdminCB(action="set_currency").pack())
        kb.button(text="🔄 Refresh servers", callback_data=AdminCB(action="refresh_servers").pack(), style="primary")
        kb.button(text="💾 DB Backup", callback_data=AdminCB(action="db_backup").pack())
        kb.button(text="🔙 Admin", callback_data=AdminCB(action="main").pack(), style="danger")
        kb.adjust(2, 2, 2, 2, 2, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    # ---- Settings Category Pages ----
    @router.callback_query(SettingsCatCB.filter(F.category == "trial"))
    async def cb_settings_trial(callback: CallbackQuery):
        trial_en = await db.get_setting_int("trial_enabled", 0)
        trial_days = await db.get_setting_int("trial_days", 3)
        trial_gb = await db.get_setting_int("trial_gb", 5)
        text = (
            "🎉 <b>Trial Settings</b>\n<pre>"
            f"┌──────────────────────────────┐\n"
            f"│ Enabled:    {'Yes' if trial_en else 'No':>11} │\n"
            f"│ Days:       {trial_days:>11} │\n"
            f"│ GB:         {trial_gb:>11} │\n"
            f"└──────────────────────────────┘</pre>"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text=f"{'✅' if trial_en else '❌'} Toggle Trial", callback_data=AdminCB(action="toggle_trial").pack())
        kb.button(text="📅 Days", callback_data=AdminCB(action="set_trial_days").pack())
        kb.button(text="💾 GB", callback_data=AdminCB(action="set_trial_gb").pack())
        kb.button(text="🔗 Inbounds", callback_data=AdminCB(action="set_trial_inbounds").pack())
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(2, 2, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(SettingsCatCB.filter(F.category == "referral"))
    async def cb_settings_referral(callback: CallbackQuery):
        ref_days = await db.get_setting_int("referral_bonus_days", 5)
        ref_gb = await db.get_setting_int("referral_bonus_gb", 2)
        text = (
            "🔗 <b>Referral Settings</b>\n<pre>"
            f"┌──────────────────────────────┐\n"
            f"│ Bonus days: {ref_days:>11} │\n"
            f"│ Bonus GB:   {ref_gb:>11} │\n"
            f"└──────────────────────────────┘</pre>"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="🎁 Bonus Days", callback_data=AdminCB(action="set_ref_days").pack())
        kb.button(text="🎁 Bonus GB", callback_data=AdminCB(action="set_ref_gb").pack())
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(2, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(SettingsCatCB.filter(F.category == "payment"))
    async def cb_settings_payment(callback: CallbackQuery):
        pay_en = await db.get_setting_int("payment_enabled", 0)
        card = await db.get_setting("payment_card_number", "-")
        holder = await db.get_setting("payment_card_holder", "-")
        min_amt = await db.get_setting_int("payment_min_amount", 50000)
        text = (
            "💳 <b>Payment Settings</b>\n<pre>"
            f"┌──────────────────────────────┐\n"
            f"│ Enabled:      {'Yes' if pay_en else 'No':>11} │\n"
            f"│ Card:         {card:>11} │\n"
            f"│ Holder:       {holder:>11} │\n"
            f"│ Min amount:   {min_amt:>11} │\n"
            f"└──────────────────────────────┘</pre>"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text=f"{'✅' if pay_en else '❌'} Toggle", callback_data=AdminCB(action="toggle_payment").pack())
        kb.button(text="💳 Card Number", callback_data=AdminCB(action="set_card_number").pack())
        kb.button(text="👤 Card Holder", callback_data=AdminCB(action="set_card_holder").pack())
        kb.button(text="🔢 Min Amount", callback_data=AdminCB(action="set_payment_min").pack())
        kb.button(text="📋 Presets", callback_data=AdminCB(action="set_payment_presets").pack())
        kb.button(text="💰 Pending Payments", callback_data=AdminCB(action="pending_payments").pack(), style="success")
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(2, 2, 2, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(SettingsCatCB.filter(F.category == "force_join"))
    async def cb_settings_force_join(callback: CallbackQuery):
        fj_en = await db.get_setting_int("force_join_enabled", 0)
        channels = await db.get_setting_json("force_join_channels", [])
        text = (
            "📢 <b>Force Join Settings</b>\n<pre>"
            f"┌──────────────────────────────┐\n"
            f"│ Enabled:      {'Yes' if fj_en else 'No':>11} │\n"
            f"│ Channels:     {len(channels):>11} │\n"
            f"└──────────────────────────────┘</pre>"
        )
        if channels:
            text += "\n<b>Channels:</b>\n"
            for ch in channels:
                text += f"• {ch.get('title', ch.get('username', 'Unknown'))} ({ch.get('chat_id', '')})\n"
        kb = InlineKeyboardBuilder()
        kb.button(text=f"{'✅' if fj_en else '❌'} Toggle", callback_data=AdminCB(action="toggle_force_join").pack())
        kb.button(text="➕ Add Channel", callback_data=AdminCB(action="add_force_join_channel").pack(), style="success")
        if channels:
            kb.button(text="🗑 Remove Channel", callback_data=AdminCB(action="remove_force_join_channel").pack(), style="danger")
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(2, 1, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(SettingsCatCB.filter(F.category == "topup"))
    async def cb_settings_topup(callback: CallbackQuery):
        topup_price = await db.get_setting_int("topup_price_per_gb", 2000)
        packages = await db.get_setting_json("topup_packages", [5, 10, 20, 50])
        text = (
            "➕ <b>Topup Settings</b>\n<pre>"
            f"┌──────────────────────────────┐\n"
            f"│ Price/GB:     {topup_price:>11} │\n"
            f"│ Packages:     {str(packages):>11} │\n"
            f"└──────────────────────────────┘</pre>"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Price/GB", callback_data=AdminCB(action="set_topup_price").pack())
        kb.button(text="📦 Packages", callback_data=AdminCB(action="set_topup_packages").pack())
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(2, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(SettingsCatCB.filter(F.category == "help_text"))
    async def cb_settings_help_text(callback: CallbackQuery):
        en_help = await db.get_setting("help_text_en") or "(default)"
        fa_help = await db.get_setting("help_text_fa") or "(default)"
        text = (
            "📚 <b>Help Text Settings</b>\n\n"
            f"<b>🇬🇧 English:</b>\n<i>{escape_html(en_help[:100])}...</i>\n\n"
            f"<b>🇮🇷 فارسی:</b>\n<i>{escape_html(fa_help[:100])}...</i>"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="🇬🇧 Edit English", callback_data=AdminCB(action="edit_help_en").pack())
        kb.button(text="🇮🇷 Edit فارسی", callback_data=AdminCB(action="edit_help_fa").pack())
        kb.button(text="🔙 Settings", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(2, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "toggle_trial"))
    async def cb_toggle_trial(callback: CallbackQuery):
        cur = await db.get_setting_int("trial_enabled", 0)
        await db.set_setting("trial_enabled", "0" if cur else "1")
        await callback.answer(f"Trial {'enabled' if not cur else 'disabled'}", show_alert=True)
        await cb_settings(callback)

    @router.callback_query(AdminCB.filter(F.action == "set_currency"))
    async def cb_set_currency(callback: CallbackQuery):
        kb = InlineKeyboardBuilder()
        kb.button(text="🇮🇷 Toman", callback_data=AdminCB(action="set_currency_val", data="toman").pack(), style="primary")
        kb.button(text="💵 USD", callback_data=AdminCB(action="set_currency_val", data="usd").pack())
        kb.button(text="🔙 Back", callback_data=AdminCB(action="settings").pack(), style="danger")
        kb.adjust(2, 1)
        await callback.message.edit_text("💵 <b>Currency</b>", reply_markup=kb.as_markup())
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
        await callback.message.edit_text("📅 Enter trial days:", reply_markup=kb_cancel("en"))
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_trial_gb"))
    async def cb_set_trial_gb(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_int", key="trial_gb", label="Trial GB")
        await callback.message.edit_text("💾 Enter trial GB:", reply_markup=kb_cancel("en"))
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_ref_days"))
    async def cb_set_ref_days(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_int", key="referral_bonus_days", label="Referral days")
        await callback.message.edit_text("🎁 Enter referral bonus days:", reply_markup=kb_cancel("en"))
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_ref_gb"))
    async def cb_set_ref_gb(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_int", key="referral_bonus_gb", label="Referral GB")
        await callback.message.edit_text("🎁 Enter referral bonus GB:", reply_markup=kb_cancel("en"))
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_topup_price"))
    async def cb_set_topup_price(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_int", key="topup_price_per_gb", label="Topup price/GB")
        await callback.message.edit_text("➕ Enter topup price per GB:", reply_markup=kb_cancel("en"))
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
            kb.button(text=f"— {escape_html(srv['alias'])} —", callback_data="noop_0")
            inbounds = await db.get_inbounds(srv["id"], enabled_only=True)
            for ib in inbounds:
                key = f"{srv['id']}_{ib['inbound_id']}"
                mark = "✅" if key in selected else "⬜"
                kb.button(text=f"{mark} {ib.get('remark') or ib['inbound_id']} ({ib.get('protocol','?')})",
                          callback_data=AdminCB(action="trial_ib_toggle", data=key).pack())
        kb.button(text="💾 Save", callback_data=AdminCB(action="settings").pack(), style="success")
        kb.button(text="⬜ Clear (use all)", callback_data=AdminCB(action="trial_ib_clear").pack(), style="danger")
        kb.adjust(1, 1)
        await callback.message.edit_text("🔗 <b>Trial inbounds</b> (empty = use all)", reply_markup=kb.as_markup())
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
        try:
            db_file = FSInputFile(DATABASE_PATH)
            await bot.send_document(callback.from_user.id, db_file, caption="💾 Database backup")
            await callback.answer("✅ Sent to your PM", show_alert=True)
        except Exception as e:
            await callback.answer(f"❌ {str(e)[:50]}", show_alert=True)

    # ---- Payment settings handlers ----
    @router.callback_query(AdminCB.filter(F.action == "toggle_payment"))
    async def cb_toggle_payment(callback: CallbackQuery):
        cur = await db.get_setting_int("payment_enabled", 0)
        await db.set_setting("payment_enabled", "0" if cur else "1")
        await callback.answer(f"Payment {'enabled' if not cur else 'disabled'}", show_alert=True)
        # Re-render payment settings
        fake_cb = callback
        await cb_settings_payment(fake_cb)

    @router.callback_query(AdminCB.filter(F.action == "set_card_number"))
    async def cb_set_card_number(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_str", key="payment_card_number", label="Card number")
        await callback.message.edit_text("💳 Enter card number:", reply_markup=kb_cancel("en"))
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_card_holder"))
    async def cb_set_card_holder(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_str", key="payment_card_holder", label="Card holder")
        await callback.message.edit_text("👤 Enter card holder name:", reply_markup=kb_cancel("en"))
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_payment_min"))
    async def cb_set_payment_min(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_int", key="payment_min_amount", label="Min payment amount")
        await callback.message.edit_text("🔢 Enter minimum payment amount (Toman):", reply_markup=kb_cancel("en"))
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "set_payment_presets"))
    async def cb_set_payment_presets(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_str", key="payment_presets", label="Payment presets")
        cur = await db.get_setting_json("payment_presets", [50000, 100000, 200000, 500000])
        await callback.message.edit_text(
            f"📋 Enter preset amounts as comma-separated numbers (Toman):\n\nCurrent: {cur}",
            reply_markup=kb_cancel("en"),
        )
        await callback.answer()

    # ---- Pending payments ----
    @router.callback_query(AdminCB.filter(F.action == "pending_payments"))
    async def cb_pending_payments(callback: CallbackQuery):
        payments = await db.get_pending_payments()
        if not payments:
            await callback.message.edit_text("💰 <b>No pending payments</b>", reply_markup=kb_admin_menu())
            await callback.answer()
            return
        text = "💰 <b>Pending Payments</b>\n\n"
        kb = InlineKeyboardBuilder()
        for p in payments:
            user = await db.get_user(p["user_tg_id"])
            uname = escape_html(user.get("first_name") or user.get("username") or str(p["user_tg_id"]))
            text += f"• #{p['id']} — {uname}: {fmt_num(p['unique_amount'], 'fa')} تومان\n"
            kb.button(
                text=f"#{p['id']} {uname[:15]} — {int(p['unique_amount'])}T",
                callback_data=PaymentCB(action="view", payment_id=p["id"]).pack(),
            )
        kb.button(text="🔙 Admin", callback_data=AdminCB(action="main").pack(), style="danger")
        kb.adjust(1, 1)
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(PaymentCB.filter(F.action == "view"))
    async def cb_payment_view(callback: CallbackQuery, callback_data: PaymentCB):
        payment = await db.get_payment(callback_data.payment_id)
        if not payment:
            await callback.answer("Not found", show_alert=True)
            return
        user = await db.get_user(payment["user_tg_id"])
        uname = escape_html(user.get("first_name") or user.get("username") or str(payment["user_tg_id"]))
        text = f"💰 <b>Payment #{payment['id']}</b>\n\n"
        text += f"👤 User: {uname} ({payment['user_tg_id']})\n"
        text += f"💵 Base amount: {int(payment['amount'])} Toman\n"
        text += f"💵 Unique amount: {int(payment['unique_amount'])} Toman\n"
        text += f"💳 Card: {escape_html(payment.get('card_number') or '-')}\n"
        text += f"📅 Created: {payment.get('created_at', '-')}\n"
        if payment.get("receipt_text"):
            text += f"📝 Receipt: {escape_html(payment['receipt_text'][:200])}\n"
        text += f"\nStatus: {payment.get('status', 'pending')}"

        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Approve", callback_data=PaymentCB(action="approve", payment_id=payment["id"]).pack(), style="success")
        kb.button(text="❌ Reject", callback_data=PaymentCB(action="reject_ask", payment_id=payment["id"]).pack(), style="danger")
        kb.button(text="🔙 Pending", callback_data=AdminCB(action="pending_payments").pack())
        kb.adjust(2, 1)

        # If receipt is a photo, send it
        if payment.get("receipt_file_id"):
            try:
                await bot.send_photo(
                    callback.from_user.id,
                    payment["receipt_file_id"],
                    caption=text,
                    reply_markup=kb.as_markup(),
                )
                try:
                    await callback.message.delete()
                except Exception:
                    pass
            except Exception:
                await callback.message.edit_text(text, reply_markup=kb.as_markup())
        else:
            await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(PaymentCB.filter(F.action == "approve"))
    async def cb_payment_approve(callback: CallbackQuery, callback_data: PaymentCB):
        payment = await db.get_payment(callback_data.payment_id)
        if not payment or payment["status"] != "pending":
            await callback.answer("Not pending", show_alert=True)
            return
        await db.approve_payment(payment["id"], callback.from_user.id)
        await db.update_user_balance(payment["user_tg_id"], payment["amount"], add=True)
        await db.add_transaction(
            user_tg_id=payment["user_tg_id"], amount=payment["amount"], type_="deposit",
            description=f"Card payment #{payment['id']}", admin_id=callback.from_user.id,
        )
        # Notify user
        user = await db.get_user(payment["user_tg_id"])
        lang = L((user or {}).get("language", DEFAULT_LANGUAGE))
        currency = await _currency()
        balance = (user or {}).get("balance", 0)
        try:
            await bot.send_message(
                payment["user_tg_id"],
                t("payment_approved", lang, amount=fmt_price(payment["amount"], lang, currency), balance=fmt_price(balance, lang, currency)),
            )
        except Exception:
            pass
        await callback.message.edit_text(
            f"✅ <b>Payment #{payment['id']} approved</b>\n"
            f"💰 {int(payment['amount'])} Toman added to user {payment['user_tg_id']}",
            reply_markup=kb_admin_menu(),
        )
        await callback.answer("✅ Approved")

    @router.callback_query(PaymentCB.filter(F.action == "reject_ask"))
    async def cb_payment_reject_ask(callback: CallbackQuery, callback_data: PaymentCB, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_reject_reason)
        await state.update_data(payment_id=callback_data.payment_id)
        await callback.message.edit_text(t("enter_reject_reason", "en"), reply_markup=kb_cancel("en"))
        await callback.answer()

    @router.message(AdminStates.waiting_for_reject_reason)
    async def ms_reject_reason(message: Message, state: FSMContext):
        reason = (message.text or "").strip()
        if reason == "-":
            reason = ""
        data = await state.get_data()
        await state.clear()
        payment = await db.get_payment(data.get("payment_id"))
        if not payment or payment["status"] != "pending":
            await message.answer("Payment not found or already processed.", reply_markup=kb_admin_menu())
            return
        await db.reject_payment(payment["id"], message.from_user.id, reason)
        # Notify user
        user = await db.get_user(payment["user_tg_id"])
        lang = L((user or {}).get("language", DEFAULT_LANGUAGE))
        try:
            await bot.send_message(
                payment["user_tg_id"],
                t("payment_rejected", lang, reason=escape_html(reason) or "No reason given"),
            )
        except Exception:
            pass
        await message.answer(
            f"❌ <b>Payment #{payment['id']} rejected</b>",
            reply_markup=kb_admin_menu(),
        )

    # ---- Force join settings handlers ----
    @router.callback_query(AdminCB.filter(F.action == "toggle_force_join"))
    async def cb_toggle_force_join(callback: CallbackQuery):
        cur = await db.get_setting_int("force_join_enabled", 0)
        await db.set_setting("force_join_enabled", "0" if cur else "1")
        await callback.answer(f"Force join {'enabled' if not cur else 'disabled'}", show_alert=True)
        fake_cb = callback
        await cb_settings_force_join(fake_cb)

    @router.callback_query(AdminCB.filter(F.action == "add_force_join_channel"))
    async def cb_add_force_join_channel(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.waiting_for_force_join_channel)
        await callback.message.edit_text(
            "📢 <b>Add Force Join Channel</b>\n\n"
            "Enter channel username (e.g. <code>@mychannel</code>) or chat ID:\n"
            "The bot must be an admin in the channel!",
            reply_markup=kb_cancel("en"),
        )
        await callback.answer()

    @router.message(AdminStates.waiting_for_force_join_channel)
    async def ms_force_join_channel(message: Message, state: FSMContext):
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
            await callback.answer("No channels to remove", show_alert=True)
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
        await callback.message.edit_text("🗑 <b>Select channel to remove:</b>", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "remove_fj_channel"))
    async def cb_remove_fj_channel(callback: CallbackQuery, callback_data: AdminCB):
        channels = await db.get_setting_json("force_join_channels", [])
        idx = int(callback_data.data)
        if 0 <= idx < len(channels):
            removed = channels.pop(idx)
            await db.set_setting("force_join_channels", json.dumps(channels))
            await callback.answer(f"✅ Removed {removed.get('title', '?')}", show_alert=True)
        await cb_settings_force_join(callback)

    # ---- Help text settings handlers ----
    @router.callback_query(AdminCB.filter(F.action == "edit_help_en"))
    async def cb_edit_help_en(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_str", key="help_text_en", label="Help text (English)")
        current = await db.get_setting("help_text_en") or t("help_text", "en")
        await callback.message.edit_text(
            f"📚 <b>Edit English help text</b>\n\nCurrent:\n<i>{escape_html(current[:200])}...</i>\n\nSend new help text:",
            reply_markup=kb_cancel("en"),
        )
        await callback.answer()

    @router.callback_query(AdminCB.filter(F.action == "edit_help_fa"))
    async def cb_edit_help_fa(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_str", key="help_text_fa", label="Help text (Farsi)")
        current = await db.get_setting("help_text_fa") or t("help_text", "fa")
        await callback.message.edit_text(
            f"📚 <b>Edit Persian help text</b>\n\nCurrent:\n<i>{escape_html(current[:200])}...</i>\n\nSend new help text:",
            reply_markup=kb_cancel("fa"),
        )
        await callback.answer()

    # ---- Topup packages settings handler ----
    @router.callback_query(AdminCB.filter(F.action == "set_topup_packages"))
    async def cb_set_topup_packages(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AdminStates.setting_edit_value)
        await state.update_data(edit_type="setting_str", key="topup_packages", label="Topup packages")
        cur = await db.get_setting_json("topup_packages", [5, 10, 20, 50])
        await callback.message.edit_text(
            f"📦 Enter topup packages as comma-separated GB values:\n\nCurrent: {cur}",
            reply_markup=kb_cancel("en"),
        )
        await callback.answer()

    # ---- catch-all for section-header "noop" buttons & stray callbacks ----
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
                        kb.button(text=t("renew", lang),
                                  callback_data=AccountCB(action="renew", email=acc["email"]).pack(),
                                  style="success")
                        kb.button(text=t("my_accounts", lang),
                                  callback_data=MenuCB(action="my_accounts").pack())
                        kb.adjust(2)
                        await bot.send_message(
                            acc["user_tg_id"],
                            f"⏰ <b>{'Subscription expiring soon!' if lang=='en' else 'اشتراک شما به‌زودی منقضی می‌شود!'}</b>\n\n"
                            f"📱 <code>{escape_html(acc['email'])}</code>\n"
                            f"📅 {fmt_remaining(acc['expiry_time'], lang)}\n"
                            f"🗓 {fmt_ts(acc['expiry_time'], lang)}",
                            reply_markup=kb.as_markup(),
                        )
                        await db.add_expiry_reminder(acc["email"], days)
                    except Exception as e:
                        logger.error("expiry reminder failed: %s", e)

            # Auto-disable fully expired accounts
            now_ms = int(datetime.now().timestamp() * 1000)
            for acc in await db.get_all_active_accounts():
                if acc["expiry_time"] > 0 and acc["expiry_time"] < now_ms:
                    server = await db.get_server(acc["server_id"])
                    if server:
                        await api.disable_client(server["panel_url"], server["api_token"], acc["email"])
                    await db.update_account(acc["email"], is_active=False)
                    user = await db.get_user(acc["user_tg_id"])
                    lang = L((user or {}).get("language", DEFAULT_LANGUAGE))
                    logger.info("Auto-disabled expired: %s", acc["email"])
                    try:
                        kb = InlineKeyboardBuilder()
                        kb.button(text=t("renew", lang),
                                  callback_data=AccountCB(action="renew", email=acc["email"]).pack(), style="success")
                        kb.button(text=t("buy", lang), callback_data=MenuCB(action="buy").pack())
                        kb.adjust(2)
                        await bot.send_message(
                            acc["user_tg_id"],
                            f"🔴 <b>{'Account expired' if lang=='en' else 'اکانت منقضی شد'}</b>\n\n"
                            f"📱 <code>{escape_html(acc['email'])}</code>\n"
                            f"🗓 {fmt_ts(acc['expiry_time'], lang)}",
                            reply_markup=kb.as_markup(),
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.error("expiry checker error: %s", e)
        await asyncio.sleep(3600)


async def task_traffic_alerts(bot: Bot, db: Database, api: PanelAPI):
    """Every 10 min: traffic-threshold alerts + auto-disable depleted."""
    logger.info("Background task started: traffic_alerts")
    while True:
        try:
            for acc in await db.get_all_active_accounts():
                server = await db.get_server(acc["server_id"])
                if not server:
                    continue
                traffic = await api.get_client_traffic(server["panel_url"], server["api_token"], acc["email"])
                if not traffic:
                    continue
                total = traffic.get("total", 0)
                used = traffic.get("up", 0) + traffic.get("down", 0)
                if total <= 0:
                    continue
                pct = (used / total) * 100
                user = await db.get_user(acc["user_tg_id"])
                lang = L((user or {}).get("language", DEFAULT_LANGUAGE))
                for threshold in (TRAFFIC_ALERT_THRESHOLD_1, TRAFFIC_ALERT_THRESHOLD_2):
                    if pct >= threshold and not await db.has_traffic_alert(acc["email"], threshold):
                        try:
                            emoji = "⚠️" if threshold < 90 else "🚨"
                            kb = InlineKeyboardBuilder()
                            kb.button(text=t("topup_traffic", lang),
                                      callback_data=AccountCB(action="topup", email=acc["email"]).pack(), style="primary")
                            kb.button(text=t("renew", lang),
                                      callback_data=AccountCB(action="renew", email=acc["email"]).pack(), style="success")
                            kb.adjust(2)
                            await bot.send_message(
                                acc["user_tg_id"],
                                f"{emoji} <b>Traffic {threshold}%</b>\n"
                                f"📱 <code>{escape_html(acc['email'])}</code>\n"
                                f"📊 {fmt_bytes(used)} / {fmt_bytes(total)}\n"
                                f"✅ {fmt_bytes(total-used)}",
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
                        kb.button(text=t("renew", lang),
                                  callback_data=AccountCB(action="renew", email=acc["email"]).pack(), style="success")
                        kb.adjust(1)
                        await bot.send_message(
                            acc["user_tg_id"],
                            f"🔴 <b>{'Traffic depleted' if lang=='en' else 'حجم تمام شد'}</b>\n"
                            f"📱 <code>{escape_html(acc['email'])}</code>",
                            reply_markup=kb.as_markup(),
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.error("traffic alerts error: %s", e)
        await asyncio.sleep(600)


async def task_server_health(bot: Bot, db: Database, api: PanelAPI):
    """Every 5 min: probe all servers, notify admins of state changes."""
    logger.info("Background task started: server_health")
    while True:
        try:
            for srv in await db.get_servers(active_only=True):
                ok, msg = await api.test_panel_connection(srv["panel_url"], srv["api_token"])
                was_healthy = bool(srv["is_healthy"])
                await db.update_server_health(srv["id"], ok, "" if ok else msg)
                if was_healthy and not ok:
                    logger.warning("Server down: %s — %s", srv["alias"], msg)
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"🔴 <b>Server Down</b>\n🖥 {escape_html(srv['alias'])}\n"
                                f"🔗 <code>{escape_html(srv['panel_url'])}</code>\n❌ {escape_html(msg)}",
                            )
                        except Exception:
                            pass
                elif not was_healthy and ok:
                    logger.info("Server recovered: %s", srv["alias"])
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"🟢 <b>Server Recovered</b>\n🖥 {escape_html(srv['alias'])}",
                            )
                        except Exception:
                            pass
        except Exception as e:
            logger.error("server health error: %s", e)
        await asyncio.sleep(300)


async def task_sync_client_counts(db: Database, api: PanelAPI):
    """Every 30 min: refresh cached client counts per server (for load balancing)."""
    logger.info("Background task started: sync_client_counts")
    while True:
        try:
            for srv in await db.get_servers(active_only=True):
                data = await api.get_clients_paged(srv["panel_url"], srv["api_token"], page=1, page_size=1)
                total = data.get("total", 0) if isinstance(data, dict) else 0
                await db.update_server(srv["id"], total_clients=total)
        except Exception as e:
            logger.error("sync client counts error: %s", e)
        await asyncio.sleep(1800)


# ============================================================================
# SECTION 13: MAIN APPLICATION
# ============================================================================

async def main():
    """Initialise DB, API, bot, routers, middleware, background tasks — then poll."""
    logger.info("=" * 60)
    logger.info("3X-UI Telegram Sales Bot — starting up")
    logger.info("=" * 60)

    db = Database(DATABASE_PATH)
    await db.connect()

    api = PanelAPI()
    lb = LoadBalancer(db, api)

    bot = Bot(token=BOT_TOKEN, default=ParseMode.HTML)
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
    ]
    logger.info("Background tasks: expiry, traffic, health, sync_counts")

    me = await bot.get_me()
    logger.info("Bot online as @%s", me.username)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"✅ <b>Bot Started</b>\n🤖 @{me.username}\n"
                f"⚙️ /admin — admin panel\n🔧 Background tasks: active",
            )
        except Exception:
            pass

    try:
        logger.info("Starting polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
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
        logger.info("Stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error("Fatal error: %s", e)
        sys.exit(1)
