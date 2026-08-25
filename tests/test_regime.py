import numpy as np
import pandas as pd
import pytest

from usv2.engines.regime import (
    MarketData, apply_confirmation, classify_raw, compute_regime, latest_regime,
    pillar_a_trend, pillar_c_volatility, REGIME_ORDER,
)


def test_pillar_scores_within_bounds(market, cfg):
    df = compute_regime(market, cfg).dropna(subset=["total"])
    assert len(df) > 1000
    for col, cap in [("pillar_a", 25), ("pillar_b", 25), ("pillar_c", 25), ("pillar_d", 25)]:
        s = df[col].dropna()
        assert s.min() >= 0, f"{col} 음수"
        assert s.max() <= cap, f"{col} 상한 {cap} 초과: {s.max()}"
    assert df["total"].between(0, 100).all()


def test_all_regimes_are_valid_labels(market, cfg):
    df = compute_regime(market, cfg).dropna(subset=["regime"])
    assert set(df["regime"].unique()).issubset(set(REGIME_ORDER))


def test_strong_bull_requires_breadth(cfg):
    """총점이 높아도 Breadth가 낮으면 STRONG_BULL이 될 수 없다(분배 국면 방어)."""
    idx = pd.date_range("2024-01-01", periods=3)
    total = pd.Series([85.0, 85.0, 85.0], index=idx)
    breadth = pd.Series([10.0, 20.0, 17.9], index=idx)   # 기준 18
    lab = classify_raw(total, breadth, cfg)
    assert lab.iloc[0] == "BULL"
    assert lab.iloc[1] == "STRONG_BULL"
    assert lab.iloc[2] == "BULL"


def test_confirmation_blocks_single_day_flip():
    """하루짜리 노이즈로 레짐이 바뀌면 안 된다."""
    raw = pd.Series(["BULL"] * 5 + ["CAUTION"] + ["BULL"] * 5)
    conf = apply_confirmation(raw, days=2)
    assert (conf == "BULL").all()


def test_confirmation_allows_two_day_change():
    raw = pd.Series(["BULL"] * 5 + ["CAUTION"] * 4)
    conf = apply_confirmation(raw, days=2)
    assert conf.iloc[-1] == "CAUTION"
    assert conf.iloc[5] == "BULL"      # 첫날은 아직 미확인


def test_vix_spike_blocks_entry(market, cfg):
    df = compute_regime(market, cfg)
    spike = market.close["^VIX"].pct_change() > cfg["overrides"]["vix_1d_spike_pct"]
    hit = spike[spike].index
    if len(hit):
        assert df.loc[hit, "entry_blocked"].all()


def test_crash_demotes_regime(market, cfg):
    df = compute_regime(market, cfg)
    crash = market.close["SPY"].pct_change(5) <= cfg["overrides"]["spy_5d_drawdown_pct"]
    hit = crash[crash].index
    rank = {r: i for i, r in enumerate(REGIME_ORDER)}
    if len(hit):
        labs = df.loc[hit, "regime"].dropna()
        assert all(rank[x] <= rank["CAUTION"] for x in labs), "급락일에 CAUTION 이상 유지됨"


def test_no_lookahead_regime_is_stable_under_truncation(market, cfg):
    """과거 시점의 레짐이 나중 데이터에 의해 바뀌면 look-ahead다."""
    full = compute_regime(market, cfg)
    cut = 1500
    md_trunc = MarketData(close=market.close.iloc[:cut],
                          breadth=market.breadth.iloc[:cut])
    trunc = compute_regime(md_trunc, cfg)
    a = full["total"].iloc[:cut].dropna()
    b = trunc["total"].dropna()
    common = a.index.intersection(b.index)
    assert len(common) > 500
    pd.testing.assert_series_equal(a.loc[common], b.loc[common], check_names=False)


def test_latest_regime_schema(market, cfg):
    reg = latest_regime(compute_regime(market, cfg))
    for k in ("date", "regime", "total", "pillar_a", "exposure_cap", "multiplier"):
        assert k in reg
    assert 0 <= reg["total"] <= 100
    assert 0.0 <= reg["exposure_cap"] <= 1.0


def test_missing_spy_raises():
    md = MarketData(close=pd.DataFrame({"QQQ": [1, 2, 3.0]},
                                       index=pd.date_range("2024-01-01", periods=3)))
    with pytest.raises(ValueError, match="SPY"):
        compute_regime(md)
