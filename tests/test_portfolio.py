from tradingbot.models import Fill, Side
from tradingbot.portfolio import Portfolio
from tradingbot.risk import RiskManager
from tradingbot.config import RiskConfig


def test_roundtrip_records_trade_and_pnl():
    p = Portfolio(starting_cash=1000.0)
    assert p.record_fill(Fill("BTCUSDT", Side.BUY, 1.0, 100.0, 0.0, 1.0)) is None
    trade = p.record_fill(Fill("BTCUSDT", Side.SELL, 1.0, 110.0, 0.0, 2.0))

    assert trade is not None
    assert trade.pnl == 10.0
    assert p.num_trades == 1
    assert p.wins == 1
    assert p.win_rate == 1.0
    assert p.realized_pnl == 10.0


def test_fees_reduce_pnl():
    p = Portfolio(starting_cash=1000.0)
    p.record_fill(Fill("BTCUSDT", Side.BUY, 1.0, 100.0, 1.0, 1.0))
    trade = p.record_fill(Fill("BTCUSDT", Side.SELL, 1.0, 100.0, 1.0, 2.0))
    # Flat price, 1 + 1 in fees -> -2 pnl.
    assert trade.pnl == -2.0
    assert p.win_rate == 0.0


def test_risk_halts_after_daily_loss_limit():
    r = RiskManager(RiskConfig(daily_loss_limit_pct=0.05), starting_equity=1000.0)
    assert not r.halted
    r.update_equity(960.0)  # -4%, still fine
    assert not r.halted
    r.update_equity(940.0)  # -6%, breaches -5%
    assert r.halted
    assert r.order_notional(1000.0) == 0.0


def test_risk_respects_min_notional():
    r = RiskManager(
        RiskConfig(order_size_pct=0.95, min_notional=10.0), starting_equity=1000.0
    )
    assert r.order_notional(1000.0) == 950.0
    assert r.order_notional(5.0) == 0.0  # 0.95*5 < 10 -> skip
