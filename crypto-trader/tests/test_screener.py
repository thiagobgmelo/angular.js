from app.screener import DemoScreener, select_universe

CFG = {
    "quote": "USDT",
    "min_quote_volume_24h": 20_000_000,
    "max_pairs": 5,
    "always_include": ["BTC/USDT", "ETH/USDT"],
    "exclude": [],
}


def market(base, quote="USDT", spot=True, active=True):
    return {"base": base, "quote": quote, "spot": spot, "active": active}


def build_fixture():
    """Payload no formato de load_markets()/fetch_tickers() do ccxt."""
    markets = {
        "BTC/USDT": market("BTC"),
        "ETH/USDT": market("ETH"),
        "SOL/USDT": market("SOL"),
        "XRP/USDT": market("XRP"),
        "ADA/USDT": market("ADA"),
        "DOGE/USDT": market("DOGE"),
        "PEPE/USDT": market("PEPE"),          # novo demais (history_ok=False)
        "USDC/USDT": market("USDC"),          # stablecoin
        "BTCUP/USDT": market("BTCUP"),        # alavancado
        "BTC/EUR": market("BTC", quote="EUR"),  # quote errada
        "OLD/USDT": market("OLD", active=False),  # deslistada
        "TINY/USDT": market("TINY"),          # sem volume
        "BTC/USDT:USDT": {"base": "BTC", "quote": "USDT", "spot": False, "active": True},
    }
    tickers = {
        "BTC/USDT": {"quoteVolume": 2_000_000_000},
        "ETH/USDT": {"quoteVolume": 1_000_000_000},
        "SOL/USDT": {"quoteVolume": 500_000_000},
        "XRP/USDT": {"quoteVolume": 400_000_000},
        "ADA/USDT": {"quoteVolume": 100_000_000},
        "DOGE/USDT": {"quoteVolume": 90_000_000},
        "PEPE/USDT": {"quoteVolume": 300_000_000},
        "USDC/USDT": {"quoteVolume": 3_000_000_000},
        "BTCUP/USDT": {"quoteVolume": 50_000_000},
        "TINY/USDT": {"quoteVolume": 1_000_000},
    }
    history = {s: True for s in tickers}
    history["PEPE/USDT"] = False
    return markets, tickers, history


def symbols(universe):
    return [u["symbol"] for u in universe]


def test_always_include_pinned_first():
    markets, tickers, history = build_fixture()
    universe = select_universe(markets, tickers, history, CFG)
    assert symbols(universe)[:2] == ["BTC/USDT", "ETH/USDT"]
    assert all(u["pinned"] for u in universe[:2])


def test_structural_exclusions():
    markets, tickers, history = build_fixture()
    got = symbols(select_universe(markets, tickers, history, CFG))
    assert "USDC/USDT" not in got       # stablecoin
    assert "BTCUP/USDT" not in got      # token alavancado
    assert "BTC/EUR" not in got         # quote errada
    assert "OLD/USDT" not in got        # inativa
    assert "BTC/USDT:USDT" not in got   # não-spot (futuro)


def test_volume_and_history_filters():
    markets, tickers, history = build_fixture()
    got = symbols(select_universe(markets, tickers, history, CFG))
    assert "TINY/USDT" not in got   # abaixo do volume mínimo
    assert "PEPE/USDT" not in got   # sem maturidade de histórico


def test_ranking_and_cap():
    markets, tickers, history = build_fixture()
    universe = select_universe(markets, tickers, history, CFG)
    # max_pairs=5: 2 pinned + top 3 por volume (SOL 500M, XRP 400M, ADA 100M)
    assert symbols(universe) == ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT"]
    assert len(universe) <= CFG["max_pairs"]


def test_exclude_denylist_beats_everything():
    markets, tickers, history = build_fixture()
    cfg = dict(CFG, exclude=["SOL/USDT", "BTC/USDT"])
    got = symbols(select_universe(markets, tickers, history, cfg))
    assert "SOL/USDT" not in got
    assert "BTC/USDT" not in got  # até pinned respeita a denylist


def test_demo_screener_returns_universe():
    universe = DemoScreener(dict(CFG, max_pairs=8)).screen()
    got = symbols(universe)
    assert got[0] == "BTC/USDT"
    assert len(got) == 8
    assert len(set(got)) == 8
