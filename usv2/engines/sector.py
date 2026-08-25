"""
Sector Rotation Engine (설계서 L2).

11개 GICS 섹터 ETF의 상대강도(RS)를 랭킹한다.

현행 국내장 시스템에서는 섹터 분석("엔비디아 강세 -> 삼성전자 주시")이
감시 대상만 정하고 점수제 어디에도 입력되지 않아 정보가 버려졌다.
V2에서는 섹터 강도가 종목 Composite Score의 F5 팩터로 직접 들어간다.
"""
from __future__ import annotations

import pandas as pd

from usv2.engines import indicators as ind

SECTOR_NAMES = {
    "XLK": "Information Technology", "XLF": "Financials", "XLV": "Health Care",
    "XLY": "Consumer Discretionary", "XLP": "Consumer Staples", "XLI": "Industrials",
    "XLE": "Energy", "XLU": "Utilities", "XLRE": "Real Estate",
    "XLB": "Materials", "XLC": "Communication Services",
}

# 기간별 가중치. 단기 편중 시 노이즈, 장기 편중 시 반응 지연.
RS_WEIGHTS = {21: 0.20, 63: 0.40, 126: 0.40}


def sector_rs(close: pd.DataFrame, benchmark: str = "SPY",
              sectors: list[str] | None = None) -> pd.DataFrame:
    """섹터별 벤치마크 대비 상대강도 시계열."""
    sectors = sectors or [s for s in SECTOR_NAMES if s in close.columns]
    bench = close[benchmark]
    out = pd.DataFrame(index=close.index)
    for s in sectors:
        score = sum(w * (ind.roc(close[s], p) - ind.roc(bench, p))
                    for p, w in RS_WEIGHTS.items())
        out[s] = score
    return out


def sector_ranking(close: pd.DataFrame, benchmark: str = "SPY",
                   as_of=None) -> pd.DataFrame:
    """특정 시점의 섹터 랭킹 테이블.

    z-score는 그 시점 11개 섹터 간 횡단면 표준화 결과다.
    """
    rs = sector_rs(close, benchmark)
    rs = rs.dropna(how="all")
    if rs.empty:
        return pd.DataFrame(columns=["sector", "name", "rs", "z", "rank"])

    row = rs.iloc[-1] if as_of is None else rs.loc[as_of]
    row = row.dropna()
    z = ind.zscore_cross_section(row)

    df = pd.DataFrame({
        "sector": row.index,
        "name": [SECTOR_NAMES.get(s, s) for s in row.index],
        "rs": row.values,
        "z": z.values,
    })
    df = df.sort_values("rs", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    return df


def top_bottom(close: pd.DataFrame, n: int = 3, benchmark: str = "SPY") -> dict:
    """상위/하위 n개 섹터. 브리핑 [3] 섹션의 SIGNAL 소스."""
    df = sector_ranking(close, benchmark)
    if df.empty:
        return {"strong": [], "weak": [], "as_of": None}
    to_rec = lambda r: {"sector": r.sector, "name": r["name"],
                        "z": round(float(r.z), 2), "rank": int(r["rank"])}
    return {
        "strong": [to_rec(r) for _, r in df.head(n).iterrows()],
        "weak": [to_rec(r) for _, r in df.tail(n).iloc[::-1].iterrows()],
        "as_of": str(close.index[-1].date()),
    }
