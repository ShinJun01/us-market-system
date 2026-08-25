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
    if mode == "proxy":
        breadth = _proxy_breadth(close)
        breadth.attrs["is_proxy"] = True
    else:
        raise NotImplementedError(
            "breadth.mode=compute는 Phase 2에서 구현합니다. "
            "S&P500 구성종목 일봉 전체가 필요합니다."
        )

    md = MarketData(close=close, breadth=breadth)
    return IngestResult(md, report, bars, provider.name)
