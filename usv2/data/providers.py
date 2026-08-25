"""
데이터 프로바이더 추상화 (설계서 L0).

왜 추상화하는가
---------------
Phase 1은 무료 데이터(yahoo)로 시작하지만, Phase 2 백테스트는 시점별 구성종목과
상장폐지 종목이 필요해서 유료 데이터로 전환해야 한다(설계서 5-2).
그때 엔진 코드를 건드리지 않으려면 지금 인터페이스를 고정해 둬야 한다.

프로바이더별 가용성
-------------------
SyntheticProvider : 네트워크 불필요. 파이프라인 검증 전용. 매매 판단 근거 사용 금지.
YahooProvider     : yfinance 필요. 로컬(집) 환경에서만 동작.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class Bars:
    """단일 심볼의 일봉 시계열 + 출처 메타데이터.

    source/fetched_at는 FACT 계층의 필수 필드다(설계서 14-2).
    출처 없는 데이터는 파이프라인에 들어올 수 없다.
    """

    symbol: str
    df: pd.DataFrame          # index=DatetimeIndex(tz-naive, 거래일), columns=OHLCV_COLUMNS
    source: str               # 예: "yfinance:SPY", "synthetic:seed=7"
    fetched_at: dt.datetime

    def __post_init__(self) -> None:
        missing = [c for c in OHLCV_COLUMNS if c not in self.df.columns]
        if missing:
            raise ValueError(f"{self.symbol}: 필수 컬럼 누락 {missing}")
        if not isinstance(self.df.index, pd.DatetimeIndex):
            raise ValueError(f"{self.symbol}: index가 DatetimeIndex가 아님")

    @property
    def as_of(self) -> dt.datetime:
        """데이터가 나타내는 마지막 시점(신선도 게이트 V2에서 사용)."""
        return self.df.index[-1].to_pydatetime()


class DataProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def fetch(self, symbol: str, start: str, end: str | None = None) -> Bars:
        ...

    def fetch_many(self, symbols: list[str], start: str, end: str | None = None) -> dict[str, Bars]:
        out: dict[str, Bars] = {}
        for s in symbols:
            try:
                out[s] = self.fetch(s, start, end)
            except Exception as e:  # noqa: BLE001
                # 개별 실패는 삼키고 커버리지 게이트(V6)에서 판정한다.
                # 여기서 예외를 올리면 심볼 하나 때문에 전체가 죽는다.
                print(f"[provider:{self.name}] {s} 수집 실패: {e}")
        return out


# ---------------------------------------------------------------------------
# 합성 데이터 (네트워크 없는 환경용)
# ---------------------------------------------------------------------------
class SyntheticProvider(DataProvider):
    """레짐 구조가 내장된 합성 시계열 생성기.

    순수 랜덤워크로는 레짐 엔진이 아무것도 구분하지 못해서 파이프라인 검증이 안 된다.
    그래서 다음 구조를 인위적으로 심는다.
      - 마르코프 레짐 전환 (bull / chop / bear)
      - 변동성 클러스터링 (레짐별 sigma가 다름)
      - 자산 간 상관 (SPY 충격이 QQQ/IWM/HYG에 전이, VIX/IEF/DXY는 역방향)

    !!! 이 데이터로 나온 어떤 성과 수치도 시장에 대한 증거가 아니다. !!!
    파이프라인이 죽지 않고 돌아가는지, 계산이 스키마대로 나오는지만 확인한다.
    """

    name = "synthetic"

    # 심볼별 (시장베타, 기본연변동성, 기본가격)
    _SPEC: dict[str, tuple[float, float, float]] = {
        "SPY": (1.00, 0.15, 400.0),
        "QQQ": (1.15, 0.20, 380.0),
        "IWM": (1.10, 0.22, 190.0),
        "RSP": (0.95, 0.15, 160.0),
        "HYG": (0.35, 0.07, 78.0),
        "IEF": (-0.20, 0.06, 95.0),
        "XLK": (1.20, 0.21, 200.0),
        "XLF": (1.00, 0.18, 38.0),
        "XLV": (0.75, 0.14, 135.0),
        "XLY": (1.10, 0.20, 175.0),
        "XLP": (0.55, 0.12, 75.0),
        "XLI": (1.00, 0.17, 110.0),
        "XLE": (0.85, 0.28, 88.0),
        "XLU": (0.45, 0.15, 68.0),
        "XLRE": (0.90, 0.19, 40.0),
        "XLB": (0.95, 0.19, 85.0),
        "XLC": (1.05, 0.20, 72.0),
        "DX-Y.NYB": (-0.25, 0.07, 103.0),
    }

    def __init__(self, seed: int = 7) -> None:
        self.seed = seed
        self._cache: dict[str, pd.DataFrame] = {}
        self._market: pd.Series | None = None
        self._regime: pd.Series | None = None
        self._index: pd.DatetimeIndex | None = None

    # -- 공통 시장 경로 ----------------------------------------------------
    def _build_market(self, start: str, end: str) -> None:
        if self._market is not None:
            return
        idx = pd.bdate_range(start=start, end=end)
        rng = np.random.default_rng(self.seed)
        n = len(idx)

        # 마르코프 레짐: 0=bull, 1=chop, 2=bear
        trans = np.array([[0.988, 0.010, 0.002],
                          [0.030, 0.955, 0.015],
                          [0.010, 0.045, 0.945]])
        mu = np.array([0.00055, 0.00000, -0.00090])     # 일간 드리프트
        sig = np.array([0.0075, 0.0105, 0.0185])        # 일간 변동성

        state = np.zeros(n, dtype=int)
        for i in range(1, n):
            state[i] = rng.choice(3, p=trans[state[i - 1]])

        shocks = rng.standard_normal(n)
        mkt = mu[state] + sig[state] * shocks

        self._index = idx
        self._market = pd.Series(mkt, index=idx, name="market")
        self._regime = pd.Series(state, index=idx, name="true_regime")

    # -- OHLCV 조립 --------------------------------------------------------
    def _make_ohlcv(self, symbol: str, rng: np.random.Generator) -> pd.DataFrame:
        assert self._market is not None and self._index is not None
        beta, base_vol, px0 = self._SPEC.get(symbol, (1.0, 0.18, 100.0))
        n = len(self._index)
        idio = rng.standard_normal(n) * (base_vol / np.sqrt(252)) * 0.6
        ret = beta * self._market.values + idio
        close = px0 * np.exp(np.cumsum(ret))

        intraday = np.abs(rng.standard_normal(n)) * close * 0.004 + close * 0.001
        open_ = close * (1 + rng.standard_normal(n) * 0.0015)
        high = np.maximum(open_, close) + intraday
        low = np.minimum(open_, close) - intraday
        vol = (rng.lognormal(mean=16.0, sigma=0.35, size=n)
               * (1 + 2.0 * np.abs(ret))).round()

        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
            index=self._index,
        )

    def _make_vix_family(self, symbol: str, rng: np.random.Generator) -> pd.DataFrame:
        """VIX / VIX9D / VIX3M.

        시장 하락 시 VIX 급등 + 단기물이 더 크게 튀는(백워데이션) 구조를 심는다.
        """
        assert self._market is not None and self._index is not None
        n = len(self._index)
        mkt = self._market.values
        rv = pd.Series(mkt).rolling(20, min_periods=5).std().bfill().values * np.sqrt(252)

        # 만기별 민감도: 단기물일수록 충격에 크게 반응
        sens = {"^VIX9D": 1.45, "^VIX": 1.00, "^VIX3M": 0.68}[symbol]
        base = {"^VIX9D": 15.5, "^VIX": 16.5, "^VIX3M": 18.5}[symbol]

        shock = np.clip(-mkt, 0, None) * 320.0 * sens
        level = base + (rv - 0.14) * 100.0 * sens + shock
        level = pd.Series(level, index=self._index).ewm(span=6, adjust=False).mean()
        level = level.clip(9.0, 65.0) * (1 + rng.standard_normal(n) * 0.02)

        c = level.values
        return pd.DataFrame(
            {"open": c, "high": c * 1.03, "low": c * 0.97, "close": c,
             "volume": np.zeros(n)},
            index=self._index,
        )

    def _make_tnx(self, rng: np.random.Generator) -> pd.DataFrame:
        """10년물 금리(^TNX는 퍼센트*10 단위가 아니라 퍼센트로 취급)."""
        assert self._index is not None
        n = len(self._index)
        # 평균회귀 + 랜덤 드리프트
        lvl = np.zeros(n)
        lvl[0] = 3.2
        for i in range(1, n):
            lvl[i] = lvl[i - 1] + 0.002 * (3.4 - lvl[i - 1]) + rng.standard_normal() * 0.035
        lvl = np.clip(lvl, 0.4, 7.0)
        return pd.DataFrame(
            {"open": lvl, "high": lvl * 1.01, "low": lvl * 0.99, "close": lvl,
             "volume": np.zeros(n)},
            index=self._index,
        )

    def fetch(self, symbol: str, start: str, end: str | None = None) -> Bars:
        end = end or dt.date.today().isoformat()
        self._build_market(start, end)
        if symbol not in self._cache:
            # 심볼별 독립 시드 -> 재현성 보장
            rng = np.random.default_rng(abs(hash((self.seed, symbol))) % (2**32))
            if symbol in ("^VIX", "^VIX9D", "^VIX3M"):
                df = self._make_vix_family(symbol, rng)
            elif symbol == "^TNX":
                df = self._make_tnx(rng)
            else:
                df = self._make_ohlcv(symbol, rng)
            self._cache[symbol] = df
        return Bars(
            symbol=symbol,
            df=self._cache[symbol].copy(),
            source=f"synthetic:seed={self.seed}",
            fetched_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )

    def true_regime(self) -> pd.Series:
        """합성 데이터에 심어둔 정답 레짐. 테스트에서만 사용."""
        assert self._regime is not None
        return self._regime.copy()


# ---------------------------------------------------------------------------
# Yahoo Finance (로컬 전용)
# ---------------------------------------------------------------------------
class YahooProvider(DataProvider):
    """yfinance 어댑터. 집(로컬) 환경에서 사용.

    주의사항
    --------
    * yfinance는 비공식 스크래핑 기반이라 예고 없이 스키마가 바뀐다.
      그래서 validate.py의 게이트가 형식적 절차가 아니라 실질 방어선이다.
    * 조정주가(auto_adjust=True)를 쓰되, 거래량은 원본을 쓴다.
      조정 거래량으로 RVOL을 계산하면 분할 시점에서 거짓 신호가 난다.
    * 시점별 구성종목을 제공하지 않는다 -> Phase 2 백테스트에는 부적합.
    """

    name = "yahoo"

    def fetch(self, symbol: str, start: str, end: str | None = None) -> Bars:
        try:
            import yfinance as yf
        except ImportError as e:
            raise RuntimeError(
                "yfinance가 없습니다. 로컬에서 `pip install yfinance` 후 사용하세요. "
                "(샌드박스 환경에서는 네트워크가 차단되어 동작하지 않습니다)"
            ) from e

        raw = yf.download(
            symbol, start=start, end=end,
            auto_adjust=True, progress=False, threads=False,
        )
        if raw is None or raw.empty:
            raise RuntimeError(f"{symbol}: 데이터 없음")

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df.astype(float)

        return Bars(
            symbol=symbol,
            df=df,
            source=f"yfinance:{symbol}",
            fetched_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )


def get_provider(name: str, **kwargs) -> DataProvider:
    name = name.lower()
    if name == "synthetic":
        return SyntheticProvider(**kwargs)
    if name == "yahoo":
        return YahooProvider()
    raise ValueError(f"알 수 없는 provider: {name}")
