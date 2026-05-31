"""VeighNa CTA 策略模板（延迟导入，避免未安装 vnpy_ctastrategy 时阻塞模块加载）。"""

VNPY_STRATEGY_MAP: dict[str, str] = {
    "ma_crossover": "strategies.vnpy_cta.ma_crossover:MaCrossoverStrategy",
    "turtle_trading": "strategies.vnpy_cta.turtle_trading:TurtleTradingStrategy",
    "rsi_demo": "strategies.vnpy_cta.rsi_demo:RsiDemoStrategy",
}


def load_vnpy_strategy(name: str) -> type:
    if name not in VNPY_STRATEGY_MAP:
        raise KeyError(f"未知 vnpy 策略: {name}")
    module_path, cls_name = VNPY_STRATEGY_MAP[name].split(":")
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, cls_name)
