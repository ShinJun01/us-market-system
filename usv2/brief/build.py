"""
브리핑 조립 (설계서 13장).

핵심 설계 사상
--------------
브리핑에 등장하는 모든 숫자는 **코드가 계산한 값**이다.
LLM은 이 파일이 만든 FACT/SIGNAL 딕셔너리를 받아 INTERPRETATION 섹션만 쓴다.
그리고 그 결과물은 numeric_guard를 통과해야만 발행된다.

Phase 1 범위: 섹션 [1] 레짐, [2] 오늘의 변수, [3] 섹터.
종목 후보/주문표([4]~[6])는 Phase 2 Stock Screening Engine이 붙은 뒤 활성화된다.
빈 섹션을 그럴듯하게 채우지 않는다 — 없는 건 없다고 쓴다.
"""
from __future__ import annotations

import datetime as dt

from usv2.engines.regime import ALLOWED_STRATEGIES

REGIME_KR = {
    "STRONG_BULL": "강한 상승", "BULL": "상승", "NEUTRAL": "중립",
    "CAUTION": "주의", "RISK_OFF": "위험회피", "CRISIS": "위기",
}


def build_facts(reg: dict, sectors: dict, meta: dict) -> dict:
    """SIGNAL 계층 딕셔너리. numeric_guard의 허용 수치 소스이기도 하다."""
    return {"regime": reg, "sectors": sectors, "meta": meta, "pillar_max": 25}


def render(reg: dict, sectors: dict, meta: dict) -> str:
    """결정론적 브리핑 본문 생성 (LLM 미사용 부분)."""
    d = dt.date.today().isoformat()
    proxy_warn = meta.get("breadth_mode") == "proxy"
    synth = meta.get("provider") == "synthetic"

    L = []
    L.append(f"# US Market Brief — {reg['date']} (T-1 종가 기준)")
    L.append("")
    L.append(f"데이터 소스: `{meta.get('provider')}` | 검증: {meta.get('validation')} | "
             f"regime.yaml v{meta.get('config_version')}")
    L.append("")

    if synth:
        L.append("> **합성 데이터 실행입니다. 매매 판단에 사용하지 마십시오.**")
        L.append("> 파이프라인 동작 확인 목적으로만 생성되었습니다.")
        L.append("")

    # [1] 시장 상태 — Q1
    L.append("## [1] 시장 상태 — Q1")
    L.append("")
    chg = " (전일 대비 변동)" if reg["changed"] else ""
    L.append(f"**REGIME: {reg['regime']}** ({REGIME_KR.get(reg['regime'], '')}) — "
             f"총점 {reg['total']}/100{chg}")
    L.append("")
    pb = reg["pillar_b"] if reg["pillar_b"] is not None else "N/A"
    L.append(f"| Pillar | 점수 |")
    L.append(f"|---|---|")
    L.append(f"| A. Trend | {reg['pillar_a']} / 25 |")
    L.append(f"| B. Breadth | {pb} / 25{' *(프록시)*' if proxy_warn else ''} |")
    L.append(f"| C. Volatility | {reg['pillar_c']} / 25 |")
    L.append(f"| D. Risk Appetite | {reg['pillar_d']} / 25 |")
    L.append("")
    L.append(f"- 전일 레짐: {reg['regime_prev']} (총점 {reg['total_prev']})")
    if reg.get("regime_raw") and reg["regime_raw"] != reg["regime"]:
        L.append(f"- **확인 대기**: 원시 판정은 {reg['regime_raw']}이나 "
                 f"2일 확인 규칙 미충족으로 {reg['regime']} 유지")
    L.append(f"- 총 노출 상한: {reg['exposure_cap']:.0%} | 종목당 상한: {reg['per_name_cap']:.0%}")
    L.append(f"- 레짐 승수: {reg['multiplier']}")
    L.append("")

    if proxy_warn:
        L.append("> Breadth가 프록시 모드입니다. 지수 내부 붕괴 감지 정확도가 낮으므로 "
                 "Pillar B 점수의 신뢰도는 제한적입니다. Phase 2 전 compute 모드 전환 필요.")
        L.append("")

    # [2] 오늘의 변수 — Q2
    L.append("## [2] 오늘의 최대 변수 — Q2")
    L.append("")
    if reg["entry_blocked"]:
        L.append(f"**신규 진입 차단 상태** — 발동 사유: `{reg['override_reason']}`")
    else:
        L.append("오버라이드 규칙 미발동. 신규 진입 가능 상태.")
    L.append("")
    L.append("> Event Risk Engine(실적/FOMC/CPI 캘린더)은 Phase 2에서 연결됩니다. "
             "현재는 매크로 이벤트가 자동 반영되지 않으므로 수동 확인이 필요합니다.")
    L.append("")

    # [3] 섹터 — Q3, Q4
    L.append("## [3] 섹터 — Q3, Q4")
    L.append("")
    if sectors.get("strong"):
        L.append("**강세 (RS 상위)**")
        L.append("")
        L.append("| 순위 | 섹터 | z-score |")
        L.append("|---|---|---|")
        for s in sectors["strong"]:
            L.append(f"| {s['rank']} | {s['name']} ({s['sector']}) | {s['z']:+.2f} |")
        L.append("")
        L.append("**약세 (RS 하위)**")
        L.append("")
        L.append("| 순위 | 섹터 | z-score |")
        L.append("|---|---|---|")
        for s in sectors["weak"]:
            L.append(f"| {s['rank']} | {s['name']} ({s['sector']}) | {s['z']:+.2f} |")
        L.append("")
    else:
        L.append("섹터 데이터 없음.")
        L.append("")

    # [5] 매매 환경 — Q6, Q7
    L.append("## [5] 오늘 매매 환경 — Q6, Q7")
    L.append("")
    allowed = ALLOWED_STRATEGIES.get(reg["regime"], [])
    if reg["entry_blocked"] or not allowed:
        L.append("**허용 전략: 없음. 신규 진입 금지.**")
    else:
        L.append(f"**허용 전략:** {', '.join(allowed)}")
        blocked = [s for k, v in ALLOWED_STRATEGIES.items() if k == reg["regime"] for s in []]
        L.append("")
        L.append("(전략 구현은 Phase 2. 현재는 레짐이 허용하는 범위만 표시합니다.)")
    L.append("")

    # [6] 종목 — Phase 2
    L.append("## [6] 감시 종목 및 주문 계획 — Q8~Q12")
    L.append("")
    L.append("**Phase 1에서는 종목 선정을 수행하지 않습니다.**")
    L.append("")
    L.append("Stock Screening Engine(설계서 5장)은 시점별 구성종목 데이터가 확보된 "
             "Phase 2에서 구현됩니다. 검증되지 않은 종목 후보를 표시하는 것은 "
             "\"백테스트되지 않은 전략을 실전 전략이라 부르지 않는다\"는 원칙에 위배됩니다.")
    L.append("")

    # [7] 오늘 거래 여부 — Q13
    L.append("## [7] 오늘 거래하지 않아야 하는가? — Q13")
    L.append("")
    if reg["entry_blocked"]:
        L.append("**YES — 거래하지 않습니다.** 오버라이드 규칙이 발동했습니다.")
    elif reg["regime"] in ("RISK_OFF", "CRISIS"):
        L.append(f"**YES — 거래하지 않습니다.** 레짐 {reg['regime']}은 신규 진입 불가 구간입니다.")
    else:
        L.append(f"**NO — 레짐상 매매 가능 구간입니다** (노출 상한 {reg['exposure_cap']:.0%}).")
        L.append("")
        L.append("단, Phase 1 시스템은 종목 신호를 생성하지 않으므로 "
                 "이 브리핑만으로 매매해서는 안 됩니다.")
    L.append("")
    L.append("---")
    L.append("")
    L.append("*본 브리핑의 모든 수치는 코드가 계산한 값이며 Numeric Guard 검증을 거쳤습니다. "
             "AI 해석 섹션은 포함되어 있지 않습니다(Phase 2에서 추가).*")
    return "\n".join(L)
