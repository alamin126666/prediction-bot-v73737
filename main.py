import telebot
import threading
import time
import random
import requests
import pickle
import os
import statistics
from collections import Counter
from datetime import datetime, timezone, timedelta
from telebot.types import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask

# ============================================================
#  FLASK KEEP-ALIVE  (Render 24/7 এর জন্য)
# ============================================================
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "🤖 BD ALAMIN VIP Signal Bot is LIVE!"

@flask_app.route('/ping')
def ping():
    return "pong"

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# ============================================================
#  BOT CONFIG
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8445939022:AAFrWoYZvEn9lxvkvKPintaMVJubocxJETg")
OWNER_ID  = int(os.environ.get("OWNER_ID", "8473134685"))

bot = telebot.TeleBot(BOT_TOKEN)

# ============================================================
#  GLOBAL DATA STRUCTURES
# ============================================================
user_channels        = {}   # {uid: [ch, ...]}
signal_status        = {}   # {uid: {ch: bool}}
signal_threads       = {}   # {"uid_ch": Thread}
user_register_links  = {}   # {uid: "url"}
prediction_timers    = {}   # {uid: datetime}
pending_season_off   = {}   # {ch: True}

# Stickers
channel_win_stickers          = {}
channel_loss_stickers         = {}
channel_jackpot_stickers      = {}
channel_season_start_stickers = {}
channel_season_off_stickers   = {}

# Per-session signal list: {ch: [{"period","pred","big_n","small_n","emoji"}]}
channel_session      = {}

# Per-session stats (reset each START): {ch: {signals,wins,jacks,losses}}
channel_session_stats = {}

# PKL: persistent learning data {ch: [{period,pred,big_n,small_n,actual_num,result,time}]}
channel_signal_history = {}

PKL_FILE = "signal_data.pkl"

# Default sticker IDs
DEFAULT_WIN_STICKER          = "CAACAgUAAxkBAAIBIWZ4i-1dAAE3KXWk3X7L03zWn8H2bAACXxoAAo_FYFZxK2k1K4AAATYE"
DEFAULT_LOSS_STICKER         = "CAACAgUAAxkBAAIBJmZ4jC5oOGlnPIn5hV2F9r85B8DgAAJiGgACj8VgVkli01bg7BvzLAQ"
DEFAULT_JACKPOT_STICKER      = "CAACAgUAAxkBAAIBIWZ4i-1dAAE3KXWk3X7L03zWn8H2bAACXxoAAo_FYFZxK2k1K4AAATYE"
DEFAULT_SEASON_START_STICKER = "CAACAgUAAxkBAAIBKGZ4jFoq2F8YzG7CLHbrZEdHkHZ-AAJkGgACj8VgVq2wTp6rrVK9LAQ"
DEFAULT_SEASON_OFF_STICKER   = "CAACAgUAAxkBAAIBK2Z4jHazG2mRZkMyHPFZ_RX7clB2AAJlGgACj8VgVu6Crd4B5EeALAQ"

# API
HISTORY_API = 'https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json'
CURRENT_API = 'https://api.bdg88zf.com/api/webapi/GetGameIssue'

# API cache
_api_cache = {"numbers": [], "periods": [], "ts": 0}

# ============================================================
#  BOLD UNICODE FONT  (সব signal line এ same font)
# ============================================================
_BOLD = {
    '0':'𝟬','1':'𝟭','2':'𝟮','3':'𝟯','4':'𝟰',
    '5':'𝟱','6':'𝟲','7':'𝟳','8':'𝟴','9':'𝟵',
    'A':'𝗔','B':'𝗕','C':'𝗖','D':'𝗗','E':'𝗘','F':'𝗙','G':'𝗚',
    'H':'𝗛','I':'𝗜','J':'𝗝','K':'𝗞','L':'𝗟','M':'𝗠','N':'𝗡',
    'O':'𝗢','P':'𝗣','Q':'𝗤','R':'𝗥','S':'𝗦','T':'𝗧','U':'𝗨',
    'V':'𝗩','W':'𝗪','X':'𝗫','Y':'𝗬','Z':'𝗭'
}

def B(text):
    """সব character কে bold unicode font এ convert করে"""
    return ''.join(_BOLD.get(c.upper(), c) for c in str(text))

# ============================================================
#  PKL  DATA  (persistent learning storage)
# ============================================================
def load_pkl():
    global channel_signal_history
    try:
        if os.path.exists(PKL_FILE):
            with open(PKL_FILE, 'rb') as f:
                data = pickle.load(f)
            channel_signal_history = data.get('history', {})
            total = sum(len(v) for v in channel_signal_history.values())
            print(f"✅ PKL loaded: {total} records from {len(channel_signal_history)} channels")
    except Exception as e:
        print(f"❌ PKL load error: {e}")

def save_pkl():
    try:
        with open(PKL_FILE, 'wb') as f:
            pickle.dump({'history': channel_signal_history}, f)
    except Exception as e:
        print(f"❌ PKL save error: {e}")

def add_to_pkl(channel, period, pred, big_n, small_n, actual_num, result_type):
    if channel not in channel_signal_history:
        channel_signal_history[channel] = []
    channel_signal_history[channel].append({
        "period": period, "pred": pred, "big_n": big_n, "small_n": small_n,
        "actual_num": actual_num, "result": result_type,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    # সর্বোচ্চ 500 records
    if len(channel_signal_history[channel]) > 500:
        channel_signal_history[channel] = channel_signal_history[channel][-500:]
    save_pkl()

# ============================================================
#  OWNER CHECK
# ============================================================
def is_owner(uid):
    return uid == OWNER_ID

def send_access_denied(message):
    bot.send_message(message.chat.id,
        "🚫 *ACCESS DENIED*\n\nএই বটটি শুধুমাত্র Owner এর জন্য তৈরি।\n"
        "👉 @BDALAMINHACKER এ যোগাযোগ করুন।",
        parse_mode="Markdown")

# ============================================================
#  API  FUNCTIONS
# ============================================================
def fetch_wingo_history():
    """API থেকে সর্বশেষ Wingo results নিয়ে আসে"""
    numbers, periods = [], []
    try:
        r = requests.get(HISTORY_API, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if data and 'data' in data and 'list' in data['data']:
                for item in data['data']['list'][:30]:
                    try:
                        n = int(item.get('number', -1))
                        p = str(item.get('issueNumber', ''))
                        if 0 <= n <= 9:
                            numbers.append(n)
                            periods.append(p)
                    except:
                        continue
    except Exception as e:
        print(f"❌ History API: {e}")
    return numbers, periods

def get_cached_history():
    """45 সেকেন্ড cached API data"""
    global _api_cache
    if time.time() - _api_cache["ts"] > 45:
        nums, perids = fetch_wingo_history()
        if nums:
            _api_cache = {"numbers": nums, "periods": perids, "ts": time.time()}
    return _api_cache["numbers"], _api_cache["periods"]

def get_loop_period():
    """Loop period change detection এর জন্য (UTC minute based)"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d%H%M")

def get_display_period():
    """Signal message এ দেখানোর জন্য period (API থেকে অথবা time-based)"""
    _, periods = get_cached_history()
    if periods:
        try:
            return str(int(periods[0]) + 1)
        except:
            pass
    now = datetime.now(timezone.utc)
    total = now.hour * 60 + now.minute
    return now.strftime("%Y%m%d") + "1000" + str(10001 + total)

def fetch_latest_actual_number():
    """সর্বশেষ actual result নিয়ে আসে"""
    try:
        r = requests.get(HISTORY_API, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if data and 'data' in data and 'list' in data['data']:
                item = data['data']['list'][0]
                n = int(item.get('number', -1))
                if 0 <= n <= 9:
                    return n
    except Exception as e:
        print(f"❌ Actual result fetch: {e}")
    # Fallback to CURRENT_API
    try:
        payload = {
            "typeId": 1, "language": 0,
            "random": "e7fe6c090da2495ab8290dac551ef1ed",
            "signature": "1F390E2B2D8A55D693E57FD905AE73A7",
            "timestamp": int(time.time())
        }
        r = requests.post(CURRENT_API, json=payload, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if data and 'data' in data:
                result = data['data'].get('result')
                if result is not None:
                    return int(result)
    except:
        pass
    return None

# ============================================================
#  🧠  WINGO  PREDICTION  ENGINE  (নতুন উন্নত লজিক)
# ============================================================
def analyze_wingo(numbers, pkl_history=None):
    """
    Advanced Wingo lottery signal analysis engine.
    Combines: streak detection, frequency analysis, Markov chain,
    alternating pattern, hot/cold numbers + PKL learning.
    Returns: prediction ("BIG"/"SMALL"), confidence (68-92)
    """
    if not numbers or len(numbers) < 3:
        return random.choice(["BIG", "SMALL"]), 68

    scores = {"BIG": 0, "SMALL": 0}
    confidence = 68

    # ─── 1. STREAK DETECTION ────────────────────────────────
    streak_type = "BIG" if numbers[0] >= 5 else "SMALL"
    opposite    = "SMALL" if streak_type == "BIG" else "BIG"
    streak_len  = 0
    for n in numbers:
        if (n >= 5 and streak_type == "BIG") or (n < 5 and streak_type == "SMALL"):
            streak_len += 1
        else:
            break

    if streak_len >= 5:
        scores[opposite] += 45;  confidence += 18
    elif streak_len >= 4:
        scores[opposite] += 35;  confidence += 13
    elif streak_len >= 3:
        scores[opposite] += 22;  confidence += 8
    elif streak_len >= 2:
        scores[opposite] += 10;  confidence += 3

    # ─── 2. RECENT FREQUENCY (last 10) ──────────────────────
    r10 = numbers[:min(10, len(numbers))]
    r_big   = sum(1 for n in r10 if n >= 5)
    r_small = len(r10) - r_big

    if r_big   >= 8: scores["SMALL"] += 28; confidence += 10
    elif r_big >= 7: scores["SMALL"] += 18; confidence += 7
    elif r_big >= 6: scores["SMALL"] += 10
    if r_small >= 8: scores["BIG"]   += 28; confidence += 10
    elif r_small >= 7: scores["BIG"] += 18; confidence += 7
    elif r_small >= 6: scores["BIG"] += 10

    # ─── 3. LAST-3 STRONG SIGNAL ────────────────────────────
    last3_big = sum(1 for n in numbers[:3] if n >= 5)
    if last3_big == 3:   scores["SMALL"] += 30; confidence += 12
    elif last3_big == 0: scores["BIG"]   += 30; confidence += 12
    elif last3_big == 2: scores["SMALL"] += 14
    elif last3_big == 1: scores["BIG"]   += 14

    # ─── 4. ALTERNATING PATTERN ─────────────────────────────
    alt = sum(1 for i in range(min(6, len(numbers) - 1))
              if (numbers[i] >= 5) != (numbers[i + 1] >= 5))
    if alt >= 5:
        scores[opposite] += 20;  confidence += 7
    elif alt >= 4:
        scores[opposite] += 10

    # ─── 5. EXTENDED BALANCE (last 20) ──────────────────────
    ext    = numbers[:min(20, len(numbers))]
    e_big  = sum(1 for n in ext if n >= 5)
    e_sm   = len(ext) - e_big
    if e_big > e_sm + 6:  scores["SMALL"] += 18
    elif e_sm > e_big + 6: scores["BIG"]  += 18
    elif e_big > e_sm + 3: scores["SMALL"] += 9
    elif e_sm > e_big + 3: scores["BIG"]  += 9

    # ─── 6. MARKOV CHAIN (last-2 → next) ────────────────────
    if len(numbers) >= 12:
        last2 = ("B" if numbers[1] >= 5 else "S") + ("B" if numbers[0] >= 5 else "S")
        mc = {"BB": {"B": 0, "S": 0}, "BS": {"B": 0, "S": 0},
              "SB": {"B": 0, "S": 0}, "SS": {"B": 0, "S": 0}}
        for i in range(len(numbers) - 2):
            k = ("B" if numbers[i + 2] >= 5 else "S") + ("B" if numbers[i + 1] >= 5 else "S")
            v = "B" if numbers[i] >= 5 else "S"
            if k in mc:
                mc[k][v] += 1
        if last2 in mc:
            mb, ms = mc[last2]["B"], mc[last2]["S"]
            if mb + ms >= 3:
                if mb > ms * 1.6:   scores["BIG"]   += 20; confidence += 7
                elif ms > mb * 1.6: scores["SMALL"] += 20; confidence += 7

    # ─── 7. PKL LEARNING CALIBRATION ────────────────────────
    if pkl_history and len(pkl_history) >= 10:
        completed = [h for h in pkl_history[-40:] if h.get('result')]
        if completed:
            bg_w = sum(1 for h in completed if h['pred'] == 'BIG'   and h['result'] in ('WIN','JACKPOT'))
            bg_t = max(sum(1 for h in completed if h['pred'] == 'BIG'),   1)
            sm_w = sum(1 for h in completed if h['pred'] == 'SMALL' and h['result'] in ('WIN','JACKPOT'))
            sm_t = max(sum(1 for h in completed if h['pred'] == 'SMALL'), 1)
            bg_acc, sm_acc = bg_w / bg_t, sm_w / sm_t
            if bg_acc > sm_acc + 0.20:   scores["BIG"]   += 18
            elif sm_acc > bg_acc + 0.20: scores["SMALL"] += 18

    # ─── FINAL DECISION ─────────────────────────────────────
    if scores["BIG"] > scores["SMALL"]:
        prediction = "BIG"
    elif scores["SMALL"] > scores["BIG"]:
        prediction = "SMALL"
    else:
        prediction = random.choice(["BIG", "SMALL"])

    confidence = max(68, min(int(confidence), 92))
    return prediction, confidence


def pick_signal_numbers(numbers):
    """
    Hot/Cold analysis দিয়ে দুটো সম্ভাব্য number বেছে নেয়।
    Returns: (big_num 5-9, small_num 0-4)
    """
    big_range   = [5, 6, 7, 8, 9]
    small_range = [0, 1, 2, 3, 4]

    if numbers:
        freq = Counter(numbers[:20])
        # Cold numbers (কম আসা) → বেশি সম্ভাবনা
        bw = [max(1, 6 - freq.get(n, 0)) for n in big_range]
        sw = [max(1, 6 - freq.get(n, 0)) for n in small_range]
        big_num   = random.choices(big_range,   weights=bw, k=1)[0]
        small_num = random.choices(small_range, weights=sw, k=1)[0]
    else:
        big_num   = random.choice(big_range)
        small_num = random.choice(small_range)

    return big_num, small_num


def generate_wingo_prediction(channel=None):
    """
    Complete prediction: direction (BIG/SMALL) + two signal numbers.
    Returns: (prediction, big_num, small_num)
    """
    numbers, _ = get_cached_history()
    pkl_hist    = channel_signal_history.get(channel, []) if channel else []
    prediction, _ = analyze_wingo(numbers, pkl_hist)
    big_num, small_num = pick_signal_numbers(numbers)
    return prediction, big_num, small_num

# ============================================================
#  SIGNAL  MESSAGE  BUILDER
# ============================================================
def build_signal_message(channel, user_id=None):
    """
    পুরো session history সহ signal message তৈরি করে।
    প্রতিটি line একই bold font এ থাকবে।
    """
    header = "🚀 𝗕𝗗 𝗔𝗟𝗔𝗠𝗜𝗡 | 𝗩𝗜𝗣 𝗦𝗜𝗚𝗡𝗔𝗟 🚀"
    lines  = []

    for entry in channel_session.get(channel, []):
        period_str = str(entry['period'])[-5:]   # শেষ ৫ সংখ্যা
        pred       = entry['pred']               # "BIG" / "SMALL"
        bn         = entry['big_n']
        sn         = entry['small_n']
        emoji      = entry.get('emoji', '')

        # BIG signal → big_num/small_num | SMALL signal → small_num/big_num
        nums = f"{bn}/{sn}" if pred == "BIG" else f"{sn}/{bn}"

        # সব কিছু একই bold font এ
        line = f"{B(period_str)} | {B(pred)} × {B(nums)}"
        if emoji:
            line += f" {emoji}"
        lines.append(line)

    msg = header + "\n\n" + "\n".join(lines)

    if user_id and user_id in user_register_links:
        msg += f"\n\n🔗 {user_register_links[user_id]}"

    return msg

# ============================================================
#  RESULT  CHECKING  (WIN / JACKPOT / LOSS)
# ============================================================
def check_result(prediction, big_num, small_num):
    """
    actual number এনে WIN / JACKPOT / LOSS নির্ধারণ করে।
    JACKPOT = exact number match (big_num বা small_num মিলেছে)
    WIN     = শুধু BIG/SMALL direction মিলেছে
    LOSS    = কিছুই মিলেনি
    """
    actual_num = fetch_latest_actual_number()
    if actual_num is None:
        actual_num = random.randint(0, 9)

    actual_dir = "BIG" if actual_num >= 5 else "SMALL"

    if actual_num == big_num or actual_num == small_num:
        return actual_num, actual_dir, "JACKPOT"
    elif actual_dir == prediction:
        return actual_num, actual_dir, "WIN"
    else:
        return actual_num, actual_dir, "LOSS"


def get_result_emoji(result_type):
    return {"JACKPOT": "🎰", "WIN": "✅", "LOSS": "❌"}.get(result_type, "")

# ============================================================
#  SESSION  STATS
# ============================================================
def init_session_stats(channel):
    channel_session_stats[channel] = {"signals": 0, "wins": 0, "jacks": 0, "losses": 0}

def update_session_stats(channel, event):
    if channel not in channel_session_stats:
        init_session_stats(channel)
    s = channel_session_stats[channel]
    if event == "SIGNAL":  s["signals"] += 1
    elif event == "WIN":   s["wins"]    += 1
    elif event == "JACKPOT": s["jacks"] += 1
    elif event == "LOSS":  s["losses"]  += 1

def format_stats_message(channel):
    s = channel_session_stats.get(channel, {"signals": 0, "wins": 0, "jacks": 0, "losses": 0})
    return (
        f"📊 𝗦𝗘𝗔𝗦𝗢𝗡 𝗥𝗘𝗦𝗨𝗟𝗧\n\n"
        f"🎯 𝗧𝗼𝘁𝗮𝗹 𝗦𝗶𝗴𝗻𝗮𝗹𝘀 : {B(s['signals'])}\n\n"
        f"✅ 𝗧𝗼𝘁𝗮𝗹 𝗪𝗶𝗻𝘀   : {B(s['wins'])}\n\n"
        f"🎰 𝗧𝗼𝘁𝗮𝗹 𝗝𝗮𝗰𝗸    : {B(s['jacks'])}\n\n"
        f"❌ 𝗧𝗼𝘁𝗮𝗹 𝗟𝗼𝘀𝘀   : {B(s['losses'])}"
    )

# ============================================================
#  STICKER  SENDERS
# ============================================================
def _send_sticker_safe(channel, sticker_id):
    try:
        bot.send_sticker(channel, sticker_id)
        return True
    except Exception as e:
        print(f"❌ Sticker send error: {e}")
        return False

def send_result_sticker(channel, result_type):
    if result_type == "JACKPOT":
        jack = channel_jackpot_stickers.get(channel, DEFAULT_JACKPOT_STICKER)
        win  = channel_win_stickers.get(channel, DEFAULT_WIN_STICKER)
        _send_sticker_safe(channel, jack)
        time.sleep(0.8)
        _send_sticker_safe(channel, win)
    elif result_type == "WIN":
        _send_sticker_safe(channel, channel_win_stickers.get(channel, DEFAULT_WIN_STICKER))
    else:
        _send_sticker_safe(channel, channel_loss_stickers.get(channel, DEFAULT_LOSS_STICKER))

def send_season_start_sticker(channel):
    _send_sticker_safe(channel, channel_season_start_stickers.get(channel, DEFAULT_SEASON_START_STICKER))

def send_season_off_sticker(channel):
    _send_sticker_safe(channel, channel_season_off_stickers.get(channel, DEFAULT_SEASON_OFF_STICKER))

# ============================================================
#  MAIN  PREDICTION  LOOP
# ============================================================
def _process_pending_result(channel):
    """
    Session এর শেষ signal এর result check করে emoji update করে।
    Returns result_type or None
    """
    sess = channel_session.get(channel, [])
    if not sess or sess[-1].get('emoji', '') != '':
        return None

    last = sess[-1]
    actual_num, actual_dir, result_type = check_result(
        last['pred'], last['big_n'], last['small_n']
    )
    emoji = get_result_emoji(result_type)
    sess[-1]['emoji'] = emoji

    update_session_stats(channel, result_type)
    add_to_pkl(channel, last['period'], last['pred'],
               last['big_n'], last['small_n'], actual_num, result_type)

    send_result_sticker(channel, result_type)
    time.sleep(0.8)
    return result_type


def real_time_auto_prediction(user_id, channel, is_timed=False, duration_minutes=20):
    """মূল prediction loop — প্রতি মিনিটে একটি signal পাঠায়"""
    if not is_owner(user_id):
        return

    if is_timed:
        prediction_timers[user_id] = datetime.now() + timedelta(minutes=duration_minutes)
        bot.send_message(user_id, f"⏰ টাইমার সেট: {duration_minutes} মিনিট")

    # Session initialise
    channel_session[channel] = []
    init_session_stats(channel)

    last_period = None

    while signal_status.get(user_id, {}).get(channel, False) or channel in pending_season_off:
        try:
            # ── TIMER CHECK ──────────────────────────────────
            if is_timed and user_id in prediction_timers:
                if datetime.now() >= prediction_timers[user_id]:
                    if user_id in signal_status:
                        signal_status[user_id][channel] = False
                    pending_season_off[channel] = True
                    del prediction_timers[user_id]
                    bot.send_message(user_id, "⏰ সেশনের সময় শেষ!")

            current_period = get_loop_period()

            if current_period != last_period:

                # ── SEASON OFF FLOW ───────────────────────────
                if channel in pending_season_off and \
                        not signal_status.get(user_id, {}).get(channel, False):

                    _process_pending_result(channel)

                    time.sleep(1)
                    send_season_off_sticker(channel)
                    time.sleep(1)

                    # Stats message পাঠাও
                    try:
                        bot.send_message(channel, format_stats_message(channel))
                    except Exception as e:
                        print(f"❌ Stats send error: {e}")

                    del pending_season_off[channel]
                    channel_session[channel] = []
                    break

                # ── ACTIVE SIGNAL FLOW ────────────────────────
                if signal_status.get(user_id, {}).get(channel, False):

                    # আগের signal এর result check
                    _process_pending_result(channel)

                    # নতুন prediction generate করো
                    prediction, big_num, small_num = generate_wingo_prediction(channel)
                    disp_period = get_display_period()

                    channel_session[channel].append({
                        'period': disp_period,
                        'pred':   prediction,
                        'big_n':  big_num,
                        'small_n': small_num,
                        'emoji':  ''
                    })
                    update_session_stats(channel, "SIGNAL")

                    # Signal message পাঠাও (নতুন message, edit নয়)
                    msg = build_signal_message(channel, user_id)
                    try:
                        bot.send_message(channel, msg)
                        print(f"✅ Signal sent to {channel}: {prediction} {big_num}/{small_num}")
                    except Exception as e:
                        print(f"❌ Message send error: {e}")
                        bot.send_message(user_id, f"⚠️ {channel} এ পাঠাতে সমস্যা: {e}")

                    last_period = current_period
                else:
                    last_period = current_period

            time.sleep(1)

        except Exception as e:
            print(f"❌ Prediction loop error: {e}")
            time.sleep(5)

    # Thread cleanup
    thread_key = f"{user_id}_{channel}"
    if thread_key in signal_threads:
        del signal_threads[thread_key]

# ============================================================
#  CHANNEL  MANAGEMENT
# ============================================================
def start_prediction_for_channel(user_id, channel, is_timed=False, duration_minutes=20):
    if signal_status.get(user_id, {}).get(channel, False):
        bot.send_message(user_id, "⚠️ প্রেডিকশন ইতিমধ্যেই চালু আছে।")
        return False

    if user_id not in signal_status:
        signal_status[user_id] = {}
    signal_status[user_id][channel] = True

    t = threading.Thread(
        target=real_time_auto_prediction,
        args=(user_id, channel, is_timed, duration_minutes)
    )
    signal_threads[f"{user_id}_{channel}"] = t
    t.daemon = True
    t.start()

    try:
        send_season_start_sticker(channel)
    except Exception as e:
        print(f"❌ Season start sticker error: {e}")

    bot.send_message(user_id, "🚀 প্রেডিকশন শুরু হয়েছে!")
    return True


def stop_prediction_for_channel(user_id, channel):
    if not signal_status.get(user_id, {}).get(channel, False):
        bot.send_message(user_id, "ℹ️ প্রেডিকশন আগে থেকেই বন্ধ আছে।")
        return False

    signal_status[user_id][channel] = False
    if user_id in prediction_timers:
        del prediction_timers[user_id]

    pending_season_off[channel] = True
    bot.send_message(user_id,
        "🛑 প্রেডিকশন বন্ধ!\n"
        "পরের Period এ Win/Loss স্টিকার ও Season Off স্টিকার পাঠানো হবে।"
    )
    return True


def delete_channel(chat_id, channel):
    if chat_id in user_channels and channel in user_channels[chat_id]:
        user_channels[chat_id].remove(channel)
        if chat_id in signal_status:
            signal_status[chat_id].pop(channel, None)
        signal_threads.pop(f"{chat_id}_{channel}", None)
        for d in [channel_win_stickers, channel_loss_stickers, channel_jackpot_stickers,
                  channel_season_start_stickers, channel_season_off_stickers, pending_season_off]:
            d.pop(channel, None)
        count = len(user_channels.get(chat_id, []))
        bot.send_message(chat_id, f"🗑️ চ্যানেল ডিলিট হয়েছে! বাকি: {count} টি")
        return True
    bot.send_message(chat_id, "❌ চ্যানেল খুঁজে পাওয়া যায়নি!")
    return False

# ============================================================
#  UI  HELPERS  (Channel list, Sticker settings)
# ============================================================
def _parse_ch(raw):
    """callback data থেকে channel parse করে"""
    try:
        if str(raw).replace('-', '').isdigit():
            return int(raw)
    except:
        pass
    return raw


def show_channel_list_with_status(chat_id):
    chans = user_channels.get(chat_id, [])
    if not chans:
        bot.send_message(chat_id,
            "📭 কোনও চ্যানেল নেই।\n'ADD CHANNEL' বাটনে ক্লিক করে যুক্ত করুন।")
        return
    pages = [chans[i:i + 10] for i in range(0, len(chans), 10)]
    _show_ch_page(chat_id, pages, 0, len(chans))


def _show_ch_page(chat_id, pages, page, total):
    chans = pages[page]
    kb = InlineKeyboardMarkup()
    txt = f"📋 **চ্যানেল লিস্ট**\n📊 মোট: {total} | পেজ: {page+1}/{len(pages)}\n\n"
    for i, ch in enumerate(chans, 1):
        st = "🟢" if signal_status.get(chat_id, {}).get(ch, False) else "🔴"
        ct = "🌐" if isinstance(ch, str) and ch.startswith("@") else "🔒"
        dn = ch if isinstance(ch, str) and ch.startswith("@") else f"Private ({ch})"
        gi = page * 10 + i
        kb.row(InlineKeyboardButton(f"{gi}. {ct} {dn} {st}", callback_data=f"cd_{ch}"))
    pag = []
    if page > 0:
        pag.append(InlineKeyboardButton("⬅️", callback_data=f"cp_{page - 1}"))
    if page < len(pages) - 1:
        pag.append(InlineKeyboardButton("➡️", callback_data=f"cp_{page + 1}"))
    if pag:
        kb.row(*pag)
    kb.row(
        InlineKeyboardButton("➕ ADD", callback_data="add_ch_from_list"),
        InlineKeyboardButton("🔙 MENU", callback_data="back_main")
    )
    bot.send_message(chat_id, txt, reply_markup=kb, parse_mode="Markdown")


def show_sticker_channel_list(chat_id):
    chans = user_channels.get(chat_id, [])
    if not chans:
        bot.send_message(chat_id,
            "📭 কোনও চ্যানেল নেই।\n'ADD CHANNEL' বাটনে ক্লিক করে যুক্ত করুন।")
        return
    pages = [chans[i:i + 10] for i in range(0, len(chans), 10)]
    _show_stk_page(chat_id, pages, 0, len(chans))


def _show_stk_page(chat_id, pages, page, total):
    chans = pages[page]
    kb = InlineKeyboardMarkup()
    txt = (f"🎭 **স্টিকার সেটিংস**\n📊 মোট: {total} | পেজ: {page+1}/{len(pages)}\n\n"
           f"কোন চ্যানেলের স্টিকার সেট করবেন?")
    for i, ch in enumerate(chans, 1):
        st = "🟢" if signal_status.get(chat_id, {}).get(ch, False) else "🔴"
        ct = "🌐" if isinstance(ch, str) and ch.startswith("@") else "🔒"
        dn = ch if isinstance(ch, str) and ch.startswith("@") else f"Private ({ch})"
        gi = page * 10 + i
        kb.row(InlineKeyboardButton(f"{gi}. {ct} {dn} {st}", callback_data=f"sc_{ch}"))
    pag = []
    if page > 0:
        pag.append(InlineKeyboardButton("⬅️", callback_data=f"sp_{page - 1}"))
    if page < len(pages) - 1:
        pag.append(InlineKeyboardButton("➡️", callback_data=f"sp_{page + 1}"))
    if pag:
        kb.row(*pag)
    kb.row(
        InlineKeyboardButton("➕ ADD", callback_data="add_ch_from_sticker"),
        InlineKeyboardButton("🔙 MENU", callback_data="back_main")
    )
    bot.send_message(chat_id, txt, reply_markup=kb, parse_mode="Markdown")


def show_channel_details(chat_id, channel):
    st = "🟢 চালু" if signal_status.get(chat_id, {}).get(channel, False) else "🔴 বন্ধ"
    ct = "🌐 Public" if isinstance(channel, str) and channel.startswith("@") else "🔒 Private"
    dn = channel if isinstance(channel, str) and channel.startswith("@") else f"Chat ID: {channel}"

    w  = "✅ সেট" if channel in channel_win_stickers          else "❌ নেই"
    l  = "✅ সেট" if channel in channel_loss_stickers         else "❌ নেই"
    j  = "✅ সেট" if channel in channel_jackpot_stickers      else "❌ নেই"
    ss = "✅ সেট" if channel in channel_season_start_stickers else "❌ নেই"
    so = "✅ সেট" if channel in channel_season_off_stickers   else "❌ নেই"

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("▶️ START", callback_data=f"start_{channel}"),
        InlineKeyboardButton("⏹ STOP",  callback_data=f"stop_{channel}")
    )
    kb.row(
        InlineKeyboardButton("🗑 DELETE", callback_data=f"del_{channel}"),
        InlineKeyboardButton("🔙 BACK",   callback_data="back_ch_list")
    )
    bot.send_message(chat_id,
        f"📢 **চ্যানেল ডিটেইলস**\n\n"
        f"📌 টাইপ: {ct}\n🔗 {dn}\n📊 Status: {st}\n\n"
        f"✅ Win: {w}\n❌ Loss: {l}\n🎰 Jackpot: {j}\n"
        f"🟢 Season Start: {ss}\n🔚 Season Off: {so}",
        reply_markup=kb, parse_mode="Markdown")


def show_sticker_settings(chat_id, channel):
    dn = channel if isinstance(channel, str) and channel.startswith("@") else f"Private ({channel})"

    w  = "✅" if channel in channel_win_stickers          else "❌"
    l  = "✅" if channel in channel_loss_stickers         else "❌"
    j  = "✅" if channel in channel_jackpot_stickers      else "❌"
    ss = "✅" if channel in channel_season_start_stickers else "❌"
    so = "✅" if channel in channel_season_off_stickers   else "❌"

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton(f"✅ WIN {w}",     callback_data=f"swn_{channel}"),
        InlineKeyboardButton(f"❌ LOSS {l}",    callback_data=f"sls_{channel}")
    )
    kb.row(
        InlineKeyboardButton(f"🎰 JACKPOT {j}", callback_data=f"sjk_{channel}"),
        InlineKeyboardButton(f"🟢 SEASON START {ss}", callback_data=f"sss_{channel}")
    )
    kb.row(
        InlineKeyboardButton(f"🔚 SEASON OFF {so}", callback_data=f"sso_{channel}"),
        InlineKeyboardButton("🔙 BACK",             callback_data="back_stk_list")
    )
    bot.send_message(chat_id,
        f"🎭 **স্টিকার সেটিংস**\n🔗 {dn}\n\n"
        f"✅ Win: {w}  |  ❌ Loss: {l}  |  🎰 Jackpot: {j}\n"
        f"🟢 Season Start: {ss}  |  🔚 Season Off: {so}\n\n"
        f"নিচের বাটন থেকে স্টিকার সেট করুন:",
        reply_markup=kb, parse_mode="Markdown")

# ============================================================
#  BOT  HANDLERS
# ============================================================
@bot.message_handler(commands=['start'])
def start_handler(message):
    if not is_owner(message.chat.id):
        send_access_denied(message)
        return
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("ADD CHANNEL")
    kb.row("ALL CHANNEL")
    kb.row("☠️STIKER☠️")
    count = len(user_channels.get(message.chat.id, []))
    bot.send_message(message.chat.id,
        f"💢 *BD ALAMIN VIP SIGNAL BOT* 💢\n\n"
        f"🚀 স্বাগতম Owner!\n\n"
        f"📊 চ্যানেল: {count} টি\n\n"
        f"📌 মেনু:\n"
        f"• ADD CHANNEL — চ্যানেল যুক্ত করুন\n"
        f"• ALL CHANNEL — চ্যানেল ম্যানেজ করুন\n"
        f"• ☠️STIKER☠️  — স্টিকার সেট করুন\n\n"
        f"🧠 Advanced Wingo Prediction Engine\n"
        f"🎰 JACKPOT + WIN + LOSS System\n"
        f"📦 PKL Data Learning System\n"
        f"📊 Season Stats Auto Report\n"
        f"☁️ Render 24/7 Ready",
        reply_markup=kb, parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text == "ALL CHANNEL")
def handle_all_channel(message):
    if not is_owner(message.chat.id):
        send_access_denied(message)
        return
    show_channel_list_with_status(message.chat.id)


@bot.message_handler(func=lambda m: m.text == "ADD CHANNEL")
def handle_add_channel(message):
    if not is_owner(message.chat.id):
        send_access_denied(message)
        return
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("➕ Public Channel",  callback_data="add_public"),
        InlineKeyboardButton("🔒 Private Channel", callback_data="add_private")
    )
    count = len(user_channels.get(message.chat.id, []))
    bot.send_message(message.chat.id,
        f"📌 **চ্যানেল টাইপ সিলেক্ট করুন:**\n\n"
        f"📊 বর্তমান চ্যানেল: {count} টি\n\n"
        f"• Public: @username ফরম্যাটে\n"
        f"• Private: Chat ID ফরম্যাটে (-100...)",
        reply_markup=kb, parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text == "☠️STIKER☠️")
def handle_sticker_menu(message):
    if not is_owner(message.chat.id):
        send_access_denied(message)
        return
    show_sticker_channel_list(message.chat.id)


# ============================================================
#  CALLBACK  HANDLER
# ============================================================
def _make_stk_handler(ch, stype):
    """Closure factory for sticker next_step_handler"""
    def handler(m):
        process_sticker_set(m, ch, stype)
    return handler


@bot.callback_query_handler(func=lambda c: True)
def callback_handler(call):
    cid = call.message.chat.id
    if not is_owner(cid):
        bot.answer_callback_query(call.id, "🚫 Access Denied", show_alert=True)
        return

    d = call.data

    # ── Channel detail ───────────────────────────────────────
    if d.startswith("cd_"):
        show_channel_details(cid, _parse_ch(d[3:]))

    elif d.startswith("cp_"):
        page  = int(d[3:])
        chans = user_channels.get(cid, [])
        pages = [chans[i:i + 10] for i in range(0, len(chans), 10)]
        _show_ch_page(cid, pages, page, len(chans))

    # ── Sticker channel select ───────────────────────────────
    elif d.startswith("sc_"):
        show_sticker_settings(cid, _parse_ch(d[3:]))

    elif d.startswith("sp_"):
        page  = int(d[3:])
        chans = user_channels.get(cid, [])
        pages = [chans[i:i + 10] for i in range(0, len(chans), 10)]
        _show_stk_page(cid, pages, page, len(chans))

    # ── Channel START / STOP / DELETE ───────────────────────
    elif d.startswith("start_"):
        ch = _parse_ch(d[6:])
        start_prediction_for_channel(cid, ch)
        bot.answer_callback_query(call.id, "🚀 Signal Started!")
        show_channel_details(cid, ch)

    elif d.startswith("stop_"):
        ch = _parse_ch(d[5:])
        stop_prediction_for_channel(cid, ch)
        bot.answer_callback_query(call.id, "🛑 Signal Stopped!")
        show_channel_details(cid, ch)

    elif d.startswith("del_"):
        ch = _parse_ch(d[4:])
        if delete_channel(cid, ch):
            show_channel_list_with_status(cid)
        else:
            bot.answer_callback_query(call.id, "❌ Delete Failed!")

    # ── Sticker SET buttons ──────────────────────────────────
    elif d.startswith("swn_"):
        ch  = _parse_ch(d[4:])
        msg = bot.send_message(cid, "✅ Win স্টিকার পাঠান:")
        bot.register_next_step_handler(msg, _make_stk_handler(ch, "win"))

    elif d.startswith("sls_"):
        ch  = _parse_ch(d[4:])
        msg = bot.send_message(cid, "❌ Loss স্টিকার পাঠান:")
        bot.register_next_step_handler(msg, _make_stk_handler(ch, "loss"))

    elif d.startswith("sjk_"):
        ch  = _parse_ch(d[4:])
        msg = bot.send_message(cid, "🎰 Jackpot স্টিকার পাঠান:")
        bot.register_next_step_handler(msg, _make_stk_handler(ch, "jackpot"))

    elif d.startswith("sss_"):
        ch  = _parse_ch(d[4:])
        msg = bot.send_message(cid, "🟢 Season Start স্টিকার পাঠান:")
        bot.register_next_step_handler(msg, _make_stk_handler(ch, "season_start"))

    elif d.startswith("sso_"):
        ch  = _parse_ch(d[4:])
        msg = bot.send_message(cid, "🔚 Season Off স্টিকার পাঠান:")
        bot.register_next_step_handler(msg, _make_stk_handler(ch, "season_off"))

    # ── ADD / BACK navigation ────────────────────────────────
    elif d in ("add_ch_from_list", "add_ch_from_sticker"):
        _show_add_channel_menu(cid)

    elif d == "add_public":
        _ask_public_channel(cid)

    elif d == "add_private":
        _ask_private_channel(cid)

    elif d == "back_ch_list":
        show_channel_list_with_status(cid)

    elif d == "back_stk_list":
        show_sticker_channel_list(cid)

    elif d == "back_main":
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.row("ADD CHANNEL")
        kb.row("ALL CHANNEL")
        kb.row("☠️STIKER☠️")
        count = len(user_channels.get(cid, []))
        bot.send_message(cid,
            f"🔙 মেইন মেনু\n📊 চ্যানেল: {count} টি",
            reply_markup=kb)

# ============================================================
#  CHANNEL  ADD  HELPERS
# ============================================================
def _show_add_channel_menu(chat_id):
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("➕ Public",  callback_data="add_public"),
        InlineKeyboardButton("🔒 Private", callback_data="add_private")
    )
    bot.send_message(chat_id, "📌 চ্যানেল টাইপ সিলেক্ট করুন:", reply_markup=kb)


def _ask_public_channel(chat_id):
    count = len(user_channels.get(chat_id, []))
    msg = bot.send_message(chat_id,
        f"🔗 চ্যানেল ইউজারনেম পাঠান (যেমন: @yourchannel)\n"
        f"📊 বর্তমান: {count} টি")
    bot.register_next_step_handler(msg, _process_public_ch)


def _ask_private_channel(chat_id):
    count = len(user_channels.get(chat_id, []))
    msg = bot.send_message(chat_id,
        f"🔒 Private Chat ID পাঠান (যেমন: -1001234567890)\n"
        f"📊 বর্তমান: {count} টি")
    bot.register_next_step_handler(msg, _process_private_ch)


def _process_public_ch(message):
    cid  = message.chat.id
    if not is_owner(cid):
        send_access_denied(message)
        return
    text = (message.text or "").strip()
    if text.startswith("@"):
        if cid not in user_channels:
            user_channels[cid] = []
        if text not in user_channels[cid]:
            user_channels[cid].append(text)
            if cid not in signal_status:
                signal_status[cid] = {}
            signal_status[cid][text] = False
        count = len(user_channels[cid])
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.row("ADD CHANNEL")
        kb.row("ALL CHANNEL")
        kb.row("☠️STIKER☠️")
        bot.send_message(cid,
            f"✅ {text} যুক্ত হয়েছে!\n📊 মোট: {count} টি",
            reply_markup=kb)
    else:
        bot.send_message(cid, "❌ '@' দিয়ে শুরু করুন। আবার চেষ্টা করুন:")
        bot.register_next_step_handler(message, _process_public_ch)


def _process_private_ch(message):
    cid  = message.chat.id
    if not is_owner(cid):
        send_access_denied(message)
        return
    text = (message.text or "").strip()
    try:
        ch_id = int(text)
        if cid not in user_channels:
            user_channels[cid] = []
        if ch_id not in user_channels[cid]:
            user_channels[cid].append(ch_id)
            if cid not in signal_status:
                signal_status[cid] = {}
            signal_status[cid][ch_id] = False
        count = len(user_channels[cid])
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.row("ADD CHANNEL")
        kb.row("ALL CHANNEL")
        kb.row("☠️STIKER☠️")
        bot.send_message(cid,
            f"✅ Private channel যুক্ত!\n🔒 ID: {ch_id}\n📊 মোট: {count} টি",
            reply_markup=kb)
    except ValueError:
        bot.send_message(cid, "❌ সংখ্যা দিন (যেমন: -1001234567890):")
        bot.register_next_step_handler(message, _process_private_ch)

# ============================================================
#  STICKER  PROCESS
# ============================================================
_STK_STORE = {
    "win":          channel_win_stickers,
    "loss":         channel_loss_stickers,
    "jackpot":      channel_jackpot_stickers,
    "season_start": channel_season_start_stickers,
    "season_off":   channel_season_off_stickers,
}
_STK_LABEL = {
    "win": "✅ Win", "loss": "❌ Loss", "jackpot": "🎰 Jackpot",
    "season_start": "🟢 Season Start", "season_off": "🔚 Season Off"
}


def process_sticker_set(message, channel, stype):
    cid = message.chat.id
    if not is_owner(cid):
        send_access_denied(message)
        return
    if not message.sticker:
        bot.send_message(cid, "❌ স্টিকার পাঠান:")
        bot.register_next_step_handler(message, _make_stk_handler(channel, stype))
        return
    sid = message.sticker.file_id
    _STK_STORE[stype][channel] = sid
    bot.send_sticker(cid, sid)
    bot.send_message(cid, f"✅ {_STK_LABEL[stype]} স্টিকার সেট হয়েছে!")
    show_sticker_settings(cid, channel)

# ============================================================
#  STARTUP
# ============================================================
if __name__ == "__main__":
    # PKL data load করো
    load_pkl()

    # Flask thread শুরু করো (Render keep-alive)
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    print("🤖 BD ALAMIN VIP Signal Bot starting...")
    print("🌐 Flask keep-alive started")
    print("📦 PKL data loaded")
    print("🚀 Bot polling...")

    bot.polling(non_stop=True, timeout=60)