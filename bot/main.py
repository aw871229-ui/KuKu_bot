import os
import re
import logging
import sqlite3
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("kuku")

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
HIDDEN_PIN = os.getenv("HIDDEN_PIN", "").strip()
SESSION_MINUTES = int(os.getenv("HIDDEN_SESSION_MINUTES", "30"))

if not re.fullmatch(r"\d{4}", HIDDEN_PIN):
    raise RuntimeError("HIDDEN_PIN must be exactly 4 digits")

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    file_id TEXT,
    file_unique_id TEXT,
    file_name TEXT,
    mime_type TEXT,
    file_size INTEGER,
    caption TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    kind TEXT NOT NULL,
    file_id TEXT,
    file_unique_id TEXT,
    file_name TEXT,
    mime_type TEXT,
    file_size BIGINT,
    caption TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def is_admin(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id == ADMIN_USER_ID)


def is_unlocked(context: ContextTypes.DEFAULT_TYPE) -> bool:
    until = context.user_data.get("unlocked_until")
    if not until:
        return False
    if datetime.now(timezone.utc) >= until:
        context.user_data.pop("unlocked_until", None)
        return False
    return True


def unlock(context: ContextTypes.DEFAULT_TYPE):
    context.user_data["unlocked_until"] = (
        datetime.now(timezone.utc) + timedelta(minutes=SESSION_MINUTES)
    )


def lock(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("unlocked_until", None)


@contextmanager
def db():
    if DATABASE_URL:
        import psycopg
        conn = psycopg.connect(DATABASE_URL)
        try:
            with conn.cursor() as cur:
                cur.execute(POSTGRES_SCHEMA)
            conn.commit()
            yield conn
        finally:
            conn.close()
    else:
        conn = sqlite3.connect("kuku.db")
        try:
            conn.executescript(SQLITE_SCHEMA)
            conn.commit()
            yield conn
        finally:
            conn.close()


def save_item(user_id, kind, file_id, file_unique_id, file_name,
              mime_type, file_size, caption):
    with db() as conn:
        cur = conn.cursor()
        if DATABASE_URL:
            cur.execute(
                """INSERT INTO items
                (user_id,kind,file_id,file_unique_id,file_name,mime_type,
                 file_size,caption,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (user_id, kind, file_id, file_unique_id, file_name, mime_type,
                 file_size, caption or "", now()),
            )
            item_id = cur.fetchone()[0]
        else:
            cur.execute(
                """INSERT INTO items
                (user_id,kind,file_id,file_unique_id,file_name,mime_type,
                 file_size,caption,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (user_id, kind, file_id, file_unique_id, file_name, mime_type,
                 file_size, caption or "", now()),
            )
            item_id = cur.lastrowid
        conn.commit()
        return item_id


def save_note(user_id, text):
    with db() as conn:
        cur = conn.cursor()
        if DATABASE_URL:
            cur.execute(
                "INSERT INTO notes(user_id,text,created_at) VALUES (%s,%s,%s) RETURNING id",
                (user_id, text, now()),
            )
            item_id = cur.fetchone()[0]
        else:
            cur.execute(
                "INSERT INTO notes(user_id,text,created_at) VALUES (?,?,?)",
                (user_id, text, now()),
            )
            item_id = cur.lastrowid
        conn.commit()
        return item_id


def parse_labeled(text):
    nums = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", text)

    def grab(label):
        m = re.search(
            rf"{label}\s*[:=]?\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))",
            text,
            re.I,
        )
        return float(m.group(1)) if m else None

    real = grab("实时")
    trade = grab("交易")
    point = grab("点位")

    if real is not None and trade is not None:
        return ("point", real, trade)
    if point is not None and real is not None:
        return ("reverse", point, real)

    if len(nums) == 2:
        a, b = map(float, nums)
        if a < b:
            return ("point", a, b)
        if a > b:
            return ("reverse", a, b)
    return None


def calculate(text):
    parsed = parse_labeled(text)
    if not parsed:
        return None

    kind, a, b = parsed
    if kind == "point":
        real, trade = a, b
        if trade == 0:
            return "❌ 交易价格不能为 0"
        p = (1 - real / trade) * 100
        return (
            "📐 点位计算\n\n"
            f"实时：{real:g}\n"
            f"交易：{trade:g}\n"
            f"点位：{p:.4f}%\n\n"
            "公式：〔1 - (实时 ÷ 交易)〕 × 100"
        )

    point, real = a, b
    x = 1 - point / 100
    if x == 0:
        return "❌ 点位不能等于 100"
    rate = real / x
    return (
        "📐 汇率反算\n\n"
        f"点位：{point:g}\n"
        f"实时：{real:g}\n"
        f"X：{x:.6f}\n"
        f"汇率：{rate:.6f}\n\n"
        "公式：1 - (点位 ÷ 100) = X\n"
        "实时 ÷ X = 汇率"
    )


def calculator_home():
    return (
        "📐 点位与汇率计算助手\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "① 点位计算\n"
        "格式：实时 7 交易 8.5\n"
        "或：实时=7 交易=8.5\n"
        "公式：【1 - (实时÷交易)】× 100\n\n"
        "② 汇率反算\n"
        "格式：点位 17 实时 7\n"
        "或：点位=17 实时=7\n"
        "公式：1 - (点位÷100) = X，实时÷X = 汇率\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💡 直接发送两个数字即可自动判断\n"
        "例如：7 8.5 或 17 7"
    )


def _http_get(url, timeout=8):
    headers = {"User-Agent": "KuKuBot/2.0"}
    r = requests.get(url, timeout=timeout, headers=headers)
    r.raise_for_status()
    return r


def _htx_reference_price():
    url = "https://www.htx.com/en-in/price/usdt/usdt-to-cny/"
    html = _http_get(url).text
    patterns = [
        r"Pay\s+([0-9]+(?:\.[0-9]+)?)\s+CNY\s+for\s+1\s+USDT",
        r"1\s+USDT\s+([0-9]+(?:\.[0-9]+)?)\s+CNY",
        r"([0-9]+(?:\.[0-9]+)?)\s+CNY\s+for\s+1\s+USDT",
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.I)
        if m:
            return float(m.group(1))
    return None


def _htx_p2p(trade_type="sell"):
    url = (
        "https://otc-api.eiijo.cn/v1/data/trade-market"
        "?country=37&currency=1&payMethod=0&currPage=1"
        f"&coinId=2&tradeType={trade_type}&blockType=general&online=1"
    )
    try:
        data = _http_get(url, timeout=8).json()
        rows = data.get("data") or []
        return rows[:10]
    except Exception as exc:
        log.warning("HTX P2P request failed: %s", exc)
        return []


def _offer_price(row):
    try:
        return float(row.get("price"))
    except Exception:
        return None


def _offer_name(row):
    for key in ("userName", "nickName", "merchantName", "user_name"):
        if row.get(key):
            return str(row[key])
    return "HTX 商家"


def _offer_limit(row):
    lo = row.get("minTrade") or row.get("minAmount") or row.get("min")
    hi = row.get("maxTrade") or row.get("maxAmount") or row.get("max")
    if lo is not None and hi is not None:
        return f"{lo}-{hi}"
    if lo is not None:
        return f"≥{lo}"
    return ""


def _offer_methods(row):
    value = (
        row.get("payMethods")
        or row.get("payMethod")
        or row.get("payMethodsList")
        or row.get("payment")
        or ""
    )
    return str(value)


def _filter_offers(rows, method):
    if method == "all":
        return rows
    needles = {
        "bank": ("bank", "银行卡", "bankcard"),
        "alipay": ("alipay", "支付宝"),
        "wechat": ("wechat", "微信"),
    }[method]
    return [r for r in rows if any(n.lower() in _offer_methods(r).lower() for n in needles)]


def build_usdt_message(method="all"):
    buy_rows = _filter_offers(_htx_p2p("sell"), method)
    sell_rows = _filter_offers(_htx_p2p("buy"), method)
    ref = _htx_reference_price()

    lines = ["💵 USDT 实时交易", "━━━━━━━━━━━━━━━━━━"]
    if ref is not None:
        lines.append(f"HTX USDT/CNY 参考价：{ref:.4f} CNY")
    else:
        lines.append("HTX USDT/CNY 参考价：暂时获取失败")

    lines.append("")
    lines.append("🟢 买入 USDT（参考商家）")
    if buy_rows:
        for i, row in enumerate(buy_rows[:10], 1):
            price = _offer_price(row)
            price_text = f"{price:.4f}" if price is not None else "—"
            limit = _offer_limit(row)
            lines.append(f"{i}️⃣ {price_text}  {_offer_name(row)}" + (f"  [{limit}]" if limit else ""))
    else:
        lines.append("暂无可用 P2P 商家数据")

    lines.append("")
    lines.append("🔴 卖出 USDT（参考商家）")
    if sell_rows:
        for i, row in enumerate(sell_rows[:10], 1):
            price = _offer_price(row)
            price_text = f"{price:.4f}" if price is not None else "—"
            limit = _offer_limit(row)
            lines.append(f"{i}️⃣ {price_text}  {_offer_name(row)}" + (f"  [{limit}]" if limit else ""))
    else:
        lines.append("暂无可用 P2P 商家数据")

    lines.append("")
    lines.append("数据源：HTX / 火币公开市场数据")
    lines.append("⚠️ 行情仅供参考，P2P 报价会随商家在线状态和市场变化。")
    return "\n".join(lines)


def usdt_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("全部", callback_data="usdt:all"),
            InlineKeyboardButton("银行卡", callback_data="usdt:bank"),
            InlineKeyboardButton("支付宝", callback_data="usdt:alipay"),
            InlineKeyboardButton("微信", callback_data="usdt:wechat"),
        ],
        [InlineKeyboardButton("🔄 刷新", callback_data="usdt:refresh")],
    ])


async def send_usdt(update: Update, method="all", edit=False):
    text = await asyncio.to_thread(build_usdt_message, method)
    if edit:
        await update.callback_query.edit_message_text(text, reply_markup=usdt_keyboard())
    else:
        await update.effective_message.reply_text(text, reply_markup=usdt_keyboard())


def hidden_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📂 最近文件", callback_data="recent"),
            InlineKeyboardButton("📝 私人信息", callback_data="notes"),
        ],
        [
            InlineKeyboardButton("📊 统计", callback_data="stats"),
            InlineKeyboardButton("🔒 锁定", callback_data="lock"),
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(calculator_home())
    await update.effective_message.reply_text(
        "💵 USDT 实时交易",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("打开实时行情", callback_data="usdt:all")]
        ]),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📐 直接发送两个数字或带标签的计算式即可。\n"
        "例如：7 8.5\n"
        "或：实时 7 交易 8.5\n\n"
        "💵 点击“打开实时行情”查看 HTX USDT 数据。"
    )


async def unlock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.effective_message.reply_text("请输入4位数字密码。")


async def lock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update):
        lock(context)
        await update.effective_message.reply_text("🔒 私人入口已锁定。")


async def save_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update) or not is_unlocked(context):
        return
    text = " ".join(context.args).strip()
    if not text:
        return await update.effective_message.reply_text("用法：/save 需要保存的文字")
    item_id = save_note(update.effective_user.id, text)
    await update.effective_message.reply_text(f"✅ 已保存私人信息 #{item_id}")


async def media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update) or not is_unlocked(context):
        return
    m = update.effective_message
    obj = None
    kind = name = mime = None
    size = None

    if m.document:
        obj, kind, name, mime, size = (
            m.document, "document", m.document.file_name,
            m.document.mime_type, m.document.file_size
        )
    elif m.video:
        obj, kind, name, mime, size = (
            m.video, "video", "video", "video/mp4", m.video.file_size
        )
    elif m.photo:
        obj, kind, name, mime, size = (
            m.photo[-1], "photo", "photo", "image/jpeg",
            m.photo[-1].file_size
        )
    elif m.audio:
        obj, kind, name, mime, size = (
            m.audio, "audio", m.audio.file_name,
            m.audio.mime_type, m.audio.file_size
        )
    elif m.voice:
        obj, kind, name, mime, size = (
            m.voice, "voice", "voice.ogg", "audio/ogg", m.voice.file_size
        )

    if not obj:
        return

    item_id = save_item(
        update.effective_user.id, kind, obj.file_id, obj.file_unique_id,
        name, mime, size, m.caption
    )
    await m.reply_text(f"✅ 已保存 #{item_id} | {kind}")


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update) or not is_unlocked(context):
        return
    with db() as conn:
        cur = conn.cursor()
        q = (
            "SELECT id,kind,file_name,caption,created_at FROM items "
            "WHERE user_id=%s ORDER BY id DESC LIMIT 15"
            if DATABASE_URL else
            "SELECT id,kind,file_name,caption,created_at FROM items "
            "WHERE user_id=? ORDER BY id DESC LIMIT 15"
        )
        cur.execute(q, (update.effective_user.id,))
        rows = cur.fetchall()
    if not rows:
        return await update.effective_message.reply_text("暂无文件。")
    await update.effective_message.reply_text(
        "📂 最近文件：\n" +
        "\n".join(f"#{r[0]}  {r[1]}  {r[2] or ''}  {r[3] or ''}" for r in rows)
    )


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update) or not is_unlocked(context):
        return
    term = " ".join(context.args).strip()
    if not term:
        return await update.effective_message.reply_text("用法：/search 关键词")
    like = f"%{term}%"
    with db() as conn:
        cur = conn.cursor()
        if DATABASE_URL:
            cur.execute(
                """SELECT id,kind,file_name,caption,created_at FROM items
                   WHERE user_id=%s AND (file_name ILIKE %s OR caption ILIKE %s)
                   ORDER BY id DESC LIMIT 20""",
                (update.effective_user.id, like, like),
            )
        else:
            cur.execute(
                """SELECT id,kind,file_name,caption,created_at FROM items
                   WHERE user_id=? AND (file_name LIKE ? OR caption LIKE ?)
                   ORDER BY id DESC LIMIT 20""",
                (update.effective_user.id, like, like),
            )
        rows = cur.fetchall()
    if not rows:
        return await update.effective_message.reply_text("没有找到。")
    await update.effective_message.reply_text(
        "\n".join(f"#{r[0]} {r[1]} {r[2] or ''} {r[3] or ''}" for r in rows)
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update) or not is_unlocked(context):
        return
    with db() as conn:
        cur = conn.cursor()
        q = (
            "SELECT kind,COUNT(*) FROM items WHERE user_id=%s GROUP BY kind"
            if DATABASE_URL else
            "SELECT kind,COUNT(*) FROM items WHERE user_id=? GROUP BY kind"
        )
        cur.execute(q, (update.effective_user.id,))
        rows = cur.fetchall()
    await update.effective_message.reply_text(
        "📊 文件统计\n" +
        ("\n".join(f"{k}: {n}" for k, n in rows) if rows else "暂无数据")
    )


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update) or not is_unlocked(context):
        return
    if not context.args or not context.args[0].isdigit():
        return await update.effective_message.reply_text("用法：/delete ID")
    item_id = int(context.args[0])
    with db() as conn:
        cur = conn.cursor()
        q = (
            "DELETE FROM items WHERE id=%s AND user_id=%s"
            if DATABASE_URL else
            "DELETE FROM items WHERE id=? AND user_id=?"
        )
        cur.execute(q, (item_id, update.effective_user.id))
        conn.commit()
        n = cur.rowcount
    await update.effective_message.reply_text("🗑️ 已删除索引。" if n else "找不到该记录。")


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data.startswith("usdt:"):
        method = q.data.split(":", 1)[1]
        if method == "refresh":
            method = "all"
        await send_usdt(update, method, edit=True)
        return

    if not is_admin(update):
        return

    if not is_unlocked(context):
        await q.message.reply_text("🔒 私人入口已锁定。")
        return

    if q.data == "recent":
        return await list_cmd(update, context)
    if q.data == "stats":
        return await stats_cmd(update, context)
    if q.data == "notes":
        await q.message.reply_text("📝 使用 /save <文字> 保存私人信息。")
    elif q.data == "lock":
        lock(context)
        await q.message.reply_text("🔒 私人入口已锁定。")


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()

    if is_admin(update) and re.fullmatch(r"\d{4}", text):
        if text == HIDDEN_PIN:
            unlock(context)
            await update.effective_message.reply_text(
                f"🔓 私人入口已解锁（{SESSION_MINUTES}分钟）",
                reply_markup=hidden_menu(),
            )
        return

    result = calculate(text)
    if result:
        await update.effective_message.reply_text(result)


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("unlock", unlock_cmd))
    app.add_handler(CommandHandler("lock", lock_cmd))
    app.add_handler(CommandHandler("save", save_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))

    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(
        MessageHandler(
            filters.PHOTO | filters.VIDEO | filters.Document.ALL |
            filters.AUDIO | filters.VOICE,
            media,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    log.info("KuKu bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
