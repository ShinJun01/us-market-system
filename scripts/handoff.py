#!/usr/bin/env python3
"""
handoff.py — 집(로컬)에서 한 번 실행하면 끝나는 통합 스크립트.

하는 일
-------
1. 같은 폴더의 *.patch 파일이 있으면 적용
2. breadth 캐시가 오래됐거나 강제 지정 시 폐기
3. run_daily / validate_regime 실행 (로그를 results/에 저장)
4. 가격·breadth·레짐 히스토리를 results/에 CSV로 덤프
5. 환경 정보(파이썬/패키지 버전, git 커밋) 기록
6. git commit + push

사용
----
    python scripts/handoff.py                    # 전체 실행
    python scripts/handoff.py --no-push          # 커밋만, 푸시 안 함
    python scripts/handoff.py --provider yahoo   # 프로바이더 지정
    python scripts/handoff.py --fresh-cache      # 구성종목 캐시 강제 재다운로드

설계 의도
---------
실패해도 멈추지 않고 끝까지 간다. HALT나 예외가 나는 것 자체가 진단 정보이고,
그 로그가 results/에 남아야 원격에서 원인을 볼 수 있다.
"성공한 실행만 기록"하면 정작 필요한 실패 사례가 사라진다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def run(cmd: list[str], log: Path | None = None, cwd: Path = ROOT) -> tuple[int, str]:
    """명령 실행. 실패해도 예외를 올리지 않고 (코드, 출력)을 돌려준다."""
    shown = " ".join(cmd)
    if len(shown) > 110:                      # -c 로 넘기는 긴 코드는 잘라서 표시
        shown = shown[:107] + "..."
    print(f"  $ {shown}")
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=3600)
        out = (p.stdout or "") + (p.stderr or "")
        code = p.returncode
    except Exception as e:  # noqa: BLE001
        out, code = f"[handoff] 실행 실패: {e!r}", 1
    if log:
        log.write_text(out, encoding="utf-8")
    return code, out


def step_patch() -> list[str]:
    """루트의 *.patch 적용. 이미 적용된 패치는 건너뛴다."""
    applied = []
    for patch in sorted(ROOT.glob("*.patch")):
        print(f"\n[패치] {patch.name}")
        code, out = run(["git", "am", "--3way", str(patch)])
        if code != 0:
            run(["git", "am", "--abort"])
            code, out = run(["git", "apply", "--3way", str(patch)])
            if code != 0:
                print(f"  건너뜀 (이미 적용됐거나 충돌): {out.strip().splitlines()[:2]}")
                continue
            run(["git", "add", "-A"])
            run(["git", "commit", "-m", f"apply {patch.name}"])
        applied.append(patch.name)
        print(f"  적용 완료")
    return applied


def step_cache(fresh: bool) -> None:
    cache = ROOT / "data/cache/sp500_close.parquet"
    if not cache.exists():
        return
    age_days = (dt.datetime.now() - dt.datetime.fromtimestamp(cache.stat().st_mtime)).days
    if fresh or age_days > 5:
        cache.unlink()
        print(f"\n[캐시] sp500_close.parquet 폐기 (경과 {age_days}일)")


def step_dump(provider: str) -> None:
    """가격/breadth/레짐 히스토리를 CSV로. 원격 재현 분석의 재료가 된다."""
    code = f'''
import sys, traceback
sys.path.insert(0, {str(ROOT)!r})
from pathlib import Path
out = Path({str(RESULTS)!r})
try:
    import yaml
    from usv2.data.ingest import ingest, load_config
    cfg = load_config({str(ROOT / "config/data_sources.yaml")!r})
    cfg["provider"] = {provider!r}
    rt = Path({str(ROOT)!r}) / "config/.runtime_data_sources.yaml"
    rt.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    r = ingest(rt)
    print(r.report.summary())
    if r.market is None:
        print("HALT — market 데이터 없음")
    else:
        r.market.close.to_csv(out / "prices.csv")
        r.market.breadth.to_csv(out / "breadth.csv")
        print("prices.csv / breadth.csv 저장")

        from usv2.engines.regime import RegimeConfig, compute_regime
        df = compute_regime(r.market, RegimeConfig.load(Path({str(ROOT)!r}) / "config/regime.yaml"))
        df.to_csv(out / "regime_history.csv")
        print("regime_history.csv 저장", df.shape)
except Exception:
    traceback.print_exc()
'''
    run([sys.executable, "-c", code], RESULTS / "03_dump.log")


def step_env() -> None:
    info = {
        "생성시각": dt.datetime.now().isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    for pkg in ("pandas", "numpy", "yfinance", "pyarrow", "requests"):
        try:
            info[pkg] = __import__(pkg).__version__
        except Exception:  # noqa: BLE001
            info[pkg] = "미설치"
    for key, cmd in [("commit", ["git", "rev-parse", "--short", "HEAD"]),
                     ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"])]:
        _, out = run(cmd)
        info[key] = out.strip()
    (RESULTS / "00_env.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="composite",
                    choices=["synthetic", "yahoo", "cboe", "composite"])
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--fresh-cache", action="store_true")
    ap.add_argument("--skip-patch", action="store_true")
    args = ap.parse_args()

    RESULTS.mkdir(exist_ok=True)
    print(f"=== handoff 시작 (provider={args.provider}) ===")

    if not args.skip_patch:
        step_patch()

    print("\n[환경 기록]")
    step_env()

    print("\n[테스트]")
    code, _ = run([sys.executable, "-m", "pytest", "-q"], RESULTS / "04_pytest.log")
    print(f"  {'통과' if code == 0 else '실패 — 로그 확인'}")

    step_cache(args.fresh_cache)

    print("\n[파이프라인]")
    run([sys.executable, "scripts/run_daily.py", "--provider", args.provider],
        RESULTS / "01_run_daily.log")
    run([sys.executable, "scripts/validate_regime.py", "--provider", args.provider],
        RESULTS / "02_validate.log")

    print("\n[데이터 덤프]")
    step_dump(args.provider)

    for name in ("regime_validation.md",):
        src = ROOT / "reports" / name
        if src.exists():
            (RESULTS / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    print("\n[결과]")
    for f in sorted(RESULTS.iterdir()):
        print(f"  {f.name:<28} {f.stat().st_size:>9,} bytes")

    # Spearman 값을 즉시 보여준다 — 이 숫자 하나가 다음 단계를 결정한다
    vlog = RESULTS / "02_validate.log"
    if vlog.exists():
        for line in vlog.read_text(encoding="utf-8").splitlines():
            if "Spearman" in line:
                print(f"\n  >>> {line.strip()}")

    print("\n[git]")
    run(["git", "add", "-A", "--force", "results"])
    run(["git", "add", "-A"])
    msg = f"결과: {dt.date.today()} provider={args.provider}"
    code, out = run(["git", "commit", "-m", msg])
    if code != 0 and "nothing to commit" in out:
        print("  변경 없음")
    if not args.no_push:
        code, out = run(["git", "push"])
        print("  푸시 완료" if code == 0 else f"  푸시 실패:\n{out}")

    print("\n=== 완료. results/ 내용이 올라갔는지 GitHub에서 확인하세요 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
