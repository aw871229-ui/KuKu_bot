import os
import re
import json
import base64
import hashlib
import logging
import asyncio
from datetime import datetime, timezone, timedelta

import requests
from cryptography.fernet import Fernet
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("kuku")

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
HIDDEN_PIN = os.environ["HIDDEN_PIN"].strip()
SESSION_MINUTES = int(os.getenv("HIDDEN_SESSION_MINUTES", "30"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "aw871229-ui/KuKu_bot")
INDEX_PATH = "data/vault.enc"

if not re.fullmatch(r"\d{4}", HIDDEN_PIN):
    raise RuntimeError("HIDDEN_PIN must be exactly 4 digits")


def now():
    return datetime.now(timezone.utc).isoformat()


def is_admin(update: Update):
    u = update.effective_user
    return bool(u and u.id == ADMIN_USER_ID)


def unlocked(context):
    until = context.user_data.get("unlocked_until")
    if not until:
        return False
    if datetime.now(timezone.utc) >= until:
        context.user_data.pop("unlocked_until", None)
        return False
    return True


def unlock(context):
    context.user_data["unlocked_until"] = datetime.now(timezone.utc) + timedelta(minutes=SESSION_MINUTES)


def lock(context):
    context.user_data.pop("unlocked_until", None)


def fernet():
    # Keep v1 derivation so existing encrypted vault indexes remain readable.
    seed = f"KuKuVault-v1|{BOT_TOKEN}|{ADMIN_USER_ID}".encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(seed).digest()))


def github_headers():
    return {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def load_index():
    default = {"version": 2, "items": [], "notes": []}
    if not GITHUB_TOKEN:
        return default
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{INDEX_PATH}"
    try:
        r = requests.get(url, headers=github_headers(), timeout=12)
        if r.status_code == 404:
            return default
        r.raise_for_status()
        raw = base64.b64decode(r.json()["content"])
        data = json.loads(fernet().decrypt(raw).decode())
        data.setdefault("items", [])
        data.setdefault("notes", [])
        return data
    except Exception as exc:
        log.warning("Could not load encrypted index: %s", exc)
        return default


def save_index(data):
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is missing")
    payload = fernet().encrypt(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())
    encoded = base64.b64encode(payload).decode()
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{INDEX_PATH}"
    existing = requests.get(url, headers=github_headers(), timeout=12)
    body = {"message": "chore: update encrypted KuKu vault index", "content": encoded, "branch": "main"}
    if existing.status_code == 200:
        body["sha"] = existing.json()["sha"]
    elif existing.status_code != 404:
        existing.raise_for_status()
    r = requests.put(url, headers=github_headers(), json=body, timeout=15)
    r.raise_for_status()


def calculator_home():
    return ("📐 点位与汇率计算助手\n\n━━━━━━━━━━━━━━━━━━\n"
            "① 点位计算\n格式：实时 7 交易 8.5\n或：实时=7 交易=8.5\n"
            "公式：【1 - (实时÷交易)】× 100\n\n"
            "② 汇率反算\n格式：点位 17 实时 7\n或：点位=17 实时=7\n"
            "公式：1 - (点位÷100) = X，实时÷X = 汇率\n\n━━━━━━━━━━━━━━━━━━\n"
            "💡 直接发送两个数字即可自动判断")


def parse_calc(text):
    def grab(label):
        m = re.search(rf"{label}\s*[:=]?\s*(-?(?:\d+(?:\.\d*)?|\.\d+))", text, re.I)
        return float(m.group(1)) if m else None
    real, trade, point = grab("实时"), grab("交易"), grab("点位")
    nums = re.findall(r"-?(?:\d+(?:\.\d*)?|\.\d+)", text)
    if real is not None and trade is not None:
        return "point", real, trade
    if point is not None and real is not None:
        return "reverse", point, real
    if len(nums) == 2:
        a, b = map(float, nums)
        return ("point", a, b) if a < b else ("reverse", a, b)
    return None


def calculate(text):
    p = parse_calc(text)
    if not p:
        return None
    kind, a, b = p
    if kind == "point":
        if b == 0:
            return "❌ 交易价格不能为 0"
        value = (1 - a / b) * 100
        return f"📐 点位计算\n\n实时：{a:g}\n交易：{b:g}\n点位：{value:.4f}%"
    x = 1 - a / 100
    if x == 0:
        return "❌ 点位不能等于 100"
    rate = b / x
    return f"📐 汇率反算\n\n点位：{a:g}\n实时：{b:g}\nX：{x:.6f}\n汇率：{rate:.6f}"


def http_get(url, timeout=8):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "KuKuBot/4.0"})
    r.raise_for_status()
    return r


def htx_reference():
    try:
        html = http_get("https://www.htx.com/en-in/price/usdt/usdt-to-cny/").text
        for p in [r"Pay\s+([0-9]+(?:\.[0-9]+)?)\s+CNY\s+for\s+1\s+USDT", r"1\s+USDT\s+([0-9]+(?:\.[0-9]+)?)\s+CNY"]:
            m = re.search(p, html, re.I)
            if m:
                return float(m.group(1))
    except Exception as exc:
        log.warning("HTX reference failed: %s", exc)
    return None


def htx_p2p(trade_type):
    url = ("https://otc-api.eiijo.cn/v1/data/trade-market?country=37&currency=1&payMethod=0"
           "&currPage=1&coinId=2&tradeType=" + trade_type + "&blockType=general&online=1")
    try:
        return (http_get(url).json().get("data") or [])[:10]
    except Exception as exc:
        log.warning("HTX P2P failed: %s", exc)
        return []


def offer(row, i):
    try:
        price = float(row.get("price"))
    except Exception:
        price = None
    name = next((str(row[k]) for k in ("userName", "nickName", "merchantName", "user_name") if row.get(k)), "HTX 商家")
    return f"{i}️⃣ {price:.4f}  {name}" if price is not None else f"{i}️⃣ —  {name}"


def usdt_text():
    ref = htx_reference()
    buy, sell = htx_p2p("sell"), htx_p2p("buy")
    lines = ["💵 USDT 实时交易", "━━━━━━━━━━━━━━━━━━", f"HTX USDT/CNY 参考价：{ref:.4f} CNY" if ref is not None else "HTX USDT/CNY 参考价：暂时获取失败", "", "🟢 买入 USDT"]
    lines += [offer(r, i) for i, r in enumerate(buy, 1)] or ["暂无 P2P 数据"]
    lines += ["", "🔴 卖出 USDT"]
    lines += [offer(r, i) for i, r in enumerate(sell, 1)] or ["暂无 P2P 数据"]
    lines += ["", "数据源：HTX / 火币公开市场数据", "⚠️ P2P 报价会随市场和商家状态变化。"]
    return "\n".join(lines)


def private_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 最近文件", callback_data="recent"), InlineKeyboardButton("📝 私人信息", callback_data="notes")],
        [InlineKeyboardButton("🔎 搜索", callback_data="search_help"), InlineKeyboardButton("📊 统计", callback_data="stats")],
        [InlineKeyboardButton("🔒 锁定", callback_data="lock")],
    ])


def item_keyboard(items):
    rows = [[InlineKeyboardButton(f"#{x['id']} {x['kind']} {x.get('file_name') or ''}"[:60], callback_data=f"get:{x['id']}")] for x in items]
    return InlineKeyboardMarkup(rows) if rows else None


async def start(update, context):
    await update.effective_message.reply_text(calculator_home())
    await update.effective_message.reply_text("💵 USDT 实时交易", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("打开实时行情", callback_data="usdt:refresh")]]))


async def help_cmd(update, context):
    if is_admin(update):
        await update.effective_message.reply_text("📐 发送数字对直接计算。\n🔐 发送4位密码进入私人库。\n/lock 立即锁定。")


async def lock_cmd(update, context):
    if is_admin(update):
        lock(context)
        await update.effective_message.reply_text("🔒 私人入口已锁定。")


async def save_note_cmd(update, context):
    if not is_admin(update) or not unlocked(context):
        return
    text = " ".join(context.args).strip()
    if not text:
        return await update.effective_message.reply_text("用法：/save 文字")
    data = load_index()
    nid = max([n["id"] for n in data["notes"]] or [0]) + 1
    data["notes"].append({"id": nid, "text": text, "created_at": now()})
    save_index(data)
    await update.effective_message.reply_text(f"✅ 已保存私人信息 #{nid}")


async def media(update, context):
    """Store Telegram media by file_id only. Nothing is copied to a channel."""
    if not is_admin(update) or not unlocked(context):
        return
    m = update.effective_message
    if not (m.photo or m.video or m.document or m.audio or m.voice):
        return

    if m.document:
        obj, kind, file_id = m.document, "document", m.document.file_id
    elif m.video:
        obj, kind, file_id = m.video, "video", m.video.file_id
    elif m.photo:
        obj, kind, file_id = m.photo[-1], "photo", m.photo[-1].file_id
    elif m.audio:
        obj, kind, file_id = m.audio, "audio", m.audio.file_id
    else:
        obj, kind, file_id = m.voice, "voice", m.voice.file_id

    data = load_index()
    iid = max([x["id"] for x in data["items"]] or [0]) + 1
    name = getattr(obj, "file_name", None) or ("photo" if kind == "photo" else kind)
    data["items"].append({
        "id": iid,
        "kind": kind,
        "file_id": file_id,
        "file_name": name,
        "caption": m.caption or "",
        "size": getattr(obj, "file_size", None),
        "created_at": now(),
    })
    try:
        save_index(data)
    except Exception as exc:
        log.exception("index save failed")
        return await m.reply_text(f"❌ 索引保存失败：{exc}")
    await m.reply_text(f"✅ 已保存 #{iid} | {kind}")


async def list_cmd(update, context):
    if not is_admin(update) or not unlocked(context):
        return
    data = load_index(); items = data["items"][-15:][::-1]
    if not items:
        return await update.effective_message.reply_text("暂无文件。")
    await update.effective_message.reply_text("📂 最近文件", reply_markup=item_keyboard(items))


async def stats_cmd(update, context):
    if not is_admin(update) or not unlocked(context):
        return
    data = load_index(); counts = {}
    for x in data["items"]:
        counts[x["kind"]] = counts.get(x["kind"], 0) + 1
    await update.effective_message.reply_text("📊 文件统计\n" + ("\n".join(f"{k}: {v}" for k, v in counts.items()) if counts else "暂无数据"))


async def search_cmd(update, context):
    if not is_admin(update) or not unlocked(context):
        return
    term = " ".join(context.args).strip().lower()
    if not term:
        return await update.effective_message.reply_text("用法：/search 关键词")
    data = load_index()
    hits = [x for x in data["items"] if term in (x.get("file_name") or "").lower() or term in (x.get("caption") or "").lower()]
    hits = hits[-20:][::-1]
    if hits:
        await update.effective_message.reply_text("🔎 搜索结果", reply_markup=item_keyboard(hits))
    else:
        await update.effective_message.reply_text("没有找到。")


async def send_item(bot, chat_id, item):
    file_id = item.get("file_id")
    if not file_id:
        return False
    caption = item.get("caption") or None
    kind = item.get("kind")
    if kind == "photo":
        await bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)
    elif kind == "video":
        await bot.send_video(chat_id=chat_id, video=file_id, caption=caption)
    elif kind == "document":
        await bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
    elif kind == "audio":
        await bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption)
    elif kind == "voice":
        await bot.send_voice(chat_id=chat_id, voice=file_id, caption=caption)
    else:
        return False
    return True


async def get_item(update, context, iid):
    data = load_index()
    item = next((x for x in data["items"] if x["id"] == iid), None)
    if not item:
        return await update.callback_query.message.reply_text("找不到该文件记录。")
    try:
        if await send_item(context.bot, update.effective_chat.id, item):
            return
        # Compatibility for older records created before file_id-only mode.
        vault = data.get("vault_chat_id")
        old_mid = item.get("vault_message_id")
        if vault and old_mid:
            await context.bot.copy_message(chat_id=update.effective_chat.id, from_chat_id=vault, message_id=old_mid)
            return
        await update.callback_query.message.reply_text("❌ 该旧记录没有可用的文件 ID。")
    except Exception as exc:
        await update.callback_query.message.reply_text(f"❌ 取回失败：{exc}")


async def button(update, context):
    q = update.callback_query
    await q.answer()
    if q.data.startswith("usdt:"):
        text = await asyncio.to_thread(usdt_text)
        await q.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 刷新", callback_data="usdt:refresh")]]))
        return
    if not is_admin(update):
        return
    if not unlocked(context):
        return await q.message.reply_text("🔒 私人入口已锁定。")
    if q.data == "recent":
        return await list_cmd(update, context)
    if q.data == "stats":
        return await stats_cmd(update, context)
    if q.data == "notes":
        data = load_index(); notes = data["notes"][-10:][::-1]
        return await q.message.reply_text("📝 最近私人信息\n" + ("\n".join(f"#{n['id']} {n['text']}" for n in notes) if notes else "暂无信息。"))
    if q.data == "search_help":
        return await q.message.reply_text("🔎 用法：/search 关键词")
    if q.data == "lock":
        lock(context); return await q.message.reply_text("🔒 已锁定。")
    if q.data.startswith("get:"):
        return await get_item(update, context, int(q.data.split(":", 1)[1]))


async def text_router(update, context):
    text = (update.effective_message.text or "").strip()
    if is_admin(update) and re.fullmatch(r"\d{4}", text):
        if text == HIDDEN_PIN:
            unlock(context)
            await update.effective_message.reply_text(f"🔓 私人入口已解锁（{SESSION_MINUTES}分钟）", reply_markup=private_menu())
        return
    result = calculate(text)
    if result:
        await update.effective_message.reply_text(result)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("lock", lock_cmd))
    app.add_handler(CommandHandler("save", save_note_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE, media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    log.info("KuKu bot starting with invisible Telegram file_id storage + encrypted GitHub index")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
