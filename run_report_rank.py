import yaml
import psycopg
import pandas as pd
import html
import requests


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


def q_strategy_tf(days: int):
    return """
    SELECT
      s.strategy,
      s.tf,
      COUNT(*) AS n,
      AVG(o.r_multiple) AS ev_r,
      SUM(CASE WHEN o.result LIKE 'tp%%' THEN 1 ELSE 0 END)::float / COUNT(*) AS win_rate,
      PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY o.r_multiple) AS med_r
    FROM outcomes o
    JOIN signals s ON s.signal_id = o.signal_id
    WHERE s.open_time >= now() - (%s || ' days')::interval
      AND o.r_multiple IS NOT NULL
    GROUP BY s.strategy, s.tf
    """


def q_symbol(days: int):
    return """
    SELECT
      s.symbol,
      COUNT(*) AS n,
      AVG(o.r_multiple) AS ev_r,
      SUM(CASE WHEN o.result LIKE 'tp%%' THEN 1 ELSE 0 END)::float / COUNT(*) AS win_rate,
      PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY o.r_multiple) AS med_r
    FROM outcomes o
    JOIN signals s ON s.signal_id = o.signal_id
    WHERE s.open_time >= now() - (%s || ' days')::interval
      AND o.r_multiple IS NOT NULL
    GROUP BY s.symbol
    """


def q_strategy_symbol(days: int):
    return """
    SELECT
      s.strategy,
      s.symbol,
      s.tf,
      COUNT(*) AS n,
      AVG(o.r_multiple) AS ev_r,
      SUM(CASE WHEN o.result LIKE 'tp%%' THEN 1 ELSE 0 END)::float / COUNT(*) AS win_rate,
      PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY o.r_multiple) AS med_r
    FROM outcomes o
    JOIN signals s ON s.signal_id = o.signal_id
    WHERE s.open_time >= now() - (%s || ' days')::interval
      AND o.r_multiple IS NOT NULL
    GROUP BY s.strategy, s.symbol, s.tf
    """


def fmt_pct(x):
    try:
        return f"{float(x)*100:.1f}%"
    except Exception:
        return "-"


def fmt_r(x):
    try:
        return f"{float(x):+.3f}R"
    except Exception:
        return "-"


def block_strategy_tf(df, min_trades, top_k, bottom_k):
    df = df[df["n"] >= min_trades].copy()
    if df.empty:
        return f"<b>전략×TF</b>: 표본 부족(n<{min_trades})\n"

    df_top = df.sort_values("ev_r", ascending=False).head(top_k)
    df_bot = df.sort_values("ev_r", ascending=True).head(bottom_k)

    lines = [f"🏁 <b>전략×TF TOP {top_k}</b>"]
    for _, r in df_top.iterrows():
        lines.append(
            f"• <code>{html.escape(r['strategy'])}</code> <code>{html.escape(r['tf'])}</code> | "
            f"n={int(r['n'])}, win={fmt_pct(r['win_rate'])}, EV={fmt_r(r['ev_r'])}, med={fmt_r(r['med_r'])}"
        )

    lines.append("")
    lines.append(f"🧨 <b>전략×TF BOTTOM {bottom_k}</b>")
    for _, r in df_bot.iterrows():
        lines.append(
            f"• <code>{html.escape(r['strategy'])}</code> <code>{html.escape(r['tf'])}</code> | "
            f"n={int(r['n'])}, win={fmt_pct(r['win_rate'])}, EV={fmt_r(r['ev_r'])}, med={fmt_r(r['med_r'])}"
        )
    return "\n".join(lines) + "\n"


def block_symbol(df, min_trades, top_k, bottom_k):
    df = df[df["n"] >= min_trades].copy()
    if df.empty:
        return f"<b>심볼</b>: 표본 부족(n<{min_trades})\n"

    df_top = df.sort_values("ev_r", ascending=False).head(top_k)
    df_bot = df.sort_values("ev_r", ascending=True).head(bottom_k)

    lines = [f"🪙 <b>심볼 TOP {top_k}</b>"]
    for _, r in df_top.iterrows():
        lines.append(
            f"• <b>{html.escape(r['symbol'])}</b> | n={int(r['n'])}, win={fmt_pct(r['win_rate'])}, EV={fmt_r(r['ev_r'])}, med={fmt_r(r['med_r'])}"
        )

    lines.append("")
    lines.append(f"🧊 <b>심볼 BOTTOM {bottom_k}</b>")
    for _, r in df_bot.iterrows():
        lines.append(
            f"• <b>{html.escape(r['symbol'])}</b> | n={int(r['n'])}, win={fmt_pct(r['win_rate'])}, EV={fmt_r(r['ev_r'])}, med={fmt_r(r['med_r'])}"
        )
    return "\n".join(lines) + "\n"


def block_pairs(df, min_trades, top_k, bottom_k):
    df = df[df["n"] >= min_trades].copy()
    if df.empty:
        return f"<b>전략×심볼</b>: 표본 부족(n<{min_trades})\n"

    df_top = df.sort_values("ev_r", ascending=False).head(top_k)
    df_bot = df.sort_values("ev_r", ascending=True).head(bottom_k)

    lines = [f"🎯 <b>전략×심볼 TOP {top_k}</b>"]
    for _, r in df_top.iterrows():
        lines.append(
            f"• <code>{html.escape(r['strategy'])}</code> <b>{html.escape(r['symbol'])}</b> <code>{html.escape(r['tf'])}</code> | "
            f"n={int(r['n'])}, win={fmt_pct(r['win_rate'])}, EV={fmt_r(r['ev_r'])}"
        )

    lines.append("")
    lines.append(f"⚠️ <b>전략×심볼 BOTTOM {bottom_k}</b>")
    for _, r in df_bot.iterrows():
        lines.append(
            f"• <code>{html.escape(r['strategy'])}</code> <b>{html.escape(r['symbol'])}</b> <code>{html.escape(r['tf'])}</code> | "
            f"n={int(r['n'])}, win={fmt_pct(r['win_rate'])}, EV={fmt_r(r['ev_r'])}"
        )

    return "\n".join(lines) + "\n"


def run():
    cfg = load_config()
    tg = cfg["telegram"]
    rcfg = cfg.get("rank_report", {})

    days = int(rcfg.get("days", 30))
    min_trades = int(rcfg.get("min_trades", 20))
    top_k = int(rcfg.get("top_k", 10))
    bottom_k = int(rcfg.get("bottom_k", 5))
    top_pairs_k = int(rcfg.get("top_pairs_k", 10))
    bottom_pairs_k = int(rcfg.get("bottom_pairs_k", 5))
    title = html.escape(rcfg.get("title", "EV Ranking"))

    with get_conn(cfg) as conn:
        df_st = pd.read_sql(q_strategy_tf(days), conn, params=(days,))
        df_sym = pd.read_sql(q_symbol(days), conn, params=(days,))
        df_pair = pd.read_sql(q_strategy_symbol(days), conn, params=(days,))

    msg = (
        f"📈 <b>{title}</b>\n"
        f"<b>기간</b>: 최근 {days}일 | <b>min n</b>={min_trades}\n\n"
        + block_strategy_tf(df_st, min_trades, top_k, bottom_k)
        + "\n"
        + block_symbol(df_sym, min_trades, top_k, bottom_k)
        + "\n"
        + block_pairs(df_pair, min_trades, top_pairs_k, bottom_pairs_k)
    )

    tg_send(
        tg["bot_token"],
        tg["chat_id"],
        msg,
        parse_mode=tg.get("parse_mode", "HTML"),
        disable_web_page_preview=bool(tg.get("disable_web_page_preview", True)),
    )
    print("[DONE] Rank report sent.")


if __name__ == "__main__":
    run()
