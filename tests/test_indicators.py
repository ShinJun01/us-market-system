import numpy as np
import pandas as pd
import pytest

from usv2.engines import indicators as ind


def test_rsi_all_up_is_100():
    s = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(s).iloc[-1] == pytest.approx(100.0)


def test_rsi_bounds():
    rng = np.random.default_rng(0)
    s = pd.Series(100 + np.cumsum(rng.standard_normal(500)))
    r = ind.rsi(s).dropna()
    assert r.between(0, 100).all()


def test_atr_positive_and_scales():
    rng = np.random.default_rng(1)
    c = pd.Series(100 + np.cumsum(rng.standard_normal(300)))
    h, l = c + 1.0, c - 1.0
    a = ind.atr(h, l, c).dropna()
    assert (a > 0).all()
    h2, l2 = c + 3.0, c - 3.0
    assert ind.atr(h2, l2, c).dropna().mean() > a.mean()


def test_pct_rank_range_and_extremes():
    s = pd.Series(np.arange(400, dtype=float))          # 단조 증가
    r = ind.pct_rank(s, 252).dropna()
    assert r.between(0, 100).all()
    assert r.iloc[-1] == pytest.approx(100.0)           # 항상 최고값


def test_rvol_no_self_reference():
    """당일 거래량이 분모 평균에 포함되면 안 된다."""
    v = pd.Series([100.0] * 30 + [1000.0])
    r = ind.rvol(v, 20)
    assert r.iloc[-1] == pytest.approx(10.0)


def test_no_lookahead_in_trailing_indicators():
    """t시점 값이 t+1 이후 데이터에 영향받지 않아야 한다."""
    rng = np.random.default_rng(3)
    s = pd.Series(100 + np.cumsum(rng.standard_normal(400)))
    full = ind.ema(s, 50)
    trunc = ind.ema(s.iloc[:300], 50)
    pd.testing.assert_series_equal(full.iloc[:300], trunc, check_names=False)


def test_zscore_cross_section_constant():
    s = pd.Series([5.0] * 10)
    assert (ind.zscore_cross_section(s) == 0).all()


def test_max_drawdown():
    eq = pd.Series([100, 120, 90, 130.0])
    assert ind.max_drawdown(eq) == pytest.approx(-0.25)
