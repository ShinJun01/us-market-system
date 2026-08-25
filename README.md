# US Market V2 — Phase 1

미국시장(NASDAQ/NYSE) AI 투자 시스템 V2의 **Phase 1 구현체**.

설계서: `docs/US_MARKET_V2_ARCHITECTURE.md`
용어 해설: `docs/V2_용어사전.md`

---

## Phase 1의 범위 — 그리고 범위가 아닌 것

**구현됨**

| 레이어 | 모듈 | 상태 |
|---|---|---|
| L0 데이터 수집 | `usv2/data/providers.py`, `ingest.py` | synthetic / yahoo |
| L0 데이터 검증 | `usv2/data/validate.py` | V1~V7 게이트 |
| L1 레짐 엔진 | `usv2/engines/regime.py` | 4 Pillar, 6단계, 확인규칙, 오버라이드 |
| L2 섹터 로테이션 | `usv2/engines/sector.py` | 11 GICS RS 랭킹 |
| 브리핑 | `usv2/brief/build.py` | 섹션 [1][2][3][5][7] |
| 환각 차단 | `usv2/brief/numeric_guard.py` | 수치 검증기 |
| 레짐 감사 | `usv2/validation/regime_validation.py` | 단조성 검정 |

**의도적으로 구현하지 않음 (Phase 2 이후)**

- 종목 선정 (Stock Screening Engine) — 시점별 구성종목 데이터 확보 후
- 매매 신호 (S1~S5 전략)
- 백테스트 엔진
- 리스크/사이징 엔진
- 브로커 연동

> Phase 1은 **매매하지 않는다.** 정확한 데이터 파이프라인과 레짐 판별기를 먼저 만든다.
> 이 단계를 건너뛰면, 나중에 손실이 났을 때 데이터 문제인지 전략 문제인지 구분할 수 없다.

---

## 빠른 시작

```bash
pip install -r requirements.txt

# 합성 데이터로 파이프라인 동작 확인 (네트워크 불필요)
python scripts/run_daily.py

# 실제 데이터 (인터넷 필요)
python scripts/run_daily.py --provider yahoo

# Phase 1 완료 조건 검증
python scripts/validate_regime.py --provider yahoo

# 테스트
pytest -q
```

---

## 설계 원칙 (코드에 강제되어 있음)

**1. 코드가 판단하고, AI는 설명만 한다**

브리핑의 모든 숫자는 `usv2/engines/`가 계산한 값이다. LLM은 해석 문장만 쓰고,
그 결과물은 `numeric_guard`를 통과해야 발행된다. 출처 없는 숫자가 하나라도 있으면 차단된다.

```
FACT → SIGNAL → RULE → ACTION      (돈에 도달하는 경로)
                  ↘ INTERPRETATION  (AI. ACTION으로 가는 화살표 없음)
```

**2. 검증 실패는 HALT다**

V1~V7 게이트 중 하나라도 FAIL이면 브리핑을 생성하지 않는다.
"데이터가 좀 이상하지만 일단 진행"은 허용하지 않는다.

**3. 미래 데이터를 참조하지 않는다**

`indicators.py`의 모든 롤링 윈도우는 과거 방향만 본다.
`forward_return()`은 사후 검증 전용이며 신호 생성 경로에서 import하면 안 된다.
`tests/test_regime.py::test_no_lookahead_regime_is_stable_under_truncation`이 이를 검사한다.

**4. 검증되지 않은 것을 전략이라 부르지 않는다**

Phase 1 브리핑은 종목 후보를 만들어내지 않는다. 없는 건 없다고 쓴다.

---

## 현재 알려진 한계

| # | 한계 | 해소 시점 |
|---|---|---|
| 1 | **Breadth가 프록시** — RSP/SPY 근사. 지수 내부 붕괴 감지 정확도 낮음 | Phase 2 전, 구성종목 직접 계산으로 전환 필수 |
| 2 | **2차 데이터 소스 없음** — V5 교차검증이 WARN에 머묾 | Phase 3(실자본) 전 필수 |
| 3 | **이벤트 캘린더 미연결** — FOMC/CPI/실적이 자동 반영 안 됨 | Phase 2 |
| 4 | **레짐 파라미터 미검증** — `config/regime.yaml`의 모든 수치는 초기 가설값 | `validate_regime.py` PASS 시 |
| 5 | Numeric Guard가 0~12 정수는 통과시킴 (문서화된 한계) | 브리핑 템플릿이 소수 표기 강제로 완화 |
| 6 | yfinance는 시점별 구성종목 미제공 | Phase 2에서 유료 데이터 전환 |

---

## 파라미터 변경 규칙

`config/*.yaml`의 값을 바꿀 때는 반드시:

1. 커밋 메시지에 **변경 사유**를 쓴다
2. 변경 **전/후 `validate_regime.py` 결과**를 함께 남긴다
3. `version` 필드를 올린다

이유: 나중에 성과가 변했을 때 "언제 무엇을 왜 바꿨는지" 모르면 원인을 해석할 수 없다.
그리고 백테스트 시도 횟수를 모르면 Deflated Sharpe를 계산할 수 없고,
계산할 수 없으면 과최적화 여부를 영영 알 수 없다.

**금지:** 성과가 좋아지는 방향으로 파라미터를 탐색하는 것.
조정은 오직 **단조성 회복**을 목표로만 한다.

---

## 다음 단계

`docs/NEXT_STEPS.md` 참조.
