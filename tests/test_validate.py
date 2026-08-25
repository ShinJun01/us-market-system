import datetime as dt
import numpy as np
import pandas as pd
import pytest

from usv2.data.providers import Bars
from usv2.data.validate import Severity, validate_all, ValidationReport, v1_schema, v3_range, v4_continuity


def mk(symbol="SPY", n=300, bad=None):
    idx = pd.bdate_range("2024-01-01", periods=n)
    c = pd.Series(400 + np.arange(n) * 0.1, index=idx)
    df = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99,
                       "close": c, "volume": 1e6}, index=idx)
    if bad:
        bad(df)
    return Bars(symbol, df, source=f"test:{symbol}",
                fetched_at=dt.datetime(2024, 1, 1))


def test_clean_data_passes():
    rep = ValidationReport()
    v1_schema(mk(), rep); v3_range(mk(), rep); v4_continuity(mk(), rep)
    assert not rep.halt


def test_v1_missing_column_fails():
    b = mk()
    df = b.df.drop(columns=["volume"])
    with pytest.raises(ValueError):
        Bars("SPY", df, "test", dt.datetime.now())


def test_v3_negative_price_fails():
    rep = ValidationReport()
    v3_range(mk(bad=lambda d: d.__setitem__("close", d["close"] * -1)), rep)
    assert rep.halt


def test_v3_high_below_low_fails():
    def corrupt(d):
        d.iloc[50, d.columns.get_loc("high")] = 1.0
    rep = ValidationReport()
    v3_range(mk(bad=corrupt), rep)
    assert rep.halt


def test_v3_vix_out_of_range_fails():
    rep = ValidationReport()
    v3_range(mk("^VIX", bad=lambda d: d.__setitem__("close", d["close"] * 0 + 500)), rep)
    assert rep.halt


def test_v4_index_split_error_halts():
    """지수 ETF가 하루 30% 이상 움직이면 시장이 아니라 데이터가 이상한 것."""
    def corrupt(d):
        d.iloc[100:, d.columns.get_loc("close")] *= 0.5   # 분할 미조정 시뮬레이션
    rep = ValidationReport()
    v4_continuity(mk("SPY", bad=corrupt), rep)
    assert rep.halt


def test_v6_missing_symbol_halts():
    rep = validate_all({"SPY": mk()}, ["SPY", "QQQ", "^VIX"],
                       ref_date=dt.date(2025, 2, 24))
    assert rep.halt
    assert any(c.gate == "V6" for c in rep.failures)


def test_v2_stale_data_halts():
    rep = validate_all({"SPY": mk()}, ["SPY"], ref_date=dt.date(2026, 8, 20))
    assert rep.halt
    assert any(c.gate == "V2" for c in rep.failures)


def test_v5_missing_secondary_is_warn_not_fail():
    """Phase 1에서는 2차 소스 부재가 WARN. Phase 3 전에는 FAIL로 승격해야 함."""
    last = pd.bdate_range("2024-01-01", periods=300)[-1].date()
    rep = validate_all({"SPY": mk()}, ["SPY"], ref_date=last)
    assert not rep.halt
    assert any(c.gate == "V5" and c.severity is Severity.WARN for c in rep.checks)
