#!/usr/bin/env python3
"""
FuturesSkill 交互式功能展示

通过菜单驱动方式调用 FuturesSkill 的全部核心能力，适合本地演示与功能验证。

运行:
  python FuturesSkillDemo.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any

import pandas as pd

from FuturesSkill import (
    BACKTEST_INPUT_GUIDE,
    FuturesSkill,
    resolve_strategy_params,
    validate_backtest_period,
    validate_date,
)

# ---------------------------------------------------------------------------
# 输入规范说明
# ---------------------------------------------------------------------------
API_INPUT_GUIDE = """
══════════════════════════════════════════════════════════════
              期货数据接口 — 用户输入规范
══════════════════════════════════════════════════════════════

【接口检索 / 选择】
  query   需求描述或关键词（中文/英文均可）
          示例: "螺纹钢历史行情" | "库存数据" | "持仓排名"

【列出可用数据】
  keyword 可选，按关键词过滤分类
          示例: "行情" | "仓单" | 留空显示全部

【查看接口字段】
  api_name  接口精确名称（从检索结果中获取）
            示例: futures_main_sina | futures_inventory_em

【调用接口】
  api_name  接口名称
  params    JSON 参数字典
            示例: {"symbol": "RB0", "start_date": "20240101", "end_date": "20241231"}

【常见合约代码】RB0 螺纹钢 | IF0 沪深300 | AU0 黄金 | M0 豆粕
══════════════════════════════════════════════════════════════
""".strip()

STRATEGY_INPUT_GUIDE = """
══════════════════════════════════════════════════════════════
              策略检索与回测 — 用户输入规范
══════════════════════════════════════════════════════════════

【策略检索 / 确认】
  name    策略名称或关键词
          示例: ma_crossover | turtle_trading | 均线 | 海龟

【单策略回测 — 必填】
  strategy  策略库中的精确名称
  symbol    品种/合约（非主连！见下）
  start     开始日期 YYYYMMDD
  end       结束日期 YYYYMMDD（留空=今天）
  params    策略参数 JSON（留空=使用默认值）

【symbol 说明 — VeighNa 回测，不使用主连】
  RB / RB0      持仓量主力换月（akshare 分合约 + 换月）
  RB2410        指定单一合约
  RB2410.SHFE   指定合约 + 交易所

【回测引擎】vnpy_ctastrategy.BacktestingEngine

【示例 params】
  ma_crossover:   {"short_window": 5, "long_window": 20}  或 {"short":5,"long":20}
  turtle_trading: {"system": 1}
  rsi_demo:       {"period": 14, "oversold": 30, "overbought": 70}
══════════════════════════════════════════════════════════════
""".strip()


# ---------------------------------------------------------------------------
# 交互工具
# ---------------------------------------------------------------------------
def _sep(char: str = "─", width: int = 60) -> None:
    print(char * width)


def _title(text: str) -> None:
    _sep("═")
    print(f"  {text}")
    _sep("═")


def _pause() -> None:
    input("\n按 Enter 继续...")


def _prompt(label: str, default: str | None = None, *, required: bool = False) -> str:
    hint = f" [{default}]" if default is not None else ""
    while True:
        value = input(f"{label}{hint}: ").strip()
        if not value and default is not None:
            return default
        if value or not required:
            return value
        print("  ⚠ 此项为必填，请重新输入。")


def _prompt_date(label: str, default: str | None = None) -> str:
    while True:
        raw = _prompt(label, default)
        try:
            return validate_date(raw, label)
        except ValueError as e:
            print(f"  ⚠ {e}")


def _prompt_json(label: str, default: str | None = None) -> dict[str, Any]:
    raw = _prompt(label, default)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  ⚠ JSON 格式错误: {e}")
        return {}


def _print_df_preview(data: Any, max_rows: int = 10) -> None:
    if isinstance(data, pd.DataFrame):
        print(f"\n数据预览（共 {len(data)} 行 × {len(data.columns)} 列）:")
        print(data.head(max_rows).to_string())
        if len(data) > max_rows:
            print(f"... 省略 {len(data) - max_rows} 行")
    elif isinstance(data, dict):
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str)[:2000])
    else:
        print(data)


# ---------------------------------------------------------------------------
# 演示主类
# ---------------------------------------------------------------------------
class FuturesSkillDemo:
    """FuturesSkill 交互式功能展示。"""

    def __init__(self) -> None:
        self.skill = FuturesSkill()

    # ----- 规范说明 -----

    def show_api_guide(self) -> None:
        _title("期货数据接口 — 输入规范")
        print(API_INPUT_GUIDE)

    def show_strategy_guide(self) -> None:
        _title("策略回测 — 输入规范")
        print(STRATEGY_INPUT_GUIDE)
        print("\n")
        print(BACKTEST_INPUT_GUIDE)

    def show_all_guides(self) -> None:
        self.show_api_guide()
        print("\n")
        self.show_strategy_guide()

    # ----- 接口库功能 -----

    def do_api_search(self) -> None:
        _title("接口检索")
        print("请输入数据需求关键词（如: 螺纹钢历史行情、库存数据）")
        query = _prompt("需求描述", required=True)
        top = _prompt("返回条数", "5")
        result = self.skill.search_apis(query, top_k=int(top))
        print(f"\n{result.message}\n")
        if result.matched:
            for i, api in enumerate(result.apis, 1):
                print(f"【{i}】{api.name} — {api.title}")
                print(f"    数据源: {api.source} | 分类: {api.category}")
                print(f"    描述: {api.description[:60]}...")
                print()

    def do_api_select(self) -> None:
        _title("接口选择（最佳匹配）")
        query = _prompt("需求描述", required=True)
        result = self.skill.select_api(query)
        print(f"\n{result.message}")

    def do_list_apis(self) -> None:
        _title("列出支持的期货数据")
        keyword = _prompt("过滤关键词（留空=全部）", "")
        data = self.skill.list_apis(keyword or None)
        print(f"\n共 {data['total']} 个接口，{len(data['categories'])} 个分类\n")
        for cat, items in data["data_by_category"].items():
            print(f"▸ {cat}")
            for item in items:
                print(f"    · {item['name']}: {item['title']}  [{item['source']}]")
            print()

    def do_show_api_fields(self) -> None:
        _title("接口详情与可用数据字段")
        name = _prompt("接口名称", required=True)
        api = self.skill.api_registry.get(name)
        if api is None:
            print(f"\n⚠ 接口「{name}」不在接口库中，请先使用「接口检索」查找。")
            return
        print(f"\n{api.summary()}\n")
        if api.input_params:
            print("【输入参数字段】")
            print(f"  {'名称':<16} {'类型':<10} 描述")
            print(f"  {'-'*16} {'-'*10} {'-'*30}")
            for p in api.input_params:
                print(f"  {p.get('名称',''):<16} {p.get('类型',''):<10} {p.get('描述','')}")
        else:
            print("【输入参数字段】无额外参数（单次返回全部数据）")
        print("\n提示: 调用接口后可在返回的 DataFrame 中查看【输出字段】（列名）。")

    def do_call_api(self) -> None:
        _title("调用接口获取数据")
        name = _prompt("接口名称", required=True)
        print('参数 JSON，示例: {"symbol":"RB0","start_date":"20240101","end_date":"20241231"}')
        params = _prompt_json("params", "{}")
        try:
            data = self.skill.call_api(name, params or None)
            if isinstance(data, pd.DataFrame):
                print(f"\n【输出字段】{list(data.columns)}")
            _print_df_preview(data)
        except Exception as e:
            print(f"\n⚠ 调用失败: {e}")

    # ----- 策略库功能 -----

    def do_list_strategies(self) -> None:
        _title("策略库列表")
        strategies = self.skill.list_strategies()
        print(f"\n共 {len(strategies)} 个策略:\n")
        for s in strategies:
            src = "自定义" if s.get("custom") else "内置"
            print(f"  [{src}] {s['name']}")
            print(f"         {s['description']}")
            print(f"         依赖接口: {', '.join(s['required_apis'])}")
            if s.get("param_schema"):
                params = ", ".join(f"{k}={v.get('default')}" for k, v in s["param_schema"].items())
                print(f"         默认参数: {params}")
            print()

    def do_strategy_search(self) -> None:
        _title("策略检索 / 确认")
        name = _prompt("策略名称或关键词", required=True)
        result = self.skill.select_strategy(name)
        print(f"\n{result.message}")
        if not result.matched:
            print("\n提示: 运行「策略库列表」查看全部可用策略。")

    def do_single_backtest(self) -> None:
        _title("单策略回测")
        print("请先确认策略名称（可运行「策略检索」查看默认参数）\n")
        strategy = _prompt("策略名称 strategy", required=True)
        sel = self.skill.select_strategy(strategy)
        if not sel.matched:
            print(f"\n{sel.message}")
            return
        print(f"\n✓ 已确认策略: {sel.strategy_name}")
        print(f"  默认参数: {json.dumps(sel.default_params, ensure_ascii=False)}")

        symbol = _prompt("合约代码 symbol", "RB0", required=True)
        start = _prompt_date("开始日期 start (YYYYMMDD)")
        end_default = datetime.now().strftime("%Y%m%d")
        end = _prompt_date("结束日期 end (YYYYMMDD)", end_default)
        validate_backtest_period(start, end)

        use_default = _prompt("使用默认参数? (y/n)", "y").lower() in ("y", "yes", "")
        params: dict[str, Any] = {}
        if not use_default:
            print(f"当前策略参数 schema: {json.dumps(sel.info.get('param_schema', {}), ensure_ascii=False)}")
            params = _prompt_json("策略参数 params JSON", json.dumps(sel.default_params))

        show_chart = _prompt("显示回测曲线? (y/n)", "y").lower() in ("y", "yes", "")

        print("\n正在回测，请稍候...", flush=True)
        try:
            result = self.skill.backtest_strategy(
                sel.strategy_name,
                symbol,
                chart=show_chart,
                show_chart=show_chart,
                start_date=start,
                end_date=end,
                **params,
            )
            print(f"\n{result.summary()}")
        except Exception as e:
            print(f"\n⚠ 回测失败: {e}")

    def do_compare_backtest(self) -> None:
        _title("全策略对比回测")
        symbol = _prompt("合约代码 symbol", "RB0", required=True)
        start = _prompt_date("开始日期 start (YYYYMMDD)")
        end_default = datetime.now().strftime("%Y%m%d")
        end = _prompt_date("结束日期 end (YYYYMMDD)", end_default)
        validate_backtest_period(start, end)

        use_all = _prompt("对比全部策略? (y/n)", "y").lower() in ("y", "yes", "")
        names: list[str] | None = None
        params_map: dict[str, dict[str, Any]] | None = None
        if not use_all:
            raw = _prompt('策略列表 JSON，如 ["ma_crossover","turtle_trading"]', required=True)
            names = json.loads(raw)
        custom_params = _prompt("为策略指定参数 params_map? (y/n)", "n").lower() in ("y", "yes")
        if custom_params:
            params_map = _prompt_json("params_map JSON", "{}")

        show_chart = _prompt("显示对比曲线? (y/n)", "y").lower() in ("y", "yes", "")

        print("\n正在对比回测，请稍候...", flush=True)
        try:
            comparison = self.skill.backtest_compare(
                symbol, start, end, names, params_map,
                chart=show_chart, show_chart=show_chart,
            )
            print(f"\n{comparison.summary_table()}")
        except Exception as e:
            print(f"\n⚠ 对比回测失败: {e}")

    def do_closing_summary(self) -> None:
        _title("每日收盘总结")
        print("生成涨跌幅排名、市场广度、板块概况与后续关注提示。")
        print("非交易日自动展示最近交易日数据。\n")
        as_of = _prompt("基准日期 YYYYMMDD（留空=今天）", "")
        top = int(_prompt("涨跌幅排名条数", "5") or "5")
        push_only = _prompt("仅输出推送文案? (y/n)", "n").lower() in ("y", "yes")

        print("\n正在拉取各交易所日线并汇总...", flush=True)
        try:
            from closing_summary import run_closing_summary

            result = run_closing_summary(
                as_of_date=as_of or None,
                top_n=top,
                on_progress=lambda m: print(f"  {m}", flush=True),
            )
            print()
            if push_only:
                print(result.push_message)
            else:
                print(result.summary())
        except Exception as e:
            print(f"\n⚠ 生成失败: {e}")

    def do_monitor(self) -> None:
        _title("策略盘中检测")
        print("检测策略是否新触发开/平仓信号（供 OpenClaw 定时推送）\n")
        strategy = _prompt("策略名称（留空=扫描全部）", "")
        symbol = _prompt("合约代码 symbol", "RB0", required=True)
        interval = _prompt("K线周期 daily/1/5/15/30/60", "daily")
        pos = int(_prompt("假定当前持仓手数 pos", "0") or "0")

        print("\n正在拉取行情并检测信号...", flush=True)
        try:
            if strategy.strip():
                sel = self.skill.select_strategy(strategy)
                if not sel.matched:
                    print(f"\n{sel.message}")
                    return
                result = self.skill.monitor_strategy(
                    sel.strategy_name, symbol, interval=interval, pos=pos,
                    **sel.default_params,
                )
                print(f"\n{result.summary()}")
            else:
                batch = self.skill.monitor_strategies(
                    symbol, interval=interval, default_pos=pos,
                )
                print(f"\n{batch.summary()}")
        except Exception as e:
            print(f"\n⚠ 检测失败: {e}")

    # ----- 快捷演示流程 -----

    def run_quick_demo(self) -> None:
        """一键演示完整流程：接口检索 → 字段查看 → 策略确认 → 回测对比。"""
        _title("快捷演示流程")
        print("将依次演示: 接口检索 → 接口字段 → 策略列表 → 策略确认 → 对比回测\n")

        print("▶ 步骤1: 检索「螺纹钢历史行情」")
        r1 = self.skill.search_apis("螺纹钢历史行情", top_k=3)
        for api in r1.apis:
            print(f"   · {api.name}: {api.title}")

        if r1.apis:
            api = r1.apis[0]
            print(f"\n▶ 步骤2: 查看接口 [{api.name}] 输入字段")
            for p in api.input_params:
                print(f"   · {p.get('名称')}: {p.get('描述', '')[:40]}")

        print("\n▶ 步骤3: 策略库")
        for s in self.skill.list_strategies():
            print(f"   · {s['name']}: {s['description'][:40]}...")

        print("\n▶ 步骤4: 确认策略 ma_crossover")
        sel = self.skill.select_strategy("ma_crossover")
        print(f"   默认参数: {sel.default_params}")

        print("\n▶ 步骤5: vnpy 全策略对比回测 RB 2023年")
        confirm = _prompt("继续执行对比回测? (y/n)", "y").lower() in ("y", "yes", "")
        if confirm:
            cmp = self.skill.backtest_compare(
                "RB", "20250101", "20251231",
                chart=True, show_chart=False,
            )
            print(f"\n{cmp.summary_table()}")
        print("\n演示完成。")

    # ----- 主菜单 -----

    def print_menu(self) -> None:
        _title("AKShare 期货 Skill 功能展示")
        print("""
  ┌─ 规范说明 ─────────────────────────────────────┐
  │  1. 接口输入规范      2. 策略回测输入规范        │
  │  3. 全部输入规范                               │
  ├─ 接口库 ───────────────────────────────────────┤
  │  4. 接口检索          5. 接口选择（最佳匹配）  │
  │  6. 列出可用数据      7. 接口字段详情          │
  │  8. 调用接口获取数据                           │
  ├─ 策略库 ───────────────────────────────────────┤
  │  9. 策略库列表       10. 策略检索 / 确认       │
  │ 11. 单策略回测       12. 全策略对比回测         │
  │ 14. 策略盘中检测       15. 每日收盘总结         │
  ├─ 其他 ─────────────────────────────────────────┤
  │ 13. 快捷演示流程（一键体验）                   │
  │  0. 退出                                       │
  └────────────────────────────────────────────────┘
""")

    def dispatch(self, choice: str) -> bool:
        """处理菜单选择，返回 False 表示退出。"""
        actions = {
            "1": self.show_api_guide,
            "2": self.show_strategy_guide,
            "3": self.show_all_guides,
            "4": self.do_api_search,
            "5": self.do_api_select,
            "6": self.do_list_apis,
            "7": self.do_show_api_fields,
            "8": self.do_call_api,
            "9": self.do_list_strategies,
            "10": self.do_strategy_search,
            "11": self.do_single_backtest,
            "12": self.do_compare_backtest,
            "13": self.run_quick_demo,
            "14": self.do_monitor,
            "15": self.do_closing_summary,
        }
        if choice == "0":
            print("\n再见！")
            return False
        action = actions.get(choice)
        if action is None:
            print("\n⚠ 无效选项，请输入 0-15。")
        else:
            print()
            action()
            _pause()
        return True

    def run(self) -> None:
        print("\n欢迎使用 FuturesSkill 交互式功能展示")
        print("首次使用建议先选择 [3] 查看全部输入规范\n")
        while True:
            self.print_menu()
            choice = input("请选择功能 [0-15]: ").strip()
            if not self.dispatch(choice):
                break


def main() -> int:
    try:
        FuturesSkillDemo().run()
        return 0
    except KeyboardInterrupt:
        print("\n\n已中断。")
        return 130
    except Exception as e:
        print(f"\n错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
