import argparse
import yaml
import psycopg
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


# -----------------------------
# Config / DB
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


# -----------------------------
# Data fetch
# -----------------------------
def fetch_symbol_trades(conn, symbol: str, tf: str, days: int):
    q = """
    SELECT
      s.signal_id,
      s.symbol,
      s.tf,
      s.strategy,
      s.direction,
      s.open_time,
      s.entry,
      s.stop_loss,
      s.take_profit_1,
      s.take_profit_2,
      o.exit_time,
      o.exit_price,
      o.result,
      o.r_multiple
    FROM signals s
    JOIN outcomes o ON o.signal_id = s.signal_id
    WHERE s.symbol = %s
      AND s.tf = %s
      AND s.open_time >= now() - (%s || ' days')::interval
    ORDER BY s.open_time ASC
    """
    df = pd.read_sql(q, conn, params=(symbol, tf, days))
    if df.empty:
        return df

    df["open_time"] = pd.to_datetime(df["open_time"])
    df["exit_time"] = pd.to_datetime(df["exit_time"])

    num_cols = ["entry", "stop_loss", "take_profit_1", "take_profit_2", "exit_price", "r_multiple"]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df


def fetch_candles(conn, symbol: str, tf: str, start_time, end_time):
    q = """
    SELECT open_time, open, high, low, close
    FROM candles
    WHERE symbol=%s AND tf=%s
      AND open_time >= %s AND open_time <= %s
    ORDER BY open_time ASC
    """
    df = pd.read_sql(q, conn, params=(symbol, tf, start_time, end_time))
    if not df.empty:
        df["open_time"] = pd.to_datetime(df["open_time"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df


# -----------------------------
# Metrics
# -----------------------------
def compute_mdd(cum: np.ndarray):
    if len(cum) == 0:
        return np.nan, None, None
    running_max = np.maximum.accumulate(cum)
    dd = cum - running_max
    trough = int(np.argmin(dd))
    mdd = float(dd[trough])
    peak = int(np.argmax(cum[:trough + 1])) if trough >= 0 else 0
    return mdd, peak, trough


# -----------------------------
# Session bucketing (KST 기준)
# -----------------------------
SESSION_BINS = [
    ("ASIA (00-08)", 0, 8),
    ("EU (08-16)", 8, 16),
    ("US (16-24)", 16, 24),
]

def assign_session_kst(dt_series: pd.Series) -> pd.Series:
    """
    dt_series: timezone-aware(KST) or naive(assume KST) datetime
    returns session label
    """
    # pandas가 tz-aware면 그대로 쓰고, naive면 KST로 간주
    hours = dt_series.dt.hour
    labels = []
    for h in hours:
        label = "UNKNOWN"
        for name, start, end in SESSION_BINS:
            if start <= int(h) < end:
                label = name
                break
        labels.append(label)
    return pd.Series(labels, index=dt_series.index)


def session_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    session별 n, sumR, avgR, win_rate
    """
    out = []
    for sess, g in df.groupby("session"):
        r = g["r_filled"].astype(float)
        n = int(len(r))
        if n == 0:
            continue
        win = float(np.mean(g["result"].astype(str).str.startswith("tp")))
        out.append({
            "session": sess,
            "n": n,
            "sum_r": float(r.sum()),
            "avg_r": float(r.mean()),
            "win_rate": win,
        })
    res = pd.DataFrame(out)
    if not res.empty:
        res = res.sort_values(["sum_r", "n"], ascending=[False, False])
    return res


# -----------------------------
# Plot helpers
# -----------------------------
def annotate_trade(ax, row, tag):
    et = row["open_time"]
    entry = row["entry"]
    xt = row["exit_time"]
    xp = row["exit_price"]
    r = row["r_multiple"]
    if pd.isna(et) or pd.isna(entry) or pd.isna(xt) or pd.isna(xp) or pd.isna(r):
        return
    ax.scatter([et], [entry], s=110, marker="o", alpha=0.9)
    ax.scatter([xt], [xp], s=110, marker="s", alpha=0.9)
    ax.text(xt, xp, f" {tag} {r:+.2f}R", fontsize=9, va="center", ha="left", alpha=0.9)


# -----------------------------
# Main
# -----------------------------
def main():
    ap = argparse.ArgumentParser(description="Plot trade story with session performance (KST)")
    ap.add_argument("--symbol", default="BNBUSDT")
    ap.add_argument("--tf", default="30m")
    ap.add_argument("--days", type=int, default=90)

    ap.add_argument("--risk-usdt", type=float, default=None, help="PnL = R * risk_usdt (optional)")
    ap.add_argument("--topk", type=int, default=3, help="highlight top-k trades by R")
    ap.add_argument("--bottomk", type=int, default=3, help="highlight bottom-k trades by R")
    ap.add_argument("--levels", action="store_true", help="draw SL/TP1/TP2 lines for each trade")
    ap.add_argument("--mdd", action="store_true", help="shade max drawdown window on cumulative chart")
    ap.add_argument("--max-trades", type=int, default=0, help="0=all, otherwise limit to last N trades")

    ap.add_argument("--outfile", default=None)
    args = ap.parse_args()

    cfg = load_config()

    with get_conn(cfg) as conn:
        trades = fetch_symbol_trades(conn, args.symbol, args.tf, args.days)
        if trades.empty:
            print(f"[INFO] No trades for {args.symbol} {args.tf} in last {args.days} days.")
            return

        if args.max_trades and args.max_trades > 0:
            trades = trades.tail(args.max_trades).copy()

        start_time = trades["open_time"].min()
        end_time = trades["exit_time"].max()
        if pd.isna(end_time):
            end_time = trades["open_time"].max()
        end_time = end_time + pd.Timedelta(days=1)

        candles = fetch_candles(conn, args.symbol, args.tf, start_time, end_time)
        if candles.empty:
            candles = None

    # Performance
    trades["r_filled"] = trades["r_multiple"].fillna(0.0)
    trades["cum_r"] = trades["r_filled"].cumsum().to_numpy()

    if args.risk_usdt is not None:
        trades["pnl_usdt"] = trades["r_filled"] * float(args.risk_usdt)
        trades["cum_pnl_usdt"] = trades["pnl_usdt"].cumsum().to_numpy()

    # Session
    trades["session"] = assign_session_kst(trades["open_time"])

    # Best/Worst index
    r_valid = trades["r_multiple"].replace([np.inf, -np.inf], np.nan)
    idx_sorted = r_valid.sort_values(ascending=False).dropna().index.tolist()
    best_idx = idx_sorted[: max(0, args.topk)]
    worst_idx = list(reversed(idx_sorted))[: max(0, args.bottomk)]

    # Session curves
    # 각 세션별 누적 R: 세션에 속하는 트레이드만 누적, 나머지는 이전값 유지(“계단형”)
    sessions = [s[0] for s in SESSION_BINS]
    base_times = trades["open_time"]

    sess_cum = {}
    for sess in sessions:
        r_s = trades["r_filled"].where(trades["session"] == sess, 0.0)
        sess_cum[sess] = r_s.cumsum().to_numpy()

    summ = session_summary(trades)

    # -----------------------------
    # Plot
    # -----------------------------
    fig = plt.figure(figsize=(14, 10))

    # (1) Price panel
    ax1 = plt.subplot(2, 1, 1)
    ax1.set_title(f"{args.symbol} {args.tf} Trade Story + Sessions(KST) (last {args.days} days)")

    if candles is not None:
        ax1.plot(candles["open_time"], candles["close"], linewidth=1)

    for i, t in trades.iterrows():
        et = t["open_time"]
        xt = t["exit_time"]
        entry = t["entry"]
        xp = t["exit_price"]
        direction = str(t["direction"]).lower()

        if pd.isna(et) or pd.isna(entry) or pd.isna(xt) or pd.isna(xp):
            continue

        ax1.scatter([et], [entry], marker="^" if direction == "long" else "v", s=30)
        ax1.plot([et, xt], [entry, xp], linewidth=1)
        ax1.scatter([xt], [xp], marker="x", s=30)

        if args.levels:
            sl = t["stop_loss"]
            tp1 = t["take_profit_1"]
            tp2 = t["take_profit_2"]
            if pd.notna(sl):
                ax1.plot([et, xt], [sl, sl], linestyle="--", linewidth=0.8, alpha=0.8)
            if pd.notna(tp1):
                ax1.plot([et, xt], [tp1, tp1], linestyle=":", linewidth=0.8, alpha=0.8)
            if pd.notna(tp2):
                ax1.plot([et, xt], [tp2, tp2], linestyle="-.", linewidth=0.8, alpha=0.8)

    for idx in best_idx:
        annotate_trade(ax1, trades.loc[idx], "BEST")
    for idx in worst_idx:
        annotate_trade(ax1, trades.loc[idx], "WORST")

    ax1.set_ylabel("Price")
    ax1.grid(True, linewidth=0.3, alpha=0.5)

    # (2) Performance panel
    ax2 = plt.subplot(2, 1, 2, sharex=ax1)

    # 전체 누적R
    ax2.plot(base_times, trades["cum_r"], linewidth=1)

    # 세션별 누적R (3개 라인)
    for sess in sessions:
        ax2.plot(base_times, sess_cum[sess], linewidth=1)

    # 개별 R 점
    ax2.scatter(base_times, trades["r_filled"], s=18)

    ax2.set_ylabel("R (cumulative)")
    ax2.set_xlabel("Time")
    ax2.grid(True, linewidth=0.3, alpha=0.5)

    # PnL axis (optional)
    if args.risk_usdt is not None:
        ax2b = ax2.twinx()
        ax2b.plot(base_times, trades["cum_pnl_usdt"], linewidth=1)
        ax2b.set_ylabel("PnL (USDT cumulative)")

    # MDD shading (optional, based on total cum_r)
    if args.mdd:
        cum = trades["cum_r"].to_numpy()
        mdd, peak_i, trough_i = compute_mdd(cum)
        if peak_i is not None and trough_i is not None and trough_i > peak_i:
            t0 = trades["open_time"].iloc[peak_i]
            t1 = trades["open_time"].iloc[trough_i]
            ax2.axvspan(t0, t1, alpha=0.18)
            ax2.text(
                t1,
                cum[trough_i],
                f" MDD {mdd:+.2f}R ({trough_i - peak_i} trades)",
                fontsize=9,
                va="center",
                ha="left",
                alpha=0.9,
            )

    # Session summary text box (no color required)
    if not summ.empty:
        lines = ["Session Summary (KST):"]
        for _, r in summ.iterrows():
            lines.append(f"- {r['session']}: n={int(r['n'])}, sumR={r['sum_r']:+.2f}, avgR={r['avg_r']:+.2f}, win={r['win_rate']*100:.1f}%")
        ax2.text(
            0.01,
            0.98,
            "\n".join(lines),
            transform=ax2.transAxes,
            va="top",
            ha="left",
            fontsize=9,
            alpha=0.9,
        )

    outfile = args.outfile or f"symbol_trade_story_sessions_{args.symbol}_{args.tf}.png"
    plt.tight_layout()
    plt.savefig(outfile, dpi=150)
    print(f"[DONE] Saved chart: {outfile}")


if __name__ == "__main__":
    main()
