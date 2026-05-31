"""海龟交易法 — vnpy CTA 模板（唐奇安通道，多空双向）。"""

from vnpy_ctastrategy import CtaTemplate
from vnpy.trader.utility import ArrayManager


class TurtleTradingStrategy(CtaTemplate):
    """突破 N 日高点开多/跌破 N 日低点开空；反向突破 M 日通道平仓。"""

    author = "FuturesSkill"
    entry_window = 20
    exit_window = 10

    entry_high = 0.0
    entry_low = 0.0
    exit_high = 0.0
    exit_low = 0.0

    parameters = ["entry_window", "exit_window"]
    variables = ["entry_high", "entry_low", "exit_high", "exit_low"]

    def on_init(self) -> None:
        self.am = ArrayManager(max(self.entry_window, self.exit_window) + 5)

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass

    def on_bar(self, bar) -> None:
        self.am.update_bar(bar)
        if not self.am.inited:
            return

        entry_highs = self.am.high[-self.entry_window - 1 : -1]
        entry_lows = self.am.low[-self.entry_window - 1 : -1]
        exit_highs = self.am.high[-self.exit_window - 1 : -1]
        exit_lows = self.am.low[-self.exit_window - 1 : -1]
        if (
            len(entry_highs) < self.entry_window
            or len(entry_lows) < self.entry_window
            or len(exit_highs) < self.exit_window
            or len(exit_lows) < self.exit_window
        ):
            return

        self.entry_high = float(entry_highs.max())
        self.entry_low = float(entry_lows.min())
        self.exit_high = float(exit_highs.max())
        self.exit_low = float(exit_lows.min())

        if self.pos == 0:
            if bar.close_price > self.entry_high:
                self.buy(bar.close_price, 1)
            elif bar.close_price < self.entry_low:
                self.short(bar.close_price, 1)
        elif self.pos > 0:
            if bar.close_price < self.exit_low:
                self.sell(bar.close_price, abs(self.pos))
        elif self.pos < 0:
            if bar.close_price > self.exit_high:
                self.cover(bar.close_price, abs(self.pos))

        self.put_event()
