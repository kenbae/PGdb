import yaml
import psycopg
import pandas as pd
import html
import requests


# -----------------------------
# Config / DB / Telegram
# -----------------------------
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
    r = requests.post(url, json=payload, timeout=20)
    if not r.ok:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")
    return r.json()


# -----------------------------
# Safety: escape everything then allowlist tags
# -----------------------------
ALLOWED_TAGS = ["b", "/b", "code", "/code"]

def tg_html_sanitize(msg: str) -> str:
    """
    텔레그램 HTML 파서가 <...>를 태그로 오인해 터지는 문제를 원천 차단.
    1) 전체 텍스트를 escape
    2) 우리가 의도한 태그만 복원(allowlist)
    """
    escaped = html.escape(msg, quote=False)
    for tag in ALLOWED_TAGS:
        escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>")
    return escaped


# -----------------------------
# Risk queries
# -----------------------------
def q_group(group_by_sql: str):
    """
    group_by_sql 예:
      "s.strategy, s.tf"
      "s.symbol"
      "s.strategy, s.symbol, s.tf"
    """
    return f"""
    SELECT
      {group_by_sql},
      COUNT(*) AS n,
      AVG(o.r_multiple) AS avg_r,
      STDDEV_SAMP(o.r_multiple) AS std_r,
      PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY o.r_multiple) AS p5_r,
      MIN(o.r_multiple) AS worst_r,
      AVG(o.mae) AS avg_mae,
      AVG(o.mfe) AS avg_mfe,
      SUM(CASE WHEN o.result LIKE 'tp%%' THEN 1 ELSE 0 END)::float / COUNT(*) AS win_rate
    FROM outcomes o
    JOIN signals s ON s.signal_id = o.signal_id
    WHERE s.open_time >= now() - (%s || ' days')::interval
      AND o.r_multiple IS NOT NULL
    GROUP BY {group_by_sql}
    """


# -----------------------------
# Formatting helpers
# -----------------------------
def f_pct(x):
    try:
        return f"{float(x)*100:.1f}%"
    except Exception:
        return "-"


def f_r(x):
    try:
        return f"{float(x):+.3f}R"
    except Exception:
        return "-"


def f_num(x):
    try:
        return f"{float(x):,.4f}".rstrip("0").rstrip(".")
    except Exception:
        return "-"


def rank_key_risk(df: pd.DataFrame) -> pd.DataFrame:
    """
    리스크 중심 랭킹 점수(보수적):
      score = EV - 0.5*|P5| - 0.2*STD
    """
    df = df.copy()
    df["std_r"] = df["std_r"].fillna(0.0)
    df["risk_score"] = df["avg_r"] - 0.5 * df["p5_r"].abs() - 0.2 * df["std_r"]
    return df


def make_key(r, mode: str) -> str:
    """
    mode:
      'strategy_tf' or 'symbol' or 'pair'
    """
    if mode == "strategy_tf":
        return f"<code>{r['strategy']}</code> <code>{r['tf']}</code>"
    if mode == "symbol":
        return f"<b>{r['symbol']}</b>"
    if mode == "pair":
        return f"<code>{r['strategy']}</code> <b>{r['symbol']}</b> <code>{r['tf']}</code>"
    return "<b>item</b>"


def build_section(df: pd.DataFrame, title: str, mode: str, min_trades: int, top_k: int, bottom_k: int) -> str:
    df = df[df["n"] >= min_trades].copy()
    if df.empty:
        # '<' 사용 금지 → 안전 문자열로 표현
        return f"🧩 <b>{title}</b>\n• 표본 부족 (min n = {min_trades})\n"

    df = rank_key_risk(df)

    df_top = df.sort_values("risk_score", ascending=False).head(top_k)
    df_bot = df.sort_values("risk_score", ascending=True).head(bottom_k)

    lines = [f"🛡️ <b>{title} TOP {top_k}</b>"]
    for _, r in df_top.iterrows():
        lines.append(
            f"• {make_key(r, mode)} | "
            f"n={int(r['n'])}, win={f_pct(r['win_rate'])}, "
            f"EV={f_r(r['avg_r'])}, P5={f_r(r['p5_r'])}, "
            f"σ={f_r(r['std_r'])}, worst={f_r(r['worst_r'])}, "
            f"MAE={f_num(r['avg_mae'])}"
        )

    lines.append("")
    lines.append(f"⚠️ <b>{title} BOTTOM {bottom_k}</b>")
    for _, r in df_bot.iterrows():
        lines.append(
            f"• {make_key(r, mode)} | "
            f"n={int(r['n'])}, win={f_pct(r['win_rate'])}, "
            f"EV={f_r(r['avg_r'])}, P5={f_r(r['p5_r'])}, "
            f"σ={f_r(r['std_r'])}, worst={f_r(r['worst_r'])}, "
            f"MAE={f_num(r['avg_mae'])}"
        )

    return "\n".join(lines) + "\n"


# -----------------------------
# Main
# -----------------------------
def run():
    cfg = load_config()
    tg = cfg["telegram"]

    rcfg = cfg.get("risk_report", {})
    days = int(rcfg.get("days", 30))
    min_trades = int(rcfg.get("min_trades", 20))
    top_k = int(rcfg.get("top_k", 8))
    bottom_k = int(rcfg.get("bottom_k", 5))
    title = rcfg.get("title", "Risk Report")

    with get_conn(cfg) as conn:
        df_st = pd.read_sql(q_group("s.strategy, s.tf"), conn, params=(days,))
        df_sym = pd.read_sql(q_group("s.symbol"), conn, params=(days,))
        df_pair = pd.read_sql(q_group("s.strategy, s.symbol, s.tf"), conn, params=(days,))

    msg_parts = []
    msg_parts.append(f"🧯 <b>{title}</b>")
    msg_parts.append(f"기간: 최근 {days}일 | min n = {min_trades}")
    msg_parts.append("")

    msg_parts.append(build_section(df_st, "전략×TF", "strategy_tf", min_trades, top_k, bottom_k))
    msg_parts.append(build_section(df_sym, "심볼", "symbol", min_trades, top_k, bottom_k))
    msg_parts.append(build_section(df_pair, "전략×심볼", "pair", min_trades, top_k, bottom_k))

    raw_msg = "\n".join(msg_parts)

    # ✅ 최종 안전 처리: 전체 escape 후 allowlist 태그만 복원
    safe_msg = tg_html_sanitize(raw_msg)

    tg_send(
        tg["bot_token"],
        tg["chat_id"],
        safe_msg,
        parse_mode=tg.get("parse_mode", "HTML"),
        disable_web_page_preview=bool(tg.get("disable_web_page_preview", True)),
    )

    print("[DONE] Risk report sent.")


if __name__ == "__main__":
    run()
