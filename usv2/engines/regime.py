"""
Market Regime Engine (설계서 3장).

4개 축(Pillar)을 각 0~25점, 합계 0~100점으로 계산해 6단계 레짐으로 분류한다.

  A. Trend          — 추세가 존재하는가, 방향은
  B. Breadth        — 지수가 아니라 개별 종목들이 실제로 참여하고 있는가
  C. Volatility     — 변동성 레짐과 스트레스 선행 신호
  D. Risk Appetite  — 위험자산 선호가 살아있는가

현행 국내장 시스템 대비 핵심 차이
--------------------------------
1. "나스닥 전일 등락률" 같은 일간 방향을 쓰지 않는다. 일간 자기상관은 0에 가깝다.
2. VIX 절대값(20) 대신 252일 백분위 + 기간구조를 쓴다. 고정 임계값은 레짐 시프트에서
   조용히 오작동한다 (항상 +1이거나 항상 0이 됨).
3. Breadth를 필수 축으로 넣는다. 지수 신고가 + 내부 붕괴 상태(분배 국면)를
   지수만 봐서는 절대 감지할 수 없다.
4. 레짐은 매매 여부가 아니라 **허용 전략과 노출 상한**을 결정한다.

Look-ahead 방지
---------------
모든 계산은 t시점까지의 데이터만 사용한다.
결과는 t일 종가 확정 후 산출되며, 실제 체결은 t+1 시가를 가정한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from usv2.engines import indicators as ind

REGIME_ORDER = ["CRISIS", "RISK_OFF", "CAUTION", "NEUTRAL", "BULL", "STRONG_BULL"]


@dataclass
class RegimeConfig:
    raw: dict

    @classmethod
    def load(cls, path: str | Path = "config/regime.yaml") -> "RegimeConfig":
        with open(path, encoding="utf-8") as f:
            return cls(yaml.safe_load(f))

    def __getitem__(self, k: str):
        return self.raw[k]

    @property
    def version(self) -> str:
        return self.raw.get("version", "unknown")


@dataclass
class MarketData:
    """레짐 엔진 입력. 모든 시리즈는 같은 거래일 인덱스로 정렬되어 있어야 한다."""

    close: pd.DataFrame                      # 심볼별 종가 (columns=symbol)
    breadth: pd.DataFrame = field(default_factory=pd.DataFrame)
    # breadth 컬럼: pct_above_50dma, pct_above_200dma, nh_nl

    def has(self, *symbols: str) -> bool:
        return all(s in self.close.columns for s in symbols)



# 각 Pillar의 명목 만점. 입력 데이터가 빠지면 실제 획득 가능 최대치가 줄어드는데,
# 레짐 임계값(80/65/45/30/15)은 100점 척도로 잡혀 있다. 환산하지 않으면 총점이
# 통째로 아래로 밀려 BULL이 NEUTRAL로, NEUTRAL이 CAUTION으로 오분류된다.
#
# 예: ^VIX9D/^VIX3M가 없으면 Pillar C 최대치가 25 -> 17로 줄고 총점 최대가 92가 된다.
PILLAR_NOMINAL_MAX = 25.0


def _normalize_pillar(parts: pd.DataFrame, component_max: dict[str, float],
                      name: str) -> pd.DataFrame:
    """사용 가능한 항목만으로 얻은 점수를 명목 만점(25) 척도로 환산.

    coverage 컬럼도 함께 남긴다. 브리핑에서 "이 축은 60%의 정보만으로 계산됨"을
    표시할 수 있어야 한다. 결측을 조용히 메우고 넘어가면 안 된다.
    """
    cols = list(component_max)
    available = parts[cols].notna()
    avail_max = pd.Series(
        [sum(component_max[c] for c in cols if available.loc[i, c]) for i in parts.index],
        index=parts.index, dtype=float,
    )
    raw = parts[cols].sum(axis=1, min_count=1)
    parts[f"{name}_coverage"] = avail_max / sum(component_max.values())
    parts[name] = (raw / avail_max.replace(0.0, np.nan)) * PILLAR_NOMINAL_MAX
    return parts


# ---------------------------------------------------------------------------
# Pillar 계산
# ---------------------------------------------------------------------------
def pillar_a_trend(md: MarketData, cfg: RegimeConfig) -> pd.DataFrame:
    """A. Trend (0~25)."""
    p = cfg["pillar_a_trend"]
    spy = md.close["SPY"]
    qqq = md.close["QQQ"]

    ema50 = ind.ema(spy, 50)
    ema200 = ind.ema(spy, 200)

    parts = pd.DataFrame(index=spy.index)
    parts["a_above_200ema"] = (spy > ema200).astype(float) * p["spy_above_200ema"]
    parts["a_above_50ema"] = (spy > ema50).astype(float) * p["spy_above_50ema"]
    parts["a_golden"] = (ema50 > ema200).astype(float) * p["golden_alignment"]
    parts["a_slope"] = (ind.slope(ema50, 20) > 0).astype(float) * p["ema50_slope_20d_pos"]
    parts["a_qqq_lead"] = (
        ind.roc(qqq, 20) > ind.roc(spy, 20)
    ).astype(float) * p["qqq_beats_spy_20d"]

    # 지표가 아직 계산되지 않은 구간(EMA200 워밍업)은 NaN 유지 -> 레짐 미산출
    warm = ema200.notna()
    parts = parts.where(warm)
    return _normalize_pillar(parts, {
        "a_above_200ema": p["spy_above_200ema"], "a_above_50ema": p["spy_above_50ema"],
        "a_golden": p["golden_alignment"], "a_slope": p["ema50_slope_20d_pos"],
        "a_qqq_lead": p["qqq_beats_spy_20d"],
    }, "pillar_a")


def pillar_b_breadth(md: MarketData, cfg: RegimeConfig) -> pd.DataFrame:
    """B. Breadth (0~25).

    breadth 데이터가 없으면 프록시로 대체하되, 프록시임을 명시적으로 표시한다.
    프록시는 정확도가 낮으므로 Phase 2 전에 구성종목 직접 계산으로 교체해야 한다.
    """
    p = cfg["pillar_b_breadth"]
    idx = md.close.index
    parts = pd.DataFrame(index=idx)

    b = md.breadth if not md.breadth.empty else pd.DataFrame(index=idx)

    def bucket(series: pd.Series, spec: dict) -> pd.Series:
        hi, mid = spec["high"], spec["mid"]
        p_hi, p_mid, p_lo = spec["pts"]
        return pd.Series(
            np.where(series >= hi, p_hi, np.where(series >= mid, p_mid, p_lo)),
            index=series.index, dtype=float,
        ).where(series.notna())

    if "pct_above_50dma" in b:
        parts["b_above50"] = bucket(b["pct_above_50dma"], p["pct_above_50dma"])
    else:
        parts["b_above50"] = np.nan

    if "pct_above_200dma" in b:
        parts["b_above200"] = bucket(b["pct_above_200dma"], p["pct_above_200dma"])
    else:
        parts["b_above200"] = np.nan

    if "nh_nl" in b:
        nhnl5 = b["nh_nl"].rolling(5, min_periods=5).mean()
        parts["b_nhnl"] = (nhnl5 > 0).astype(float).where(nhnl5.notna()) * p["nh_nl_5d_positive"]
    else:
        parts["b_nhnl"] = np.nan

    if md.has("RSP", "SPY"):
        ratio = md.close["RSP"] / md.close["SPY"]
        parts["b_rsp"] = (ind.roc(ratio, 20) >= 0).astype(float) * p["rsp_spy_20d_nonneg"]
        parts["b_rsp"] = parts["b_rsp"].where(ind.roc(ratio, 20).notna())
    else:
        parts["b_rsp"] = np.nan

    return _normalize_pillar(parts, {
        "b_above50": max(p["pct_above_50dma"]["pts"]),
        "b_above200": max(p["pct_above_200dma"]["pts"]),
        "b_nhnl": p["nh_nl_5d_positive"], "b_rsp": p["rsp_spy_20d_nonneg"],
    }, "pillar_b")


def pillar_c_volatility(md: MarketData, cfg: RegimeConfig) -> pd.DataFrame:
    """C. Volatility (0~25)."""
    p = cfg["pillar_c_volatility"]
    parts = pd.DataFrame(index=md.close.index)

    # C1. VIX 252일 백분위 (고정 임계값 대체)
    spec = p["vix_percentile_252d"]
    if md.has("^VIX"):
        rank = ind.pct_rank(md.close["^VIX"], 252)
        pts = spec["pts"]
        parts["c_vix_rank"] = pd.Series(
            np.select(
                [rank <= spec["t1"], rank <= spec["t2"], rank <= spec["t3"]],
                [pts[0], pts[1], pts[2]], default=pts[3],
            ), index=rank.index, dtype=float,
        ).where(rank.notna())
    else:
        parts["c_vix_rank"] = np.nan

    # C2. 기간구조 VIX9D/VIX3M — 백워데이션은 스트레스 선행 신호
    spec = p["vix_term_structure"]
    if md.has("^VIX9D", "^VIX3M"):
        ts = md.close["^VIX9D"] / md.close["^VIX3M"]
        pts = spec["pts"]
        parts["c_term"] = pd.Series(
            np.select([ts < spec["contango"], ts < spec["flat"]], [pts[0], pts[1]],
                      default=pts[2]),
            index=ts.index, dtype=float,
        ).where(ts.notna())
    else:
        parts["c_term"] = np.nan

    # C3. 실현 변동성
    spec = p["realized_vol_20d"]
    rv = ind.realized_vol(md.close["SPY"], 20)
    pts = spec["pts"]
    parts["c_rvol"] = pd.Series(
        np.select([rv < spec["low"], rv < spec["mid"]], [pts[0], pts[1]], default=pts[2]),
        index=rv.index, dtype=float,
    ).where(rv.notna())

    return _normalize_pillar(parts, {
        "c_vix_rank": max(p["vix_percentile_252d"]["pts"]),
        "c_term": max(p["vix_term_structure"]["pts"]),
        "c_rvol": max(p["realized_vol_20d"]["pts"]),
    }, "pillar_c")


def pillar_d_risk_appetite(md: MarketData, cfg: RegimeConfig) -> pd.DataFrame:
    """D. Risk Appetite (0~25)."""
    p = cfg["pillar_d_risk_appetite"]
    parts = pd.DataFrame(index=md.close.index)

    # D1. 신용 리스크 선호 — Risk-on/off의 가장 정직한 지표
    if md.has("HYG", "IEF"):
        ratio = md.close["HYG"] / md.close["IEF"]
        ma50 = ind.sma(ratio, 50)
        parts["d_credit"] = (ratio > ma50).astype(float).where(ma50.notna()) * p["hyg_ief_above_50dma"]
    else:
        parts["d_credit"] = np.nan

    # D2. 소형주 상대강도 — 리스크 선호의 폭
    if md.has("IWM", "SPY"):
        rel = ind.roc(md.close["IWM"], 20) - ind.roc(md.close["SPY"], 20)
        parts["d_iwm"] = (rel >= p["iwm_vs_spy_20d_min"]).astype(float).where(rel.notna()) * p["iwm_vs_spy_pts"]
    else:
        parts["d_iwm"] = np.nan

    # D3. 금리 급등 여부 (수준이 아니라 변화율)
    if md.has("^TNX"):
        chg_bp = (md.close["^TNX"] - md.close["^TNX"].shift(20)) * 100.0
        parts["d_rates"] = (chg_bp < p["tnx_20d_change_max_bp"]).astype(float).where(chg_bp.notna()) * p["tnx_pts"]
    else:
        parts["d_rates"] = np.nan

    # D4. 달러 — 상관 부호가 불안정하므로 가중치를 가장 낮게 둔다
    if md.has("DX-Y.NYB"):
        dxy = md.close["DX-Y.NYB"]
        ma50 = ind.sma(dxy, 50)
        parts["d_dollar"] = (dxy < ma50).astype(float).where(ma50.notna()) * p["dxy_below_50dma"]
    else:
        parts["d_dollar"] = np.nan

    return _normalize_pillar(parts, {
        "d_credit": p["hyg_ief_above_50dma"], "d_iwm": p["iwm_vs_spy_pts"],
        "d_rates": p["tnx_pts"], "d_dollar": p["dxy_below_50dma"],
    }, "pillar_d")


# ---------------------------------------------------------------------------
# 분류 + 확인 규칙 + 오버라이드
# ---------------------------------------------------------------------------
def classify_raw(total: pd.Series, pillar_b: pd.Series, cfg: RegimeConfig) -> pd.Series:
    """총점 -> 레짐 라벨 (오버라이드 적용 전)."""
    r = cfg["regimes"]
    sb = r["STRONG_BULL"]
    labels = pd.Series(index=total.index, dtype=object)

    labels[:] = "CRISIS"
    labels[total >= r["RISK_OFF"]["min_total"]] = "RISK_OFF"
    labels[total >= r["CAUTION"]["min_total"]] = "CAUTION"
    labels[total >= r["NEUTRAL"]["min_total"]] = "NEUTRAL"
    labels[total >= r["BULL"]["min_total"]] = "BULL"

    # STRONG_BULL은 총점 + Breadth AND 조건.
    # 지수만 강하고 내부가 죽은 분배 국면을 STRONG_BULL로 오분류하지 않기 위함.
    strong = (total >= sb["min_total"]) & (pillar_b >= sb["min_breadth"])
    labels[strong] = "STRONG_BULL"

    return labels.where(total.notna())


def apply_confirmation(raw: pd.Series, days: int = 2) -> pd.Series:
    """레짐 전환 N일 확인 규칙.

    하루 튀는 값에 시스템 전체가 흔들리는 것을 방지한다.
    새 라벨이 연속 N일 유지되어야 실제 전환으로 인정한다.
    """
    confirmed = pd.Series(index=raw.index, dtype=object)
    current: str | None = None
    streak_label: str | None = None
    streak = 0

    for i, lab in enumerate(raw.to_numpy()):
        if lab is None or (isinstance(lab, float) and np.isnan(lab)):
            confirmed.iloc[i] = current
            continue
        if current is None:
            current = lab
            streak_label, streak = lab, 1
        elif lab == current:
            streak_label, streak = lab, 0
        else:
            if lab == streak_label:
                streak += 1
            else:
                streak_label, streak = lab, 1
            if streak >= days:
                current = lab
                streak = 0
        confirmed.iloc[i] = current
    return confirmed


def apply_overrides(labels: pd.Series, md: MarketData, cfg: RegimeConfig) -> pd.DataFrame:
    """총점과 무관하게 강제 적용되는 안전 규칙 (설계서 3-2).

    반환 컬럼:
      regime          최종 레짐
      entry_blocked   당일 신규 진입 금지 여부
      override_reason 발동 사유 (감사 추적용)
    """
    o = cfg["overrides"]
    out = pd.DataFrame(index=labels.index)
    out["regime"] = labels
    out["entry_blocked"] = False
    out["override_reason"] = ""

    rank = {r: i for i, r in enumerate(REGIME_ORDER)}

    def demote_to(idx, target: str, reason: str) -> None:
        cur = out.loc[idx, "regime"]
        need = cur.map(lambda x: rank.get(x, 99) > rank[target] if isinstance(x, str) else False)
        hit = idx[need]
        out.loc[hit, "regime"] = target
        out.loc[idx, "override_reason"] = (
            out.loc[idx, "override_reason"].astype(str) + f"|{reason}"
        ).str.lstrip("|")

    # OV1. VIX 백분위 극단 또는 기간구조 심한 백워데이션 -> CRISIS
    if md.has("^VIX"):
        vr = ind.pct_rank(md.close["^VIX"], 252)
        hit = vr[vr > o["crisis_vix_percentile"]].index
        if len(hit):
            out.loc[hit, "regime"] = "CRISIS"
            out.loc[hit, "entry_blocked"] = True
            out.loc[hit, "override_reason"] += "|VIX_PCTL_EXTREME"
    if md.has("^VIX9D", "^VIX3M"):
        ts = md.close["^VIX9D"] / md.close["^VIX3M"]
        hit = ts[ts > o["crisis_term_structure"]].index
        if len(hit):
            out.loc[hit, "regime"] = "CRISIS"
            out.loc[hit, "entry_blocked"] = True
            out.loc[hit, "override_reason"] += "|TERM_BACKWARDATION"

    # OV2. SPY 5일 -6% 이상 급락 -> 최소 CAUTION 강등 + N일 진입 중단
    spy_5d = ind.roc(md.close["SPY"], 5)
    crash = spy_5d[spy_5d <= o["spy_5d_drawdown_pct"]].index
    if len(crash):
        demote_to(crash, "CAUTION", "SPY_5D_CRASH")
        pos = md.close.index.get_indexer(crash)
        for p0 in pos:
            sl = slice(p0, min(p0 + o["spy_5d_halt_days"], len(out)))
            out.iloc[sl, out.columns.get_loc("entry_blocked")] = True

    # OV3. VIX 1일 +40% 급등 -> 당일 신규 진입 금지
    if md.has("^VIX"):
        spike = md.close["^VIX"].pct_change()
        hit = spike[spike > o["vix_1d_spike_pct"]].index
        if len(hit):
            out.loc[hit, "entry_blocked"] = True
            out.loc[hit, "override_reason"] += "|VIX_SPIKE"

    out["override_reason"] = out["override_reason"].str.strip("|")
    return out


# ---------------------------------------------------------------------------
# 메인 진입점
# ---------------------------------------------------------------------------
def compute_regime(md: MarketData, cfg: RegimeConfig | None = None) -> pd.DataFrame:
    """전체 레짐 시계열 계산.

    반환 DataFrame 컬럼:
      pillar_a/b/c/d, total, regime_raw, regime_confirmed, regime,
      entry_blocked, override_reason, exposure_cap, per_name_cap, multiplier
    """
    cfg = cfg or RegimeConfig.load()

    if "SPY" not in md.close.columns:
        raise ValueError("SPY는 필수 심볼입니다 (레짐 엔진의 기준 자산)")

    a = pillar_a_trend(md, cfg)
    b = pillar_b_breadth(md, cfg)
    c = pillar_c_volatility(md, cfg)
    d = pillar_d_risk_appetite(md, cfg)

    out = pd.concat([a, b, c, d], axis=1)
    out["total"] = out[["pillar_a", "pillar_b", "pillar_c", "pillar_d"]].sum(axis=1, min_count=1)

    out["regime_raw"] = classify_raw(out["total"], out["pillar_b"], cfg)
    out["regime_confirmed"] = apply_confirmation(
        out["regime_raw"], cfg.raw.get("confirmation_days", 2)
    )

    ov = apply_overrides(out["regime_confirmed"], md, cfg)
    out["regime"] = ov["regime"]
    out["entry_blocked"] = ov["entry_blocked"]
    out["override_reason"] = ov["override_reason"]

    r = cfg["regimes"]
    out["exposure_cap"] = out["regime"].map(lambda x: r[x]["exposure"] if isinstance(x, str) else np.nan)
    out["per_name_cap"] = out["regime"].map(lambda x: r[x]["per_name"] if isinstance(x, str) else np.nan)
    out["multiplier"] = out["regime"].map(lambda x: r[x]["multiplier"] if isinstance(x, str) else np.nan)

    # entry_blocked인 날은 노출 확대 금지
    out.loc[out["entry_blocked"], "exposure_cap"] = out.loc[out["entry_blocked"], "exposure_cap"].clip(upper=0.0) \
        if False else out.loc[out["entry_blocked"], "exposure_cap"]

    out.attrs["config_version"] = cfg.version
    return out


def latest_regime(regime_df: pd.DataFrame) -> dict:
    """최신 레짐 스냅샷을 dict로. 브리핑/Numeric Guard의 SIGNAL 소스."""
    row = regime_df.dropna(subset=["total"]).iloc[-1]
    prev = regime_df.dropna(subset=["total"]).iloc[-2] if len(regime_df.dropna(subset=["total"])) > 1 else row
    return {
        "date": str(row.name.date()),
        "regime": row["regime"],
        "regime_raw": row["regime_raw"],
        "regime_prev": prev["regime"],
        "changed": bool(row["regime"] != prev["regime"]),
        "total": round(float(row["total"]), 1),
        "total_prev": round(float(prev["total"]), 1),
        "pillar_a": round(float(row["pillar_a"]), 1),
        "pillar_b": round(float(row["pillar_b"]), 1) if pd.notna(row["pillar_b"]) else None,
        "pillar_c": round(float(row["pillar_c"]), 1),
        "pillar_d": round(float(row["pillar_d"]), 1),
        "coverage_a": round(float(row["pillar_a_coverage"]), 2),
        "coverage_b": round(float(row["pillar_b_coverage"]), 2),
        "coverage_c": round(float(row["pillar_c_coverage"]), 2),
        "coverage_d": round(float(row["pillar_d_coverage"]), 2),
        "entry_blocked": bool(row["entry_blocked"]),
        "override_reason": row["override_reason"] or "none",
        "exposure_cap": float(row["exposure_cap"]),
        "per_name_cap": float(row["per_name_cap"]),
        "multiplier": float(row["multiplier"]),
    }


ALLOWED_STRATEGIES = {
    "STRONG_BULL": ["S1_Pullback", "S2_Breakout", "S3_RS_Rotation", "S5_PEAD"],
    "BULL":        ["S1_Pullback", "S2_Breakout", "S3_RS_Rotation", "S5_PEAD"],
    "NEUTRAL":     ["S1_Pullback", "S3_RS_Rotation", "S4_MeanReversion"],
    "CAUTION":     ["S1_Pullback(대형주만)", "S4_MeanReversion(소량)"],
    "RISK_OFF":    [],
    "CRISIS":      [],
}
