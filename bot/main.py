import os
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("kuku")

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

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

def allowed(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id == ADMIN_USER_ID)

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

def save_item(user_id, kind, file_id, file_unique_id, file_name, mime_type, file_size, caption):
    with db() as conn:
        cur = conn.cursor()
        if DATABASE_URL:
            cur.execute(
                """INSERT INTO items
                (user_id,kind,file_id,file_unique_id,file_name,mime_type,file_size,caption,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (user_id, kind, file_id, file_unique_id, file_name, mime_type, file_size, caption or "", now()),
            )
            item_id = cur.fetchone()[0]
        else:
            cur.execute(
                """INSERT INTO items
                (user_id,kind,file_id,file_unique_id,file_name,mime_type,file_size,caption,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (user_id, kind, file_id, file_unique_id, file_name, mime_type, file_size, caption or "", now()),
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await update.effective_message.reply_text("⛔ 无权限。")
    kb = [
        [InlineKeyboardButton("📂 最近文件", callback_data="recent"), InlineKeyboardButton("📝 私人信息", callback_data="notes")],
        [InlineKeyboardButton("📊 统计", callback_data="stats")],
    ]
    await update.effective_message.reply_text(
        "🔐 KuKu 私人资料库\n\n"
        "直接发送照片、视频、文件或音频，我会记录它们的 Telegram file_id。\n"
        "保存文字：/save 这里写私人信息\n"
        "搜索：/search 关键词\n"
        "最近文件：/list\n"
        "帮助：/help",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.effective_message.reply_text(
        "/save <文字>  保存私人信息\n"
        "/search <关键词> 搜索文件名、备注\n"
        "/list  最近文件\n"
        "/stats 统计\n"
        "/delete <ID> 删除索引记录\n\n"
        "直接发送照片/视频/文件/音频即可保存。"
    )

async def save_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    text = " ".join(context.args).strip()
    if not text:
        return await update.effective_message.reply_text("用法：/save 需要保存的文字")
    item_id = save_note(update.effective_user.id, text)
    await update.effective_message.reply_text(f"✅ 已保存私人信息 #{item_id}")

async def media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    m = update.effective_message
    obj = None
    kind = name = mime = None
    size = None
    if m.document:
        obj, kind, name, mime, size = m.document, "document", m.document.file_name, m.document.mime_type, m.document.file_size
    elif m.video:
        obj, kind, name, mime, size = m.video, "video", "video", "video/mp4", m.video.file_size
    elif m.photo:
        obj, kind, name, mime, size = m.photo[-1], "photo", "photo", "image/jpeg", m.photo[-1].file_size
    elif m.audio:
        obj, kind, name, mime, size = m.audio, "audio", m.audio.file_name, m.audio.mime_type, m.audio.file_size
    elif m.voice:
        obj, kind, name, mime, size = m.voice, "voice", "voice.ogg", "audio/ogg", m.voice.file_size
    if not obj:
        return
    item_id = save_item(update.effective_user.id, kind, obj.file_id, obj.file_unique_id, name, mime, size, m.caption)
    await m.reply_text(f"✅ 已保存 #{item_id} | {kind}")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    with db() as conn:
        cur = conn.cursor()
        q = ("SELECT id,kind,file_name,caption,created_at FROM items WHERE user_id=%s ORDER BY id DESC LIMIT 15"
             if DATABASE_URL else
             "SELECT id,kind,file_name,caption,created_at FROM items WHERE user_id=? ORDER BY id DESC LIMIT 15")
        cur.execute(q, (update.effective_user.id,))
        rows = cur.fetchall()
    if not rows:
        return await update.effective_message.reply_text("暂无文件。")
    await update.effective_message.reply_text("📂 最近文件：\n" + "\n".join(
        f"#{r[0]}  {r[1]}  {r[2] or ''}  {r[3] or ''}" for r in rows
    ))

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
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
                   ORDER BY id DESC LIMIT 20""", (update.effective_user.id, like, like))
        else:
            cur.execute(
                """SELECT id,kind,file_name,caption,created_at FROM items
                   WHERE user_id=? AND (file_name LIKE ? OR caption LIKE ?)
                   ORDER BY id DESC LIMIT 20""", (update.effective_user.id, like, like))
        rows = cur.fetchall()
    if not rows:
        return await update.effective_message.reply_text("没有找到。")
    await update.effective_message.reply_text("\n".join(
        f"#{r[0]} {r[1]} {r[2] or ''} {r[3] or ''}" for r in rows
    ))

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    with db() as conn:
        cur = conn.cursor()
        q = ("SELECT kind,COUNT(*) FROM items WHERE user_id=%s GROUP BY kind"
             if DATABASE_URL else
             "SELECT kind,COUNT(*) FROM items WHERE user_id=? GROUP BY kind")
        cur.execute(q, (update.effective_user.id,))
        rows = cur.fetchall()
    await update.effective_message.reply_text("📊 文件统计\n" + ("\n".join(f"{k}: {n}" for k, n in rows) if rows else "暂无数据"))

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    if not context.args or not context.args[0].isdigit():
        return await update.effective_message.reply_text("用法：/delete ID")
    item_id = int(context.args[0])
    with db() as conn:
        cur = conn.cursor()
        q = ("DELETE FROM items WHERE id=%s AND user_id=%s" if DATABASE_URL else "DELETE FROM items WHERE id=? AND user_id=?")
        cur.execute(q, (item_id, update.effective_user.id))
        conn.commit()
        n = cur.rowcount
    await update.effective_message.reply_text("🗑️ 已删除索引。" if n else "找不到该记录。")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not allowed(update):
        return
    if q.data == "recent":
        return await list_cmd(update, context)
    if q.data == "stats":
        return await stats_cmd(update, context)
    if q.data == "notes":
        await q.message.reply_text("📝 使用 /save <文字> 保存私人信息。")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("save", save_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE, media))
    log.info("KuKu bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
