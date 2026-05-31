---
name: futures-akshare
description: >-
  AKShare 期货数据接口检索、调用与策略回测。根据 akshare_futures.md 选择 akshare 接口获取期货数据，
  支持库存/行情/持仓/仓单/手续费等检索，策略库含双均线与海龟交易法回测，支持盘中策略信号检测（OpenClaw）。
  在用户询问期货数据、akshare 接口、期货策略回测、盘中监控、OpenClaw 期货 skill 时使用。
---

# AKShare 期货分析 Skill

## 资源

- 接口文档: [akshare_futures.md](../../akshare_futures.md)
- 执行模块: [FuturesSkill.py](../../FuturesSkill.py)
- 交互演示: [FuturesSkillDemo.py](../../FuturesSkillDemo.py)（菜单驱动，适合功能展示）

## 交互式演示

```bash
python FuturesSkillDemo.py
```

提供完整菜单：输入规范说明、接口检索/字段查看/调用、策略检索/单策略回测/全策略对比、快捷演示流程。

## 工作流

### 1. 检索/选择接口

用户描述数据需求时，先运行检索：

```bash
python FuturesSkill.py search "螺纹钢 历史行情"
python FuturesSkill.py select "获取大商所持仓排名"
```

- `search`: 返回多个候选接口及基本信息、数据源
- `select`: 返回最佳匹配接口的完整详情
- 若无匹配，告知用户「无法获取，不存在相应接口」

### 2. 列出支持的期货数据

```bash
python FuturesSkill.py list-apis
python FuturesSkill.py list-apis --keyword 库存
```

### 3. 调用接口获取数据

确认接口名与参数后：

```bash
python FuturesSkill.py call futures_main_sina --params '{"symbol":"RB0","start_date":"20240101","end_date":"20241231"}'
```

也可在 Python 中调用：

```python
from FuturesSkill import FuturesSkill
skill = FuturesSkill()
df = skill.call_api("futures_main_sina", {"symbol": "RB0", "start_date": "20240101", "end_date": "20241231"})
```

### 4. 添加新接口

当 akshare 新增接口或需扩展自定义接口：

```bash
python FuturesSkill.py add-api \
  --name new_api_name \
  --title "接口标题" \
  --url "https://数据源地址" \
  --description "接口描述"
```

自定义接口持久化在 `api_custom.json`，与 `akshare_futures.md` 解析结果合并。

### 5. 策略回测（VeighNa / vnpy 引擎，非主连）

回测使用 [VeighNa](https://github.com/vnpy/vnpy) 的 `vnpy_ctastrategy.BacktestingEngine`：
- 数据来自 akshare `get_futures_daily`（各交易所**分合约**日线）
- 默认 **持仓量主力换月**（`RB`/`RB0`），非新浪主连虚假拼接
- 也可指定单一合约（如 `RB2410`）

```bash
pip install -r requirements.txt   # 含 vnpy, vnpy_ctastrategy
```

**推荐流程（Agent 必须遵循）：**

```
list-strategies → select-strategy <名称> → backtest / backtest-compare
```

**查看输入规范（提示用户如何填写参数）：**

```bash
python FuturesSkill.py backtest-guide
```

**确认所选策略（返回策略详情、默认参数、推荐命令）：**

```bash
python FuturesSkill.py select-strategy ma_crossover
python FuturesSkill.py select-strategy turtle_trading
python FuturesSkill.py select-strategy 均线   # 关键词模糊匹配
```

**单策略回测（必填 start/end 自定义时间段）：**

```bash
python FuturesSkill.py backtest ma_crossover \
  --symbol RB --start 20230101 --end 20241231 \
  --params '{"short_window":5,"long_window":20}'
```

指定单一合约回测：

```bash
python FuturesSkill.py backtest ma_crossover --symbol RB2410 --start 20240101 --end 20241231
```

默认自动生成回测曲线图，保存至 `output/backtest/`。可选参数：

- `--no-chart` 不生成图表
- `-o path.png` 指定保存路径
- `--no-show` 仅保存不弹窗（适合 Agent/服务器环境）

```bash
python FuturesSkill.py backtest turtle_trading \
  --symbol RB0 --start 20230101 --end 20241231 \
  --params '{"system":1}'
```

**全策略对比回测：**

```bash
python FuturesSkill.py backtest-compare \
  --symbol RB0 --start 20230101 --end 20241231
```

```bash
# 仅对比指定策略，并为某策略自定义参数
python FuturesSkill.py backtest-compare \
  --symbol RB0 --start 20230101 --end 20241231 \
  --strategies '["ma_crossover","turtle_trading","rsi_demo"]' \
  --params-map '{"ma_crossover":{"short":10,"long":30}}'
```

#### VeighNa 回测引擎参数详解

回测底层调用 `vnpy_ctastrategy.backtesting.BacktestingEngine`，由 `vnpy_engine.run_vnpy_backtest()` 封装。参数分三层：

**① 项目层（Python API / 策略 `backtest()` kwargs）**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `symbol` | str | — | 品种/合约，见下方 symbol 解析 |
| `start_date` | str | — | 开始日期 `YYYYMMDD` |
| `end_date` | str | 当天 | 结束日期 `YYYYMMDD` |
| `data_mode` | str | 自动 | `dominant` 持仓量换月；`specific` 指定合约 |
| `capital` | int | `1000000` | 初始资金，**仅用于收益率/净值统计** |
| `slippage` | float | `0` | 滑点（价格单位），传给 vnpy |
| `on_progress` | callable | — | 进度回调，接收字符串消息 |
| `verbose` | bool | `True` | 是否打印 `[1/4]~[4/4]` 进度 |

CLI 的 `backtest` 子命令目前暴露 `--symbol/--start/--end/--params` 及图表选项；`capital`、`slippage` 需通过 Python API 传入：

```python
from FuturesSkill import FuturesSkill
skill = FuturesSkill()
result = skill.backtest_strategy(
    "ma_crossover", "RB0",
    start_date="20230101", end_date="20241231",
    short_window=5, long_window=20,
    capital=500_000,   # 初始资金
    slippage=1.0,      # 每手滑点 1 个最小跳动
)
```

**② vnpy `engine.set_parameters()` 映射**

| vnpy 参数 | 来源 | 说明 |
|-----------|------|------|
| `vt_symbol` | `parse_futures_symbol()` | 如 `rb_domin.SHFE`、`rb2410.SHFE` |
| `interval` | 固定 `Interval.DAILY` | 目前仅支持日线回测 |
| `start` / `end` | K 线首尾 bar 时间 | 由实际数据决定，非用户字符串 |
| `rate` | `resolve_trading_costs()` | 手续费率，按 turnover × rate 计费 |
| `size` | akshare 手续费表 / 静态 | 合约乘数（如 RB=10） |
| `pricetick` | akshare 手续费表 / 静态 | 最小变动价位 |
| `slippage` | 用户 `slippage` | 成交价偏移 |
| `capital` | 用户 `capital` | 账户初始余额 |

**③ 手续费 `rate` 换算逻辑**

优先级：`futures_fees_info` → `futures_comm_info` → `VARIETY_META` 静态值。

`rate` 由 akshare「1 手开+平仓费用 ÷ 名义价值」换算，使 vnpy 的 `turnover * rate` 近似真实成本；固定手续费品种（如 AU）同样适用。回测结果 `params` 中会附带 `commission_rate`、`fee_source`、`fee_contract`。

**④ 仓位与保证金**

- 三个内置策略每次开/平仓 **固定 1 手**（`buy/sell/short/cover` 数量为 1）
- vnpy CTA 回测 **不支持保证金比例约束**，不会因资金不足拒单
- `capital` 不影响能否开仓，只影响 `%` 收益率与净值曲线缩放

**⑤ 策略 setting（传给 vnpy CtaTemplate）**

| 策略 | vnpy 类参数 | 别名（--params 可混用） |
|------|-------------|-------------------------|
| `ma_crossover` | `short_window`, `long_window` | `short`, `long` |
| `turtle_trading` | `entry_window`, `exit_window` | `system=1→20/10`, `system=2→55/20` |
| `rsi_demo` | `period`, `oversold`, `overbought` | — |

**⑥ 回测输出 `BacktestResult` 字段**

| 字段 | 说明 |
|------|------|
| `total_return_pct` | 总收益率 % |
| `max_drawdown_pct` | 最大回撤 % |
| `sharpe_ratio` | 夏普比率 |
| `trade_count` | 成交笔数 |
| `win_rate_pct` | 按盈利/亏损**日**计算的胜率 |
| `signals` | 成交信号列表：`open_long` / `close_long` / `open_short` / `close_short` |
| `data_mode` | `dominant` 或 `specific` |
| `contracts_used` | 换月涉及的具体合约列表 |
| `equity_curve` / `benchmark_curve` | 净值与价格基准（起点=100） |

**⑦ 进度阶段**

```
[1/4] 解析合约
[2/4] 拉取手续费率
[3/4] 拉取历史 K 线（akshare 网络，最耗时）
[4/4] vnpy 策略回测 + 统计
```

**⑧ 已知限制**

- 空仓时若已处死叉/多头排列，回测会在下一根 bar **立即开空/开多**（非等下一次交叉）
- 盘中检测仅在**边缘触发**时告警，与回测 bar 逐根执行逻辑略有差异
- 不支持 vnpy 的 `risk_free`、多合约组合、分钟级回测

### 用户输入规范（Agent 向用户收集信息时使用）

| 字段 | 必填 | 格式 | 示例 |
|------|------|------|------|
| strategy | 是 | 策略库精确名称 | `ma_crossover` |
| symbol | 是 | 品种(RB) / 指定合约(RB2410)，**非主连** | `RB`, `RB2410` |
| start | 是 | YYYYMMDD | `20230101` |
| end | 否 | YYYYMMDD，默认当天 | `20241231` |
| params | 否 | JSON，未填则用默认值 | `{"short":5,"long":20}` |

各策略默认参数见 `select-strategy <名称>` 输出。

对比模式：用户说「全策略对比」「比较所有策略」→ 使用 `backtest-compare`，无需指定 strategy。

回测输出必须包含：总收益率、最大回撤、夏普比率、交易次数、胜率；对比模式额外标注最优策略。

### 6. 动态扩展策略库

**创建自定义策略模板**（生成到 `strategies/custom/`）：

```bash
python FuturesSkill.py init-strategy --name my_strategy \
  --description "我的策略描述" \
  --apis '["futures_main_sina","futures_inventory_em"]' \
  --params '{"period":{"type":"int","default":14,"help":"周期"}}'
```

编辑生成的 `.py` 文件，实现 `fetch_data`（获取数据）和 `generate_signals`（生成 signal 列）。  
自定义策略可通过 `self.call_api("接口名", {...})` 调用接口库中的任意 akshare 接口。

**加载 / 注册策略：**

```bash
python FuturesSkill.py reload-strategies          # 重新扫描 strategies/custom/
python FuturesSkill.py register-strategy /path/to/my_strategy.py
python FuturesSkill.py show-strategy my_strategy  # 查看参数、依赖接口
python FuturesSkill.py list-strategies            # [内置]/[自定义] 分类展示
```

**通用参数回测**（适用于任意策略）：

```bash
python FuturesSkill.py backtest my_strategy --symbol RB0 \
  --params '{"period":14,"oversold":30,"overbought":70}' --start 20230101
```

策略清单持久化在 `strategy_registry.json`。

## 输出规范

向用户报告接口选择结果时，必须包含：

1. **选择的接口名称**（如 `futures_main_sina`）
2. **数据源**（如 新浪财经、东方财富）
3. **接口描述与限量说明**
4. **推荐调用方式**（CLI 或 Python 示例）

策略回测结果需包含：总收益率、最大回撤、夏普比率、交易次数、胜率。对比回测需输出排名表并标注最优策略。

Agent 回测前必须先 `select-strategy` 确认策略名，并向用户确认 symbol / start / end / params。

### 7. 策略盘中检测（OpenClaw 定时任务）

拉取新浪实时/分时行情，检测策略**新触发**信号（金叉、突破等），输出 `alert_message` 供推送。

```bash
python FuturesSkill.py monitor-guide
```

**单策略检测：**

```bash
python FuturesSkill.py monitor-strategy ma_crossover \
  --symbol RB0 --interval daily --pos 0 \
  --params '{"short_window":5,"long_window":20}'
```

**全策略扫描（推荐 OpenClaw cron）：**

```bash
python FuturesSkill.py monitor-strategies \
  --symbol RB0 --interval daily --json --alert-only --exit-on-alert
```

| 字段 | 说明 | 示例 |
|------|------|------|
| symbol | 新浪合约代码 | `RB0`, `IF0`, `RB2410` |
| interval | `daily` 或分钟 `1/5/15/30/60` | `daily` |
| pos | 假定当前持仓（影响开/平判断） | `0` 空仓, `1` 持多 |
| params | 策略参数 JSON | 同回测 |

**OpenClaw 集成要点：**

- `--json`：结构化输出，含 `triggered`、`signal`、`alert_message`
- `--alert-only`：无信号时不输出详情（减少噪音）
- `--exit-on-alert`：有信号时退出码 `2`，便于工作流分支
- 内置支持：`ma_crossover`、`turtle_trading`、`rsi_demo`

Python 调用：

```python
from FuturesSkill import FuturesSkill
skill = FuturesSkill()
r = skill.monitor_strategy("ma_crossover", "RB0", interval="daily", pos=0)
if r.triggered:
    print(r.alert_message)  # 推送到 OpenClaw / 企微 / 邮件
```

## 常见需求 → 接口映射

| 需求关键词 | 推荐接口 |
|-----------|---------|
| 实时行情 | `futures_zh_spot`, `futures_zh_realtime` |
| 历史日线 | `futures_main_sina`, `futures_zh_daily_sina`, `futures_hist_em` |
| 库存/仓单 | `futures_inventory_em`, `futures_warehouse_receipt_*` |
| 持仓排名 | `futures_dce_position_rank`, `futures_hold_pos_sina` |
| 手续费保证金 | `futures_comm_info`, `futures_fees_info` |
| 合约详情 | `futures_contract_detail`, `futures_contract_detail_em` |

## 依赖

```bash
pip install -r requirements.txt
```
