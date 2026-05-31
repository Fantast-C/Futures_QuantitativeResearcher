# Project_Futures

基于 [AKShare](https://github.com/akfamily/akshare) 与 [VeighNa (vnpy)](https://github.com/vnpy/vnpy) 的期货数据检索、策略回测与盘中检测工具集。面向本地演示、Agent / OpenClaw 自动化调用设计。

## 功能概览

| 模块 | 说明 |
|------|------|
| **接口库** | 从 `akshare_futures.md` 解析 akshare 期货接口，支持关键词检索、调用、自定义扩展 |
| **策略库** | 内置双均线、海龟、RSI 三个 vnpy CTA 策略（**多空双向**），支持自定义策略插件 |
| **回测** | vnpy `BacktestingEngine`，使用分合约日线 + 持仓量主力换月（**非主连拼接**） |
| **盘中检测** | 拉取新浪行情，检测策略新触发信号，输出 `alert_message` 供 OpenClaw 推送 |
| **收盘总结** | 独立模块 `closing_summary.py`：涨跌幅排名、市场广度、板块概况、关注提示 |
| **交互演示** | `FuturesSkillDemo.py` 菜单驱动，适合功能验证与展示 |

## 项目结构

```
Project_Futures/
├── FuturesSkill.py          # 核心入口：接口库 + 策略库 + CLI
├── FuturesSkillDemo.py      # 交互式菜单演示
├── vnpy_engine.py           # vnpy 回测引擎集成（数据加载、手续费、进度）
├── strategy_monitor.py      # 盘中策略信号检测
├── closing_summary.py       # 每日收盘总结（独立模块，可单独运行）
├── akshare_futures.md       # akshare 期货接口文档（接口库数据源）
├── requirements.txt         # Python 依赖
├── strategy_registry.json   # 自定义策略注册清单（自动生成）
│
├── strategies/
│   ├── vnpy_cta/            # 内置 vnpy CTA 策略模板
│   │   ├── ma_crossover.py
│   │   ├── turtle_trading.py
│   │   └── rsi_demo.py
│   └── custom/              # 用户自定义策略目录
│
├── skills/futures-akshare/
│   └── SKILL.md             # Cursor / OpenClaw Agent Skill 说明
│
├── docs/                    # 展示文档（非运行依赖）
│   ├── 策略逻辑说明.docx
│   └── images/              # 策略流程图 PNG
│
└── output/backtest/         # 回测曲线图输出目录（运行后生成）
```

## 环境要求

- Python 3.10+
- 网络访问（akshare 拉取行情与手续费数据）

## 安装

```bash
cd Project_Futures
pip install -r requirements.txt
```

主要依赖：`akshare`、`pandas`、`matplotlib`、`vnpy`、`vnpy_ctastrategy`。

## 快速开始

### 交互式演示

```bash
python FuturesSkillDemo.py
```

菜单包含：接口检索、策略回测、全策略对比、盘中检测等。

### CLI 常用命令

**接口检索与调用**

```bash
python FuturesSkill.py search "螺纹钢历史行情"
python FuturesSkill.py list-apis --keyword 行情
python FuturesSkill.py call futures_zh_daily_sina --params '{"symbol":"RB0"}'
```

**策略回测**

```bash
# 查看回测输入规范
python FuturesSkill.py backtest-guide

# 单策略回测（RB0 = 持仓量主力换月，非新浪主连）
python FuturesSkill.py backtest ma_crossover \
  --symbol RB0 --start 20230101 --end 20241231 \
  --params '{"short_window":5,"long_window":20}'

# 全策略对比
python FuturesSkill.py backtest-compare \
  --symbol RB0 --start 20230101 --end 20241231
```

**盘中检测（OpenClaw 定时任务）**

```bash
python FuturesSkill.py monitor-guide

# 单策略
python FuturesSkill.py monitor-strategy ma_crossover \
  --symbol RB0 --interval daily --pos 0 --json

# 全策略扫描，有信号时退出码 2
python FuturesSkill.py monitor-strategies \
  --symbol RB0 --interval daily --json --alert-only --exit-on-alert
```

**每日收盘总结（OpenClaw 定时推送）**

```bash
# 独立模块（推荐单独维护/调试）
python closing_summary.py --push

# 或通过 FuturesSkill 薄封装
python FuturesSkill.py closing-summary --push --json

# 指定基准日（非交易日自动回溯最近交易日）
python closing_summary.py --date 20241228 --top 5
```

### Python API

```python
from FuturesSkill import FuturesSkill

skill = FuturesSkill()

# 检索接口
result = skill.search_apis("螺纹钢历史行情")

# 回测
bt = skill.backtest_strategy(
    "ma_crossover", "RB0",
    start_date="20230101", end_date="20241231",
    short_window=5, long_window=20,
)
print(bt.summary())

# 盘中检测
mon = skill.monitor_strategy("ma_crossover", "RB0", interval="daily", pos=0)
if mon.triggered:
    print(mon.alert_message)

# 收盘总结
summary = skill.closing_summary(top_n=5)
print(summary.push_message)
```

## 内置策略

三个策略均为 **多空双向**，每次开/平仓 **固定 1 手**。

| 策略名 | 说明 | 默认参数 |
|--------|------|----------|
| `ma_crossover` | 双均线：金叉开多/平空，死叉开空/平多 | short=5, long=20 |
| `turtle_trading` | 海龟唐奇安通道突破 | system=1 → 20/10 日 |
| `rsi_demo` | RSI 超卖做多、超买做空 | period=14, 30/70 |

策略逻辑流程图见 `docs/策略逻辑说明.docx`。

## 回测说明

### 数据模式

| symbol 输入 | 含义 |
|-------------|------|
| `RB` / `RB0` | 持仓量主力换月序列（akshare 分合约 + 换月逻辑） |
| `RB2410` | 指定单一合约 |
| `RB2410.SHFE` | 指定合约 + 交易所 |

**不使用新浪主连虚假拼接**，避免换月跳空失真。

### 交易成本与 vnpy 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `capital` | 1,000,000 | 初始资金，**仅影响收益率统计**，不约束开仓 |
| `slippage` | 0 | 滑点（价格单位） |
| `rate` | akshare 自动 | 手续费率，由 `futures_fees_info` → `futures_comm_info` → 静态值 |
| `size` / `pricetick` | akshare 自动 | 合约乘数、最小跳动 |

- 每次开/平仓固定 **1 手**；vnpy CTA 回测**不支持保证金比例**
- CLI `backtest` 暂未暴露 `capital`/`slippage`，Python API 可传：

```python
skill.backtest_strategy("ma_crossover", "RB0",
    start_date="20230101", end_date="20241231",
    capital=500_000, slippage=1.0)
```

完整参数说明见 [`skills/futures-akshare/SKILL.md`](skills/futures-akshare/SKILL.md) 中「VeighNa 回测引擎参数详解」。

### 回测输出

- 总收益率、最大回撤、夏普比率、交易次数、胜率
- `signals` 列表：每笔成交的开多/平多/开空/平空记录
- 可选生成净值曲线图至 `output/backtest/`

## 盘中检测说明

- **数据源**：新浪日线 + 1 分钟线刷新（`daily`），或 1/5/15/30/60 分钟 K 线
- **触发逻辑**：仅在信号**新触发**（如均线刚交叉、刚突破通道）时 `triggered=True`
- **持仓假设**：`pos=0` 空仓，`>0` 持多，`<0` 持空，影响开/平仓判断
- **退出码**：`0` 无信号 / `1` 错误 / `2` 有信号（配合 `--exit-on-alert`）

## 每日收盘总结

独立模块 [`closing_summary.py`](closing_summary.py)，与策略回测/盘中检测逻辑隔离。

- **数据源**：akshare `get_futures_daily`（上期所/大商所/郑商所/中金所/能源中心/广期所）
- **主力定义**：各品种当日**持仓量最大**合约（与回测换月逻辑一致，非新浪主连）
- **涨跌幅**：结算价相对前结算价 `(settle - pre_settle) / pre_settle`
- **休息日**：基准日为非交易日时，自动展示**最近交易日**总结

推送内容包含：涨/跌/平家数、涨幅/跌幅 Top N、板块均涨跌幅、成交活跃品种、后续关注提示。

```bash
# Demo 菜单 [15]
python FuturesSkillDemo.py

# 收盘后 cron（工作日 15:10，Asia/Shanghai）
10 15 * * 1-5 cd /path/to/Project_Futures && python3 closing_summary.py --push
```

### OpenClaw 定时任务示例

**系统 crontab（推荐，零 LLM 成本）** — 工作日 15:05 扫描：

```bash
5 15 * * 1-5 cd /path/to/Project_Futures && \
  python3 FuturesSkill.py monitor-strategies \
  --symbol RB0 --interval daily --json --alert-only --exit-on-alert
```

**OpenClaw cron（Agent 驱动）**：

```bash
openclaw cron add --name "期货-RB0" \
  --cron "5 15 * * 1-5" --tz Asia/Shanghai --session isolated \
  --message "运行 monitor-strategies --symbol RB0 --interval daily --json --alert-only --exit-on-alert；退出码 2 时推送 alerts"
```

更多示例（shell 推送脚本、`jobs.json` 格式、多品种配置）见 [`skills/futures-akshare/SKILL.md`](skills/futures-akshare/SKILL.md) 第 7 节。

## 自定义策略

```bash
# 创建模板
python FuturesSkill.py init-strategy --name my_strategy \
  --description "我的策略" \
  --apis '["get_futures_daily"]'

# 编辑 strategies/custom/my_strategy.py 后加载
python FuturesSkill.py reload-strategies
python FuturesSkill.py backtest my_strategy --symbol RB0 --start 20230101
```

自定义策略需实现 vnpy `CtaTemplate` 或继承项目 `BaseStrategy` 包装。

## OpenClaw / Agent 集成

- Skill 文档：[`skills/futures-akshare/SKILL.md`](skills/futures-akshare/SKILL.md)（含 vnpy 参数详解、OpenClaw cron 配置）
- 推荐工作流：`list-strategies` → `select-strategy` → `backtest` / `monitor-strategies`
- 接口文档源：[`akshare_futures.md`](akshare_futures.md)
- OpenClaw cron 官方文档：[Cron jobs](https://docs.clawcentral.io/automation/cron-jobs/)

## 常见问题

**Q: 回测很慢，进度长时间停在 `[3/4]`？**  
A: 主要在拉取 akshare `get_futures_daily`，网络重试属正常，请耐心等待。

**Q: 回测交易次数为 0？**  
A: 检查日期区间是否足够长、该区间是否有均线交叉/突破信号。


## 许可

接口数据来源于 AKShare 及各公开数据源。
