"""Guards on the shipped config.yaml.

These exist because config.yaml silently overrides the dataclass defaults.
A default was once tightened in code while the YAML kept the old value, and
the result was that one position consumed 95% of the account and the
concurrent-position cap never bound — the exact thing it was added to stop.
"""

import os

import pytest

from tradingbot.config import Config

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config.yaml")


@pytest.fixture(scope="module")
def cfg():
    return Config.load(CONFIG)


def test_position_size_leaves_room_for_the_other_positions(cfg):
    """No single entry may claim more than its fair share of the book."""
    fair_share = 1.0 / cfg.risk.max_concurrent_positions
    assert cfg.risk.order_size_pct <= fair_share + 1e-9, (
        f"order_size_pct={cfg.risk.order_size_pct} with "
        f"max_concurrent_positions={cfg.risk.max_concurrent_positions} starves "
        "later signals — the first entry eats the cash."
    )


def test_all_configured_positions_can_actually_open(cfg):
    """Walk the compounding sizes and check the last one clears min_notional."""
    cash = cfg.broker.starting_cash
    for _ in range(cfg.risk.max_concurrent_positions):
        take = cash * cfg.risk.order_size_pct
        assert take >= cfg.risk.min_notional
        cash -= take
    assert cash > 0


def test_take_profit_clears_the_round_trip(cfg):
    """A winning trade must actually be a win after costs."""
    cost_bp = 2 * cfg.broker.fee_bps + cfg.broker.slippage_bps
    assert cfg.strategy.take_profit * 10000 > cost_bp, (
        "take_profit does not cover the round trip; every 'win' loses money."
    )


def test_break_even_win_rate_is_reachable(cfg):
    cost_bp = 2 * cfg.broker.fee_bps + cfg.broker.slippage_bps
    net_win = cfg.strategy.take_profit * 10000 - cost_bp
    net_loss = cfg.strategy.stop_loss * 10000 + cost_bp
    required = net_loss / (net_win + net_loss)
    assert required < 0.70, f"needs a {required:.0%} win rate — not realistic"


def test_backend_does_not_ship_a_public_bind_without_a_token(cfg):
    if cfg.server.host not in ("127.0.0.1", "localhost", "::1"):
        assert cfg.server.token, "public bind requires TB_API_TOKEN"
