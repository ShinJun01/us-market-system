#!/usr/bin/env python3
"""
S&P500 구성종목 리스트 확보 (NEXT_STEPS.md 3번, 최초 1회 실행).

Wikipedia 표를 받아 config/sp500_constituents.csv 로 저장한다.
yfinance 티커 표기에 맞춰 '.'을 '-'로 치환한다 (예: BRK.B -> BRK-B).

!!! 주의 !!!
이건 "오늘 시점"의 구성종목이다. 과거로 소급 적용하면 생존편향이 생긴다.
Phase 1 브리핑(breadth 계산)용으로만 쓰고, Phase 2 백테스트에는 쓰지 않는다
(설계서 5-2, NEXT_STEPS.md 3번 하단 주의사항).

사용:
    python scripts/fetch_sp500_constituents.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
OUT = ROOT / "config/sp500_constituents.csv"


def main() -> int:
    resp = requests.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    table = pd.read_html(io.StringIO(resp.text), attrs={"id": "constituents"})[0]

    symbols = table["Symbol"].str.replace(".", "-", regex=False).str.strip()
    out = pd.DataFrame({"symbol": symbols.sort_values().unique()})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"완료: {OUT} ({len(out)}종목)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
