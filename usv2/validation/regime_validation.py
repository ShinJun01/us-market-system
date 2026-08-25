"""
레짐 유효성 검증 (설계서 L11-C, Phase 1 완료 조건).

핵심 질문: **레짐 라벨이 실제로 미래 수익률을 구분하는가?**

레짐 엔진은 시스템 최상단이라 오류가 전부로 전파된다. 검증 없이 아래 레이어를
만들면, 나중에 손실이 났을 때 "전략이 나쁜 건지 레짐 판별이 고장난 건지"를
영영 구분할 수 없다.

합격 조건: 레짐별 후행 수익률 평균이 단조 정렬되어야 한다.
  STRONG_BULL > BULL > NEUTRAL > CAUTION > RISK_OFF

이게 성립하지 않으면 config/regime.yaml의 가중치를 수정한다.
!!! 단, 성과 최대화가 아니라 단조성 회복을 목표로만 조정한다. !!!
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from usv2.engines.indicators import forward_return  # 사후 검증 전용 (look-ahead 주의)

REGIME_SEQ = ["STRONG_BULL", "BULL", "NEUTRAL", "CAUTION", "RISK_OFF", "CRISIS"]


def forward_return_by_regime(regime_df: pd.DataFrame, spy_close: pd.Series,
                             horizons=(5, 10, 20)) -> pd.DataFrame:
    """레짐 라벨별 후행 수익률 분포."""
    df = pd.DataFrame({"regime": regime_df["regime"]})
    for h in horizons:
        df[f"fwd_{h}d"] = forward_return(spy_close, h)
    df = df.dropna(subset=["regime"])

    rows = []
    for reg in REGIME_SEQ:
        sub = df[df["regime"] == reg]
        if sub.empty:
            continue
        rec = {"regime": reg, "days": len(sub),
               "share": len(sub) / len(df)}
        for h in horizons:
            col = sub[f"fwd_{h}d"].dropna()
            rec[f"mean_{h}d"] = col.mean() if len(col) else np.nan
            rec[f"median_{h}d"] = col.median() if len(col) else np.nan
            rec[f"hit_{h}d"] = (col > 0).mean() if len(col) else np.nan
        rows.append(rec)
    return pd.DataFrame(rows).set_index("regime")


def monotonicity_score(table: pd.DataFrame, horizon: int = 20) -> dict:
    """단조성 검정.

    Spearman 순위상관: 레짐 서열과 평균 후행수익률 서열이 얼마나 일치하는가.
    +1.0 = 완벽한 단조 정렬, 0 = 무관, -1.0 = 완전 역전(엔진 고장).
    """
    col = f"mean_{horizon}d"
    present = [r for r in REGIME_SEQ if r in table.index and pd.notna(table.loc[r, col])]
    if len(present) < 3:
        return {"passed": False, "spearman": np.nan,
                "reason": f"레짐 종류 부족 ({len(present)}종). 데이터 기간을 늘리세요."}

    expected = pd.Series(range(len(present), 0, -1), index=present, dtype=float)
    actual = table.loc[present, col].astype(float)
    rho = expected.corr(actual, method="spearman")

    strict = all(
        actual.loc[present[i]] >= actual.loc[present[i + 1]]
        for i in range(len(present) - 1)
    )
    return {
        "passed": bool(rho >= 0.8),
        "strictly_monotonic": bool(strict),
        "spearman": float(rho),
        "regimes_present": present,
        "horizon": horizon,
    }


def render_report(table: pd.DataFrame, mono: dict, meta: dict) -> str:
    lines = [
        "# 레짐 유효성 검증 리포트",
        "",
        f"- 데이터 소스: `{meta.get('provider')}`",
        f"- 기간: {meta.get('start')} ~ {meta.get('end')}",
        f"- regime.yaml 버전: {meta.get('config_version')}",
        f"- breadth 모드: {meta.get('breadth_mode')}",
        "",
    ]
    if meta.get("provider") == "synthetic":
        lines += [
            "> **경고: 합성 데이터입니다.**",
            "> 아래 수치는 파이프라인이 작동한다는 것만 보여줄 뿐,",
            "> 시장에 대한 어떤 증거도 아닙니다. 실제 검증은 로컬에서",
            "> `provider: yahoo`로 다시 실행해야 합니다.",
            "",
        ]

    lines += ["## 레짐별 후행 수익률", "", table.to_markdown(floatfmt=".4f"), ""]
    lines += [
        "## 단조성 검정",
        "",
        f"- Spearman 순위상관: **{mono.get('spearman'):.3f}** (기준 ≥ 0.80)",
        f"- 엄격 단조: {mono.get('strictly_monotonic')}",
        f"- 판정: **{'PASS' if mono.get('passed') else 'FAIL'}**",
        "",
    ]
    if not mono.get("passed"):
        lines += [
            "### FAIL 시 조치",
            "",
            "1. breadth가 proxy 모드라면 compute로 전환 후 재실행 (가장 흔한 원인)",
            "2. Pillar 가중치를 조정 — 단, **단조성 회복만을 목표로** 한다.",
            "   수익률 최대화 방향으로 튜닝하면 그 순간 과최적화가 시작된다.",
            "3. 데이터 기간이 짧아 특정 레짐 표본이 부족한지 확인",
            "",
            "**이 검증을 통과하기 전에 종목 선정(Phase 2)으로 넘어가면 안 된다.**",
        ]
    return "\n".join(lines)
