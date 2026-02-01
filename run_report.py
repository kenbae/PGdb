import yaml
import psycopg
import pandas as pd
import html
import requests
from datetime import datetime, timezone


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


def fetch_report_df(conn, days: int):
    q = """
    SELECT
      s.strategy,
      s.tf,
      COUNT(*) AS n,
      AVG(o.r_multiple) AS avg_r,
      SUM(CASE WHEN o.result LIKE 'tp%%' THEN 1 ELSE 0 END)::float / COUNT(*) AS win_rate,
      SUM(CASE WHEN o.result = 'sl' THEN 1 ELSE 0 END) AS sl_cnt,
      SUM(CASE WHEN o.result LIKE 'tp%%' THEN 1 ELSE 0 END) AS tp_cnt
    FROM outcomes o
    JOIN signals s ON s.signal_id = o.signal_id
    WHERE s.created_at >= now() - (%s || ' days')::interval
    GROUP BY s.strategy, s.tf
    """
    return pd.read_sql(q, conn, params=(days,))


def format_block(df, days, min_trades, top_k):
    if df.empty:
        return f"<b>최근 {days}일</b>: 데이터 없음\n"

    df = df[df["n"] >= min_trades].copy()
    if df.empty:
        return f"<b>최근 {days}일</b>: 표본 부족(n<{min_trades})\n"

    df = df.sort_values("avg_r", ascending=False).head(top_k)

    lines = [f"<b>최근 {days}일</b> (Top {top_k})"]
    for _, r in df.iterrows():
        strategy = html.escape(str(r["strategy"]))
        tf = html.escape(str(r["tf"]))
        n = int(r["n"])
        avg_r = float(r["avg_r"])
        win = float(r["win_rate"]) * 100.0

        lines.append(
            f"• <code>{strategy}</code> <code>{tf}</code> | "
            f"n={n}, "
            f"win={win:.1f}%, "
            f"EV={avg_r:+.3f}R"
        )
    return "\n".join(lines) + "\n"


def run_report():
    cfg = load_config()
    rcfg = cfg["report"]
    tg = cfg["telegram"]

    windows = rcfg.get("windows_days", [7, 30])
    min_trades = int(rcfg.get("min_trades", 20))
    top_k = int(rcfg.get("top_k", 5))
    label = html.escape(rcfg.get("schedule_label", "Report"))

    with get_conn(cfg) as conn:
        blocks = []
        for d in windows:
            df = fetch_report_df(conn, d)
            blocks.append(format_block(df, d, min_trades, top_k))

    now_kst = datetime.now(timezone.utc).astimezone(
        timezone(offset=timezone.utc.utcoffset(None))
    )
    title = f"📊 <b>{label} Performance Report</b>"

    msg = title + "\n\n" + "\n".join(blocks)

    tg_send(
        tg["bot_token"],
        tg["chat_id"],
        msg,
        parse_mode=tg.get("parse_mode", "HTML"),
        disable_web_page_preview=bool(tg.get("disable_web_page_preview", True)),
    )

    print("[DONE] Report sent.")


if __name__ == "__main__":
    run_report()
