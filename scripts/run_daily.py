#!/usr/bin/env python3
"""
일일 실행 (설계서 7장 'T-1 마감 후 분석' 시점).

실행 시각: 16:30 ET / 익일 05:30 KST
흐름: 데이터 수집 -> V1~V7 검증 -> 레짐 -> 섹터 -> 브리핑 -> Numeric Guard -> 저장

사용:
    python scripts/run_daily.py
    python scripts/run_daily.py --provider yahoo     # 로컬(집)에서
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from usv2.brief import build as brief_build, numeric_guard  # noqa: E402
from usv2.data.ingest import ingest, load_config  # noqa: E402
from usv2.engines import sector  # noqa: E402
from usv2.engines.regime import RegimeConfig, compute_regime, latest_regime  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["synthetic", "yahoo", "cboe", "composite"], default=None)
    ap.add_argument("--out", default="reports")
    args = ap.parse_args()

    cfg_path = ROOT / "config/data_sources.yaml"
    if args.provider:
        cfg = load_config(cfg_path)
        cfg["provider"] = args.provider
        cfg_path = ROOT / "config/.runtime_data_sources.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    print("[1/5] 데이터 수집 중...")
    res = ingest(cfg_path)

    print("[2/5] 데이터 검증 (V1~V7)")
    print(res.report.summary())
    if res.halt:
        print("\n>>> HALT. 브리핑을 생성하지 않습니다.")
        print(">>> 검증되지 않은 데이터로 판단을 내리지 않는 것이 이 시스템의 원칙입니다.")
        return 2

    print("[3/5] 레짐 계산")
    rcfg = RegimeConfig.load(ROOT / "config/regime.yaml")
    regime_df = compute_regime(res.market, rcfg)
    reg = latest_regime(regime_df)
    print(f"    REGIME={reg['regime']} total={reg['total']} "
          f"(A{reg['pillar_a']} B{reg['pillar_b']} C{reg['pillar_c']} D{reg['pillar_d']})")

    print("[4/5] 섹터 랭킹")
    sectors = sector.top_bottom(res.market.close, n=3)

    meta = {
        "provider": res.provider,
        "validation": "PASS" if not res.report.halt else "HALT",
        "config_version": rcfg.version,
        "breadth_mode": "proxy" if res.market.breadth.attrs.get("is_proxy") else "compute",
    }

    print("[5/5] 브리핑 생성 + Numeric Guard")
    text = brief_build.render(reg, sectors, meta)
    facts = brief_build.build_facts(reg, sectors, meta)
    guard = numeric_guard.check(text, facts)
    print(f"    {guard.report()}")
    if not guard.passed:
        print(">>> 브리핑 발행 차단.")
        return 3

    outdir = ROOT / args.out
    outdir.mkdir(exist_ok=True)
    stamp = reg["date"].replace("-", "")
    (outdir / f"brief_{stamp}.md").write_text(text, encoding="utf-8")
    (outdir / f"regime_{stamp}.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    regime_df.to_csv(outdir / "regime_history.csv")

    print(f"\n완료: {outdir}/brief_{stamp}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
