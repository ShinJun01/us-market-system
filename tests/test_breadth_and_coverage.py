"""breadth 계산 및 Pillar 커버리지 정규화 회귀 테스트.

두 버그 이력에 대한 방어선:
  1. breadth 분모가 500 고정이라 미상장 종목이 "50일선 아래"로 집계됨
  2. 입력 심볼이 빠지면 Pillar 만점이 줄어드는데 임계값은 100점 척도라 총점이 밀림
"""
import numpy as np
import pandas as pd
import pytest

from usv2.data.ingest import _compute_breadth, _BREADTH_MIN_NAMES
from usv2.engines.regime import MarketData, compute_regime


def _panel(n_listed: int, frac_above: float, n_total: int = 500, days: int = 300):
    """n_total 종목 중 n_listed개만 상장, 그중 frac_above 비율이 우상향."""
    idx = pd.bdate_range("2020-01-01", periods=days)
    data = {}
    n_up = int(n_listed * frac_above)
    for i in range(n_total):
        if i >= n_listed:
            data[f"S{i}"] = np.full(days, np.nan)
        elif i < n_up:
            data[f"S{i}"] = 100 + np.arange(days) * 0.5      # 상승 -> 50일선 위
        else:
            data[f"S{i}"] = 200 - np.arange(days) * 0.3      # 하락 -> 50일선 아래
    return pd.DataFrame(data, index=idx)


def test_breadth_denominator_excludes_unlisted(monkeypatch):
    """미상장 종목(NaN)이 분모에 들어가면 breadth가 과소평가된다."""
    panel = _panel(n_listed=350, frac_above=0.75)
    monkeypatch.setattr("usv2.data.ingest._universe_close", lambda *a, **k: panel)

    b = _compute_breadth({}, "2020-01-01", None)
    got = b["pct_above_50dma"].dropna().iloc[-1]

    assert got == pytest.approx(75.0, abs=1.0), f"기대 75%, 실제 {got:.1f}%"
    # 버그 버전이었다면 350/500 * 75% = 52.5% 가 나왔다
    assert got > 60.0


def test_breadth_suppressed_when_too_few_names(monkeypatch):
    """유효 종목이 적으면 값을 만들어내지 않고 NaN을 반환해야 한다."""
    panel = _panel(n_listed=_BREADTH_MIN_NAMES - 50, frac_above=0.8)
    monkeypatch.setattr("usv2.data.ingest._universe_close", lambda *a, **k: panel)

    b = _compute_breadth({}, "2020-01-01", None)
    assert b["pct_above_50dma"].isna().all()


def test_pillar_normalized_when_input_missing(market, cfg):
    """^VIX9D/^VIX3M가 없어도 총점 척도가 100을 유지해야 한다."""
    full = compute_regime(market, cfg).dropna(subset=["total"])

    reduced_cols = [c for c in market.close.columns if c not in ("^VIX9D", "^VIX3M")]
    md2 = MarketData(close=market.close[reduced_cols], breadth=market.breadth)
    part = compute_regime(md2, cfg).dropna(subset=["total"])

    assert part["pillar_c"].max() > 20, "Pillar C가 17점에 갇힘 - 정규화 미작동"
    assert part["total"].max() > 90, f"총점 최대 {part['total'].max():.1f} - 척도 붕괴"
    # 평균 총점이 8점씩 밀리면 레짐이 통째로 강등된다
    assert abs(part["total"].mean() - full["total"].mean()) < 5


def test_coverage_is_reported(market, cfg):
    """정보가 부족한 축은 조용히 넘어가지 말고 커버리지를 남겨야 한다."""
    reduced = [c for c in market.close.columns if c not in ("^VIX9D", "^VIX3M")]
    md2 = MarketData(close=market.close[reduced], breadth=market.breadth)
    df = compute_regime(md2, cfg)

    cov = df["pillar_c_coverage"].dropna().iloc[-1]
    assert cov == pytest.approx(17 / 25, abs=0.01), f"커버리지 {cov:.2f}"
    assert df["pillar_a_coverage"].dropna().iloc[-1] == pytest.approx(1.0)
