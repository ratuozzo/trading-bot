from tradingbot.brokers import PaperBroker
import pytest

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


# -- shorts ----------------------------------------------------------------

def test_short_profits_when_price_falls():
    b = PaperBroker(starting_cash=1000.0, fee_bps=0.0, slippage_bps=0.0)
    b.open("BTCUSDT", Side.SELL, 1000.0, 100.0, 1.0)
    qty = b.position("BTCUSDT").quantity
    assert qty == 10.0                       # $1000 at $100
    b.close("BTCUSDT", 90.0, 2.0)            # -10% move, in our favour
    assert b.cash == pytest.approx(1100.0)   # +$100 profit
    assert not b.position("BTCUSDT").is_open


def test_short_loses_when_price_rises():
    b = PaperBroker(starting_cash=1000.0, fee_bps=0.0, slippage_bps=0.0)
    b.open("BTCUSDT", Side.SELL, 1000.0, 100.0, 1.0)
    b.close("BTCUSDT", 110.0, 2.0)
    assert b.cash == pytest.approx(900.0)


def test_flat_price_short_roundtrip_returns_capital():
    b = PaperBroker(starting_cash=1000.0, fee_bps=0.0, slippage_bps=0.0)
    b.open("BTCUSDT", Side.SELL, 1000.0, 100.0, 1.0)
    b.close("BTCUSDT", 100.0, 2.0)
    assert b.cash == pytest.approx(1000.0)


def test_short_slippage_works_against_us_on_both_legs():
    """Selling to open fills lower, buying to close fills higher."""
    b = PaperBroker(starting_cash=1000.0, fee_bps=0.0, slippage_bps=10.0)
    open_fill = b.open("BTCUSDT", Side.SELL, 1000.0, 100.0, 1.0)
    assert open_fill.price < 100.0
    close_fill = b.close("BTCUSDT", 100.0, 2.0)
    assert close_fill.price > 100.0
    assert b.cash < 1000.0        # flat price, still lost to slippage


def test_short_fees_charged_on_both_legs():
    b = PaperBroker(starting_cash=1000.0, fee_bps=10.0, slippage_bps=0.0)
    o = b.open("BTCUSDT", Side.SELL, 1000.0, 100.0, 1.0)
    c = b.close("BTCUSDT", 100.0, 2.0)
    assert o.fee > 0 and c.fee > 0
    assert b.cash == pytest.approx(1000.0 - o.fee - c.fee, abs=1e-6)


def test_position_records_side_and_direction():
    b = PaperBroker(starting_cash=1000.0)
    b.open("BTCUSDT", Side.SELL, 500.0, 100.0, 1.0)
    pos = b.position("BTCUSDT")
    assert pos.side is Side.SELL
    assert pos.direction == -1
    assert pos.quantity > 0            # size stays positive
    assert pos.unrealized_pnl(90.0) > 0   # short gains as price falls
    assert pos.unrealized_pnl(110.0) < 0


def test_cannot_open_second_position():
    b = PaperBroker(starting_cash=1000.0)
    assert b.open("BTCUSDT", Side.BUY, 500.0, 100.0, 1.0) is not None
    assert b.open("BTCUSDT", Side.SELL, 100.0, 100.0, 2.0) is None


def test_equity_tracks_short_position():
    b = PaperBroker(starting_cash=1000.0, fee_bps=0.0, slippage_bps=0.0)
    b.open("BTCUSDT", Side.SELL, 1000.0, 100.0, 1.0)
    assert b.equity(100.0, "BTCUSDT") == pytest.approx(1000.0)
    assert b.equity(90.0, "BTCUSDT") == pytest.approx(1100.0)
    assert b.equity(110.0, "BTCUSDT") == pytest.approx(900.0)
