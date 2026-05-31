"""
策略盘中检测 — 拉取实时/分时行情，评估策略信号，供 OpenClaw 等自动化调度。

信号仅在「新触发」（交叉/突破边缘）时 alert=True，避免重复推送。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

SIGNAL_LABELS: dict[str, str] = {
    "none": "无新信号",
    "open_long": "开多信号",
    "close_long": "平多信号",
    "open_short": "开空信号",
    "close_short": "平空信号",
}

VALID_INTERVALS = {"daily", "1", "5", "15", "30", "60"}


@dataclass
class MonitorResult:
    """单策略盘中检测结果。"""

    strategy: str
    symbol: str
    interval: str
    signal: str
    triggered: bool
    message: str
    alert_message: str
    price: float
    bar_time: str
    pos_assumed: int
    indicators: dict[str, Any]
    data_source: str
    params: dict[str, Any] = field(default_factory=dict)
    bar_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"策略: {self.strategy}",
            f"合约: {self.symbol} | 周期: {self.interval}",
            f"信号: {SIGNAL_LABELS.get(self.signal, self.signal)}"
            + (" ⚠ 已触发" if self.triggered else ""),
            f"时间: {self.bar_time} | 价格: {self.price}",
            f"假定持仓: {self.pos_assumed} 手",
            f"指标: {self.indicators}",
            f"数据: {self.data_source} ({self.bar_count} 根K线)",
            f"说明: {self.message}",
        ]
        if self.triggered:
            lines.append(f"\n{self.alert_message}")
        return "\n".join(lines)


@dataclass
class MonitorBatchResult:
    """多策略盘中检测汇总。"""

    symbol: str
    interval: str
    results: list[MonitorResult]
    checked_at: str = ""
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "checked_at": self.checked_at,
            "results": [r.to_dict() for r in self.results],
            "alerts": [r.to_dict() for r in self.alerts],
            "alert_count": len(self.alerts),
            "errors": self.errors,
        }

    @property
    def alerts(self) -> list[MonitorResult]:
        return [r for r in self.results if r.triggered]

    def summary(self) -> str:
        lines = [
            f"盘中检测 · {self.symbol} · 周期 {self.interval} · {self.checked_at}",
            f"共检测 {len(self.results)} 个策略，触发 {len(self.alerts)} 个信号",
            "",
        ]
        for r in self.results:
            flag = "⚠ " if r.triggered else "  "
            lines.append(
                f"{flag}{r.strategy:<18} {SIGNAL_LABELS.get(r.signal, r.signal):<8} "
                f"价格 {r.price} @ {r.bar_time}"
            )
        if self.alerts:
            lines.append("\n--- 告警详情 ---")
            for r in self.alerts:
                lines.append(r.alert_message)
        return "\n".join(lines)


def normalize_sina_symbol(symbol: str) -> str:
    """转为新浪行情 symbol（如 RB → RB0，RB2410 保持不变）。"""
    s = symbol.strip().upper()
    if re.match(r"^[A-Z]+\d{4}$", s):
        return s
    if re.match(r"^[A-Z]+\d$", s):
        return s
    if re.match(r"^[A-Z]+$", s):
        return f"{s}0"
    return s


def _standardize_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "datetime" not in out.columns and "date" in out.columns:
        out.rename(columns={"date": "datetime"}, inplace=True)
    out["datetime"] = pd.to_datetime(out["datetime"])
    for col in ("open", "high", "low", "close", "volume"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["close"]).sort_values("datetime").reset_index(drop=True)
    return out


def _refresh_last_bar_with_minute(daily_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """用最新 1 分钟线刷新/追加当日 K 线，使盘中检测更及时。"""
    import akshare as ak

    sym = normalize_sina_symbol(symbol)
    try:
        minute_df = ak.futures_zh_minute_sina(symbol=sym, period="1")
    except Exception:
        return daily_df
    if minute_df is None or minute_df.empty:
        return daily_df

    last = minute_df.iloc[-1]
    bar_dt = pd.Timestamp(pd.to_datetime(last["datetime"]).date())
    row = {
        "datetime": bar_dt,
        "open": float(last["open"]),
        "high": float(last["high"]),
        "low": float(last["low"]),
        "close": float(last["close"]),
        "volume": float(last.get("volume") or 0),
    }
    if daily_df.empty:
        return pd.DataFrame([row])

    if daily_df.iloc[-1]["datetime"].date() == bar_dt.date():
        daily_df = daily_df.copy()
        prev = daily_df.iloc[-1]
        daily_df.iloc[-1] = {
            "datetime": bar_dt,
            "open": float(prev["open"]),
            "high": max(float(prev["high"]), row["high"]),
            "low": min(float(prev["low"]), row["low"]),
            "close": row["close"],
            "volume": float(prev.get("volume") or 0) + row["volume"],
        }
    else:
        daily_df = pd.concat([daily_df, pd.DataFrame([row])], ignore_index=True)
    return daily_df


def load_monitor_bars(symbol: str, interval: str = "daily") -> tuple[pd.DataFrame, str]:
    """
    加载盘中检测用 K 线。

    interval:
      - daily: 新浪日线 + 1 分钟线刷新当日 bar
      - 1/5/15/30/60: 新浪分时 K 线
    """
    import akshare as ak

    sym = normalize_sina_symbol(symbol)
    if interval == "daily":
        df = ak.futures_zh_daily_sina(symbol=sym)
        df = _standardize_ohlc(df)
        df = _refresh_last_bar_with_minute(df, symbol)
        return df, "futures_zh_daily_sina+futures_zh_minute_sina"

    df = ak.futures_zh_minute_sina(symbol=sym, period=interval)
    return _standardize_ohlc(df), f"futures_zh_minute_sina(period={interval})"


def _calc_rsi(closes: pd.Series, period: int) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - 100 / (1 + rs)


def eval_ma_crossover(
    df: pd.DataFrame, setting: dict[str, Any], pos: int,
) -> tuple[str, dict[str, Any], str]:
    short = int(setting.get("short_window", setting.get("short", 5)))
    long = int(setting.get("long_window", setting.get("long", 20)))
    need = long + 2
    if len(df) < need:
        return "none", {}, f"K 线不足（需 ≥{need} 根，当前 {len(df)}）"

    close = df["close"]
    s_ma = close.rolling(short).mean()
    l_ma = close.rolling(long).mean()
    curr_s, curr_l = float(s_ma.iloc[-1]), float(l_ma.iloc[-1])
    prev_s, prev_l = float(s_ma.iloc[-2]), float(l_ma.iloc[-2])
    price = float(close.iloc[-1])
    indicators = {
        "short_ma": round(curr_s, 4),
        "long_ma": round(curr_l, 4),
        "short_window": short,
        "long_window": long,
    }

    if pos == 0 and prev_s <= prev_l and curr_s > curr_l:
        return "open_long", indicators, f"金叉：短均线上穿长均线（{curr_s:.2f} > {curr_l:.2f}）"
    if pos == 0 and prev_s >= prev_l and curr_s < curr_l:
        return "open_short", indicators, f"死叉：短均线下穿长均线（{curr_s:.2f} < {curr_l:.2f}）"
    if pos > 0 and prev_s >= prev_l and curr_s < curr_l:
        return "close_long", indicators, f"死叉平多：短均线下穿长均线（{curr_s:.2f} < {curr_l:.2f}）"
    if pos < 0 and prev_s <= prev_l and curr_s > curr_l:
        return "close_short", indicators, f"金叉平空：短均线上穿长均线（{curr_s:.2f} > {curr_l:.2f}）"

    bias = "多头" if curr_s > curr_l else "空头" if curr_s < curr_l else "中性"
    return "none", indicators, f"暂无新交叉，当前均线排列偏{bias}"


def eval_turtle_trading(
    df: pd.DataFrame, setting: dict[str, Any], pos: int,
) -> tuple[str, dict[str, Any], str]:
    entry_w = int(setting.get("entry_window", 20))
    exit_w = int(setting.get("exit_window", 10))
    need = max(entry_w, exit_w) + 2
    if len(df) < need:
        return "none", {}, f"K 线不足（需 ≥{need} 根，当前 {len(df)}）"

    highs = df["high"]
    lows = df["low"]
    close = df["close"]
    entry_high = float(highs.iloc[-entry_w - 1 : -1].max())
    entry_low = float(lows.iloc[-entry_w - 1 : -1].min())
    exit_high = float(highs.iloc[-exit_w - 1 : -1].max())
    exit_low = float(lows.iloc[-exit_w - 1 : -1].min())
    price = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    indicators = {
        "entry_high": round(entry_high, 4),
        "entry_low": round(entry_low, 4),
        "exit_high": round(exit_high, 4),
        "exit_low": round(exit_low, 4),
        "entry_window": entry_w,
        "exit_window": exit_w,
    }

    if pos == 0 and prev_close <= entry_high and price > entry_high:
        return "open_long", indicators, f"突破 {entry_w} 日高点 {entry_high:.2f}（现价 {price:.2f}）"
    if pos == 0 and prev_close >= entry_low and price < entry_low:
        return "open_short", indicators, f"跌破 {entry_w} 日低点 {entry_low:.2f}（现价 {price:.2f}）"
    if pos > 0 and prev_close >= exit_low and price < exit_low:
        return "close_long", indicators, f"跌破 {exit_w} 日低点 {exit_low:.2f} 平多（现价 {price:.2f}）"
    if pos < 0 and prev_close <= exit_high and price > exit_high:
        return "close_short", indicators, f"突破 {exit_w} 日高点 {exit_high:.2f} 平空（现价 {price:.2f}）"

    if pos == 0 and price > entry_high * 0.998:
        return "none", indicators, f"接近突破高点 {entry_high:.2f}，尚未确认"
    if pos == 0 and price < entry_low * 1.002:
        return "none", indicators, f"接近跌破低点 {entry_low:.2f}，尚未确认"
    return "none", indicators, (
        f"通道内运行（入多 {entry_high:.2f} / 入空 {entry_low:.2f}）"
    )


def eval_rsi_demo(
    df: pd.DataFrame, setting: dict[str, Any], pos: int,
) -> tuple[str, dict[str, Any], str]:
    period = int(setting.get("period", 14))
    oversold = float(setting.get("oversold", 30))
    overbought = float(setting.get("overbought", 70))
    need = period + 3
    if len(df) < need:
        return "none", {}, f"K 线不足（需 ≥{need} 根，当前 {len(df)}）"

    rsi = _calc_rsi(df["close"], period)
    curr_rsi = float(rsi.iloc[-1])
    prev_rsi = float(rsi.iloc[-2])
    price = float(df["close"].iloc[-1])
    indicators = {
        "rsi": round(curr_rsi, 2),
        "oversold": oversold,
        "overbought": overbought,
        "period": period,
    }

    if pos == 0 and prev_rsi >= oversold and curr_rsi < oversold:
        return "open_long", indicators, f"RSI 进入超卖区开多（{curr_rsi:.1f} < {oversold}）"
    if pos == 0 and prev_rsi <= overbought and curr_rsi > overbought:
        return "open_short", indicators, f"RSI 进入超买区开空（{curr_rsi:.1f} > {overbought}）"
    if pos > 0 and prev_rsi <= overbought and curr_rsi > overbought:
        return "close_long", indicators, f"RSI 超买平多（{curr_rsi:.1f} > {overbought}）"
    if pos < 0 and prev_rsi >= oversold and curr_rsi < oversold:
        return "close_short", indicators, f"RSI 超卖平空（{curr_rsi:.1f} < {oversold}）"

    zone = "超卖" if curr_rsi < oversold else "超买" if curr_rsi > overbought else "中性"
    return "none", indicators, f"RSI={curr_rsi:.1f}，处于{zone}区域，暂无新触发"


EVALUATORS: dict[str, Any] = {
    "ma_crossover": eval_ma_crossover,
    "turtle_trading": eval_turtle_trading,
    "rsi_demo": eval_rsi_demo,
}


def _build_alert_message(
    strategy: str,
    symbol: str,
    signal: str,
    bar_time: str,
    price: float,
    detail: str,
    indicators: dict[str, Any],
) -> str:
    label = SIGNAL_LABELS.get(signal, signal)
    ind_txt = " | ".join(f"{k}={v}" for k, v in indicators.items())
    return (
        f"【期货策略信号】{strategy} · {symbol}\n"
        f"动作: {label}\n"
        f"时间: {bar_time} | 价格: {price}\n"
        f"{detail}\n"
        f"指标: {ind_txt}"
    )


def run_strategy_monitor(
    strategy_name: str,
    symbol: str,
    setting: dict[str, Any] | None = None,
    *,
    interval: str = "daily",
    pos: int = 0,
) -> MonitorResult:
    """执行单策略盘中检测。"""
    if interval not in VALID_INTERVALS:
        raise ValueError(f"interval 须为 {sorted(VALID_INTERVALS)} 之一，当前: {interval}")

    evaluator = EVALUATORS.get(strategy_name)
    if evaluator is None:
        raise ValueError(
            f"策略 {strategy_name} 暂不支持盘中检测，"
            f"可用: {list(EVALUATORS.keys())}"
        )

    params = dict(setting or {})
    df, data_source = load_monitor_bars(symbol, interval)
    signal, indicators, message = evaluator(df, params, int(pos))

    bar_time = str(df.iloc[-1]["datetime"])
    price = float(df.iloc[-1]["close"])
    triggered = signal != "none"
    alert_message = (
        _build_alert_message(strategy_name, symbol, signal, bar_time, price, message, indicators)
        if triggered
        else ""
    )

    return MonitorResult(
        strategy=strategy_name,
        symbol=symbol,
        interval=interval,
        signal=signal,
        triggered=triggered,
        message=message,
        alert_message=alert_message,
        price=price,
        bar_time=bar_time,
        pos_assumed=int(pos),
        indicators=indicators,
        data_source=data_source,
        params=params,
        bar_count=len(df),
    )


def run_monitor_batch(
    symbol: str,
    strategy_names: list[str],
    params_map: dict[str, dict[str, Any]] | None = None,
    *,
    interval: str = "daily",
    pos_map: dict[str, int] | None = None,
    default_pos: int = 0,
) -> MonitorBatchResult:
    """批量盘中检测（供 OpenClaw 定时任务一次扫描多策略）。"""
    results: list[MonitorResult] = []
    errors: list[dict[str, str]] = []
    params_map = params_map or {}
    pos_map = pos_map or {}

    for name in strategy_names:
        try:
            setting = params_map.get(name, {})
            pos = pos_map.get(name, default_pos)
            results.append(
                run_strategy_monitor(name, symbol, setting, interval=interval, pos=pos)
            )
        except Exception as e:
            errors.append({"strategy": name, "error": str(e)})

    return MonitorBatchResult(
        symbol=symbol,
        interval=interval,
        results=results,
        checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        errors=errors,
    )
