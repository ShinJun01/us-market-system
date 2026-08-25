"""
결정론적 지표 계산 (설계서 4장).

설계 원칙
---------
1. 모든 함수는 순수 함수다. 같은 입력 -> 항상 같은 출력.
2. 미래 데이터를 절대 참조하지 않는다. 모든 롤링 윈도우는 과거 방향(trailing)만 본다.
   -> center=True, shift(-n) 형태는 이 파일에 존재해서는 안 된다.
3. LLM이 이 값을 만들지 않는다. 이 파일이 SIGNAL 계층의 유일한 생성자다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def ema(s: pd.Series, span: int) -> pd.Series:
    """지수이동평균."""
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def sma(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=window).mean()


def roc(s: pd.Series, window: int) -> pd.Series:
    """Rate of Change. window일 전 대비 수익률(소수)."""
    return s.pct_change(window)


def slope(s: pd.Series, window: int) -> pd.Series:
    """window일에 걸친 단순 기울기 (현재값 - window일 전 값)."""
    return s - s.shift(window)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average True Range (Wilder). 포지션 사이징의 기준 단위."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def atr_pct(high, low, close, window: int = 14) -> pd.Series:
    return atr(high, low, close, window) / close


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder RSI.

    주의: V2에서 RSI는 점수 가산 항목이 아니다(설계서 4-3).
    Mean Reversion 전략의 진입 조건과 전략 선택 스위치로만 사용한다.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 (전부 상승) -> RSI 100
    out = out.where(avg_loss != 0.0, 100.0)
    return out.where(avg_gain.notna())


def rvol(volume: pd.Series, window: int = 20) -> pd.Series:
    """Relative Volume. 당일 거래량 / 과거 window일 평균 거래량.

    주의: 당일 값을 평균에 포함시키면 자기참조가 되어 신호가 둔화된다.
    shift(1)로 과거 평균만 사용한다.
    """
    base = volume.shift(1).rolling(window, min_periods=window).mean()
    return volume / base


def addv(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
    """Average Daily Dollar Volume. 유동성 게이트(G1)의 기준."""
    dollar = close * volume
    return dollar.rolling(window, min_periods=window).mean()


def realized_vol(close: pd.Series, window: int = 20, annualize: bool = True) -> pd.Series:
    """실현 변동성. 로그수익률 표준편차."""
    r = np.log(close / close.shift(1))
    v = r.rolling(window, min_periods=window).std()
    return v * np.sqrt(TRADING_DAYS) if annualize else v


def pct_rank(s: pd.Series, window: int = TRADING_DAYS) -> pd.Series:
    """트레일링 백분위 순위 (0~100).

    고정 임계값(VIX 20 등)의 레짐 시프트 취약성을 대체하는 핵심 도구.
    현재값이 과거 window개 관측치 중 몇 % 지점인지 반환한다.
    """
    def _rank(x: np.ndarray) -> float:
        return float((x[:-1] < x[-1]).sum()) / float(len(x) - 1) * 100.0

    return s.rolling(window, min_periods=max(20, window // 5)).apply(_rank, raw=True)


def zscore_cross_section(s: pd.Series, clip: float = 3.0) -> pd.Series:
    """횡단면 z-score. 같은 시점의 여러 종목 간 비교용.

    단위가 다른 팩터들을 같은 자로 재기 위한 변환(설계서 5-4).
    """
    mu = s.mean()
    sd = s.std(ddof=0)
    if not np.isfinite(sd) or sd == 0:
        return pd.Series(0.0, index=s.index)
    return ((s - mu) / sd).clip(-clip, clip)


def drawdown(equity: pd.Series) -> pd.Series:
    """고점 대비 낙폭 (음수)."""
    return equity / equity.cummax() - 1.0


def max_drawdown(equity: pd.Series) -> float:
    return float(drawdown(equity).min())


def forward_return(close: pd.Series, horizon: int) -> pd.Series:
    """미래 수익률.

    !!! 경고 !!!
    이 함수는 미래 데이터를 참조한다. 오직 사후 검증(레짐 유효성 측정)에만 사용한다.
    신호 생성 경로에서 호출하면 look-ahead bias가 발생한다.
    usv2/engines/ 아래 신호 생성 코드에서 이 함수를 import하면 CI가 실패한다.
    """
    return close.shift(-horizon) / close - 1.0
