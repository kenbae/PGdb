#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backfill_binance_vision_parallel.py
- Binance Vision futures/spot kline backfiller
- Parallel download with ThreadPoolExecutor
- Windows-safe subprocess handling (no UTF-8 decode crash)
"""

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime


def run_cmd(cmd):
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False
    )
    out, err = proc.communicate()
    if proc.returncode != 0:
        print(f"[ERROR] {' '.join(cmd)}")
        if err:
            try:
                print(err.decode(errors="ignore"))
            except Exception:
                pass
        return False
    return True


def build_tasks(symbols, tfs, start_year, start_month, end_year, end_month):
    tasks = []
    cur_year, cur_month = start_year, start_month

    while (cur_year, cur_month) <= (end_year, end_month):
        for s in symbols:
            for tf in tfs:
                tasks.append((s, tf, cur_year, cur_month))
        if cur_month == 12:
            cur_year += 1
            cur_month = 1
        else:
            cur_month += 1
    return tasks


def worker(task, market_type):
    s, tf, y, m = task
    cmd = [
        sys.executable,
        "backfill_binance_vision.py",
        "--symbol", s,
        "--tf", tf,
        "--year", str(y),
        "--month", str(m),
        "--market-type", market_type,
    ]
    ok = run_cmd(cmd)
    print(f"[{'OK' if ok else 'FAIL'}] {s} {tf} {y}-{m:02d}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, required=True)
    ap.add_argument("--start-month", type=int, required=True)
    ap.add_argument("--end-year", type=int, required=True)
    ap.add_argument("--end-month", type=int, required=True)
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--tfs", nargs="+", required=True)
    ap.add_argument("--market-type", choices=["spot", "futures"], default="futures")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    tasks = build_tasks(
        args.symbols, args.tfs,
        args.start_year, args.start_month,
        args.end_year, args.end_month
    )

    print(f"[INFO] tasks={len(tasks)} workers={args.workers}")

    with ThreadPoolExecutor(max_workers=args.workers) as exe:
        futures = [exe.submit(worker, t, args.market_type) for t in tasks]
        for _ in as_completed(futures):
            pass

    print("[DONE] backfill finished")


if __name__ == "__main__":
    main()
