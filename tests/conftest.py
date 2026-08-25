import sys
from pathlib import Path
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from usv2.data.providers import SyntheticProvider
from usv2.data.ingest import _proxy_breadth, required_symbols, load_config
from usv2.engines.regime import MarketData, RegimeConfig


@pytest.fixture(scope="session")
def cfg():
    return RegimeConfig.load(ROOT / "config/regime.yaml")


@pytest.fixture(scope="session")
def bars():
    p = SyntheticProvider(seed=7)
    syms = required_symbols(load_config(ROOT / "config/data_sources.yaml"))
    return p.fetch_many(syms, start="2015-01-01", end="2026-08-20")


@pytest.fixture(scope="session")
def market(bars):
    close = pd.DataFrame({s: b.df["close"] for s, b in bars.items()}).sort_index()
    br = _proxy_breadth(close)
    return MarketData(close=close, breadth=br)
