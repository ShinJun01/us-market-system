"""
데이터 수집 오케스트레이션 (설계서 L0).

흐름: provider -> Bars 수집 -> V1~V7 검증 -> HALT 판정 -> MarketData 조립
검증을 통과하지 못한 데이터는 절대 아래 레이어로 내려가지 않는다.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from usv2.data.providers import Bars, get_provider
from usv2.data.validate import ValidationReport, validate_all
from usv2.engines.regime import MarketData


# breadth 계산 임계값
_BREADTH_MIN_COVERAGE = 0.90          # 구성종목 수신율 하한
_BREADTH_MIN_NAMES = 200              # 유효 종목 수 하한 (미만이면 breadth 미산출)
_BREADTH_CACHE_MAX_STALE_DAYS = 3     # 캐시 허용 경과 영업일


@dataclass
class IngestResult:
    market: MarketData | None
    report: ValidationReport
    bars: dict[str, Bars]
    provider: str

    @property
    def halt(self) -> bool:
        return self.report.halt or self.market is None


def load_config(path: str | Path = "config/data_sources.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def required_symbols(cfg: dict) -> list[str]:
    s = cfg["symbols"]
    return [x for group in s.values() for x in group]


def _proxy_breadth(close: pd.DataFrame) -> pd.DataFrame:
    """Breadth 프록시 (Phase 1 임시).

    !!! 경고 !!!
    이것은 근사치다. 진짜 breadth는 S&P500 구성종목 500개를 전부 받아서
    각 종목이 자기 50일선 위인지 세어야 나온다.
    프록시는 "지수 내부가 무너지는데 지수는 신고가"라는 상황을 놓칠 수 있고,
    그 상황이 바로 breadth를 쓰는 이유다.

    -> Phase 2 진입 전 반드시 breadth.mode=compute로 전환할 것.
       전환 전까지 pillar_b 점수는 신뢰도가 낮음을 브리핑에 명시한다.
    """
    from usv2.engines import indicators as ind

    out = pd.DataFrame(index=close.index)
    if "RSP" in close and "SPY" in close:
        ratio = close["RSP"] / close["SPY"]
        rel = ind.roc(ratio, 20)
        # RSP/SPY 20일 변화를 40~80% 범위로 사상한 대략적 대용치
        out["pct_above_50dma"] = (60.0 + rel * 400.0).clip(5, 95)
        rel60 = ind.roc(ratio, 60)
        out["pct_above_200dma"] = (55.0 + rel60 * 250.0).clip(5, 95)
        out["nh_nl"] = rel * 1000.0
    return out


def _universe_close(cfg: dict, start: str, end: str | None) -> pd.DataFrame:
    """S&P500 구성종목 종가 패널. 로컬 parquet 캐시 우선, 없으면 yfinance 배치 다운로드.

    500종목 × 10년치라 매번 받으면 느리고 야후 쪽 요청도 많아진다.
    최초 1회만 받고 이후에는 캐시를 쓴다(NEXT_STEPS.md 3번).
    캐시를 새로 받고 싶으면 캐시 파일을 지우면 된다 — 별도 만료 로직은 두지 않는다.
    """
    constituents_file = Path(cfg["breadth"]["constituents_file"])
    if not constituents_file.exists():
        raise FileNotFoundError(
            f"{constituents_file} 없음. 먼저 `python scripts/fetch_sp500_constituents.py` 실행."
        )
    symbols = pd.read_csv(constituents_file)["symbol"].tolist()

    cache_path = Path(cfg.get("cache_dir", "data/cache")) / "sp500_close.parquet"
    if cache_path.exists():
        cached = pd.read_parquet(cache_path)
        # 캐시 신선도 검사.
        # 이게 없으면 8월에 받은 캐시를 10월에 그대로 쓰면서 breadth만 조용히
        # 멈춰 있고 가격은 갱신되는 상태가 된다. V1~V7 게이트는 Bars 객체만
        # 검사하므로 이 실패를 잡지 못한다 -> 여기서 직접 막는다.
        stale = len(pd.bdate_range(cached.index[-1], dt.date.today())) - 1
        if stale > _BREADTH_CACHE_MAX_STALE_DAYS:
            print(f"[ingest] breadth 캐시 {stale}영업일 경과 -> 재다운로드")
            cache_path.unlink()
        else:
            return cached

    import yfinance as yf

    raw = yf.download(symbols, start=start, end=end, auto_adjust=True,
                       progress=False, threads=True, group_by="ticker")
    close = pd.DataFrame({
        s: raw[s]["Close"] for s in symbols if s in raw.columns.get_level_values(0)
    }).sort_index()
    close = close.dropna(how="all")

    # 커버리지 검사. yfinance는 실패한 티커를 조용히 버린다.
    # 500개 중 300개만 받아졌는데 그대로 진행하면 breadth가 틀린 줄도 모르고 쓰인다.
    ratio = len(close.columns) / len(symbols)
    if ratio < _BREADTH_MIN_COVERAGE:
        raise RuntimeError(
            f"구성종목 수신율 {ratio:.1%} ({len(close.columns)}/{len(symbols)}) — "
            f"기준 {_BREADTH_MIN_COVERAGE:.0%} 미만. breadth를 신뢰할 수 없어 중단합니다."
        )
    print(f"[ingest] 구성종목 {len(close.columns)}/{len(symbols)} 수신 ({ratio:.1%})")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    close.to_parquet(cache_path)
    return close


def _compute_breadth(cfg: dict, start: str, end: str | None) -> pd.DataFrame:
    """실제 breadth (설계서 5장 F5 계열): S&P500 구성종목 전체에서 직접 계산.

    _proxy_breadth의 근사(RSP/SPY)를 대체한다. "지수는 신고가인데 내부 종목
    절반이 하락 추세"인 분배 국면은 개별 종목을 안 보면 감지할 수 없다.
    """
    close = _universe_close(cfg, start, end)

    out = pd.DataFrame(index=close.index)

    # 분모는 "그날 데이터가 있는 종목 수"여야 한다.
    # .mean(axis=1)을 쓰면 분모가 항상 500이 되고, 그날 미상장이라 NaN인 종목이
    # "50일선 아래"로 집계된다. 오늘의 구성종목으로 과거를 계산하면 과거로
    # 갈수록 미상장 종목이 늘어나므로, breadth가 체계적으로 과소평가된다.
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    valid50 = (close.notna() & ma50.notna()).sum(axis=1)
    valid200 = (close.notna() & ma200.notna()).sum(axis=1)

    out["pct_above_50dma"] = (close > ma50).sum(axis=1) / valid50.replace(0, np.nan) * 100.0
    out["pct_above_200dma"] = (close > ma200).sum(axis=1) / valid200.replace(0, np.nan) * 100.0

    roll_max = close.rolling(252, min_periods=100).max()
    roll_min = close.rolling(252, min_periods=100).min()
    nh = (close >= roll_max).sum(axis=1)
    nl = (close <= roll_min).sum(axis=1)
    # 신고가-신저가도 유효 종목 수로 정규화한다(종목 수가 변해도 비교 가능하도록).
    out["nh_nl"] = (nh - nl) / valid200.replace(0, np.nan) * 100.0

    # 표본이 너무 적은 초기 구간은 계산하지 않는다. 억지로 값을 만들면
    # 레짐 엔진이 그것을 사실로 받아들인다.
    out.loc[valid50 < _BREADTH_MIN_NAMES, "pct_above_50dma"] = np.nan
    out.loc[valid200 < _BREADTH_MIN_NAMES, ["pct_above_200dma", "nh_nl"]] = np.nan
    return out


def ingest(config_path: str | Path = "config/data_sources.yaml",
           end: str | None = None,
           ref_date: dt.date | None = None) -> IngestResult:
    cfg = load_config(config_path)
    provider = get_provider(cfg["provider"])
    syms = required_symbols(cfg)

    bars = provider.fetch_many(syms, start=cfg["start_date"], end=end)
    report = validate_all(bars, syms, ref_date=ref_date)

    if report.halt:
        return IngestResult(None, report, bars, provider.name)

    close = pd.DataFrame({s: b.df["close"] for s, b in bars.items()}).sort_index()
    close = close.dropna(how="all")

    mode = cfg.get("breadth", {}).get("mode", "proxy")
    if mode == "compute" and provider.name == "yahoo":
        breadth = _compute_breadth(cfg, cfg["start_date"], end)
        breadth = breadth.reindex(close.index).ffill()
        breadth.attrs["is_proxy"] = False
    else:
        if mode == "compute":
            print(f"[ingest] breadth.mode=compute는 yahoo provider 전용. "
                  f"provider={provider.name} -> proxy로 대체")
        breadth = _proxy_breadth(close)
        breadth.attrs["is_proxy"] = True

    md = MarketData(close=close, breadth=breadth)
    return IngestResult(md, report, bars, provider.name)
