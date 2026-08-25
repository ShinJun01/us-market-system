#!/usr/bin/env python3
"""
레짐 유효성 검증 (Phase 1 완료 조건).

레짐 라벨이 실제로 미래 수익률을 구분하는지 측정한다.
이 검증을 통과하기 전에 Phase 2로 넘어가면 안 된다.

사용:
    python scripts/validate_regime.py --provider yahoo
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from usv2.data.ingest import ingest, load_config  # noqa: E402
from usv2.engines.regime import RegimeConfig, compute_regime  # noqa: E402
from usv2.validation.regime_validation import (  # noqa: E402
    forward_return_by_regime, monotonicity_score, render_report,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["synthetic", "yahoo"], default=None)
    ap.add_argument("--horizon", type=int, default=20)
    args = ap.parse_args()

    cfg_path = ROOT / "config/data_sources.yaml"
    if args.provider:
        cfg = load_config(cfg_path)
        cfg["provider"] = args.provider
        cfg_path = ROOT / "config/.runtime_data_sources.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    res = ingest(cfg_path)
    print(res.report.summary())
    if res.halt:
        return 2

    rcfg = RegimeConfig.load(ROOT / "config/regime.yaml")
    regime_df = compute_regime(res.market, rcfg)

    table = forward_return_by_regime(regime_df, res.market.close["SPY"])
    mono = monotonicity_score(table, args.horizon)

    meta = {
        "provider": res.provider,
        "start": str(res.market.close.index[0].date()),
        "end": str(res.market.close.index[-1].date()),
        "config_version": rcfg.version,
        "breadth_mode": "proxy" if res.market.breadth.attrs.get("is_proxy") else "compute",
    }
    report = render_report(table, mono, meta)

    out = ROOT / "reports/regime_validation.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding="utf-8")

    print("\n" + table.to_string(float_format=lambda x: f"{x:.4f}"))
    print(f"\nSpearman = {mono['spearman']:.3f}  ->  {'PASS' if mono['passed'] else 'FAIL'}")
    print(f"리포트: {out}")
    return 0 if mono["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
