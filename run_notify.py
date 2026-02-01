import os
import json
import yaml
import html
import requests
import psycopg
import pandas as pd
from datetime import datetime, timezone


STATE_FILE = "notify_state.json"


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_conn(cfg):
    dsn = (
        f"host={cfg['db']['host']} port={cfg['db']['port']} "
        f"dbname={cfg['db']['name']} user={cfg['db']['user']} "
        f"password={cfg['db']['password']}"
    )
    return psycopg.connect(dsn)


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_created_at": None}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def tg_send(bot_token, chat_id, text, parse_mode="HTML", disable_web_page_preview=True):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }
    r = requests.post(url, json=payload, timeout=15)
    if not r.ok:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")
    return r.json()


def format_signal_msg(sig: dict) -> str:
    # HTML escape로 텔레그램 파싱 에러 원천 차단
    symbol = html.escape(str(sig["symbol"]))
    direction = html.escape(str(sig["direction"]).upper())
    tf = html.escape(str(sig["tf"]))
    strategy = html.escape(str(sig["strategy"]))
    score = html.escape(str(sig["score"]))

    open_time = sig["open_time"]
    created_at = sig["created_at"]

    # open_time/created_at은 보기 좋게 KST 표시
    def fmt_kst(dt):
        if dt is None:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        kst = dt.astimezone(timezone.utc).astimezone(timezone(timedelta(hours=9)))
        return kst.strftime("%Y-%m-%d %H:%M KST")

    # 안전하게 숫자 포맷
    def fnum(x):
        try:
            return f"{float(x):,.6f}".rstrip("0").rstrip(".")
        except Exception:
            return str(x)

    entry = fnum(sig["entry"])
    sl = fnum(sig["stop_loss"])
    tp1 = fnum(sig["take_profit_1"]) if sig["take_profit_1"] is not None else "-"
    tp2 = fnum(sig["take_profit_2"]) if sig["take_profit_2"] is not None else "-"

    # TradingView / Binance 링크(필요하면 교체 가능)
    # Binance Futures 심볼 페이지는 URL 패턴이 자주 바뀌니, TradingView 중심으로 제공 추천
    tv_symbol = symbol.replace("USDT", "USDT")  # 그대로
    tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{tv_symbol}"
    tv_link = html.escape(tv_link)

    # 방향별 아이콘
    icon = "🟢" if "LONG" in direction else "🔴"

    msg = (
        f"{icon} <b>{symbol}</b> <b>{direction}</b>  <code>{tf}</code>\n"
        f"<b>Strategy</b>: <code>{strategy}</code>\n"
        f"<b>Score</b>: <code>{score}</code>\n"
        f"<b>Signal Time</b>: <code>{fmt_kst(open_time)}</code>\n"
        f"<b>Entry</b>: <code>{html.escape(entry)}</code>\n"
        f"<b>SL</b>: <code>{html.escape(sl)}</code>\n"
        f"<b>TP1</b>: <code>{html.escape(tp1)}</code>\n"
        f"<b>TP2</b>: <code>{html.escape(tp2)}</code>\n"
        f"<b>TV</b>: {tv_link}\n"
    )
    return msg


def run_notify():
    cfg = load_config()
    tg = cfg["telegram"]
    bot_token = tg["bot_token"]
    chat_id = tg["chat_id"]
    parse_mode = tg.get("parse_mode", "HTML")
    disable_preview = bool(tg.get("disable_web_page_preview", True))

    state = load_state()
    last_created_at = state.get("last_created_at")

    with get_conn(cfg) as conn:
        if last_created_at:
            q = """
                SELECT symbol, tf, open_time, strategy, direction,
                       entry, stop_loss, take_profit_1, take_profit_2,
                       score, reason_json, created_at
                FROM signals
                WHERE created_at > %s
                ORDER BY created_at ASC
                LIMIT 100
            """
            df = pd.read_sql(q, conn, params=(last_created_at,))
        else:
            # 최초 실행: 최근 20개만 보내거나(원하면 0개로 시작도 가능)
            q = """
                SELECT symbol, tf, open_time, strategy, direction,
                       entry, stop_loss, take_profit_1, take_profit_2,
                       score, reason_json, created_at
                FROM signals
                ORDER BY created_at DESC
                LIMIT 20
            """
            df = pd.read_sql(q, conn)

    if df.empty:
        print("[INFO] No new signals to notify.")
        return

    # created_at 최신값으로 state 갱신 준비
    newest = df["created_at"].max()

    sent = 0
    for _, row in df.iterrows():
        sig = row.to_dict()
        text = format_signal_msg(sig)
        tg_send(bot_token, chat_id, text, parse_mode=parse_mode, disable_web_page_preview=disable_preview)
        sent += 1

    # 상태 저장 (중복 방지)
    state["last_created_at"] = newest.isoformat()
    save_state(state)

    print(f"[DONE] Notified {sent} signals. last_created_at={state['last_created_at']}")


if __name__ == "__main__":
    from datetime import timedelta  # fmt_kst에서 사용
    run_notify()
