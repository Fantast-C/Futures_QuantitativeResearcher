"""RSI 超买超卖策略 — vnpy CTA 模板（多空双向）。"""

from vnpy_ctastrategy import CtaTemplate
from vnpy.trader.utility import ArrayManager


class RsiDemoStrategy(CtaTemplate):
    """RSI 超卖开多/超买平空，超买开空/超卖平多。"""

    author = "FuturesSkill"
    period = 14
    oversold = 30
    overbought = 70

    rsi_value = 0.0

    parameters = ["period", "oversold", "overbought"]
    variables = ["rsi_value"]

    def on_init(self) -> None:
        self.am = ArrayManager(self.period + 10)

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass

    def _calc_rsi(self) -> float:
        if not self.am.inited:
            return 50.0
        closes = self.am.close
        gains, losses = [], []
        for i in range(1, min(len(closes), self.period + 1)):
            diff = closes[-i] - closes[-i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = sum(losses) / len(losses) if losses else 1e-10
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)

    def on_bar(self, bar) -> None:
        self.am.update_bar(bar)
        if not self.am.inited:
            return

        self.rsi_value = self._calc_rsi()

        if self.pos == 0:
            if self.rsi_value < self.oversold:
                self.buy(bar.close_price, 1)
            elif self.rsi_value > self.overbought:
                self.short(bar.close_price, 1)
        elif self.pos > 0:
            if self.rsi_value > self.overbought:
                self.sell(bar.close_price, abs(self.pos))
        elif self.pos < 0:
            if self.rsi_value < self.oversold:
                self.cover(bar.close_price, abs(self.pos))

        self.put_event()
