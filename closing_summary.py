#!/usr/bin/env python3
"""
期货每日收盘总结 — 独立模块，与 FuturesSkill 核心逻辑隔离。

收盘后（或休息日）生成简明推送文案：涨跌幅排名、市场广度、板块概况、后续关注提示。
数据源：akshare get_futures_daily（各交易所分合约，按持仓量取品种主力）。

单独调用:
  python closing_summary.py
  python closing_summary.py --date 20241231 --top 5 --json
  python closing_summary.py --push   # 仅输出 push_message（适合 cron 推送）

OpenClaw 定时（工作日 15:10）:
  python closing_summary.py --push --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

import pandas as pd

ProgressCallback = Callable[[str], None]

EXCHANGES = ("SHFE", "DCE", "CZCE", "CFFEX", "INE", "GFEX")

# 品种 → 板块（用于板块涨跌统计）
VARIETY_SECTOR: dict[str, str] = {
    "RB": "黑色", "HC": "黑色", "I": "黑色", "J": "黑色", "JM": "黑色",
    "SF": "黑色", "SM": "黑色", "SS": "黑色",
    "CU": "有色", "AL": "有色", "ZN": "有色", "PB": "有色", "NI": "有色",
    "SN": "有色", "AO": "有色", "BC": "有色",
    "AU": "贵金属", "AG": "贵金属",
    "TA": "化工", "MA": "化工", "PP": "化工", "L": "化工", "V": "化工",
    "EG": "化工", "EB": "化工", "PG": "化工", "BU": "化工", "FU": "化工",
    "LU": "化工", "UR": "化工", "SA": "化工", "FG": "化工", "SP": "化工",
    "M": "农产品", "Y": "农产品", "P": "农产品", "C": "农产品", "A": "农产品",
    "B": "农产品", "CS": "农产品", "JD": "农产品", "LH": "农产品", "AP": "农产品",
    "SR": "农产品", "CF": "农产品", "RM": "农产品", "OI": "农产品", "PK": "农产品",
    "CJ": "农产品", "CY": "农产品",
    "IF": "金融", "IH": "金融", "IC": "金融", "IM": "金融",
    "T": "金融", "TF": "金融", "TS": "金融", "TL": "金融",
    "SC": "能源", "NR": "能源", "EC": "航运",
}

EXCHANGE_LABEL: dict[str, str] = {
    "SHFE": "上期所", "DCE": "大商所", "CZCE": "郑商所",
    "CFFEX": "中金所", "INE": "能源中心", "GFEX": "广期所",
}


def _noop(_: str) -> None:
    pass


def _parse_yyyymmdd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def _fmt_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def _weekday_cn(d: date) -> str:
    return "一二三四五六日"[d.weekday()]


@dataclass
class VarietySnapshot:
    """单品种主力合约某日快照。"""

    variety: str
    symbol: str
    name: str
    exchange: str
    sector: str
    close: float
    settle: float
    pre_settle: float
    change_pct: float
    volume: float
    open_interest: float
    turnover: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClosingSummaryResult:
    """收盘总结结果。"""

    trade_date: str
    as_of_date: str
    is_rest_day: bool
    message: str
    push_message: str
    total_varieties: int
    up_count: int
    down_count: int
    flat_count: int
    top_gainers: list[dict[str, Any]] = field(default_factory=list)
    top_losers: list[dict[str, Any]] = field(default_factory=list)
    sector_stats: list[dict[str, Any]] = field(default_factory=list)
    volume_leaders: list[dict[str, Any]] = field(default_factory=list)
    follow_up_hints: list[str] = field(default_factory=list)
    data_source: str = "akshare get_futures_daily"
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"期货收盘总结 · {self.trade_date}"
            + ("（非交易日，展示最近交易日）" if self.is_rest_day else ""),
            f"生成时间: {self.generated_at}",
            f"市场广度: 涨 {self.up_count} | 跌 {self.down_count} | 平 {self.flat_count}"
            f"（共 {self.total_varieties} 品种）",
            "",
            self.push_message,
        ]
        return "\n".join(lines)


def load_main_symbol_names(on_progress: ProgressCallback | None = None) -> dict[str, str]:
    """新浪主力连续名称表：symbol → 中文名。"""
    import akshare as ak

    report = on_progress or _noop
    report("      拉取主力连续品种名称 ...")
    df = ak.futures_display_main_sina()
    mapping: dict[str, str] = {}
    for _, row in df.iterrows():
        sym = str(row.get("symbol", "")).upper()
        name = str(row.get("name", sym))
        if sym:
            mapping[sym] = name
        variety = re.match(r"^([A-Z]+)", sym)
        if variety:
            mapping[variety.group(1)] = name.replace("连续", "").strip() or name
    return mapping


def fetch_exchange_daily(
    start: str,
    end: str,
    market: str,
    *,
    on_progress: ProgressCallback | None = None,
) -> pd.DataFrame:
    import akshare as ak

    report = on_progress or _noop
    report(f"      {EXCHANGE_LABEL.get(market, market)} ({start}~{end}) ...")
    df = ak.get_futures_daily(start_date=start, end_date=end, market=market)
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out["exchange"] = market
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y%m%d")
    for col in ("open", "high", "low", "close", "volume", "open_interest", "turnover", "settle", "pre_settle"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def fetch_all_daily(
    start: str,
    end: str,
    *,
    on_progress: ProgressCallback | None = None,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for ex in EXCHANGES:
        try:
            sub = fetch_exchange_daily(start, end, ex, on_progress=on_progress)
            if not sub.empty:
                frames.append(sub)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def dominant_by_date(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """某日各品种持仓量最大合约。"""
    day = df[df["date"] == trade_date].copy()
    if day.empty:
        return day
    day = day.dropna(subset=["open_interest", "settle"])
    if day.empty:
        return day
    idx = day.groupby("variety")["open_interest"].idxmax()
    return day.loc[idx].sort_values("variety")


def snapshots_from_dominant(
    dominant: pd.DataFrame,
    name_map: dict[str, str],
) -> list[VarietySnapshot]:
    rows: list[VarietySnapshot] = []
    for _, r in dominant.iterrows():
        variety = str(r.get("variety", "")).upper()
        symbol = str(r.get("symbol", "")).upper()
        settle = float(r.get("settle") or 0)
        pre = float(r.get("pre_settle") or 0)
        if pre <= 0:
            pre = settle
        change = (settle - pre) / pre * 100 if pre else 0.0
        close = float(r.get("close") or settle)
        name = name_map.get(symbol) or name_map.get(f"{variety}0") or name_map.get(variety) or variety
        exchange = str(r.get("exchange", ""))
        rows.append(
            VarietySnapshot(
                variety=variety,
                symbol=symbol,
                name=name,
                exchange=exchange,
                sector=VARIETY_SECTOR.get(variety, "其他"),
                close=close,
                settle=settle,
                pre_settle=pre,
                change_pct=round(change, 2),
                volume=float(r.get("volume") or 0),
                open_interest=float(r.get("open_interest") or 0),
                turnover=float(r.get("turnover") or 0),
            )
        )
    return rows


def resolve_latest_trade_date(
    as_of: date,
    *,
    lookback_days: int = 15,
    on_progress: ProgressCallback | None = None,
) -> tuple[str, bool]:
    """
    解析最近交易日。

    Returns:
        (trade_date YYYYMMDD, is_rest_day)
    """
    report = on_progress or _noop
    start = as_of - timedelta(days=lookback_days)
    report(f"[1/3] 解析交易日（基准 {as_of}）...")
    df = fetch_all_daily(_fmt_date(start), _fmt_date(as_of), on_progress=on_progress)
    if df.empty:
        raise ValueError("未能拉取交易所日线数据，请检查网络或 akshare")

    available = sorted(df["date"].unique())
    as_of_str = _fmt_date(as_of)
    if as_of_str in available:
        return as_of_str, False

    prior = [d for d in available if d < as_of_str]
    if not prior:
        raise ValueError(f"近 {lookback_days} 日内无可用交易日数据")
    return prior[-1], True


def compute_sector_stats(snapshots: list[VarietySnapshot]) -> list[dict[str, Any]]:
    if not snapshots:
        return []
    df = pd.DataFrame([s.to_dict() for s in snapshots])
    stats: list[dict[str, Any]] = []
    for sector, grp in df.groupby("sector"):
        stats.append({
            "sector": sector,
            "count": len(grp),
            "avg_change_pct": round(float(grp["change_pct"].mean()), 2),
            "up": int((grp["change_pct"] > 0).sum()),
            "down": int((grp["change_pct"] < 0).sum()),
        })
    stats.sort(key=lambda x: x["avg_change_pct"], reverse=True)
    return stats


def build_follow_up_hints(
    gainers: list[VarietySnapshot],
    losers: list[VarietySnapshot],
    volume_leaders: list[VarietySnapshot],
    sector_stats: list[dict[str, Any]],
) -> list[str]:
    hints: list[str] = []
    if gainers:
        top = gainers[0]
        hints.append(
            f"涨幅榜首 {top.name}({top.variety}) +{top.change_pct:.2f}%，"
            f"关注强势能否延续及主力 {top.symbol} 换月情况。"
        )
    if losers:
        bot = losers[0]
        hints.append(
            f"跌幅居前 {bot.name}({bot.variety}) {bot.change_pct:.2f}%，"
            f"留意是否超跌反弹或趋势延续。"
        )
    if volume_leaders:
        vol = volume_leaders[0]
        hints.append(
            f"成交活跃 {vol.name}({vol.variety}) 成交量 {vol.volume:,.0f} 手，"
            f"价格波动可能加大，适合纳入监控列表。"
        )
    if sector_stats:
        best = sector_stats[0]
        worst = sector_stats[-1]
        if best["sector"] != worst["sector"]:
            hints.append(
                f"板块分化：{best['sector']}平均{best['avg_change_pct']:+.2f}%偏强，"
                f"{worst['sector']}{worst['avg_change_pct']:+.2f}%偏弱，可留意轮动机会。"
            )
    if len(gainers) >= 3:
        names = "、".join(f"{g.name}({g.change_pct:+.2f}%)" for g in gainers[:3])
        hints.append(f"涨幅前三：{names}，后续可结合策略信号（如 monitor-strategies）跟踪。")
    return hints[:5]


def build_push_message(
    trade_date: str,
    is_rest_day: bool,
    snapshots: list[VarietySnapshot],
    top_n: int,
    sector_stats: list[dict[str, Any]],
    hints: list[str],
) -> str:
    d = datetime.strptime(trade_date, "%Y%m%d").date()
    title = f"📊 期货收盘总结 {trade_date}（周{_weekday_cn(d)}）"
    if is_rest_day:
        title += " · 最近交易日"

    up = sum(1 for s in snapshots if s.change_pct > 0)
    down = sum(1 for s in snapshots if s.change_pct < 0)
    flat = len(snapshots) - up - down

    sorted_snaps = sorted(snapshots, key=lambda s: s.change_pct, reverse=True)
    gainers = sorted_snaps[:top_n]
    losers = sorted(sorted_snaps, key=lambda s: s.change_pct)[:top_n]

    lines = [
        title,
        f"市场：涨 {up} / 跌 {down} / 平 {flat}（{len(snapshots)} 品种主力）",
        "",
        f"🔺 涨幅前{top_n}",
    ]
    for i, s in enumerate(gainers, 1):
        lines.append(
            f"  {i}. {s.name} {s.symbol}  {s.change_pct:+.2f}%  "
            f"结算 {s.settle:g}  持仓 {s.open_interest:,.0f}"
        )

    lines.append("")
    lines.append(f"🔻 跌幅前{top_n}")
    for i, s in enumerate(losers, 1):
        lines.append(
            f"  {i}. {s.name} {s.symbol}  {s.change_pct:+.2f}%  "
            f"结算 {s.settle:g}  持仓 {s.open_interest:,.0f}"
        )

    if sector_stats:
        lines.append("")
        lines.append("📂 板块均涨跌幅")
        for st in sector_stats[:6]:
            lines.append(
                f"  · {st['sector']}  均{st['avg_change_pct']:+.2f}%  "
                f"({st['up']}涨/{st['down']}跌)"
            )

    if hints:
        lines.append("")
        lines.append("💡 后续关注")
        for h in hints:
            lines.append(f"  · {h}")

    lines.append("")
    lines.append("— 数据来源 akshare 交易所日线 · 主力=持仓量最大合约")
    return "\n".join(lines)


def run_closing_summary(
    *,
    as_of_date: str | None = None,
    top_n: int = 5,
    on_progress: ProgressCallback | None = None,
) -> ClosingSummaryResult:
    """生成收盘总结。"""
    report = on_progress or _noop
    as_of = _parse_yyyymmdd(as_of_date) if as_of_date else date.today()
    as_of_str = _fmt_date(as_of)

    trade_date, is_rest = resolve_latest_trade_date(as_of, on_progress=report)

    report(f"[2/3] 拉取 {trade_date} 主力合约数据 ...")
    start = _parse_yyyymmdd(trade_date) - timedelta(days=3)
    df = fetch_all_daily(_fmt_date(start), trade_date, on_progress=report)
    dominant = dominant_by_date(df, trade_date)
    if dominant.empty:
        raise ValueError(f"交易日 {trade_date} 无有效主力合约数据")

    name_map = load_main_symbol_names(on_progress=report)
    snapshots = snapshots_from_dominant(dominant, name_map)

    report("[3/3] 汇总排名与推送文案 ...")
    sorted_snaps = sorted(snapshots, key=lambda s: s.change_pct, reverse=True)
    gainers = sorted_snaps[:top_n]
    losers = sorted(sorted_snaps, key=lambda s: s.change_pct)[:top_n]
    vol_leaders = sorted(snapshots, key=lambda s: s.volume, reverse=True)[:top_n]

    sector_stats = compute_sector_stats(snapshots)
    hints = build_follow_up_hints(gainers, losers, vol_leaders, sector_stats)
    push = build_push_message(trade_date, is_rest, snapshots, top_n, sector_stats, hints)

    up = sum(1 for s in snapshots if s.change_pct > 0)
    down = sum(1 for s in snapshots if s.change_pct < 0)
    flat = len(snapshots) - up - down

    msg = "休息日，展示最近交易日数据。" if is_rest else "当日收盘总结。"

    return ClosingSummaryResult(
        trade_date=trade_date,
        as_of_date=as_of_str,
        is_rest_day=is_rest,
        message=msg,
        push_message=push,
        total_varieties=len(snapshots),
        up_count=up,
        down_count=down,
        flat_count=flat,
        top_gainers=[g.to_dict() for g in gainers],
        top_losers=[l.to_dict() for l in losers],
        sector_stats=sector_stats,
        volume_leaders=[v.to_dict() for v in vol_leaders],
        follow_up_hints=hints,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="期货每日收盘总结（独立模块）")
    p.add_argument("--date", default=None, help="基准日期 YYYYMMDD，默认今天；非交易日自动回溯")
    p.add_argument("--top", type=int, default=5, help="涨跌幅排名条数，默认 5")
    p.add_argument("--json", action="store_true", help="JSON 输出")
    p.add_argument("--push", action="store_true", help="仅输出 push_message（适合推送）")
    p.add_argument("--quiet", action="store_true", help="不显示进度")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    progress = _noop if args.quiet else lambda m: print(m, flush=True)
    try:
        result = run_closing_summary(
            as_of_date=args.date,
            top_n=args.top,
            on_progress=progress,
        )
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    if args.push and not args.json:
        print(result.push_message)
    elif args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
