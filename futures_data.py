"""
期货交易所日线数据共享层 — 并行拉取、交易日解析、主力筛选。

供 closing_summary、后续 vnpy 等模块复用。
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

import pandas as pd

ProgressCallback = Callable[[str], None]

EXCHANGES = ("SHFE", "DCE", "CZCE", "CFFEX", "INE", "GFEX")

EXCHANGE_LABEL: dict[str, str] = {
    "SHFE": "上期所",
    "DCE": "大商所",
    "CZCE": "郑商所",
    "CFFEX": "中金所",
    "INE": "能源中心",
    "GFEX": "广期所",
}

_NUMERIC_COLS = (
    "open", "high", "low", "close", "volume",
    "open_interest", "turnover", "settle", "pre_settle",
)


def _noop(_: str) -> None:
    pass


def fmt_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def parse_yyyymmdd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def variety_from_symbol(symbol: str) -> str:
    m = re.match(r"^([A-Z]+)", symbol.strip().upper())
    return m.group(1) if m else symbol.upper()


@dataclass
class DailyFetchResult:
    """多交易所日线拉取结果。"""

    df: pd.DataFrame
    start: str
    end: str
    successes: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.df.empty


def fetch_exchange_daily(
    start: str,
    end: str,
    market: str,
    *,
    retries: int = 1,
) -> pd.DataFrame:
    import akshare as ak
    import time

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            df = ak.get_futures_daily(start_date=start, end_date=end, market=market)
            if df is None or df.empty:
                if attempt < retries:
                    time.sleep(1.5)
                    continue
                return pd.DataFrame()
            out = df.copy()
            out["exchange"] = market
            out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y%m%d")
            for col in _NUMERIC_COLS:
                if col in out.columns:
                    out[col] = pd.to_numeric(out[col], errors="coerce")
            if "variety" in out.columns:
                out["variety"] = out["variety"].astype(str).str.upper()
            return out
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5)
            else:
                raise last_err from e
    return pd.DataFrame()


def fetch_all_daily_parallel(
    start: str,
    end: str,
    *,
    exchanges: tuple[str, ...] = EXCHANGES,
    max_workers: int = 6,
    on_progress: ProgressCallback | None = None,
) -> DailyFetchResult:
    """并行拉取各交易所日线；失败记录到 errors，不静默吞掉。"""
    report = on_progress or _noop
    report(f"      并行拉取 {len(exchanges)} 个交易所 ({start}~{end}) ...")

    frames: list[pd.DataFrame] = []
    successes: list[str] = []
    errors: list[dict[str, str]] = []

    def _task(market: str) -> tuple[str, pd.DataFrame | None, str | None]:
        try:
            sub = fetch_exchange_daily(start, end, market)
            if sub.empty:
                return market, None, "返回空数据"
            return market, sub, None
        except Exception as e:
            return market, None, str(e)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_task, ex): ex for ex in exchanges}
        for fut in as_completed(futures):
            market, sub, err = fut.result()
            label = EXCHANGE_LABEL.get(market, market)
            if err:
                errors.append({"exchange": market, "label": label, "error": err})
                report(f"      ✗ {label}: {err}")
            else:
                successes.append(market)
                report(f"      ✓ {label}: {len(sub)} 行")
                frames.append(sub)

    if not frames:
        return DailyFetchResult(
            pd.DataFrame(), start, end, successes=successes, errors=errors,
        )
    return DailyFetchResult(
        pd.concat(frames, ignore_index=True),
        start,
        end,
        successes=successes,
        errors=errors,
    )


def resolve_trade_date_from_df(
    as_of: date,
    df: pd.DataFrame,
) -> tuple[str, bool]:
    """从已拉取的日线 DataFrame 解析最近交易日。"""
    if df.empty or "date" not in df.columns:
        raise ValueError("日线数据为空，无法解析交易日")

    available = sorted(df["date"].astype(str).unique())
    as_of_str = fmt_date(as_of)
    if as_of_str in available:
        return as_of_str, False

    prior = [d for d in available if d < as_of_str]
    if not prior:
        raise ValueError("数据区间内无早于基准日的交易日")
    return prior[-1], True


def dominant_by_date(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """某日各品种持仓量最大合约。"""
    day = df[df["date"].astype(str) == trade_date].copy()
    if day.empty:
        return day
    day = day.dropna(subset=["open_interest", "settle"])
    if day.empty:
        return day
    if "variety" not in day.columns:
        day["variety"] = day["symbol"].astype(str).map(variety_from_symbol)
    idx = day.groupby("variety")["open_interest"].idxmax()
    return day.loc[idx].sort_values("variety")


def load_sina_main_universe(
    on_progress: ProgressCallback | None = None,
) -> tuple[dict[str, str], list[str]]:
    """
    新浪主力连续品种表。

    Returns:
        name_map: symbol/variety → 中文名
        expected_varieties: 期望覆盖的品种代码列表（如 RB, IF）
    """
    import akshare as ak

    report = on_progress or _noop
    report("      拉取新浪主力连续品种表 ...")
    df = ak.futures_display_main_sina()
    name_map: dict[str, str] = {}
    varieties: list[str] = []
    for _, row in df.iterrows():
        sym = str(row.get("symbol", "")).upper()
        name = str(row.get("name", sym))
        if not sym:
            continue
        name_map[sym] = name
        var = variety_from_symbol(sym)
        name_map[var] = name.replace("连续", "").strip() or name
        varieties.append(var)
    return name_map, sorted(set(varieties))


def compare_variety_coverage(
    expected: list[str],
    found: list[str],
) -> dict[str, Any]:
    """对比期望品种与实得品种。"""
    exp_set = set(expected)
    found_set = set(found)
    missing = sorted(exp_set - found_set)
    extra = sorted(found_set - exp_set)
    return {
        "expected_count": len(exp_set),
        "found_count": len(found_set),
        "missing_varieties": missing,
        "extra_varieties": extra,
        "coverage_pct": round(len(found_set & exp_set) / len(exp_set) * 100, 1) if exp_set else 0.0,
    }
