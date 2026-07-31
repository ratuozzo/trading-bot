"""Feed URL construction and message parsing, all offline."""

import json

import pytest

from tradingbot.config import FeedConfig
from tradingbot.feeds import BinanceWebSocketFeed, BybitFeed, OKXFeed, build_feed


# -- URLs ------------------------------------------------------------------

def test_binance_futures_url_uses_fstream():
    f = BinanceWebSocketFeed("btcusdt", "aggTrade", market="futures")
    assert f.url == "wss://fstream.binance.com/ws/btcusdt@aggTrade"


def test_binance_spot_url():
    f = BinanceWebSocketFeed("ethusdt", "trade", market="spot")
    assert f.url == "wss://stream.binance.com:9443/ws/ethusdt@trade"


def test_binance_rejects_bad_stream_and_market():
    with pytest.raises(ValueError):
        BinanceWebSocketFeed("btcusdt", "candles")
    with pytest.raises(ValueError):
        BinanceWebSocketFeed("btcusdt", "trade", market="options")


def test_factory_builds_each_exchange():
    assert isinstance(
        build_feed(FeedConfig(exchange="binance", stream="aggTrade")),
        BinanceWebSocketFeed,
    )
    assert isinstance(
        build_feed(FeedConfig(exchange="bybit", symbol="BTCUSDT", stream="trade")),
        BybitFeed,
    )
    assert isinstance(
        build_feed(FeedConfig(exchange="okx", symbol="BTC-USDT-SWAP", stream="trade")),
        OKXFeed,
    )
    with pytest.raises(ValueError):
        build_feed(FeedConfig(exchange="nasdaq"))


# -- parsing ---------------------------------------------------------------

def test_binance_aggtrade_parse():
    f = BinanceWebSocketFeed("btcusdt", "aggTrade", market="futures")
    msg = json.dumps(
        {"e": "aggTrade", "E": 1700000000200, "s": "BTCUSDT",
         "p": "43210.55", "q": "0.015", "T": 1700000000120}
    )
    tick = f._parse(msg)
    assert tick.price == 43210.55
    assert tick.quantity == 0.015
    assert tick.timestamp == pytest.approx(1700000000.12)


def test_binance_bookticker_midprice():
    f = BinanceWebSocketFeed("btcusdt", "bookTicker")
    tick = f._parse(json.dumps(
        {"u": 1, "s": "BTCUSDT", "b": "100.0", "B": "1", "a": "102.0", "A": "2"}
    ))
    assert tick.price == 101.0
    assert tick.bid == 100.0 and tick.ask == 102.0
    assert tick.spread == 2.0


def test_bybit_public_trade_parse():
    f = BybitFeed("BTCUSDT", "trade")
    msg = {
        "topic": "publicTrade.BTCUSDT",
        "ts": 1700000000300,
        "data": [
            {"T": 1700000000100, "s": "BTCUSDT", "S": "Buy", "v": "0.5", "p": "43000.0"},
            {"T": 1700000000250, "s": "BTCUSDT", "S": "Buy", "v": "0.2", "p": "43005.5"},
        ],
    }
    tick = f._parse(msg)
    # uses the most recent trade in the batch
    assert tick.price == 43005.5
    assert tick.quantity == 0.2
    assert tick.timestamp == pytest.approx(1700000000.25)


def test_bybit_orderbook_keeps_last_known_side():
    f = BybitFeed("BTCUSDT", "bookTicker")
    snap = {
        "topic": "orderbook.1.BTCUSDT", "ts": 1700000000000,
        "data": {"s": "BTCUSDT", "b": [["100.0", "1"]], "a": [["101.0", "1"]]},
    }
    assert f._parse(snap).price == 100.5

    # A delta touching only the ask must retain the previous bid.
    delta = {
        "topic": "orderbook.1.BTCUSDT", "ts": 1700000000100,
        "data": {"s": "BTCUSDT", "b": [], "a": [["103.0", "1"]]},
    }
    tick = f._parse(delta)
    assert tick.bid == 100.0 and tick.ask == 103.0


def test_bybit_ignores_zero_size_removal():
    f = BybitFeed("BTCUSDT", "bookTicker")
    f._parse({"topic": "orderbook.1.BTCUSDT", "ts": 1,
              "data": {"b": [["100.0", "1"]], "a": [["101.0", "1"]]}})
    # size 0 means the level was removed; keep the old bid until a real one lands
    tick = f._parse({"topic": "orderbook.1.BTCUSDT", "ts": 2,
                     "data": {"b": [["100.0", "0"]], "a": [["101.0", "1"]]}})
    assert tick.bid == 100.0


def test_okx_trades_parse():
    f = OKXFeed("BTC-USDT-SWAP", "trade")
    msg = {
        "arg": {"channel": "trades", "instId": "BTC-USDT-SWAP"},
        "data": [{"instId": "BTC-USDT-SWAP", "px": "43100.1", "sz": "3",
                  "side": "buy", "ts": "1700000000500"}],
    }
    tick = f._parse(msg)
    assert tick.price == 43100.1
    assert tick.timestamp == pytest.approx(1700000000.5)


def test_okx_bbo_tbt_parse():
    f = OKXFeed("BTC-USDT-SWAP", "bookTicker")
    msg = {
        "arg": {"channel": "bbo-tbt", "instId": "BTC-USDT-SWAP"},
        "data": [{"bids": [["100.0", "1", "0", "1"]],
                  "asks": [["102.0", "1", "0", "1"]],
                  "ts": "1700000000000"}],
    }
    tick = f._parse(msg)
    assert tick.price == 101.0
    assert tick.bid == 100.0 and tick.ask == 102.0


def test_okx_subscribe_payload_shape():
    f = OKXFeed("BTC-USDT-SWAP", "bookTicker")
    assert f._subscribe_payload() == [
        {"op": "subscribe",
         "args": [{"channel": "bbo-tbt", "instId": "BTC-USDT-SWAP"}]}
    ]


@pytest.mark.parametrize(
    "feed",
    [
        BinanceWebSocketFeed("btcusdt", "trade"),
        BybitFeed("BTCUSDT", "trade"),
        OKXFeed("BTC-USDT-SWAP", "trade"),
    ],
)
def test_garbage_messages_are_ignored(feed):
    junk = {"unrelated": "payload"}
    parsed = feed._parse(json.dumps(junk) if isinstance(feed, BinanceWebSocketFeed) else junk)
    assert parsed is None


# -- MEXC futures ----------------------------------------------------------

def test_mexc_url_and_subscribe_shape():
    from tradingbot.feeds import MEXCFuturesFeed
    f = MEXCFuturesFeed("BTC_USDT")
    assert f.url == "wss://contract.mexc.com/edge"
    assert f._subscribe_payload() == [
        {"method": "sub.deal", "param": {"symbol": "BTC_USDT"}}
    ]
    # MEXC drops idle sockets at 60s; we ping well inside that.
    assert f.keepalive_interval <= 20
    assert f.keepalive_payload == {"method": "ping"}


@pytest.mark.parametrize(
    "given,expected",
    [("BTCUSDT", "BTC_USDT"), ("btc_usdt", "BTC_USDT"),
     ("ETHUSDT", "ETH_USDT"), ("SOL_USDT", "SOL_USDT")],
)
def test_mexc_symbol_normalisation(given, expected):
    from tradingbot.feeds import MEXCFuturesFeed
    assert MEXCFuturesFeed(given).symbol == expected


def test_mexc_push_deal_parse():
    from tradingbot.feeds import MEXCFuturesFeed
    f = MEXCFuturesFeed("BTC_USDT")
    tick = f._parse({
        "channel": "push.deal",
        "data": {"p": 43210.5, "v": 12, "T": 1, "t": 1700000000500},
        "symbol": "BTC_USDT",
        "ts": 1700000000600,
    })
    assert tick.price == 43210.5
    assert tick.quantity == 12
    assert tick.timestamp == pytest.approx(1700000000.5)


def test_mexc_ignores_other_channels():
    from tradingbot.feeds import MEXCFuturesFeed
    f = MEXCFuturesFeed("BTC_USDT")
    assert f._parse({"channel": "pong", "data": 1700000000}) is None
    assert f._parse({"channel": "rs.error", "data": "symbol not exist"}) is None
    assert f._parse({"channel": "push.deal"}) is None


def test_factory_builds_mexc_by_default():
    from tradingbot.feeds import MEXCFuturesFeed
    assert isinstance(build_feed(FeedConfig()), MEXCFuturesFeed)
