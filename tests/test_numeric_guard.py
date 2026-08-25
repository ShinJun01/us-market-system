"""Numeric Guard 테스트.

이 테스트가 통과한다는 것 = "AI가 지어낸 숫자가 브리핑을 통과할 수 없다"의 증명.
현행 국내장 시스템의 최대 취약점을 막는 장치이므로 가장 엄격하게 검사한다.
"""
import pytest

from usv2.brief import numeric_guard as ng

FACTS = {
    "regime": {"date": "2026-08-24", "regime": "BULL", "total": 71.0,
               "pillar_a": 21.0, "pillar_b": 15.0, "pillar_c": 20.0, "pillar_d": 15.0,
               "exposure_cap": 0.80, "per_name_cap": 0.12, "multiplier": 1.00},
    "sectors": {"strong": [{"sector": "XLK", "z": 2.14, "rank": 1}]},
}


def test_accurate_text_passes():
    txt = "REGIME BULL, 총점 71.0/100. Pillar A 21.0, 노출 상한 80%. XLK z=2.14."
    assert ng.check(txt, FACTS).passed


def test_hallucinated_number_blocked():
    """실제 데이터에 없는 VIX 값을 지어낸 경우."""
    txt = "REGIME BULL, 총점 71.0. VIX는 14.89로 안정적입니다."
    res = ng.check(txt, FACTS)
    assert not res.passed
    assert "14.89" in res.unknown


def test_subtly_wrong_number_blocked():
    """71.0을 72.0으로 살짝 틀리게 쓴 경우 — 가장 잡기 어렵고 가장 위험하다."""
    res = ng.check("총점 72.0입니다.", FACTS)
    assert not res.passed


def test_percent_representation_allowed():
    """0.80을 80%로 쓰는 것은 정당한 표기 변형."""
    assert ng.check("노출 상한은 80%입니다.", FACTS).passed


def test_rounding_variants_allowed():
    assert ng.check("총점 71점, z-score 2.1", FACTS).passed


def test_dates_and_times_exempt():
    txt = "2026-08-24 종가 기준, 09:30 ET 개장. 총점 71.0."
    assert ng.check(txt, FACTS).passed


def test_section_and_version_markers_exempt():
    txt = "## [1] 시장 상태 — Q1. 전략 S1, 게이트 V3, regime.yaml v1.0.0. 총점 71.0"
    assert ng.check(txt, FACTS).passed


def test_enforce_raises_on_fail():
    with pytest.raises(ValueError, match="미검증"):
        ng.enforce("VIX 33.2 급등", FACTS)


def test_enforce_returns_text_on_pass():
    txt = "총점 71.0"
    assert ng.enforce(txt, FACTS) == txt


def test_fail_closed_on_empty_facts():
    """참조 데이터가 없으면 임의 수치는 전부 차단되어야 한다."""
    res = ng.check("VIX 17.4, 금리 4.28%", {})
    assert not res.passed
    assert len(res.unknown) >= 2


def test_rounding_coincidence_does_not_leak():
    """회귀 테스트.

    버그 이력: 환각 수치 14.89를 0자리 반올림하면 15가 되고, 그것이 실제
    pillar_b=15.0과 우연히 일치해 통과했다. 토큰 쪽 반올림 확장을 제거해 수정.
    """
    for hallucinated in ["14.89", "15.4", "20.6", "0.79"]:
        res = ng.check(f"측정값은 {hallucinated}입니다.", FACTS)
        assert not res.passed, f"{hallucinated}가 반올림 우연으로 통과함"


def test_known_limitation_small_integers():
    """알려진 한계 (의도적).

    0~12 범위의 맨 정수는 문장 구조상 불가피해서 화이트리스트로 통과시킨다.
    따라서 "VIX 11" 같은 환각은 잡히지 않는다.
    -> 완화책: 브리핑 템플릿에서 시장 수치를 항상 소수점 1자리 이상으로 표기한다.
       (build.py가 f-string으로 강제하므로 실제 파이프라인에서는 노출되지 않는다)
    """
    assert ng.check("VIX 11", FACTS).passed        # 잡히지 않음 — 문서화된 한계
    assert not ng.check("VIX 11.3", FACTS).passed  # 소수 표기면 잡힘
