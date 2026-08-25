"""
데이터 수집 오케스트레이션 (설계서 L0).

흐름: provider -> Bars 수집 -> V1~V7 검증 -> HALT 판정 -> MarketData 조립
검증을 통과하지 못한 데이터는 절대 아래 레이어로 내려가지 않는다.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from usv2.data.providers import Bars, get_provider
from usv2.data.validate import ValidationReport, validate_all
from usv2.engines.regime import MarketData


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
        return pd.read_parquet(cache_path)

    import yfinance as yf

    raw = yf.download(symbols, start=start, end=end, auto_adjust=True,
                       progress=False, threads=True, group_by="ticker")
    close = pd.DataFrame({
        s: raw[s]["Close"] for s in symbols if s in raw.columns.get_level_values(0)
    }).sort_index()
    close = close.dropna(how="all")

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
    out["pct_above_50dma"] = (close > close.rolling(50).mean()).mean(axis=1) * 100.0
    out["pct_above_200dma"] = (close > close.rolling(200).mean()).mean(axis=1) * 100.0

    roll_max = close.rolling(252, min_periods=100).max()
    roll_min = close.rolling(252, min_periods=100).min()
    nh = (close >= roll_max).sum(axis=1)
    nl = (close <= roll_min).sum(axis=1)
    out["nh_nl"] = nh - nl
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
