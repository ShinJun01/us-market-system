"""
데이터 검증 게이트 V1~V7 (설계서 14-2).

이 모듈의 목적은 하나다: **검증되지 않은 데이터가 파이프라인 아래로 흘러가는 것을 막는다.**

현행 국내장 시스템의 최대 취약점은 "AI가 시장 수치를 직접 생성하고 그 수치가
매매 신호가 된다"는 점이었다. 그 경로를 끊는 첫 번째 장치가 이 파일이고,
두 번째 장치가 brief/numeric_guard.py다.

게이트 실패 시 동작
-------------------
FAIL 하나라도 발생 -> HALT. 당일 브리핑 발행 금지, 매매 금지.
"데이터가 좀 이상하지만 일단 진행"은 허용하지 않는다.
그렇게 만들면 언젠가 반드시 잘못된 숫자로 매매하게 된다.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from usv2.data.providers import OHLCV_COLUMNS, Bars


class Severity(str, Enum):
    PASS = "PASS"
    WARN = "WARN"    # 기록하되 진행
    FAIL = "FAIL"    # HALT


@dataclass
class Check:
    gate: str
    symbol: str
    severity: Severity
    message: str


@dataclass
class ValidationReport:
    checks: list[Check] = field(default_factory=list)
    as_of: dt.datetime | None = None

    def add(self, gate: str, symbol: str, severity: Severity, message: str) -> None:
        self.checks.append(Check(gate, symbol, severity, message))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.severity is Severity.FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.severity is Severity.WARN]

    @property
    def halt(self) -> bool:
        return len(self.failures) > 0

    def summary(self) -> str:
        if self.halt:
            lines = [f"HALT — 검증 실패 {len(self.failures)}건"]
            lines += [f"  [{c.gate}] {c.symbol}: {c.message}" for c in self.failures[:20]]
            if self.warnings:
                lines.append(f"  (경고 {len(self.warnings)}건)")
            return "\n".join(lines)
        if self.warnings:
            head = f"PASS (경고 {len(self.warnings)}건)"
            body = [f"  [{c.gate}] {c.symbol}: {c.message}" for c in self.warnings[:10]]
            return "\n".join([head, *body])
        return "PASS — 전 게이트 통과"


# ---------------------------------------------------------------------------
# 개별 게이트
# ---------------------------------------------------------------------------
def v1_schema(bars: Bars, rep: ValidationReport) -> None:
    """V1 스키마: 필수 컬럼 존재, 타입 일치, 인덱스 정렬/중복."""
    df = bars.df
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        rep.add("V1", bars.symbol, Severity.FAIL, f"컬럼 누락 {missing}")
        return
    non_numeric = [c for c in OHLCV_COLUMNS if not pd.api.types.is_numeric_dtype(df[c])]
    if non_numeric:
        rep.add("V1", bars.symbol, Severity.FAIL, f"비수치 컬럼 {non_numeric}")
    if df.index.duplicated().any():
        n = int(df.index.duplicated().sum())
        rep.add("V1", bars.symbol, Severity.FAIL, f"중복 날짜 {n}건")
    if not df.index.is_monotonic_increasing:
        rep.add("V1", bars.symbol, Severity.FAIL, "인덱스가 시간순 정렬 아님")
    if not bars.source:
        rep.add("V1", bars.symbol, Severity.FAIL, "source 메타데이터 없음")


def v2_freshness(bars: Bars, rep: ValidationReport, max_stale_days: int = 5,
                 ref_date: dt.date | None = None) -> None:
    """V2 신선도: 마지막 데이터가 최근 거래일인가.

    지연된 데이터로 오늘 판단을 내리는 것을 막는다.
    주말/공휴일을 고려해 기본 5영업일까지 허용하되, 그 이상은 FAIL.
    """
    ref = ref_date or dt.date.today()
    last = bars.as_of.date()
    gap = len(pd.bdate_range(last, ref)) - 1
    if gap > max_stale_days:
        rep.add("V2", bars.symbol, Severity.FAIL,
                f"데이터 {gap}영업일 지연 (최종 {last})")
    elif gap > 2:
        rep.add("V2", bars.symbol, Severity.WARN, f"데이터 {gap}영업일 지연")


def v3_range(bars: Bars, rep: ValidationReport) -> None:
    """V3 물리적 범위: 가격>0, high>=low, VIX 5~150 등."""
    df = bars.df
    sym = bars.symbol

    price_cols = ["open", "high", "low", "close"]
    if (df[price_cols] <= 0).any().any():
        rep.add("V3", sym, Severity.FAIL, "0 이하 가격 존재")
    if (df["high"] < df["low"]).any():
        n = int((df["high"] < df["low"]).sum())
        rep.add("V3", sym, Severity.FAIL, f"high < low {n}건")
    if (df["volume"] < 0).any():
        rep.add("V3", sym, Severity.FAIL, "음수 거래량")
    if df[price_cols].isna().any().any():
        n = int(df[price_cols].isna().any(axis=1).sum())
        rep.add("V3", sym, Severity.FAIL, f"가격 결측 {n}행")

    if sym in ("^VIX", "^VIX9D", "^VIX3M"):
        c = df["close"]
        if (c < 5).any() or (c > 150).any():
            rep.add("V3", sym, Severity.FAIL, f"VIX 범위 이탈 (min {c.min():.1f}, max {c.max():.1f})")
    if sym == "^TNX":
        c = df["close"]
        if (c < 0).any() or (c > 20).any():
            rep.add("V3", sym, Severity.FAIL, f"금리 범위 이탈 (max {c.max():.2f})")


# 자산군별 "물리적으로 가능한" 일간 변동 상한.
# VIX는 실제로도 하루 +100% 넘게 튄 적이 있다(2018-02-05). 같은 잣대를 쓰면
# 정상 데이터가 계속 경고를 뱉고, 경고가 흔해지면 아무도 안 보게 된다.
_V4_THRESHOLD = {"^VIX": 1.20, "^VIX9D": 1.50, "^VIX3M": 0.80, "^TNX": 0.25}


def v4_continuity(bars: Bars, rep: ValidationReport, threshold: float | None = None) -> None:
    """V4 연속성: 비정상적 인접일 변동 -> 분할/배당 조정 오류 의심.

    실제 급등락일 수도 있으므로 기본 WARN. 다만 지수 ETF는 FAIL 처리한다.
    지수 ETF가 하루 30% 움직였다면 그건 시장이 아니라 데이터가 이상한 것이다.
    """
    threshold = threshold or _V4_THRESHOLD.get(bars.symbol, 0.30)
    ret = bars.df["close"].pct_change()
    bad = ret[ret.abs() > threshold]
    if bad.empty:
        return
    sev = Severity.FAIL if bars.symbol in ("SPY", "QQQ", "IWM", "RSP") else Severity.WARN
    worst = bad.abs().idxmax()
    rep.add("V4", bars.symbol, sev,
            f"급변 {len(bad)}건 (최대 {bad.loc[worst]:+.1%} @ {worst.date()}) — 분할/조정 오류 의심")


def v5_cross_source(primary: Bars, secondary: Bars | None,
                    rep: ValidationReport, tol: float = 0.001) -> None:
    """V5 교차검증: 핵심 지표는 독립된 2개 소스에서 조회 후 대조.

    편차가 0.1%를 넘으면 HALT. 어느 쪽이 맞는지 시스템은 알 수 없으므로
    "둘 중 하나를 고르지 않고 멈춘다"가 유일하게 안전한 선택이다.

    Phase 1에서 secondary가 없으면 WARN으로 기록하고 통과시킨다.
    Phase 3(실자본) 이전에는 반드시 두 번째 소스를 붙여야 한다.
    """
    if secondary is None:
        rep.add("V5", primary.symbol, Severity.WARN, "2차 소스 미설정 (Phase 3 전 필수)")
        return
    a = primary.df["close"]
    b = secondary.df["close"]
    common = a.index.intersection(b.index)
    if len(common) == 0:
        rep.add("V5", primary.symbol, Severity.FAIL, "두 소스 간 공통 날짜 없음")
        return
    dev = (a.loc[common] - b.loc[common]).abs() / b.loc[common]
    if dev.max() > tol:
        rep.add("V5", primary.symbol, Severity.FAIL,
                f"소스 간 편차 {dev.max():.3%} (허용 {tol:.1%})")


def v6_coverage(bars_map: dict[str, Bars], required: list[str],
                rep: ValidationReport, min_ratio: float = 0.98) -> None:
    """V6 커버리지: 필수 심볼 수신 비율.

    레짐 엔진은 4개 축을 전부 필요로 한다. 심볼이 빠지면 해당 축 점수가
    조용히 0이 되고, 시스템은 "시장이 나쁘다"고 오판한다.
    누락을 침묵으로 처리하지 않고 명시적으로 실패시킨다.
    """
    missing = [s for s in required if s not in bars_map]
    ratio = 1.0 - len(missing) / max(1, len(required))
    if missing:
        sev = Severity.FAIL if ratio < min_ratio else Severity.WARN
        rep.add("V6", "-", sev, f"심볼 누락 {len(missing)}/{len(required)}: {missing}")


def v7_calendar(bars_map: dict[str, Bars], rep: ValidationReport,
                max_gap_days: int = 6) -> None:
    """V7 캘린더: 심볼 간 마지막 날짜 정합성 + 비정상적 데이터 공백.

    NYSE 공식 휴장일 캘린더는 Phase 2에서 pandas_market_calendars로 교체한다.
    Phase 1에서는 심볼 간 상대 비교로 이상을 잡는다.
    """
    if not bars_map:
        rep.add("V7", "-", Severity.FAIL, "수신 데이터 없음")
        return

    last_dates = {s: b.as_of.date() for s, b in bars_map.items()}
    newest = max(last_dates.values())
    for s, d in last_dates.items():
        gap = len(pd.bdate_range(d, newest)) - 1
        if gap > 3:
            rep.add("V7", s, Severity.FAIL,
                    f"다른 심볼 대비 {gap}영업일 뒤처짐 (최종 {d} vs 최신 {newest})")

    for s, b in bars_map.items():
        gaps = b.df.index.to_series().diff().dt.days
        big = gaps[gaps > max_gap_days]
        if len(big) > 0:
            rep.add("V7", s, Severity.WARN,
                    f"{max_gap_days}일 초과 데이터 공백 {len(big)}건 (최대 {int(big.max())}일)")


# ---------------------------------------------------------------------------
# 통합 실행
# ---------------------------------------------------------------------------
def validate_all(bars_map: dict[str, Bars],
                 required_symbols: list[str],
                 ref_date: dt.date | None = None,
                 secondary_map: dict[str, Bars] | None = None,
                 critical_symbols: tuple[str, ...] = ("SPY", "^VIX")) -> ValidationReport:
    """V1~V7 전체 실행. 이 함수의 반환값이 HALT면 아래 레이어는 실행되지 않는다."""
    rep = ValidationReport(as_of=dt.datetime.now())

    v6_coverage(bars_map, required_symbols, rep)

    for sym, bars in bars_map.items():
        v1_schema(bars, rep)
        v2_freshness(bars, rep, ref_date=ref_date)
        v3_range(bars, rep)
        v4_continuity(bars, rep)

    for sym in critical_symbols:
        if sym in bars_map:
            v5_cross_source(bars_map[sym], (secondary_map or {}).get(sym), rep)

    v7_calendar(bars_map, rep)
    return rep
