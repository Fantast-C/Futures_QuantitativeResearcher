"""
VeighNa (vnpy) 回测引擎集成

- 使用 akshare get_futures_daily 获取各交易所分合约日线（非主连）
- 按持仓量最大原则构建主力合约序列（dominant roll）
- 使用 vnpy_ctastrategy.BacktestingEngine 进行 CTA 回测
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any, TYPE_CHECKING

import pandas as pd

ProgressCallback = Callable[[str], None]

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData

if TYPE_CHECKING:
    from FuturesSkill import BacktestResult

# 品种 → 交易所及合约参数（乘数、最小变动、手续费率）
VARIETY_META: dict[str, dict[str, Any]] = {
    "RB": {"exchange": "SHFE", "size": 10, "pricetick": 1.0, "rate": 0.0001},
    "HC": {"exchange": "SHFE", "size": 10, "pricetick": 1.0, "rate": 0.0001},
    "CU": {"exchange": "SHFE", "size": 5, "pricetick": 10.0, "rate": 0.0001},
    "AL": {"exchange": "SHFE", "size": 5, "pricetick": 5.0, "rate": 0.0001},
    "ZN": {"exchange": "SHFE", "size": 5, "pricetick": 5.0, "rate": 0.0001},
    "AU": {"exchange": "SHFE", "size": 1000, "pricetick": 0.02, "rate": 0.0001},
    "AG": {"exchange": "SHFE", "size": 15, "pricetick": 1.0, "rate": 0.0001},
    "RU": {"exchange": "SHFE", "size": 10, "pricetick": 5.0, "rate": 0.0001},
    "IF": {"exchange": "CFFEX", "size": 300, "pricetick": 0.2, "rate": 0.000023},
    "IH": {"exchange": "CFFEX", "size": 300, "pricetick": 0.2, "rate": 0.000023},
    "IC": {"exchange": "CFFEX", "size": 200, "pricetick": 0.2, "rate": 0.000023},
    "IM": {"exchange": "CFFEX", "size": 200, "pricetick": 0.2, "rate": 0.000023},
    "I": {"exchange": "DCE", "size": 100, "pricetick": 0.5, "rate": 0.0001},
    "J": {"exchange": "DCE", "size": 100, "pricetick": 0.5, "rate": 0.0001},
    "JM": {"exchange": "DCE", "size": 60, "pricetick": 0.5, "rate": 0.0001},
    "M": {"exchange": "DCE", "size": 10, "pricetick": 1.0, "rate": 0.0001},
    "Y": {"exchange": "DCE", "size": 10, "pricetick": 2.0, "rate": 0.0001},
    "P": {"exchange": "DCE", "size": 10, "pricetick": 2.0, "rate": 0.0001},
    "C": {"exchange": "DCE", "size": 10, "pricetick": 1.0, "rate": 0.0001},
    "TA": {"exchange": "CZCE", "size": 5, "pricetick": 2.0, "rate": 0.0001},
    "MA": {"exchange": "CZCE", "size": 10, "pricetick": 1.0, "rate": 0.0001},
    "SR": {"exchange": "CZCE", "size": 10, "pricetick": 1.0, "rate": 0.0001},
    "CF": {"exchange": "CZCE", "size": 5, "pricetick": 5.0, "rate": 0.0001},
    "SC": {"exchange": "INE", "size": 1000, "pricetick": 0.1, "rate": 0.0001},
}

_fees_cache: pd.DataFrame | None = None


def _default_progress(msg: str) -> None:
    print(msg, flush=True)


def _noop_progress(_msg: str) -> None:
    pass


def _wrap_engine_output(engine: Any) -> None:
    """vnpy 回测 progress 逐条 print 后立即 flush，避免与结果一并显示。"""
    original = engine.output

    def output(msg: str) -> None:
        original(msg)
        sys.stdout.flush()

    engine.output = output


def _fetch_fees_info() -> pd.DataFrame:
    """从 akshare futures_fees_info 拉取全市场手续费表。"""
    import akshare as ak

    df = ak.futures_fees_info()
    df = df.copy()
    df["_variety"] = df["品种代码"].astype(str).str.upper()
    df["_contract"] = df["合约代码"].astype(str).str.upper()
    return df


def get_fees_table(
    *,
    refresh: bool = False,
    on_progress: ProgressCallback | None = None,
) -> pd.DataFrame:
    """获取（缓存）openctp 手续费参照表。"""
    global _fees_cache
    report = on_progress or _noop_progress
    if _fees_cache is None or refresh:
        report("      连接 openctp 拉取 futures_fees_info ...")
        _fees_cache = _fetch_fees_info()
        report(f"      手续费表就绪（{len(_fees_cache)} 条）")
    else:
        report("      使用已缓存的手续费表")
    return _fees_cache


def _pick_fee_row(
    df: pd.DataFrame,
    variety: str,
    contract: str | None = None,
) -> pd.Series | None:
    """按品种/合约选取手续费行；未指定合约时取持仓量最大者。"""
    sub = df[df["_variety"] == variety.upper()]
    if sub.empty:
        return None

    if contract:
        match = sub[sub["_contract"] == contract.upper()]
        if not match.empty:
            return match.iloc[0]

    if "持仓量" in sub.columns and sub["持仓量"].notna().any():
        idx = sub["持仓量"].fillna(0).idxmax()
        return sub.loc[idx]

    return sub.iloc[0]


def _calc_rate_from_comm_info_row(row: pd.Series) -> float | None:
    """从 futures_comm_info 单行推算 vnpy 等效 rate（优先万分之字段）。"""
    open_wf = row.get("手续费标准-开仓-万分之")
    close_wf = row.get("手续费标准-平昨-万分之")
    open_rate = float(open_wf) / 10000 if pd.notna(open_wf) and float(open_wf) > 0 else 0.0
    close_rate = float(close_wf) / 10000 if pd.notna(close_wf) and float(close_wf) > 0 else 0.0

    if open_rate > 0 or close_rate > 0:
        if open_rate > 0 and close_rate > 0:
            return (open_rate + close_rate) / 2
        return open_rate or close_rate
    return None


def _try_comm_info(variety: str, contract: str | None = None) -> dict[str, Any] | None:
    """备用：九期网 futures_comm_info（网络不稳定时可能失败）。"""
    import akshare as ak

    df = ak.futures_comm_info(symbol="所有")
    if df is None or df.empty:
        return None

    df = df.copy()
    df["_contract"] = df["合约代码"].astype(str).str.upper()

    sub = df[df["_contract"].str.startswith(variety.upper())]
    if sub.empty:
        return None

    if contract:
        match = sub[sub["_contract"] == contract.upper()]
        row = match.iloc[0] if not match.empty else sub.iloc[0]
    else:
        row = sub.iloc[0]

    rate = _calc_rate_from_comm_info_row(row)
    if rate is None or rate <= 0:
        return None

    return {
        "rate": rate,
        "fee_source": "futures_comm_info",
        "fee_contract": str(row.get("合约代码", "")),
        "fee_updated": str(row.get("手续费更新时间", "")),
    }


def calc_vnpy_rate_from_fees(row: pd.Series) -> float:
    """
    将 akshare 手续费行换算为 vnpy BacktestingEngine 的 rate。

    vnpy 按 turnover * rate 计费，故用「1手开/平仓费用 ÷ 名义价值」
    取开平均值作为等效 rate；固定手续费品种（如 AU）也能正确近似。
    """
    price = float(row.get("最新价") or row.get("上日收盘价") or row.get("上日结算价") or 0)
    size = float(row.get("合约乘数") or 0)
    open_rate = float(row.get("开仓费率") or 0)
    close_rate = float(row.get("平仓费率") or 0)

    if price > 0 and size > 0:
        turnover = price * size
        open_cost = float(row.get("1手开仓费用") or 0)
        close_cost = float(row.get("1手平仓费用") or 0)
        if open_cost > 0 and close_cost > 0:
            return (open_cost + close_cost) / (2 * turnover)
        if open_cost > 0:
            return open_cost / turnover
        if close_cost > 0:
            return close_cost / turnover

    if open_rate > 0 or close_rate > 0:
        return (open_rate + close_rate) / 2 if (open_rate and close_rate) else (open_rate or close_rate)

    return 0.0001


def resolve_trading_costs(
    parsed: dict[str, Any],
    contract: str | None = None,
    *,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """
    对接 akshare 手续费接口，覆盖静态 VARIETY_META 中的 rate/size/pricetick。

    优先级: futures_fees_info → futures_comm_info → VARIETY_META 静态值
    """
    variety = parsed["variety"]
    target_contract = contract or parsed.get("contract")
    costs: dict[str, Any] = {
        "rate": parsed.get("rate", 0.0001),
        "size": parsed.get("size", 10),
        "pricetick": parsed.get("pricetick", 1.0),
        "fee_source": "static",
        "fee_contract": target_contract or "",
        "fee_updated": "",
    }

    report = on_progress or _noop_progress
    try:
        row = _pick_fee_row(
            get_fees_table(on_progress=on_progress), variety, target_contract,
        )
        if row is not None:
            costs["rate"] = calc_vnpy_rate_from_fees(row)
            if pd.notna(row.get("合约乘数")):
                costs["size"] = float(row["合约乘数"])
            if pd.notna(row.get("最小跳动")):
                costs["pricetick"] = float(row["最小跳动"])
            costs["fee_source"] = "futures_fees_info"
            costs["fee_contract"] = str(row.get("合约代码", ""))
            costs["fee_updated"] = str(row.get("更新时间", ""))
            report(
                f"      费率 {costs['rate']:.6f}（参照 {costs['fee_contract']}）"
            )
            return costs
    except Exception:
        pass

    try:
        report("      主源失败，尝试 futures_comm_info ...")
        comm = _try_comm_info(variety, target_contract)
        if comm:
            costs.update(comm)
            return costs
    except Exception:
        pass

    return costs


def _require_vnpy_cta():
    try:
        from vnpy_ctastrategy.backtesting import BacktestingEngine
        from vnpy_ctastrategy import CtaTemplate
        return BacktestingEngine, CtaTemplate
    except ImportError as e:
        raise ImportError(
            "请先安装 vnpy_ctastrategy: pip install vnpy_ctastrategy\n"
            "VeighNa 回测框架文档: https://github.com/vnpy/vnpy"
        ) from e


def parse_futures_symbol(symbol: str) -> dict[str, Any]:
    """
    解析用户输入的合约/品种代码。

    支持:
      - RB2410 / rb2410       → 指定合约
      - RB2410.SHFE           → 指定合约 + 交易所
      - RB0 / rb0             → 品种主力序列（持仓量换月，非主连）
      - RB                    → 同上
    """
    raw = symbol.strip()
    upper = raw.upper()

    if "." in upper:
        sym, ex = upper.split(".", 1)
        variety = re.match(r"^([A-Z]+)", sym).group(1)
        meta = VARIETY_META.get(variety, {"exchange": ex, "size": 10, "pricetick": 1.0, "rate": 0.0001})
        return {
            "variety": variety,
            "exchange": ex,
            "contract": sym,
            "data_mode": "specific",
            "vt_symbol": f"{sym.lower()}.{ex}",
            **meta,
        }

    m = re.match(r"^([A-Z]+)(\d{4})$", upper)
    if m:
        variety, _ = m.group(1), m.group(2)
        meta = VARIETY_META.get(variety, {"exchange": "SHFE", "size": 10, "pricetick": 1.0, "rate": 0.0001})
        ex = meta["exchange"]
        return {
            "variety": variety,
            "exchange": ex,
            "contract": upper,
            "data_mode": "specific",
            "vt_symbol": f"{upper.lower()}.{ex}",
            **meta,
        }

    if upper.endswith("0") and re.match(r"^[A-Z]+\d$", upper):
        variety = upper[:-1]
    else:
        variety = upper

    meta = VARIETY_META.get(variety, {"exchange": "SHFE", "size": 10, "pricetick": 1.0, "rate": 0.0001})
    ex = meta["exchange"]
    return {
        "variety": variety,
        "exchange": ex,
        "contract": None,
        "data_mode": "dominant",
        "vt_symbol": f"{variety.lower()}_dom.{ex}",
        **meta,
    }


def load_futures_bars(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    data_mode: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[list[BarData], pd.DataFrame, dict[str, Any]]:
    """
    从 akshare 加载期货 K 线（分合约，非主连拼接）。

    Returns:
        bars: vnpy BarData 列表
        meta_df: 原始/主力序列 DataFrame
        info: 解析信息（data_mode, contracts_used 等）
    """
    import akshare as ak

    report = on_progress or _noop_progress
    parsed = parse_futures_symbol(symbol)
    mode = data_mode or parsed["data_mode"]
    exchange_str = parsed["exchange"]
    variety = parsed["variety"]
    exchange = Exchange(exchange_str)

    report(
        f"      请求 akshare get_futures_daily"
        f"（{exchange_str} · {start_date}~{end_date}）..."
        f"\n      网络较慢，若出现「链接失败」属正常重试，请稍候"
    )
    df = ak.get_futures_daily(start_date=start_date, end_date=end_date, market=exchange_str)
    if df is None or df.empty:
        raise ValueError(f"未获取到 {exchange_str} 在 {start_date}~{end_date} 的期货数据")

    report(f"      原始数据 {len(df)} 行，筛选品种 {variety} ...")
    df = df.copy()
    if "variety" in df.columns:
        df = df[df["variety"].astype(str).str.upper() == variety]
    else:
        df = df[df["symbol"].astype(str).str.upper().str.startswith(variety)]

    if df.empty:
        raise ValueError(f"交易所 {exchange_str} 中未找到品种 {variety} 的数据")

    for col in ("open", "high", "low", "close", "volume", "open_interest", "settle", "turnover"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["date"] = pd.to_datetime(df["date"])

    if mode == "specific":
        contract = parsed.get("contract") or symbol.upper()
        sub = df[df["symbol"].astype(str).str.upper() == contract.upper()]
        if sub.empty:
            raise ValueError(f"未找到指定合约 {contract} 的数据，请检查合约代码与日期区间")
        roll_df = sub.sort_values("date")
    else:
        report("      按持仓量构建主力换月序列 ...")
        df = df.dropna(subset=["open_interest", "close"])
        idx = df.groupby("date")["open_interest"].idxmax()
        roll_df = df.loc[idx].sort_values("date")

    vt_sym = parsed["vt_symbol"]
    sym_part = vt_sym.split(".")[0]
    bars: list[BarData] = []
    for _, row in roll_df.iterrows():
        dt = row["date"].to_pydatetime().replace(hour=15, minute=0, second=0, microsecond=0)
        bars.append(
            BarData(
                gateway_name="BACKTESTING",
                symbol=sym_part,
                exchange=exchange,
                datetime=dt,
                interval=Interval.DAILY,
                open_price=float(row["open"]),
                high_price=float(row["high"]),
                low_price=float(row["low"]),
                close_price=float(row["close"]),
                volume=float(row.get("volume") or 0),
                open_interest=float(row.get("open_interest") or 0),
                turnover=float(row.get("turnover") or 0),
            )
        )

    contracts_used = roll_df["symbol"].astype(str).unique().tolist()
    info = {
        **parsed,
        "data_mode": mode,
        "contracts_used": contracts_used,
        "bar_count": len(bars),
    }
    contracts_txt = ", ".join(contracts_used[:3])
    if len(contracts_used) > 3:
        contracts_txt += f" 等{len(contracts_used)}个"
    report(f"      K 线就绪：{len(bars)} 根（{contracts_txt}）")
    return bars, roll_df, info


TRADE_SIGNAL_LABELS: dict[str, str] = {
    "open_long": "开多",
    "close_long": "平多",
    "open_short": "开空",
    "close_short": "平空",
}


def extract_trade_signals(engine: Any) -> list[dict[str, Any]]:
    """从 vnpy 回测引擎成交记录提取多空双向信号列表。"""
    from vnpy.trader.constant import Direction, Offset

    trades = engine.get_all_trades()
    trade_list = list(trades.values()) if isinstance(trades, dict) else list(trades)
    signals: list[dict[str, Any]] = []

    for trade in sorted(trade_list, key=lambda t: t.datetime or datetime.min):
        if trade.direction == Direction.LONG and trade.offset == Offset.OPEN:
            signal = "open_long"
        elif trade.direction == Direction.SHORT and trade.offset == Offset.CLOSE:
            signal = "close_long"
        elif trade.direction == Direction.SHORT and trade.offset == Offset.OPEN:
            signal = "open_short"
        elif trade.direction == Direction.LONG and trade.offset == Offset.CLOSE:
            signal = "close_short"
        else:
            signal = "unknown"

        signals.append({
            "signal": signal,
            "label": TRADE_SIGNAL_LABELS.get(signal, signal),
            "datetime": str(trade.datetime) if trade.datetime else "",
            "price": round(float(trade.price), 4),
            "volume": float(trade.volume),
        })
    return signals


def run_vnpy_backtest(
    strategy_class: type,
    strategy_name: str,
    symbol: str,
    setting: dict[str, Any],
    *,
    start_date: str,
    end_date: str,
    capital: int = 1_000_000,
    slippage: float = 0.0,
    data_mode: str | None = None,
    on_progress: ProgressCallback | None = None,
    verbose: bool = True,
) -> BacktestResult:
    """使用 vnpy BacktestingEngine 执行回测并返回 BacktestResult。"""
    from FuturesSkill import BacktestResult

    BacktestingEngine, _ = _require_vnpy_cta()
    progress = on_progress or (_default_progress if verbose else _noop_progress)

    parsed = parse_futures_symbol(symbol)
    progress(f"[1/4] 解析合约 {symbol} → {parsed['variety']} @ {parsed['exchange']}")

    progress("[2/4] 拉取手续费率 ...")
    costs = resolve_trading_costs(
        parsed, contract=parsed.get("contract"), on_progress=progress,
    )
    if costs["fee_source"] == "static":
        progress(f"      使用静态默认费率 {costs['rate']:.6f}")

    progress(f"[3/4] 拉取历史 K 线（{start_date} ~ {end_date}）...")
    bars, roll_df, info = load_futures_bars(
        symbol, start_date, end_date, data_mode=data_mode, on_progress=progress,
    )
    if not bars:
        raise ValueError("回测数据为空")

    vt_symbol = info["vt_symbol"]
    start_dt = bars[0].datetime
    end_dt = bars[-1].datetime

    progress(f"[4/4] vnpy 策略回测（{strategy_name}）...")
    engine = BacktestingEngine()
    _wrap_engine_output(engine)
    engine.set_parameters(
        vt_symbol=vt_symbol,
        interval=Interval.DAILY,
        start=start_dt,
        end=end_dt,
        rate=costs["rate"],
        slippage=slippage,
        size=costs["size"],
        pricetick=costs["pricetick"],
        capital=capital,
    )
    engine.add_strategy(strategy_class, setting)
    engine.history_data = bars
    engine.run_backtesting()

    progress("      计算盈亏与统计指标 ...")
    daily_df = engine.calculate_result()
    stats = engine.calculate_statistics(output=False)

    if daily_df is None or daily_df.empty or not stats:
        raise ValueError("vnpy 回测未产生有效结果（可能无成交）")

    total_return = float(stats.get("total_return", 0))
    max_dd = float(stats.get("max_ddpercent", 0))
    sharpe = float(stats.get("sharpe_ratio", 0))
    trade_count = int(stats.get("total_trade_count", 0))
    profit_days = int(stats.get("profit_days", 0))
    loss_days = int(stats.get("loss_days", 0))
    win_rate = (profit_days / (profit_days + loss_days) * 100) if (profit_days + loss_days) else 0.0

    balance = daily_df["balance"]
    equity_curve = (balance / capital * 100).round(2).tolist()
    curve_dates = [str(d) for d in daily_df.index]
    rolling_max = balance.cummax()
    drawdown_curve = ((balance - rolling_max) / rolling_max * 100).round(2).tolist()

    closes = roll_df.set_index("date")["close"].reindex(daily_df.index, method="ffill")
    benchmark = (closes / closes.iloc[0] * 100).round(2).fillna(100).tolist()

    display_params = {k: v for k, v in setting.items()}
    if data_mode:
        display_params["data_mode"] = data_mode
    display_params["commission_rate"] = round(costs["rate"], 8)
    display_params["fee_source"] = costs["fee_source"]
    if costs.get("fee_contract"):
        display_params["fee_contract"] = costs["fee_contract"]

    trade_signals = extract_trade_signals(engine)

    return BacktestResult(
        strategy=strategy_name,
        symbol=symbol,
        params=display_params,
        total_return_pct=round(total_return, 2),
        max_drawdown_pct=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 2),
        trade_count=trade_count,
        win_rate_pct=round(win_rate, 2),
        start_date=str(curve_dates[0])[:10],
        end_date=str(curve_dates[-1])[:10],
        data_points=len(bars),
        curve_dates=curve_dates,
        equity_curve=equity_curve,
        benchmark_curve=benchmark,
        drawdown_curve=drawdown_curve,
        backtest_engine="vnpy",
        data_mode=info["data_mode"],
        vt_symbol=vt_symbol,
        contracts_used=info.get("contracts_used", []),
        signals=trade_signals,
    )
