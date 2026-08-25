"""엔드투엔드 통합 테스트: 수집 -> 검증 -> 레짐 -> 섹터 -> 브리핑 -> 가드."""
import pandas as pd

from usv2.brief import build as brief_build, numeric_guard
from usv2.engines import sector
from usv2.engines.regime import compute_regime, latest_regime


def test_full_pipeline_produces_guarded_brief(market, cfg):
    regime_df = compute_regime(market, cfg)
    reg = latest_regime(regime_df)
    sectors = sector.top_bottom(market.close, n=3)
    meta = {"provider": "synthetic", "validation": "PASS",
            "config_version": cfg.version, "breadth_mode": "proxy"}

    text = brief_build.render(reg, sectors, meta)
    facts = brief_build.build_facts(reg, sectors, meta)
    res = numeric_guard.check(text, facts)

    assert res.passed, res.report()
    assert "REGIME" in text
    assert len(text) > 800


def test_brief_does_not_fabricate_stock_candidates(market, cfg):
    """Phase 1 브리핑은 종목 후보를 만들어내면 안 된다.

    검증되지 않은 전략으로 종목을 추천하는 것은
    '백테스트되지 않은 전략을 실전 전략이라 부르지 않는다' 원칙 위반이다.
    """
    reg = latest_regime(compute_regime(market, cfg))
    text = brief_build.render(reg, sector.top_bottom(market.close), {})
    assert "Phase 1에서는 종목 선정을 수행하지 않습니다" in text


def test_sector_ranking_covers_all_sectors(market):
    df = sector.sector_ranking(market.close)
    assert len(df) == 11
    assert df["rank"].tolist() == list(range(1, 12))
    assert df["rs"].is_monotonic_decreasing


def test_regime_history_has_variety(market, cfg):
    """레짐이 한 값에 고정되면 엔진이 아무것도 구분하지 못하는 것이다."""
    df = compute_regime(market, cfg).dropna(subset=["regime"])
    counts = df["regime"].value_counts(normalize=True)
    assert len(counts) >= 3, f"레짐 종류 부족: {counts.to_dict()}"
    assert counts.max() < 0.90, f"단일 레짐 편중: {counts.to_dict()}"
