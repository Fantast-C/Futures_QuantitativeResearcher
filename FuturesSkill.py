#!/usr/bin/env python3
"""
AKShare 期货数据 Skill — 接口库 + 策略库

基于 futures.md 解析 akshare 期货接口，支持：
  1. 关键词检索并选择接口，返回接口信息与数据源
  2. 列出/检索 akshare 支持的期货数据类型
  3. 动态扩展接口库
  4. 策略库（vnpy CTA 回测，分合约/换月，非主连）
  5. 返回策略列表供选择

用法（CLI，供 OpenClaw / Agent 调用）:
  python FuturesSkill.py search "螺纹钢历史行情"
  python FuturesSkill.py select "获取库存数据"
  python FuturesSkill.py list-apis [--keyword 行情]
  python FuturesSkill.py call futures_main_sina --params '{"symbol":"RB0"}'
  python FuturesSkill.py add-api --name my_api --title "自定义" --url "..." --description "..."
  python FuturesSkill.py list-strategies
  python FuturesSkill.py select-strategy ma_crossover
  python FuturesSkill.py backtest-guide
  python FuturesSkill.py backtest ma_crossover --symbol RB0 --start 20230101 --end 20241231 --params '{"short":5,"long":20}'
  python FuturesSkill.py backtest-compare --symbol RB0 --start 20230101 --end 20241231
  python FuturesSkill.py monitor-strategy ma_crossover --symbol RB0 --interval daily --pos 0
  python FuturesSkill.py monitor-strategies --symbol RB0 --interval daily --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
import textwrap
from abc import ABC
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
FUTURES_MD = BASE_DIR / "futures.md"
CUSTOM_API_JSON = BASE_DIR / "api_custom.json"
CUSTOM_STRATEGIES_DIR = BASE_DIR / "strategies" / "custom"
STRATEGY_MANIFEST = BASE_DIR / "strategy_registry.json"
BACKTEST_OUTPUT_DIR = BASE_DIR / "output" / "backtest"

# 常见期货术语同义词，用于中文需求匹配
TERM_ALIASES: dict[str, list[str]] = {
    "行情": ["行情", "spot", "realtime", "daily", "hist", "price", "quotes"],
    "历史": ["历史", "daily", "hist", "日频", "日线"],
    "实时": ["实时", "spot", "realtime", "zh_spot"],
    "库存": ["库存", "inventory", "warehouse", "receipt", "仓单"],
    "持仓": ["持仓", "position", "hold", "rank"],
    "手续费": ["手续费", "comm", "fees", "保证金"],
    "交割": ["交割", "delivery"],
    "连续": ["连续", "main", "主力"],
    "螺纹钢": ["rb", "螺纹钢", "rb0"],
    "铁矿石": ["i", "铁矿石", "i0"],
    "原油": ["sc", "原油", "sc0"],
    "沪深300": ["if", "沪深300", "if0"],
    "合约": ["合约", "contract", "detail"],
    "外盘": ["外盘", "foreign", "global", "comex"],
    "基差": ["基差", "spot_price"],
    "展期": ["展期", "roll_yield"],
    "仓单": ["仓单", "receipt", "warehouse"],
    "资讯": ["资讯", "news"],
    "生猪": ["生猪", "hog"],
}


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class ApiInfo:
    """单个 akshare 期货接口的元信息。"""

    name: str
    title: str
    category: str
    url: str
    description: str
    limit: str = ""
    input_params: list[dict[str, str]] = field(default_factory=list)
    example: str = ""
    source: str = ""  # 从目标地址/描述推断的数据源
    keywords: list[str] = field(default_factory=list)
    custom: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"接口名称: {self.name}",
            f"标题: {self.title}",
            f"分类: {self.category}",
            f"数据源: {self.source or self.url}",
            f"目标地址: {self.url}",
            f"描述: {self.description}",
        ]
        if self.limit:
            lines.append(f"限量: {self.limit}")
        if self.input_params:
            lines.append("输入参数:")
            for p in self.input_params:
                lines.append(f"  - {p.get('名称', p.get('name', ''))}: {p.get('描述', p.get('desc', ''))}")
        if self.example:
            lines.append(f"调用示例: {self.example}")
        return "\n".join(lines)


@dataclass
class SearchResult:
    """接口检索结果。"""

    query: str
    keywords: list[str]
    matched: bool
    apis: list[ApiInfo]
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "keywords": self.keywords,
            "matched": self.matched,
            "message": self.message,
            "apis": [a.to_dict() for a in self.apis],
        }


@dataclass
class BacktestResult:
    """策略回测结果。"""

    strategy: str
    symbol: str
    params: dict[str, Any]
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    trade_count: int
    win_rate_pct: float
    start_date: str
    end_date: str
    data_points: int
    curve_dates: list[str] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    benchmark_curve: list[float] = field(default_factory=list)
    drawdown_curve: list[float] = field(default_factory=list)
    chart_path: str = ""
    backtest_engine: str = "vnpy"
    data_mode: str = "dominant"
    vt_symbol: str = ""
    contracts_used: list[str] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"策略: {self.strategy}",
            f"合约: {self.symbol}",
            f"回测引擎: {self.backtest_engine} (VeighNa)",
            f"数据模式: {self.data_mode} ({'持仓量主力换月' if self.data_mode == 'dominant' else '指定合约'})",
            f"vt_symbol: {self.vt_symbol}",
            f"参数: {self.params}",
            f"区间: {self.start_date} ~ {self.end_date} ({self.data_points} 条)",
            f"总收益率: {self.total_return_pct:.2f}%",
            f"最大回撤: {self.max_drawdown_pct:.2f}%",
            f"夏普比率: {self.sharpe_ratio:.2f}",
            f"交易次数: {self.trade_count}",
            f"胜率: {self.win_rate_pct:.2f}%",
        ]
        if self.signals:
            long_cnt = sum(1 for s in self.signals if s["signal"] in ("open_long", "close_long"))
            short_cnt = sum(1 for s in self.signals if s["signal"] in ("open_short", "close_short"))
            lines.append(f"交易信号: 多头 {long_cnt} 笔 | 空头 {short_cnt} 笔")
            show = self.signals[-8:]
            if len(self.signals) > 8:
                lines.append(f"  （末 {len(show)} 笔，共 {len(self.signals)} 笔）")
            for s in show:
                dt = s.get("datetime", "")[:16]
                lines.append(f"  {dt}  {s.get('label', s.get('signal'))}  @ {s.get('price')}")
        if self.contracts_used:
            shown = self.contracts_used[:5]
            suffix = f" ...共{len(self.contracts_used)}个" if len(self.contracts_used) > 5 else ""
            lines.append(f"涉及合约: {', '.join(shown)}{suffix}")
        if self.params.get("commission_rate") is not None:
            src = self.params.get("fee_source", "static")
            ref = self.params.get("fee_contract", "")
            ref_txt = f", 参照合约 {ref}" if ref else ""
            lines.append(
                f"手续费率: {self.params['commission_rate']:.6f} "
                f"(来源: {src}{ref_txt})"
            )
        if self.chart_path:
            lines.append(f"回测曲线: {self.chart_path}")
        return "\n".join(lines)


@dataclass
class StrategySelectResult:
    """策略选择确认结果。"""

    query: str
    matched: bool
    strategy_name: str
    info: dict[str, Any]
    default_params: dict[str, Any]
    input_spec: str
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "matched": self.matched,
            "strategy_name": self.strategy_name,
            "info": self.info,
            "default_params": self.default_params,
            "input_spec": self.input_spec,
            "message": self.message,
        }


@dataclass
class BacktestComparison:
    """多策略回测对比结果。"""

    symbol: str
    start_date: str
    end_date: str
    results: list[BacktestResult]
    errors: list[dict[str, str]]
    chart_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "results": [r.to_dict() for r in self.results],
            "errors": self.errors,
        }

    def summary_table(self) -> str:
        lines = [
            f"全策略回测对比 | 合约: {self.symbol} | 区间: {self.start_date} ~ {self.end_date}",
            "",
            f"{'策略':<18} {'总收益%':>9} {'最大回撤%':>10} {'夏普':>7} {'交易次数':>8} {'胜率%':>7}",
            "-" * 64,
        ]
        sorted_results = sorted(self.results, key=lambda r: r.total_return_pct, reverse=True)
        for r in sorted_results:
            lines.append(
                f"{r.strategy:<18} {r.total_return_pct:>9.2f} {r.max_drawdown_pct:>10.2f} "
                f"{r.sharpe_ratio:>7.2f} {r.trade_count:>8} {r.win_rate_pct:>7.2f}"
            )
        if self.errors:
            lines.append("")
            lines.append("以下策略回测失败:")
            for err in self.errors:
                lines.append(f"  - {err['strategy']}: {err['error']}")
        if sorted_results:
            best = sorted_results[0]
            lines.append("")
            lines.append(f"最优策略（按总收益率）: {best.strategy} ({best.total_return_pct:.2f}%)")
        if self.chart_path:
            lines.append(f"对比曲线: {self.chart_path}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 回测曲线可视化
# ---------------------------------------------------------------------------
CHART_COLORS = ["#2563EB", "#059669", "#D97706", "#7C3AED", "#DC2626", "#0891B2"]


def _setup_chart_style() -> None:
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "figure.facecolor": "#FFFFFF",
            "axes.facecolor": "#FAFAFA",
            "axes.edgecolor": "#E5E7EB",
            "axes.labelcolor": "#4B5563",
            "text.color": "#374151",
            "xtick.color": "#9CA3AF",
            "ytick.color": "#9CA3AF",
            "grid.color": "#E5E7EB",
            "grid.alpha": 0.6,
            "font.size": 10,
            "axes.titlesize": 11,
            "axes.titleweight": "500",
            "legend.frameon": False,
            "figure.dpi": 120,
            "savefig.dpi": 150,
            "savefig.bbox": "tight",
            "font.sans-serif": ["PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"],
            "axes.unicode_minus": False,
        }
    )


def _default_chart_path(prefix: str, symbol: str, start: str, end: str, ext: str = "png") -> Path:
    BACKTEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_start = start.replace("-", "")[:8]
    safe_end = end.replace("-", "")[:8]
    return BACKTEST_OUTPUT_DIR / f"{prefix}_{symbol}_{safe_start}_{safe_end}.{ext}"


def plot_backtest_curve(
    result: BacktestResult,
    output_path: Path | str | None = None,
    *,
    show: bool = True,
) -> Path:
    """绘制单策略回测曲线：净值 + 回撤。"""
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    if not result.curve_dates:
        raise ValueError("回测结果缺少曲线数据")

    _setup_chart_style()
    dates = pd.to_datetime(result.curve_dates)

    fig, (ax_eq, ax_dd) = plt.subplots(
        2, 1, figsize=(10, 5.5), sharex=True, gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08}
    )

    ax_eq.plot(dates, result.equity_curve, color="#2563EB", linewidth=1.8, label="策略净值")
    if result.benchmark_curve:
        ax_eq.plot(
            dates, result.benchmark_curve, color="#9CA3AF",
            linewidth=1.2, linestyle="--", label="基准（持有）",
        )
    ax_eq.axhline(100, color="#E5E7EB", linewidth=0.8, zorder=0)
    ax_eq.set_ylabel("净值")
    ax_eq.legend(loc="upper left")
    ax_eq.grid(True, axis="y", linewidth=0.5)
    ax_eq.spines["top"].set_visible(False)
    ax_eq.spines["right"].set_visible(False)
    ax_eq.set_title(
        f"{result.strategy}  ·  {result.symbol}  ·  {result.start_date} ~ {result.end_date}",
        loc="left", pad=12, color="#111827",
    )

    ret_label = f"{result.total_return_pct:+.2f}%"
    ax_eq.text(
        0.99, 0.04, ret_label, transform=ax_eq.transAxes, ha="right", va="bottom",
        fontsize=13, color="#2563EB" if result.total_return_pct >= 0 else "#DC2626", fontweight="500",
    )

    ax_dd.fill_between(dates, result.drawdown_curve, 0, color="#FCA5A5", alpha=0.45)
    ax_dd.plot(dates, result.drawdown_curve, color="#EF4444", linewidth=1.0)
    ax_dd.set_ylabel("回撤 %")
    ax_dd.grid(True, axis="y", linewidth=0.5)
    ax_dd.spines["top"].set_visible(False)
    ax_dd.spines["right"].set_visible(False)

    ax_dd.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax_dd.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=0, ha="center")

    path = Path(output_path) if output_path else _default_chart_path(
        result.strategy, result.symbol, result.start_date, result.end_date,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=fig.get_facecolor())
    if show:
        plt.show()
    else:
        plt.close(fig)
    return path


def plot_compare_curves(
    comparison: BacktestComparison,
    output_path: Path | str | None = None,
    *,
    show: bool = True,
) -> Path:
    """绘制多策略净值对比曲线。"""
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    valid = [r for r in comparison.results if r.curve_dates]
    if not valid:
        raise ValueError("无可用回测曲线数据")

    _setup_chart_style()
    fig, ax = plt.subplots(figsize=(10, 4.8))

    for i, r in enumerate(valid):
        color = CHART_COLORS[i % len(CHART_COLORS)]
        dates = pd.to_datetime(r.curve_dates)
        ax.plot(
            dates, r.equity_curve, color=color, linewidth=1.6,
            label=f"{r.strategy}  ({r.total_return_pct:+.2f}%)",
        )

    if valid[0].benchmark_curve:
        dates = pd.to_datetime(valid[0].curve_dates)
        ax.plot(
            dates, valid[0].benchmark_curve, color="#D1D5DB",
            linewidth=1.2, linestyle="--", label="基准（持有）",
        )

    ax.axhline(100, color="#E5E7EB", linewidth=0.8, zorder=0)
    ax.set_ylabel("净值")
    ax.legend(loc="upper left")
    ax.grid(True, axis="y", linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title(
        f"策略对比  ·  {comparison.symbol}  ·  {comparison.start_date} ~ {comparison.end_date}",
        loc="left", pad=12, color="#111827",
    )

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=0, ha="center")

    path = Path(output_path) if output_path else _default_chart_path(
        "compare", comparison.symbol, comparison.start_date, comparison.end_date,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=fig.get_facecolor())
    if show:
        plt.show()
    else:
        plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# 回测输入规范与校验
# ---------------------------------------------------------------------------
DATE_RE = re.compile(r"^\d{8}$")

BACKTEST_INPUT_GUIDE = """
══════════════════════════════════════════════════════════════
              策略回测 — 用户输入规范
══════════════════════════════════════════════════════════════

【步骤 1】确认策略
  1. 运行 list-strategies 查看策略库全部策略
  2. 运行 select-strategy <策略名> 确认所选策略及默认参数
  策略名示例: ma_crossover | turtle_trading | rsi_demo

【步骤 2】必填项
  ┌─────────────┬──────────────────────────────────────────────┐
  │ 字段        │ 说明                                         │
  ├─────────────┼──────────────────────────────────────────────┤
  │ strategy    │ 策略库中的策略名称（精确匹配）               │
  │ symbol      │ 品种/合约代码（见下方说明，非主连）          │
  │ start       │ 回测开始日期，格式 YYYYMMDD，如 20230101     │
  │ end         │ 回测结束日期，格式 YYYYMMDD，默认当天        │
  └─────────────┴──────────────────────────────────────────────┘

【symbol 输入说明 — 不使用主连虚假拼接】
  RB0 / RB     持仓量主力换月序列（akshare 分合约 + 换月逻辑）
  RB2410       指定单一合约回测
  RB2410.SHFE  指定合约及交易所

【回测引擎】VeighNa (vnpy_ctastrategy.BacktestingEngine)
  数据来源: akshare get_futures_daily（各交易所真实分合约日线）
  手续费率: 自动从 akshare futures_fees_info 拉取并覆盖静态默认值
            （备用 futures_comm_info；失败时回退 VARIETY_META）

【步骤 3】策略参数（--params JSON，未指定则用默认值）
  ma_crossover:    {"short": 5, "long": 20}
  turtle_trading:  {"system": 1}  或 {"entry_window": 20, "exit_window": 10}
  rsi_demo:        {"period": 14, "oversold": 30, "overbought": 70}

【步骤 4】单策略回测示例
  python FuturesSkill.py backtest ma_crossover \\
    --symbol RB0 --start 20230101 --end 20241231 \\
    --params '{"short":5,"long":20}'

【步骤 5】全策略对比回测
  python FuturesSkill.py backtest-compare \\
    --symbol RB0 --start 20230101 --end 20241231

  可选：仅对比指定策略
    --strategies '["ma_crossover","turtle_trading"]'
  可选：为各策略指定参数
    --params-map '{"ma_crossover":{"short":10,"long":30}}'

【常见合约代码】
  RB0 螺纹钢 | IF0 沪深300 | IC0 中证500 | AU0 黄金 | AG0 白银
  CU0 铜     | AL0 铝       | ZN0 锌       | M0  豆粕 | I0  铁矿石
══════════════════════════════════════════════════════════════
""".strip()

MONITOR_INPUT_GUIDE = """
══════════════════════════════════════════════════════════════
              策略盘中检测 — 用户输入规范（OpenClaw）
══════════════════════════════════════════════════════════════

【用途】
  定时拉取行情，检测策略是否**新触发**开/平仓信号；触发时输出 alert_message 供推送。

【推荐流程】
  list-strategies → monitor-strategy / monitor-strategies

【单策略检测】
  python FuturesSkill.py monitor-strategy ma_crossover \\
    --symbol RB0 --interval daily --pos 0 \\
    --params '{"short_window":5,"long_window":20}'

【全策略扫描（OpenClaw 定时任务）】
  python FuturesSkill.py monitor-strategies \\
    --symbol RB0 --interval daily --json

  仅输出有信号的条目:
    --alert-only

  有信号时进程退出码 2（便于 OpenClaw 分支）:
    --exit-on-alert

【字段说明】
  strategy   策略库精确名称（ma_crossover | turtle_trading | rsi_demo）
  symbol     新浪行情代码：RB0 / IF0 / RB2410 等
  interval   daily（日线+分钟刷新）| 1 | 5 | 15 | 30 | 60（分钟 K）
  pos        假定当前持仓手数，0=空仓，>0=持多，<0=持空（影响开/平仓判断）
  params     策略参数 JSON，默认同 select-strategy

【输出 JSON 关键字段】
  triggered      是否新触发信号
  signal         none | open_long | close_long
  alert_message  可直接推送的告警文本
  indicators     当前指标快照

【symbol 示例】RB0 螺纹钢主力 | IF0 沪深300 | AU0 黄金
══════════════════════════════════════════════════════════════
""".strip()


def validate_date(date_str: str, field_name: str = "日期") -> str:
    """校验 YYYYMMDD 格式日期。"""
    if not DATE_RE.match(date_str):
        raise ValueError(f"{field_name}格式错误: {date_str}，请使用 YYYYMMDD，如 20230101")
    try:
        datetime.strptime(date_str, "%Y%m%d")
    except ValueError as e:
        raise ValueError(f"{field_name}无效: {date_str}") from e
    return date_str


def validate_backtest_period(start: str, end: str) -> tuple[str, str]:
    """校验回测时间区间。"""
    start = validate_date(start, "开始日期")
    end = validate_date(end, "结束日期")
    if start > end:
        raise ValueError(f"开始日期 ({start}) 不能晚于结束日期 ({end})")
    return start, end


def resolve_strategy_params(strategy: BaseStrategy, user_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """合并用户参数与策略 param_schema 默认值。"""
    resolved: dict[str, Any] = {}
    for key, spec in strategy.param_schema.items():
        if "default" in spec:
            resolved[key] = spec["default"]
    if user_params:
        resolved.update(user_params)
    return resolved


def build_strategy_input_spec(strategy: BaseStrategy) -> str:
    """生成单策略的回测输入说明。"""
    lines = [
        f"策略: {strategy.name} [{'自定义' if strategy.custom else '内置'}]",
        f"描述: {strategy.description}",
        f"依赖接口: {', '.join(strategy.required_apis)}",
        "",
        "回测必填:",
        "  symbol  品种(RB/RB0) 或指定合约(RB2410)，非主连",
        "  start   开始日期 YYYYMMDD",
        "  end     结束日期 YYYYMMDD",
        "  data_mode  dominant(持仓量换月,默认) | specific(指定合约)",
        "",
        "策略参数 (--params JSON):",
    ]
    if strategy.param_schema:
        for key, spec in strategy.param_schema.items():
            default = spec.get("default", "")
            help_text = spec.get("help", "")
            typ = spec.get("type", "any")
            lines.append(f"  {key} ({typ}, 默认={default}): {help_text}")
    else:
        lines.append("  （无额外参数）")
    defaults = resolve_strategy_params(strategy)
    lines.extend([
        "",
        "推荐命令:",
        f'  python FuturesSkill.py backtest {strategy.name} \\',
        f'    --symbol RB0 --start 20230101 --end 20241231 \\',
        f"    --params '{json.dumps(defaults, ensure_ascii=False)}'",
    ])
    return "\n".join(lines)


def _build_backtest_kwargs(
    strategy_name: str,
    start: str,
    end: str,
    params_json: str | None = None,
    *,
    short: int = 5,
    long: int = 20,
    system: int = 1,
    entry: int | None = None,
    exit_window: int | None = None,
    registry: StrategyRegistry | None = None,
) -> dict[str, Any]:
    """构建回测 kwargs，合并默认参数与用户输入。"""
    start, end = validate_backtest_period(start, end)
    kwargs: dict[str, Any] = {"start_date": start, "end_date": end}

    user_params: dict[str, Any] = {}
    if params_json:
        user_params = json.loads(params_json)

    if registry:
        strategy = registry.get(strategy_name)
        kwargs.update(resolve_strategy_params(strategy, user_params or None))
    elif user_params:
        kwargs.update(user_params)
    elif strategy_name == "ma_crossover":
        kwargs.update(short=short, long=long)
    elif strategy_name == "turtle_trading":
        kwargs["system"] = system
        if entry is not None:
            kwargs["entry_window"] = entry
        if exit_window is not None:
            kwargs["exit_window"] = exit_window

    return kwargs


# ---------------------------------------------------------------------------
# futures.md 解析
# ---------------------------------------------------------------------------
class FuturesMdParser:
    """从 futures.md 解析 akshare 期货接口定义。"""

    HEADING_RE = re.compile(r"^(#{2,5})\s+(.+)$")
    INTERFACE_RE = re.compile(r"^接口:\s*(\S+)\s*$")
    URL_RE = re.compile(r"^目标地址:\s*(.+)$")
    DESC_RE = re.compile(r"^描述:\s*(.+)$")
    LIMIT_RE = re.compile(r"^限量:\s*(.+)$")
    EXAMPLE_RE = re.compile(r"ak\.(\w+)\(")

    @classmethod
    def parse(cls, md_path: Path = FUTURES_MD) -> list[ApiInfo]:
        if not md_path.exists():
            raise FileNotFoundError(f"接口文档不存在: {md_path}")

        text = md_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        apis: list[ApiInfo] = []

        section_stack: dict[int, str] = {}
        i = 0
        while i < len(lines):
            line = lines[i]

            heading = cls.HEADING_RE.match(line)
            if heading:
                level = len(heading.group(1))
                title = heading.group(2).strip()
                section_stack = {k: v for k, v in section_stack.items() if k < level}
                section_stack[level] = title
                i += 1
                continue

            iface = cls.INTERFACE_RE.match(line)
            if iface:
                api_name = iface.group(1)
                title = ""
                for lvl in sorted(section_stack.keys(), reverse=True):
                    if lvl >= 4:
                        title = section_stack[lvl]
                        break
                if not title:
                    for lvl in sorted(section_stack.keys(), reverse=True):
                        title = section_stack[lvl]
                        break

                category = section_stack.get(3, section_stack.get(2, "期货数据"))

                url, desc, limit_text = "", "", ""
                input_params: list[dict[str, str]] = []
                example = ""

                j = i + 1
                while j < len(lines):
                    nl = lines[j]
                    if cls.HEADING_RE.match(nl) or cls.INTERFACE_RE.match(nl):
                        break
                    if not url and (m := cls.URL_RE.match(nl)):
                        url = m.group(1).strip()
                    elif not desc and (m := cls.DESC_RE.match(nl)):
                        desc = m.group(1).strip()
                    elif not limit_text and (m := cls.LIMIT_RE.match(nl)):
                        limit_text = m.group(1).strip()
                    elif nl.strip() == "输入参数" and j + 2 < len(lines):
                        input_params = cls._parse_param_table(lines, j + 2)
                    elif nl.strip().startswith("```python"):
                        block, consumed = cls._read_code_block(lines, j)
                        j += consumed
                        em = cls.EXAMPLE_RE.search(block)
                        if em:
                            example = em.group(0).rstrip("(") + "(...)"
                        continue
                    j += 1

                source = cls._infer_source(url, desc, title)
                keywords = cls._build_keywords(api_name, title, category, desc)

                apis.append(
                    ApiInfo(
                        name=api_name,
                        title=title,
                        category=category,
                        url=url,
                        description=desc,
                        limit=limit_text,
                        input_params=input_params,
                        example=example or f"ak.{api_name}(...)",
                        source=source,
                        keywords=keywords,
                    )
                )
                i = j
                continue

            i += 1

        return apis

    @staticmethod
    def _read_code_block(lines: list[str], start: int) -> tuple[str, int]:
        block_lines: list[str] = []
        j = start + 1
        while j < len(lines) and not lines[j].strip().startswith("```"):
            block_lines.append(lines[j])
            j += 1
        return "\n".join(block_lines), j - start

    @staticmethod
    def _parse_param_table(lines: list[str], start: int) -> list[dict[str, str]]:
        params: list[dict[str, str]] = []
        for k in range(start, min(start + 30, len(lines))):
            row = lines[k].strip()
            if not row.startswith("|") or row.startswith("|--") or row.startswith("| ----"):
                if params:
                    break
                continue
            cells = [c.strip() for c in row.strip("|").split("|")]
            if len(cells) >= 3 and cells[0] not in ("名称", "-", ""):
                params.append({"名称": cells[0], "类型": cells[1], "描述": cells[2]})
        return params

    @staticmethod
    def _infer_source(url: str, desc: str, title: str) -> str:
        source_map = {
            "sina": "新浪财经",
            "eastmoney": "东方财富",
            "em": "东方财富",
            "shfe": "上海期货交易所",
            "dce": "大连商品交易所",
            "czce": "郑州商品交易所",
            "cffex": "中国金融期货交易所",
            "ine": "上海国际能源交易中心",
            "gfex": "广州期货交易所",
            "jin10": "金十数据",
            "9qihuo": "九期网",
            "openctp": "OpenCTP",
            "gtjaqh": "国泰君安期货",
            "99qh": "99期货网",
            "sgx": "新加坡交易所",
            "comex": "COMEX",
            "shmet": "上海金属网",
        }
        combined = f"{url} {desc} {title}".lower()
        for key, label in source_map.items():
            if key in combined:
                return label
        if url:
            return url.split("/")[2] if "://" in url else url
        return "akshare"

    @staticmethod
    def _build_keywords(name: str, title: str, category: str, desc: str) -> list[str]:
        raw = f"{name} {title} {category} {desc}".lower()
        tokens = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z_]+|\d+", raw)
        stop = {"接口", "数据", "期货", "单次", "返回", "指定", "akshare", "the", "and"}
        return list(dict.fromkeys(t for t in tokens if t not in stop and len(t) > 1))


# ---------------------------------------------------------------------------
# 接口库
# ---------------------------------------------------------------------------
class ApiRegistry:
    """akshare 期货接口库：解析、检索、扩展、调用。"""

    def __init__(self, md_path: Path = FUTURES_MD, custom_path: Path = CUSTOM_API_JSON):
        self.md_path = md_path
        self.custom_path = custom_path
        self._apis: dict[str, ApiInfo] = {}
        self.reload()

    def reload(self) -> None:
        self._apis = {a.name: a for a in FuturesMdParser.parse(self.md_path)}
        if self.custom_path.exists():
            for item in json.loads(self.custom_path.read_text(encoding="utf-8")):
                api = ApiInfo(**item, custom=True)
                self._apis[api.name] = api

    @property
    def apis(self) -> list[ApiInfo]:
        return list(self._apis.values())

    def get(self, name: str) -> ApiInfo | None:
        return self._apis.get(name)

    def list_categories(self) -> list[str]:
        return sorted({a.category for a in self._apis.values()})

    def extract_keywords(self, query: str) -> list[str]:
        q = query.lower().strip()
        tokens: list[str] = re.findall(r"[a-zA-Z_]+|\d+", q)
        # 中文：先匹配已知术语，再拆分为 2~4 字 n-gram
        cn_text = re.sub(r"[a-zA-Z0-9_\s]+", "", query)
        for term, aliases in TERM_ALIASES.items():
            if term in cn_text:
                tokens.extend(aliases)
        for n in (4, 3, 2):
            for i in range(len(cn_text) - n + 1):
                tokens.append(cn_text[i : i + n])
        stop = {"获取", "查询", "数据", "我想", "请", "帮我", "的", "和", "与", "什么", "哪些"}
        return list(dict.fromkeys(t for t in tokens if t not in stop and len(t) > 1))

    def _score(self, api: ApiInfo, keywords: list[str]) -> float:
        if not keywords:
            return 0.0
        haystack = " ".join(
            [api.name, api.title, api.category, api.description, api.source, " ".join(api.keywords)]
        ).lower()
        score = 0.0
        query_blob = " ".join(keywords).lower()
        for kw in keywords:
            kl = kw.lower()
            if kl == api.name.lower():
                score += 10
            elif kl in api.name.lower():
                score += 6
            elif kl in haystack:
                score += 2
            if kl in api.title.lower():
                score += 3
            if kl in api.category.lower():
                score += 2
        # 反向：接口标题/描述中的词出现在用户需求中
        for part in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z_]{3,}", haystack):
            if part in query_blob:
                score += 1.5
        # 内盘/外盘偏好
        if any(k in query_blob for k in ("库存", "inventory", "仓单")):
            if "inventory" in api.name or "warehouse" in api.name or "receipt" in api.name:
                score += 5
            if "comex" in api.name or ("foreign" in api.name and "inventory" not in api.name):
                if not any(k in query_blob for k in ("comex", "外盘", "国际", "comex库存")):
                    score -= 15
        return score

    def search(self, query: str, top_k: int = 5) -> SearchResult:
        keywords = self.extract_keywords(query)
        scored = [(self._score(api, keywords), api) for api in self._apis.values()]
        scored = [(s, a) for s, a in scored if s > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [a for _, a in scored[:top_k]]

        if results:
            msg = f"找到 {len(results)} 个匹配接口（关键词: {', '.join(keywords) or query}）"
        else:
            msg = f"无法获取：未在 akshare 期货接口库中找到与「{query}」匹配的数据接口，不存在相应接口。"

        return SearchResult(
            query=query,
            keywords=keywords,
            matched=bool(results),
            apis=results,
            message=msg,
        )

    def select(self, query: str, top_k: int = 1) -> SearchResult:
        """根据需求选择最佳接口，返回接口详情与数据源。"""
        result = self.search(query, top_k=top_k)
        if result.matched and result.apis:
            best = result.apis[0]
            result.message = (
                f"已选择接口: {best.name}\n"
                f"匹配关键词: {', '.join(result.keywords)}\n\n"
                f"{best.summary()}"
            )
        return result

    def list_supported(self, keyword: str | None = None) -> dict[str, Any]:
        """列出 akshare 当前支持的期货数据类型及对应接口。"""
        apis = self.apis
        if keyword:
            sr = self.search(keyword, top_k=100)
            apis = sr.apis

        by_category: dict[str, list[dict[str, str]]] = {}
        for api in apis:
            by_category.setdefault(api.category, []).append(
                {"name": api.name, "title": api.title, "description": api.description, "source": api.source}
            )

        return {
            "total": len(apis),
            "categories": list(by_category.keys()),
            "data_by_category": by_category,
        }

    def add_api(self, api: ApiInfo) -> ApiInfo:
        """向接口库添加自定义接口。"""
        api.custom = True
        self._apis[api.name] = api
        custom_list = []
        if self.custom_path.exists():
            custom_list = json.loads(self.custom_path.read_text(encoding="utf-8"))
        custom_list = [c for c in custom_list if c.get("name") != api.name]
        custom_list.append(api.to_dict())
        self.custom_path.write_text(json.dumps(custom_list, ensure_ascii=False, indent=2), encoding="utf-8")
        return api

    def call(self, name: str, params: dict[str, Any] | None = None) -> Any:
        """动态调用 akshare 接口。"""
        if name not in self._apis:
            raise ValueError(f"接口 {name} 不在接口库中，请先 search/list 确认或使用 add-api 添加。")

        try:
            import akshare as ak
        except ImportError as e:
            raise ImportError("请先安装 akshare: pip install akshare") from e

        func = getattr(ak, name, None)
        if func is None:
            raise AttributeError(f"akshare 中不存在函数: {name}")

        params = params or {}
        return func(**params)


def fetch_futures_ohlcv(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """获取期货日线（vnpy 引擎：分合约/主力换月，非主连）。"""
    from vnpy_engine import load_futures_bars

    _, roll_df, _ = load_futures_bars(symbol, start_date, end_date)
    df = roll_df.copy()
    rename = {
        "date": "date",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
        "open_interest": "hold",
        "symbol": "contract",
    }
    for src, dst in rename.items():
        if src in df.columns and dst not in df.columns:
            df.rename(columns={src: dst}, inplace=True)
    if "date" in df.columns:
        df["date"] = df["date"].astype(str).str[:10]
    df.sort_values("date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# ---------------------------------------------------------------------------
# 策略基类与注册（vnpy CTA 回测）
# ---------------------------------------------------------------------------
class BaseStrategy(ABC):
    """策略元信息包装，回测由 vnpy BacktestingEngine 执行。"""

    name: str = "base"
    description: str = ""
    required_apis: list[str] = ["get_futures_daily"]
    custom: bool = False
    param_schema: dict[str, dict[str, Any]] = {}
    api_registry: ApiRegistry | None = None
    vnpy_strategy_class: type | None = None
    vnpy_strategy_key: str = ""

    def get_vnpy_class(self) -> type:
        if self.vnpy_strategy_class is not None:
            return self.vnpy_strategy_class
        if self.vnpy_strategy_key:
            from strategies.vnpy_cta import load_vnpy_strategy
            return load_vnpy_strategy(self.vnpy_strategy_key)
        raise NotImplementedError(f"策略 {self.name} 未绑定 vnpy CtaTemplate")

    def get_setting(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return resolve_strategy_params(self, kwargs)

    def call_api(self, api_name: str, params: dict[str, Any] | None = None) -> Any:
        if self.api_registry is None:
            raise RuntimeError(f"策略 {self.name} 未绑定 ApiRegistry，无法调用接口")
        return self.api_registry.call(api_name, params)

    def info_dict(self) -> dict[str, Any]:
        apis_ok: list[str] = []
        apis_missing: list[str] = []
        if self.api_registry:
            for api in self.required_apis:
                if self.api_registry.get(api):
                    apis_ok.append(api)
                else:
                    apis_missing.append(api)
        return {
            "name": self.name,
            "description": self.description,
            "required_apis": self.required_apis,
            "custom": self.custom,
            "param_schema": self.param_schema,
            "apis_available": apis_ok,
            "apis_missing": apis_missing,
            "backtest_engine": "vnpy",
        }
    # 回测调用入口以及参数设置，数据模式，资金，滑点等
    def backtest(self, symbol: str, **kwargs) -> BacktestResult:
        from vnpy_engine import run_vnpy_backtest

        setting = self.get_setting(kwargs)
        data_mode = kwargs.get("data_mode")
        capital = int(kwargs.get("capital", 1_000_000))
        slippage = float(kwargs.get("slippage", 0))
        return run_vnpy_backtest(
            self.get_vnpy_class(),
            self.name,
            symbol,
            setting,
            start_date=kwargs["start_date"],
            end_date=kwargs.get("end_date", datetime.now().strftime("%Y%m%d")),
            capital=capital,
            slippage=slippage,
            data_mode=data_mode,
            on_progress=kwargs.get("on_progress"),
            verbose=kwargs.get("verbose", True),
        )


class MovingAverageCrossoverStrategy(BaseStrategy):
    name = "ma_crossover"
    description = "双均线交叉（多空双向）：金叉开多/平空，死叉开空/平多。"
    required_apis = ["get_futures_daily"]
    vnpy_strategy_key = "ma_crossover"
    param_schema = {
        "short_window": {"type": "int", "default": 5, "help": "短期均线周期"},
        "long_window": {"type": "int", "default": 20, "help": "长期均线周期"},
    }

    def get_setting(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        s = resolve_strategy_params(self, kwargs)
        return {
            "short_window": int(s.get("short_window", s.get("short", 5))),
            "long_window": int(s.get("long_window", s.get("long", 20))),
        }


class TurtleTradingStrategy(BaseStrategy):
    name = "turtle_trading"
    description = (
        "海龟交易法（多空双向）：突破 N 日高点开多/跌破 N 日低点开空，"
        "反向突破 M 日通道平仓。系统1 默认 20/10，系统2 为 55/20。"
    )
    required_apis = ["get_futures_daily"]
    vnpy_strategy_key = "turtle_trading"
    param_schema = {
        "system": {"type": "int", "default": 1, "help": "海龟系统：1=20/10, 2=55/20"},
        "entry_window": {"type": "int", "default": 20, "help": "入场唐奇安通道周期"},
        "exit_window": {"type": "int", "default": 10, "help": "出场唐奇安通道周期"},
    }
    SYSTEM_PRESETS = {1: (20, 10), 2: (55, 20)}

    def get_setting(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        system = int(kwargs.get("system", 1))
        preset = self.SYSTEM_PRESETS.get(system, (20, 10))
        return {
            "entry_window": int(kwargs.get("entry_window", preset[0])),
            "exit_window": int(kwargs.get("exit_window", preset[1])),
        }


class RsiDemoStrategyWrapper(BaseStrategy):
    name = "rsi_demo"
    description = "RSI 超买超卖（多空双向）：超卖开多/超买平空，超买开空/超卖平多"
    required_apis = ["get_futures_daily"]
    vnpy_strategy_key = "rsi_demo"
    param_schema = {
        "period": {"type": "int", "default": 14, "help": "RSI周期"},
        "oversold": {"type": "int", "default": 30, "help": "超卖阈值"},
        "overbought": {"type": "int", "default": 70, "help": "超买阈值"},
    }


def _wrap_vnpy_cta_class(cta_cls: type) -> BaseStrategy:
    """将 vnpy CtaTemplate 子类包装为 BaseStrategy 注册项。"""

    class _VnpyWrapper(BaseStrategy):
        pass

    _VnpyWrapper.name = getattr(cta_cls, "strategy_name", cta_cls.__name__.replace("Strategy", "").lower())
    _VnpyWrapper.description = (cta_cls.__doc__ or "自定义 vnpy CTA 策略").strip().split("\n")[0]
    _VnpyWrapper.custom = True
    _VnpyWrapper.required_apis = ["get_futures_daily"]
    _VnpyWrapper.param_schema = {
        p: {"type": "any", "default": getattr(cta_cls, p, None), "help": p}
        for p in getattr(cta_cls, "parameters", [])
    }
    _VnpyWrapper.vnpy_strategy_class = cta_cls

    def _init(self) -> None:
        self.vnpy_strategy_class = cta_cls

    _VnpyWrapper.__init__ = _init  # type: ignore[method-assign]
    return _VnpyWrapper()


def strategy_name_to_class(name: str) -> str:
    parts = re.split(r"[_\-\s]+", name.strip())
    return "".join(p.capitalize() for p in parts if p) + "Strategy"


class StrategyLoader:
    """从 strategies/custom/ 目录动态加载自定义策略插件。"""

    @staticmethod
    def ensure_custom_dir() -> Path:
        CUSTOM_STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
        init_file = CUSTOM_STRATEGIES_DIR / "__init__.py"
        if not init_file.exists():
            init_file.write_text('"""自定义策略插件目录"""\n', encoding="utf-8")
        return CUSTOM_STRATEGIES_DIR

    @classmethod
    def load_from_directory(cls, registry: "StrategyRegistry", directory: Path | None = None) -> list[str]:
        directory = directory or cls.ensure_custom_dir()
        loaded: list[str] = []
        disabled = cls._load_disabled_names()

        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))

        for py_file in sorted(directory.glob("*.py")):
            if py_file.name.startswith("_") or py_file.name == "__init__.py":
                continue
            module_key = f"_futures_strategy_{py_file.stem}"
            try:
                if module_key in sys.modules:
                    del sys.modules[module_key]
                spec = importlib.util.spec_from_file_location(module_key, py_file)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for attr_name in dir(module):
                    obj = getattr(module, attr_name)
                    if not isinstance(obj, type):
                        continue
                    # vnpy CtaTemplate 自定义策略
                    if any(getattr(b, "__name__", "") == "CtaTemplate" for b in getattr(obj, "__mro__", [])):
                        wrapper = _wrap_vnpy_cta_class(obj)
                        wrapper.name = py_file.stem
                        if wrapper.name in disabled:
                            continue
                        registry.register(wrapper, overwrite=True)
                        loaded.append(wrapper.name)
                        continue
                    # 旧版 BaseStrategy 包装（含 vnpy_strategy_class）
                    if not (hasattr(obj, "get_vnpy_class") or hasattr(obj, "vnpy_strategy_class")):
                        if not (hasattr(obj, "backtest") and getattr(obj, "name", "base") != "base"):
                            continue
                    if obj is BaseStrategy:
                        continue
                    strategy_name = getattr(obj, "name", "base")
                    if strategy_name in ("", "base"):
                        continue
                    if not any(getattr(b, "__name__", "") == "BaseStrategy" for b in obj.__mro__):
                        continue
                    instance = obj()
                    if instance.name in disabled:
                        continue
                    instance.custom = True
                    registry.register(instance, overwrite=True)
                    loaded.append(instance.name)
            except Exception as e:
                print(f"警告: 加载策略文件 {py_file.name} 失败: {e}", file=sys.stderr)
        return loaded

    @staticmethod
    def _load_disabled_names() -> set[str]:
        if not STRATEGY_MANIFEST.exists():
            return set()
        try:
            data = json.loads(STRATEGY_MANIFEST.read_text(encoding="utf-8"))
            return {s["name"] for s in data.get("disabled", []) if "name" in s}
        except (json.JSONDecodeError, OSError):
            return set()

    @staticmethod
    def save_manifest(custom_names: list[str]) -> None:
        """更新策略清单（记录已注册的自定义策略）。"""
        manifest = {"custom_strategies": custom_names, "disabled": []}
        if STRATEGY_MANIFEST.exists():
            try:
                existing = json.loads(STRATEGY_MANIFEST.read_text(encoding="utf-8"))
                manifest["disabled"] = existing.get("disabled", [])
            except (json.JSONDecodeError, OSError):
                pass
        STRATEGY_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def create_template(
        name: str,
        description: str = "自定义期货策略",
        required_apis: list[str] | None = None,
        param_schema: dict[str, dict[str, Any]] | None = None,
    ) -> Path:
        StrategyLoader.ensure_custom_dir()
        file_path = CUSTOM_STRATEGIES_DIR / f"{name}.py"
        if file_path.exists():
            raise FileExistsError(f"策略文件已存在: {file_path}")

        apis_json = json.dumps(required_apis or ["get_futures_daily"], ensure_ascii=False)
        params_list = list((param_schema or {"example_period": {"default": 14}}).keys())
        params_defaults = {k: v.get("default", 0) for k, v in (param_schema or {"example_period": {"default": 14}}).items()}

        content = f'''\
"""
自定义 vnpy CTA 策略: {name}
继承 CtaTemplate，在 on_bar 中实现交易逻辑。回测由 VeighNa BacktestingEngine 执行。
"""

from vnpy_ctastrategy import CtaTemplate
from vnpy.trader.utility import ArrayManager


class {strategy_name_to_class(name)}(CtaTemplate):
    """{description}"""

    author = "FuturesSkill"
    {chr(10).join(f"    {p} = {params_defaults[p]}" for p in params_defaults)}

    parameters = {json.dumps(params_list)}
    variables = []

    def on_init(self) -> None:
        self.am = ArrayManager(100)

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass

    def on_bar(self, bar) -> None:
        self.am.update_bar(bar)
        if not self.am.inited:
            return
        # TODO: 在此实现策略逻辑
        self.put_event()
'''
        file_path.write_text(content, encoding="utf-8")
        return file_path


class StrategyRegistry:
    """期货策略库：内置策略 + 动态加载自定义插件。"""

    def __init__(self, api_registry: ApiRegistry):
        self.api_registry = api_registry
        self._strategies: dict[str, BaseStrategy] = {}
        self._load_builtin()
        self.reload_custom()

    def _load_builtin(self) -> None:
        for cls in (MovingAverageCrossoverStrategy, TurtleTradingStrategy, RsiDemoStrategyWrapper):
            self.register(cls())

    def reload_custom(self) -> list[str]:
        """重新扫描 strategies/custom/ 并加载自定义策略。"""
        self._strategies = {
            k: v for k, v in self._strategies.items() if not getattr(v, "custom", False)
        }
        loaded = StrategyLoader.load_from_directory(self)
        StrategyLoader.save_manifest(loaded)
        return loaded

    def register(self, strategy: BaseStrategy, *, overwrite: bool = False) -> None:
        if strategy.name in self._strategies and not overwrite:
            existing = self._strategies[strategy.name]
            if not getattr(existing, "custom", False):
                raise ValueError(f"内置策略 {strategy.name} 不可覆盖，请使用其他名称")
        strategy.api_registry = self.api_registry
        self._strategies[strategy.name] = strategy

    def register_from_file(self, file_path: Path, *, reload: bool = True) -> Path:
        """将外部策略文件复制到 custom 目录并加载。"""
        StrategyLoader.ensure_custom_dir()
        src = Path(file_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"策略文件不存在: {src}")
        dest = CUSTOM_STRATEGIES_DIR / src.name
        shutil.copy2(src, dest)
        if reload:
            self.reload_custom()
        return dest

    def list_strategies(self) -> list[dict[str, Any]]:
        return [s.info_dict() for s in self._strategies.values()]

    def get(self, name: str) -> BaseStrategy:
        if name not in self._strategies:
            raise KeyError(f"策略 {name} 不存在，可用: {list(self._strategies.keys())}")
        return self._strategies[name]

    def show(self, name: str) -> dict[str, Any]:
        strategy = self.get(name)
        info = strategy.info_dict()
        info["source"] = "custom" if strategy.custom else "builtin"
        return info

    def select(self, query: str) -> StrategySelectResult:
        """确认并返回策略库中的特定策略及其输入规范。"""
        query = query.strip()
        available = list(self._strategies.keys())

        # 精确匹配
        if query in self._strategies:
            strategy = self._strategies[query]
            defaults = resolve_strategy_params(strategy)
            spec = build_strategy_input_spec(strategy)
            return StrategySelectResult(
                query=query,
                matched=True,
                strategy_name=strategy.name,
                info=strategy.info_dict(),
                default_params=defaults,
                input_spec=spec,
                message=f"已确认选择策略: {strategy.name}\n\n{spec}",
            )

        # 模糊匹配
        query_lower = query.lower()
        candidates = [
            s for s in self._strategies.values()
            if query_lower in s.name.lower()
            or query_lower in s.description.lower()
            or any(query_lower in k.lower() for k in (s.name, s.description))
        ]

        if len(candidates) == 1:
            s = candidates[0]
            defaults = resolve_strategy_params(s)
            spec = build_strategy_input_spec(s)
            return StrategySelectResult(
                query=query,
                matched=True,
                strategy_name=s.name,
                info=s.info_dict(),
                default_params=defaults,
                input_spec=spec,
                message=f"已匹配策略: {s.name}\n\n{spec}",
            )

        if len(candidates) > 1:
            names = [c.name for c in candidates]
            return StrategySelectResult(
                query=query,
                matched=False,
                strategy_name="",
                info={},
                default_params={},
                input_spec="",
                message=f"匹配到多个策略 {names}，请指定精确策略名。可用: {available}",
            )

        return StrategySelectResult(
            query=query,
            matched=False,
            strategy_name="",
            info={},
            default_params={},
            input_spec=BACKTEST_INPUT_GUIDE,
            message=f"策略「{query}」不存在。可用策略: {available}\n\n{BACKTEST_INPUT_GUIDE}",
        )

    def backtest(self, name: str, symbol: str, **kwargs) -> BacktestResult:
        strategy = self.get(name)
        missing = [a for a in strategy.required_apis if not self.api_registry.get(a)]
        if missing:
            raise ValueError(
                f"策略 {name} 所需接口 {missing} 不在接口库中，"
                f"请先用 add-api 添加或使用 list-apis 确认可用接口"
            )
        if "start_date" in kwargs:
            kwargs["start_date"], kwargs["end_date"] = validate_backtest_period(
                kwargs["start_date"], kwargs.get("end_date", datetime.now().strftime("%Y%m%d"))
            )
        return strategy.backtest(symbol, **kwargs)

    def backtest_compare(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        strategy_names: list[str] | None = None,
        params_map: dict[str, dict[str, Any]] | None = None,
    ) -> BacktestComparison:
        """对多个策略在同一合约、同一时间段内回测并对比。"""
        start_date, end_date = validate_backtest_period(start_date, end_date)
        names = strategy_names or list(self._strategies.keys())
        results: list[BacktestResult] = []
        errors: list[dict[str, str]] = []

        for name in names:
            try:
                strategy = self.get(name)
                params = resolve_strategy_params(strategy)
                if params_map and name in params_map:
                    params.update(params_map[name])
                result = self.backtest(
                    name, symbol,
                    start_date=start_date, end_date=end_date, **params,
                )
                results.append(result)
            except Exception as e:
                errors.append({"strategy": name, "error": str(e)})

        return BacktestComparison(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            results=results,
            errors=errors,
        )

    def backtest_compare_with_chart(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        strategy_names: list[str] | None = None,
        params_map: dict[str, dict[str, Any]] | None = None,
        *,
        chart: bool = True,
        output_path: Path | str | None = None,
        show: bool = True,
    ) -> BacktestComparison:
        comparison = self.backtest_compare(symbol, start_date, end_date, strategy_names, params_map)
        if chart and comparison.results:
            path = plot_compare_curves(comparison, output_path, show=show)
            comparison.chart_path = str(path)
        return comparison

    def monitor(
        self,
        name: str,
        symbol: str,
        *,
        interval: str = "daily",
        pos: int = 0,
        **kwargs,
    ):
        from strategy_monitor import run_strategy_monitor

        strategy = self.get(name)
        setting = strategy.get_setting(resolve_strategy_params(strategy, kwargs or None))
        return run_strategy_monitor(
            name, symbol, setting, interval=interval, pos=int(pos),
        )

    def monitor_batch(
        self,
        symbol: str,
        strategy_names: list[str] | None = None,
        params_map: dict[str, dict[str, Any]] | None = None,
        *,
        interval: str = "daily",
        pos_map: dict[str, int] | None = None,
        default_pos: int = 0,
    ):
        from strategy_monitor import run_monitor_batch

        names = strategy_names or list(self._strategies.keys())
        resolved_map: dict[str, dict[str, Any]] = {}
        for n in names:
            try:
                resolved_map[n] = resolve_strategy_params(
                    self.get(n), (params_map or {}).get(n),
                )
            except KeyError:
                continue
        return run_monitor_batch(
            symbol, names, resolved_map,
            interval=interval, pos_map=pos_map, default_pos=default_pos,
        )


# ---------------------------------------------------------------------------
# Skill 主入口
# ---------------------------------------------------------------------------
class FuturesSkill:
    """AKShare 期货分析 Skill 统一入口。"""

    def __init__(self):
        self.api_registry = ApiRegistry()
        self.strategy_registry = StrategyRegistry(self.api_registry)

    def search_apis(self, query: str, top_k: int = 5) -> SearchResult:
        return self.api_registry.search(query, top_k)

    def select_api(self, query: str) -> SearchResult:
        return self.api_registry.select(query)

    def list_apis(self, keyword: str | None = None) -> dict[str, Any]:
        return self.api_registry.list_supported(keyword)

    def call_api(self, name: str, params: dict[str, Any] | None = None) -> Any:
        return self.api_registry.call(name, params)

    def add_api(
        self,
        name: str,
        title: str,
        url: str,
        description: str,
        category: str = "自定义",
        limit: str = "",
    ) -> ApiInfo:
        api = ApiInfo(
            name=name,
            title=title,
            category=category,
            url=url,
            description=description,
            limit=limit,
            source=FuturesMdParser._infer_source(url, description, title),
            keywords=FuturesMdParser._build_keywords(name, title, category, description),
        )
        return self.api_registry.add_api(api)

    def list_strategies(self) -> list[dict[str, Any]]:
        return self.strategy_registry.list_strategies()

    def show_strategy(self, name: str) -> dict[str, Any]:
        return self.strategy_registry.show(name)

    def select_strategy(self, query: str) -> StrategySelectResult:
        """确认策略库中的特定策略，返回参数说明与输入规范。"""
        return self.strategy_registry.select(query)

    def backtest_guide(self) -> str:
        return BACKTEST_INPUT_GUIDE

    def reload_strategies(self) -> list[str]:
        return self.strategy_registry.reload_custom()

    def init_strategy(
        self,
        name: str,
        description: str = "自定义期货策略",
        required_apis: list[str] | None = None,
        param_schema: dict[str, dict[str, Any]] | None = None,
        *,
        reload: bool = True,
    ) -> Path:
        """创建自定义策略模板文件并加载到策略库。"""
        path = StrategyLoader.create_template(name, description, required_apis, param_schema)
        if reload:
            self.strategy_registry.reload_custom()
        return path

    def register_strategy(self, file_path: str | Path) -> Path:
        """注册外部策略文件（复制到 strategies/custom/ 并加载）。"""
        return self.strategy_registry.register_from_file(Path(file_path))

    def backtest_strategy(
        self,
        strategy_name: str,
        symbol: str,
        *,
        chart: bool = True,
        chart_output: Path | str | None = None,
        show_chart: bool = True,
        **kwargs,
    ) -> BacktestResult:
        result = self.strategy_registry.backtest(strategy_name, symbol, **kwargs)
        if chart and result.curve_dates:
            path = plot_backtest_curve(result, chart_output, show=show_chart)
            result.chart_path = str(path)
        return result

    def backtest_compare(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        strategy_names: list[str] | None = None,
        params_map: dict[str, dict[str, Any]] | None = None,
        *,
        chart: bool = True,
        chart_output: Path | str | None = None,
        show_chart: bool = True,
    ) -> BacktestComparison:
        """全策略（或指定策略）对比回测，可选生成对比曲线。"""
        return self.strategy_registry.backtest_compare_with_chart(
            symbol, start_date, end_date, strategy_names, params_map,
            chart=chart, output_path=chart_output, show=show_chart,
        )

    def monitor_guide(self) -> str:
        return MONITOR_INPUT_GUIDE

    def monitor_strategy(
        self,
        strategy_name: str,
        symbol: str,
        *,
        interval: str = "daily",
        pos: int = 0,
        **kwargs,
    ):
        """单策略盘中检测；信号触发时 MonitorResult.triggered=True。"""
        return self.strategy_registry.monitor(
            strategy_name, symbol, interval=interval, pos=pos, **kwargs,
        )

    def monitor_strategies(
        self,
        symbol: str,
        strategy_names: list[str] | None = None,
        params_map: dict[str, dict[str, Any]] | None = None,
        *,
        interval: str = "daily",
        pos_map: dict[str, int] | None = None,
        default_pos: int = 0,
    ):
        """多策略盘中扫描，返回 alerts 列表供 OpenClaw 推送。"""
        return self.strategy_registry.monitor_batch(
            symbol, strategy_names, params_map,
            interval=interval, pos_map=pos_map, default_pos=default_pos,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_json(obj: Any) -> None:
    if hasattr(obj, "to_dict"):
        print(json.dumps(obj.to_dict(), ensure_ascii=False, indent=2))
    elif isinstance(obj, dict):
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AKShare 期货数据 Skill")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="根据关键词检索接口")
    p_search.add_argument("query", help="需求描述或关键词")
    p_search.add_argument("--top", type=int, default=5)

    p_select = sub.add_parser("select", help="选择最佳匹配接口并返回详情")
    p_select.add_argument("query", help="需求描述")

    p_list = sub.add_parser("list-apis", help="列出支持的期货数据接口")
    p_list.add_argument("--keyword", "-k", default=None)

    p_call = sub.add_parser("call", help="调用指定 akshare 接口")
    p_call.add_argument("name", help="接口名称")
    p_call.add_argument("--params", "-p", default="{}", help="JSON 参数字典")

    p_add = sub.add_parser("add-api", help="添加自定义接口到接口库")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--url", required=True)
    p_add.add_argument("--description", required=True)
    p_add.add_argument("--category", default="自定义")
    p_add.add_argument("--limit", default="")

    sub.add_parser("list-strategies", help="列出策略库中的策略")

    sub.add_parser("backtest-guide", help="显示策略回测用户输入规范")

    p_sel_st = sub.add_parser("select-strategy", help="确认选择的策略并显示回测输入规范")
    p_sel_st.add_argument("name", help="策略名称或关键词")

    p_show = sub.add_parser("show-strategy", help="查看策略详情（参数、依赖接口等）")
    p_show.add_argument("name", help="策略名称")

    p_init = sub.add_parser("init-strategy", help="创建自定义策略模板文件")
    p_init.add_argument("--name", "-n", required=True, help="策略名称（英文/下划线）")
    p_init.add_argument("--description", "-d", default="自定义期货策略")
    p_init.add_argument("--apis", default='["futures_main_sina","futures_zh_daily_sina"]', help="所需接口 JSON 列表")
    p_init.add_argument("--params", default="{}", help="参数 schema JSON，如 {\"period\":{\"type\":\"int\",\"default\":14}}")

    p_reg = sub.add_parser("register-strategy", help="注册外部策略 .py 文件到策略库")
    p_reg.add_argument("file", help="策略 Python 文件路径")

    sub.add_parser("reload-strategies", help="重新扫描并加载 strategies/custom/ 下的策略")

    p_bt = sub.add_parser("backtest", help="运行策略回测")
    p_bt.add_argument("strategy", help="策略名称，如 ma_crossover")
    p_bt.add_argument("--symbol", "-s", default="RB0", help="合约代码，如 RB0")
    p_bt.add_argument("--params", "-p", default=None, help='JSON 策略参数字典，如 \'{"short":5,"long":20}\'')
    p_bt.add_argument("--short", type=int, default=5, help="短期均线周期（ma_crossover，无 --params 时生效）")
    p_bt.add_argument("--long", type=int, default=20, help="长期均线周期（ma_crossover，无 --params 时生效）")
    p_bt.add_argument("--entry", type=int, default=None, help="海龟入场通道周期（turtle_trading）")
    p_bt.add_argument("--exit", type=int, default=None, help="海龟出场通道周期（turtle_trading）")
    p_bt.add_argument("--system", type=int, choices=[1, 2], default=1, help="海龟系统：1=20/10, 2=55/20")
    p_bt.add_argument("--start", required=True, help="回测开始日期 YYYYMMDD")
    p_bt.add_argument("--end", default=None, help="回测结束日期 YYYYMMDD，默认当天")
    p_bt.add_argument("--no-chart", action="store_true", help="不生成回测曲线图")
    p_bt.add_argument("-o", "--output", default=None, help="曲线图保存路径")
    p_bt.add_argument("--no-show", action="store_true", help="保存曲线但不弹出窗口")

    p_cmp = sub.add_parser("backtest-compare", help="全策略/多策略对比回测")
    p_cmp.add_argument("--symbol", "-s", default="RB0", help="合约代码")
    p_cmp.add_argument("--start", required=True, help="回测开始日期 YYYYMMDD")
    p_cmp.add_argument("--end", default=None, help="回测结束日期 YYYYMMDD，默认当天")
    p_cmp.add_argument("--strategies", default=None, help='策略名 JSON 列表，默认全部，如 \'["ma_crossover","turtle_trading"]\'')
    p_cmp.add_argument("--params-map", default=None, help='各策略参数 JSON，如 \'{"ma_crossover":{"short":10}}\'')
    p_cmp.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    p_cmp.add_argument("--no-chart", action="store_true", help="不生成对比曲线图")
    p_cmp.add_argument("-o", "--output", default=None, help="曲线图保存路径")
    p_cmp.add_argument("--no-show", action="store_true", help="保存曲线但不弹出窗口")

    sub.add_parser("monitor-guide", help="显示策略盘中检测输入规范")

    p_mon = sub.add_parser("monitor-strategy", help="单策略盘中检测（信号触发告警）")
    p_mon.add_argument("strategy", help="策略名称")
    p_mon.add_argument("--symbol", "-s", default="RB0", help="合约代码，如 RB0")
    p_mon.add_argument("--interval", "-i", default="daily",
                       help="K线周期: daily | 1 | 5 | 15 | 30 | 60")
    p_mon.add_argument("--pos", type=int, default=0, help="假定持仓：0空仓，正数持多，负数持空")
    p_mon.add_argument("--params", "-p", default=None, help="策略参数 JSON")
    p_mon.add_argument("--json", action="store_true", help="JSON 输出")
    p_mon.add_argument("--alert-only", action="store_true", help="仅在有信号时输出")
    p_mon.add_argument("--exit-on-alert", action="store_true",
                       help="有信号时退出码 2（OpenClaw 分支用）")

    p_mons = sub.add_parser("monitor-strategies", help="多策略盘中扫描")
    p_mons.add_argument("--symbol", "-s", default="RB0", help="合约代码")
    p_mons.add_argument("--interval", "-i", default="daily",
                        help="K线周期: daily | 1 | 5 | 15 | 30 | 60")
    p_mons.add_argument("--strategies", default=None,
                        help='策略 JSON 列表，默认全部')
    p_mons.add_argument("--params-map", default=None, help="各策略参数 JSON")
    p_mons.add_argument("--pos-map", default=None, help='各策略假定持仓 JSON，如 {"ma_crossover":1}')
    p_mons.add_argument("--default-pos", type=int, default=0, help="未指定 pos-map 时的默认持仓")
    p_mons.add_argument("--json", action="store_true", help="JSON 输出")
    p_mons.add_argument("--alert-only", action="store_true", help="仅输出触发的策略")
    p_mons.add_argument("--exit-on-alert", action="store_true", help="有信号时退出码 2")

    p_parse = sub.add_parser("parse-md", help="重新解析 futures.md 并统计接口数量")
    p_parse.set_defaults(command="parse-md")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    skill = FuturesSkill()

    try:
        if args.command == "search":
            result = skill.search_apis(args.query, top_k=args.top)
            if result.matched:
                print(result.message)
                for api in result.apis:
                    print(f"\n--- {api.name} ---")
                    print(api.summary())
            else:
                print(result.message)
            return 0 if result.matched else 1

        if args.command == "select":
            result = skill.select_api(args.query)
            print(result.message)
            return 0 if result.matched else 1

        if args.command == "list-apis":
            data = skill.list_apis(args.keyword)
            print(f"共 {data['total']} 个接口，分类: {', '.join(data['categories'])}")
            for cat, items in data["data_by_category"].items():
                print(f"\n## {cat}")
                for item in items:
                    print(f"  - {item['name']}: {item['title']} [{item['source']}]")
            return 0

        if args.command == "call":
            params = json.loads(args.params)
            data = skill.call_api(args.name, params)
            if isinstance(data, pd.DataFrame):
                print(data.to_string(max_rows=20))
            else:
                print(data)
            return 0

        if args.command == "add-api":
            api = skill.add_api(
                name=args.name,
                title=args.title,
                url=args.url,
                description=args.description,
                category=args.category,
                limit=args.limit,
            )
            print(f"已添加接口: {api.name}")
            print(api.summary())
            return 0

        if args.command == "list-strategies":
            strategies = skill.list_strategies()
            print(f"策略库共 {len(strategies)} 个策略:\n")
            for s in strategies:
                src = "自定义" if s.get("custom") else "内置"
                print(f"- [{src}] {s['name']}: {s['description']}")
                print(f"  依赖接口: {', '.join(s['required_apis'])}")
                if s.get("param_schema"):
                    params = ", ".join(f"{k}={v.get('default')}" for k, v in s["param_schema"].items())
                    print(f"  参数: {params}")
            print(f"\n提示: 使用 select-strategy <名称> 确认策略 | backtest-guide 查看输入规范")
            return 0

        if args.command == "backtest-guide":
            print(skill.backtest_guide())
            return 0

        if args.command == "select-strategy":
            result = skill.select_strategy(args.name)
            print(result.message)
            return 0 if result.matched else 1

        if args.command == "show-strategy":
            info = skill.show_strategy(args.name)
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0

        if args.command == "init-strategy":
            path = skill.init_strategy(
                name=args.name,
                description=args.description,
                required_apis=json.loads(args.apis),
                param_schema=json.loads(args.params) or None,
            )
            print(f"已创建策略模板: {path}")
            print("请编辑 fetch_data / generate_signals 后执行: python FuturesSkill.py reload-strategies")
            return 0

        if args.command == "register-strategy":
            path = skill.register_strategy(args.file)
            print(f"已注册策略文件: {path}")
            return 0

        if args.command == "reload-strategies":
            loaded = skill.reload_strategies()
            print(f"已加载 {len(loaded)} 个自定义策略: {', '.join(loaded) or '无'}")
            return 0

        if args.command == "backtest":
            end = args.end or datetime.now().strftime("%Y%m%d")
            # 先确认策略存在
            sel = skill.select_strategy(args.strategy)
            if not sel.matched:
                print(sel.message)
                return 1
            print(f"▶ 已确认策略: {sel.strategy_name}")
            bt_kwargs = _build_backtest_kwargs(
                sel.strategy_name, args.start, end,
                params_json=args.params,
                short=args.short, long=args.long,
                system=args.system, entry=args.entry, exit_window=args.exit,
                registry=skill.strategy_registry,
            )
            result = skill.backtest_strategy(
                sel.strategy_name, args.symbol,
                chart=not args.no_chart,
                chart_output=args.output,
                show_chart=not args.no_show,
                **bt_kwargs,
            )
            print(result.summary())
            return 0

        if args.command == "backtest-compare":
            end = args.end or datetime.now().strftime("%Y%m%d")
            names = json.loads(args.strategies) if args.strategies else None
            params_map = json.loads(args.params_map) if args.params_map else None
            comparison = skill.backtest_compare(
                args.symbol, args.start, end, names, params_map,
                chart=not args.no_chart,
                chart_output=args.output,
                show_chart=not args.no_show,
            )
            if args.json:
                print(json.dumps(comparison.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(comparison.summary_table())
            return 0 if comparison.results else 1

        if args.command == "monitor-guide":
            print(skill.monitor_guide())
            return 0

        if args.command == "monitor-strategy":
            params = json.loads(args.params) if args.params else {}
            result = skill.monitor_strategy(
                args.strategy, args.symbol,
                interval=args.interval, pos=args.pos, **params,
            )
            if args.alert_only and not result.triggered:
                return 0
            if args.json:
                _print_json(result)
            else:
                print(result.summary())
            if args.exit_on_alert and result.triggered:
                return 2
            return 0

        if args.command == "monitor-strategies":
            names = json.loads(args.strategies) if args.strategies else None
            params_map = json.loads(args.params_map) if args.params_map else None
            pos_map = json.loads(args.pos_map) if args.pos_map else None
            batch = skill.monitor_strategies(
                args.symbol, names, params_map,
                interval=args.interval, pos_map=pos_map, default_pos=args.default_pos,
            )
            if args.alert_only:
                if args.json:
                    alerts = [r.to_dict() for r in batch.alerts]
                    print(json.dumps(
                        {"symbol": batch.symbol, "interval": batch.interval,
                         "checked_at": batch.checked_at, "alerts": alerts,
                         "alert_count": len(alerts), "errors": batch.errors},
                        ensure_ascii=False, indent=2,
                    ))
                elif batch.alerts:
                    for r in batch.alerts:
                        print(r.alert_message)
                        print()
                else:
                    print(f"无新信号（{batch.symbol} · {batch.interval}）")
            elif args.json:
                _print_json(batch)
            else:
                print(batch.summary())
            if args.exit_on_alert and batch.alerts:
                return 2
            return 0

        if args.command == "parse-md":
            apis = FuturesMdParser.parse()
            print(f"从 futures.md 解析到 {len(apis)} 个接口")
            for a in apis[:5]:
                print(f"  {a.name}: {a.title}")
            return 0

    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
