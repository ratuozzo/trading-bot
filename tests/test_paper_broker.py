from tradingbot.brokers import PaperBroker
from tradingbot.models import Side


def test_buy_spends_cash_and_opens_position():
    b = PaperBroker(starting_cash=1000.0, fee_bps=10.0, slippage_bps=0.0)
    fill = b.buy("BTCUSDT", quote_amount=1000.0, ref_price=100.0, timestamp=1.0)

    assert fill is not None
    assert fill.side == Side.BUY
    # notional + fee must not exceed the cash we spent
    assert fill.quantity * fill.price + fill.fee <= 1000.0 + 1e-9
    assert b.cash < 1.0  # nearly all cash deployed
    assert b.position("BTCUSDT").quantity == fill.quantity


def test_roundtrip_pnl_positive_on_price_rise():
    b = PaperBroker(starting_cash=1000.0, fee_bps=0.0, slippage_bps=0.0)
    b.buy("BTCUSDT", quote_amount=1000.0, ref_price=100.0, timestamp=1.0)
    qty = b.position("BTCUSDT").quantity
    b.sell("BTCUSDT", qty, ref_price=110.0, timestamp=2.0)

    assert b.position("BTCUSDT").quantity == 0.0
    # +10% move with no fees -> ~1100 cash
    assert b.cash == 1100.0


def test_fees_and_slippage_cost_money():
    b = PaperBroker(starting_cash=1000.0, fee_bps=10.0, slippage_bps=5.0)
    b.buy("BTCUSDT", quote_amount=1000.0, ref_price=100.0, timestamp=1.0)
    qty = b.position("BTCUSDT").quantity
    b.sell("BTCUSDT", qty, ref_price=100.0, timestamp=2.0)
    # Flat price but costs on both sides -> ended with less than we started.
    assert b.cash < 1000.0


def test_sell_without_position_returns_none():
    b = PaperBroker(starting_cash=1000.0)
    assert b.sell("BTCUSDT", 1.0, ref_price=100.0, timestamp=1.0) is None


def test_cannot_spend_more_than_cash():
    b = PaperBroker(starting_cash=50.0, fee_bps=0.0, slippage_bps=0.0)
    fill = b.buy("BTCUSDT", quote_amount=1000.0, ref_price=100.0, timestamp=1.0)
    assert fill is not None
    assert b.cash >= -1e-9  # never goes negative
