"""双均线交叉策略 — vnpy CTA 模板（多空双向）。"""

from vnpy_ctastrategy import CtaTemplate
from vnpy.trader.utility import ArrayManager


class MaCrossoverStrategy(CtaTemplate):
    """金叉开多/平空，死叉开空/平多。"""

    author = "FuturesSkill"
    short_window = 5
    long_window = 20

    short_ma = 0.0
    long_ma = 0.0

    parameters = ["short_window", "long_window"]
    variables = ["short_ma", "long_ma"]

    def on_init(self) -> None:
        self.am = ArrayManager(self.long_window + 5)

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass

    def on_bar(self, bar) -> None:
        self.am.update_bar(bar)
        if not self.am.inited:
            return

        self.short_ma = self.am.sma(self.short_window)
        self.long_ma = self.am.sma(self.long_window)

        if self.pos == 0:
            if self.short_ma > self.long_ma:
                self.buy(bar.close_price, 1)
            elif self.short_ma < self.long_ma:
                self.short(bar.close_price, 1)
        elif self.pos > 0:
            if self.short_ma < self.long_ma:
                self.sell(bar.close_price, abs(self.pos))
        elif self.pos < 0:
            if self.short_ma > self.long_ma:
                self.cover(bar.close_price, abs(self.pos))

        self.put_event()
