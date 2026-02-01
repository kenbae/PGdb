import yaml
import psycopg
import pandas as pd
import numpy as np
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
    escaped = html.escape(msg, quote=False)
    for tag in ALLOWED_TAGS:
        escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>")
    return escaped


# -----------------------------
# Metrics: MDD
# -----------------------------
def max_drawdown(r_series: np.ndarray):
    if len(r_series) == 0:
        return np.nan, None, None, 0
    equity = np.cumsum(r_series)
    running_max = np.maximum.accumulate(equity)
    dd = equity - running_max  # <= 0
    trough = int(np.argmin(dd))
    mdd = float(dd[trough])
    peak = int(np.argmax(equity[:trough + 1])) if trough >= 0 else 0
    dur = trough - peak if (peak is not None and trough is not None) else 0
    return mdd, peak, trough, int(dur)


def fetch_trades(conn, days: int):
    q = """
    SELECT
      s.open_time,
      s.strategy,
      s.tf,
      s.symbol,
      o.r_multiple,
      o.mae,
      o.mfe,
      o.result
    FROM outcomes o
    JOIN signals s ON s.signal_id = o.signal_id
    WHERE s.open_time >= now() - (%s || ' days')::interval
      AND o.r_multiple IS NOT NULL
    ORDER BY s.open_time ASC
    """
    return pd.read_sql(q, conn, params=(days,))


def summarize_group(df: pd.DataFrame, key_cols):
    out = []
    for key, g in df.groupby(key_cols):
        r = g["r_multiple"].astype(float).to_numpy()
        n = len(r)
        if n == 0:
            continue

        avg_r = float(np.mean(r))
        std_r = float(np.std(r, ddof=1)) if n >= 2 else 0.0
        p5_r = float(np.quantile(r, 0.05))
        worst_r = float(np.min(r))
        win_rate = float(np.mean(g["result"].astype(str).str.startswith("tp")))
        avg_mae = float(np.mean(g["mae"].astype(float))) if "mae" in g else np.nan
        avg_mfe = float(np.mean(g["mfe"].astype(float))) if "mfe" in g else np.nan
        mdd_r, _, _, mdd_dur = max_drawdown(r)

        row = {}
        if isinstance(key, tuple):
            for c, v in zip(key_cols, key):
                row[c] = v
        else:
            row[key_cols[0]] = key

        row.update({
            "n": n,
            "avg_r": avg_r,
            "std_r": std_r,
            "p5_r": p5_r,
            "worst_r": worst_r,
            "win_rate": win_rate,
            "avg_mae": avg_mae,
            "avg_mfe": avg_mfe,
            "mdd_r": mdd_r,
            "mdd_dur": int(mdd_dur),
        })
        out.append(row)

    return pd.DataFrame(out)


def risk_score(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["std_r"] = df["std_r"].fillna(0.0)
    df["risk_score"] = (
        df["avg_r"]
        - 0.5 * df["p5_r"].abs()
        - 0.2 * df["std_r"]
        - 0.15 * df["mdd_r"].abs()
    )
    return df


# -----------------------------
# Formatting
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


def make_key(r, mode: str) -> str:
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
        return f"🧩 <b>{title}</b>\n• 표본 부족 (min n = {min_trades})\n"

    df = risk_score(df)

    # 키 컬럼
    if mode == "strategy_tf":
        key_cols = ["strategy", "tf"]
    elif mode == "symbol":
        key_cols = ["symbol"]
    else:
        key_cols = ["strategy", "symbol", "tf"]

    # TOP
    top = df.sort_values("risk_score", ascending=False).head(top_k)

    # TOP 제외 후 BOTTOM (중복 방지)
    top_keys = set(tuple(x) for x in top[key_cols].to_numpy())
    rest = df[~df[key_cols].apply(lambda r: tuple(r.to_list()) in top_keys, axis=1)].copy()

    bottom = rest.sort_values("risk_score", ascending=True).head(bottom_k) if not rest.empty else pd.DataFrame()

    lines = [f"🛡️ <b>{title} TOP {min(top_k, len(top))}</b>"]
    for _, r in top.iterrows():
        lines.append(
            f"• {make_key(r, mode)} | n={int(r['n'])}, win={f_pct(r['win_rate'])}, "
            f"EV={f_r(r['avg_r'])}, P5={f_r(r['p5_r'])}, σ={f_r(r['std_r'])}, "
            f"MDD={f_r(r['mdd_r'])}({int(r['mdd_dur'])}t), worst={f_r(r['worst_r'])}, MAE={f_num(r['avg_mae'])}"
        )

    lines.append("")
    if bottom.empty:
        lines.append(f"⚠️ <b>{title} BOTTOM</b>\n• 후보 부족 (유효 조합 수가 적음)\n")
    else:
        lines.append(f"⚠️ <b>{title} BOTTOM {min(bottom_k, len(bottom))}</b>")
        for _, r in bottom.iterrows():
            lines.append(
                f"• {make_key(r, mode)} | n={int(r['n'])}, win={f_pct(r['win_rate'])}, "
                f"EV={f_r(r['avg_r'])}, P5={f_r(r['p5_r'])}, σ={f_r(r['std_r'])}, "
                f"MDD={f_r(r['mdd_r'])}({int(r['mdd_dur'])}t), worst={f_r(r['worst_r'])}, MAE={f_num(r['avg_mae'])}"
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
    title = rcfg.get("title", "Risk+MDD Report")

    # 섹션별 설정
    st_cfg = rcfg.get("strategy_tf", {})
    sym_cfg = rcfg.get("symbol", {})
    pair_cfg = rcfg.get("pair", {})

    st_min = int(st_cfg.get("min_trades", 20))
    st_top = int(st_cfg.get("top_k", 8))
    st_bot = int(st_cfg.get("bottom_k", 5))

    sym_min = int(sym_cfg.get("min_trades", 5))
    sym_top = int(sym_cfg.get("top_k", 10))
    sym_bot = int(sym_cfg.get("bottom_k", 5))

    pair_min = int(pair_cfg.get("min_trades", 5))
    pair_top = int(pair_cfg.get("top_k", 10))
    pair_bot = int(pair_cfg.get("bottom_k", 5))

    with get_conn(cfg) as conn:
        df = fetch_trades(conn, days)

    if df.empty:
        raw = f"🧯 <b>{title}</b>\n기간: 최근 {days}일\n\n데이터 없음"
        safe = tg_html_sanitize(raw)
        tg_send(
            tg["bot_token"], tg["chat_id"], safe,
            parse_mode=tg.get("parse_mode", "HTML"),
            disable_web_page_preview=bool(tg.get("disable_web_page_preview", True)),
        )
        print("[DONE] Risk+MDD report sent (no data).")
        return

    st = summarize_group(df, ["strategy", "tf"])
    sym = summarize_group(df, ["symbol"])
    pair = summarize_group(df, ["strategy", "symbol", "tf"])

    parts = []
    parts.append(f"🧯 <b>{title}</b>")
    parts.append(f"기간: 최근 {days}일")
    parts.append("MDD=누적 R 최대낙폭(음수) / (t)=트레이드 수 기준 낙폭 지속")
    parts.append("")

    parts.append(build_section(st, f"전략×TF (min n={st_min})", "strategy_tf", st_min, st_top, st_bot))
    parts.append(build_section(sym, f"심볼 (min n={sym_min})", "symbol", sym_min, sym_top, sym_bot))
    parts.append(build_section(pair, f"전략×심볼 (min n={pair_min})", "pair", pair_min, pair_top, pair_bot))

    raw_msg = "\n".join(parts)
    safe_msg = tg_html_sanitize(raw_msg)

    tg_send(
        tg["bot_token"],
        tg["chat_id"],
        safe_msg,
        parse_mode=tg.get("parse_mode", "HTML"),
        disable_web_page_preview=bool(tg.get("disable_web_page_preview", True)),
    )

    print("[DONE] Risk+MDD report sent.")


if __name__ == "__main__":
    run()
