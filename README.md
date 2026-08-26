# US Market V2 — Phase 1

미국시장(NASDAQ/NYSE) AI 투자 시스템 V2의 **Phase 1 구현체**.

> **이 문서 하나만 보고 따라가면 된다.** 다른 문서를 먼저 읽을 필요 없다.

---

## 0. 지금 어디까지 왔나

| 항목 | 상태 | 비고 |
|---|---|---|
| Phase | **1** — 데이터 + 레짐 + 브리핑 | **매매 안 함. 종목 추천 안 함.** |
| 테스트 | 47개 | `pytest -q` |
| 데이터 프로바이더 | synthetic / yahoo / cboe / composite | 로컬 권장은 `composite` |
| breadth | **compute 모드** | 분모 버그 수정 완료 |
| VIX 기간구조 | CBOE 어댑터 | ⚠️ **실응답 미검증** — 첫 실행에서 확인 필요 |
| Pillar 척도 정규화 | 적용됨 | 입력 누락 시 100점 척도 보정 |
| **레짐 검증** | **미통과** | ← **이번 라운드의 목표** |
| 2차 데이터 소스 | 없음 | V5가 WARN에 머묾. Phase 3 전 필수 |

**이번 라운드에서 확인해야 할 것은 단 하나다.**

```
>>> Spearman = 0.xxx  ->  PASS / FAIL     (기준 0.80)
```

이 숫자가 Phase 1의 합격/불합격을 가른다. 나머지는 전부 이 숫자를 얻기 위한 준비다.

---

## 1. 집에서 할 일 — 명령 한 줄

```bash
cd us-market-system
python scripts/handoff.py
```

끝이다. 이 스크립트가 패치 적용 → 테스트 → 파이프라인 실행 → CSV 덤프 → 커밋 → 푸시를
순서대로 전부 한다.

**실패해도 멈추지 않는다.** HALT나 예외가 나는 것 자체가 진단 정보이고, 그 로그가
`results/`에 남아야 원격에서 원인을 볼 수 있다. 성공한 실행만 기록하면 정작 필요한
실패 사례가 사라진다.

### 옵션

```bash
python scripts/handoff.py --no-push        # 커밋만, 푸시는 수동으로
python scripts/handoff.py --provider yahoo # CBOE 없이 yfinance만
python scripts/handoff.py --fresh-cache    # 구성종목 캐시 강제 재다운로드
python scripts/handoff.py --skip-patch     # 패치 적용 건너뛰기
```

---

## 2. 커리큘럼 — 이번 라운드

순서대로 하고, 각 단계의 **완료 조건**을 만족하지 못하면 다음으로 넘어가지 않는다.

### STEP 0 — 준비 (5분)

```bash
cd us-market-system
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

받은 파일을 제자리에 놓는다.

| 파일 | 위치 |
|---|---|
| `fix_breadth_and_scale.patch` | 저장소 루트 |
| `handoff.py` | `scripts/` |
| `README.md` (이 파일) | 저장소 루트 (기존 것 덮어쓰기) |
| `US_MARKET_V2_ARCHITECTURE.md` | `docs/` |
| `V2_용어사전.md` | `docs/` |

> `docs/`는 용량 때문에 전송 payload에서 빠져 있었다. 없어도 코드는 돌지만,
> 설계 근거를 찾을 때 필요하니 넣어두는 편이 낫다.

**완료 조건:** `pip list`에 pandas, numpy, yfinance, requests, pyarrow가 보인다.

---

### STEP 1 — 패치 적용 + 테스트 (5분)

```bash
python scripts/handoff.py --provider synthetic --no-push
```

합성 데이터로 먼저 돌린다. **네트워크를 안 타므로 코드 자체의 문제와
데이터의 문제를 분리할 수 있다.** 이 구분이 나중에 원인 추적을 훨씬 쉽게 만든다.

**완료 조건**
- `results/04_pytest.log` → `47 passed`
- 터미널에 `Spearman = 0.xxx` 줄이 출력됨 (값이 FAIL이어도 무방)

여기서 실패하면 데이터 문제가 아니라 **코드/환경 문제**다. `results/04_pytest.log`를
그대로 전달하고 멈춘다.

---

### STEP 2 — 구성종목 확보 (최초 1회, 2분)

```bash
python scripts/fetch_sp500_constituents.py
```

**완료 조건:** `config/sp500_constituents.csv`에 490~510행.

> 이건 **오늘 시점**의 구성종목이다. 과거로 소급 적용하면 생존편향이 생긴다.
> Phase 1 브리핑(breadth 계산)용으로만 쓰고, **Phase 2 백테스트에는 절대 쓰지 않는다.**

---

### STEP 3 — 실제 데이터 첫 실행 (10~30분)

```bash
rm -f data/cache/sp500_close.parquet     # 버그 있던 캐시 폐기. Windows: del
python scripts/handoff.py --provider composite
```

500종목 × 10년치를 처음 받으므로 시간이 걸린다. 이후 실행은 캐시를 쓴다.

**중간에 확인할 것**

```
[ingest] 구성종목 4xx/50x 수신 (9x.x%)
```

90% 미만이면 스크립트가 중단시킨다. 그건 버그가 아니라 설계된 동작이다 —
절반만 받은 데이터로 breadth를 계산하면 틀린 줄도 모르고 쓰게 된다.

**완료 조건**
- `results/01_run_daily.log`에 `REGIME=...` 줄이 나옴
- `results/prices.csv` 파일 크기가 0이 아님

---

### STEP 4 — 결과 해석 (5분)

터미널 마지막 줄을 본다.

```
>>> Spearman = 0.xxx  ->  PASS/FAIL
```

| 결과 | 의미 | 다음 |
|---|---|---|
| **PASS (≥0.80)** | 레짐 라벨이 실제로 미래 수익률을 구분함 | STEP 5로. Phase 2 진입 조건 검토 시작 |
| **FAIL (<0.80)** | 레짐 라벨에 예측력이 없음 | 결과 푸시 → 원격에서 원인 진단 |

**FAIL이라고 실패한 게 아니다. 검증 장치가 작동한다는 증거다.**

여기서 통과시키려고 `config/regime.yaml`을 만지면 그게 과최적화의 출발점이다.
원인을 특정하기 전에는 손대지 않는다.

`results/regime_validation.md`를 열면 레짐별 후행 수익률 표가 있다.
어느 구간에서 순서가 뒤집혔는지 눈으로 확인해 두면 다음 논의가 빨라진다.

---

### STEP 5 — 푸시 (자동)

`handoff.py`가 자동으로 한다. 실패하면:

```bash
gh auth login          # 또는 SSH 키 설정
git push
```

> **개인 액세스 토큰을 코드·채팅·커밋에 넣지 않는다.** 대화 로그에 영구히 남는다.

**완료 조건:** GitHub 웹에서 `results/` 폴더에 파일 9개가 보인다.

---

### STEP 6 — 전달

저장소 URL만 말하면 된다. 복사할 것 없다.

```
https://github.com/ShinJun01/us-market-system
```

원격이 clone해서 읽는 순서:

1. `results/02_validate.log` — Spearman 값
2. `results/01_run_daily.log` — V1~V7 게이트 결과
3. `results/prices.csv` + `regime_history.csv` — **판정을 독립 재현할 재료**
4. `results/breadth.csv` — breadth 품질
5. `results/00_env.json` — 버전 차이로 인한 재현 불일치 추적

3번이 있어야 원격이 "어느 Pillar가 단조성을 깨는지"를 직접 계산할 수 있다.
로그만 있으면 추측만 하게 된다.

---

## 3. 문제 대응표

| 증상 | 원인 | 대응 |
|---|---|---|
| `git am` 충돌 | 패치가 이미 적용됨 | 정상. 스크립트가 자동으로 건너뛴다 |
| **CBOE 어댑터 실패** | URL/컬럼명 변경 | 예외에 받은 헤더가 찍힌다. `01_run_daily.log`에 남으니 그대로 푸시 |
| **V6 게이트 HALT** | 심볼 누락 | 로그에 어느 심볼인지 나온다. `--provider yahoo`로 우회 가능하나 Pillar C 정보가 준다 |
| 구성종목 수신율 90% 미만 | yfinance 일부 실패 | 재실행. 반복되면 `--fresh-cache` |
| V2 신선도 FAIL | 주말/공휴일 실행 | 정상 동작. 평일 미국장 마감 후 실행 |
| `^VIX9D` 없음 | yfinance 미지원 | `composite`가 CBOE로 라우팅한다. 그래도 실패하면 헤더 전달 |
| pytest 실패 | 패치 충돌 / 버전 차이 | `04_pytest.log` + `00_env.json` 전달 |
| 브리핑에 종목이 없음 | **정상** | Phase 1은 종목 선정을 하지 않는다 |

---

## 4. Phase 1의 범위 — 그리고 범위가 아닌 것

**구현됨**

| 레이어 | 모듈 |
|---|---|
| L0 데이터 수집 | `usv2/data/providers.py`, `ingest.py` |
| L0 데이터 검증 | `usv2/data/validate.py` — V1~V7 게이트 |
| L1 레짐 엔진 | `usv2/engines/regime.py` — 4 Pillar, 6단계, 2일 확인규칙, 오버라이드 |
| L2 섹터 로테이션 | `usv2/engines/sector.py` — 11 GICS RS 랭킹 |
| 브리핑 | `usv2/brief/build.py` — 섹션 [1][2][3][5][7] |
| 환각 차단 | `usv2/brief/numeric_guard.py` |
| 레짐 감사 | `usv2/validation/regime_validation.py` — 단조성 검정 |

**의도적으로 구현하지 않음**

- 종목 선정 (Stock Screening Engine)
- 매매 신호 (S1~S5 전략)
- 백테스트 엔진
- 리스크 / 포지션 사이징
- 브로커 연동

> Phase 1은 **매매하지 않는다.** 정확한 데이터 파이프라인과 레짐 판별기를 먼저 만든다.
> 이 단계를 건너뛰면, 나중에 손실이 났을 때 데이터 문제인지 전략 문제인지 구분할 수 없다.

---

## 5. 설계 원칙 (코드에 강제되어 있음)

**1. 코드가 판단하고, AI는 설명만 한다**

브리핑의 모든 숫자는 `usv2/engines/`가 계산한 값이다. LLM은 해석 문장만 쓰고,
그 결과물은 `numeric_guard`를 통과해야 발행된다.

```
FACT → SIGNAL → RULE → ACTION       (돈에 도달하는 경로)
                  ↘ INTERPRETATION   (AI. ACTION으로 가는 화살표 없음)
```

**2. 검증 실패는 HALT다**

V1~V7 중 하나라도 FAIL이면 브리핑을 생성하지 않는다.
"데이터가 좀 이상하지만 일단 진행"은 허용하지 않는다.
**게이트를 끄는 순간 이 시스템은 기존 국내장 시스템과 같은 취약점을 갖는다.**

**3. 미래 데이터를 참조하지 않는다**

`indicators.py`의 모든 롤링 윈도우는 과거 방향만 본다.
`forward_return()`은 사후 검증 전용이며, 신호 생성 경로에서 import하면 CI가 실패한다.
`tests/test_regime.py::test_no_lookahead_regime_is_stable_under_truncation`이 검사한다.

**4. 정보 부족을 숨기지 않는다**

입력 심볼이 빠지면 해당 Pillar의 획득 가능 최대치가 줄어든다. 그대로 두면 총점이
통째로 아래로 밀려 BULL이 NEUTRAL로 오분류된다. 그래서 사용 가능한 항목의 만점으로
나눠 25점 척도로 환산하고, `pillar_x_coverage`로 "이 축은 몇 %의 정보로 계산됐는지"를
드러낸다.

**5. 검증되지 않은 것을 전략이라 부르지 않는다**

Phase 1 브리핑은 종목 후보를 만들어내지 않는다. 없는 건 없다고 쓴다.

---

## 6. 알려진 한계

| # | 한계 | 영향 | 해소 시점 |
|---|---|---|---|
| 1 | **CBOE 어댑터 실응답 미검증** | VIX 기간구조 확보 실패 시 Pillar C 커버리지 68% | 첫 실행에서 확인 |
| 2 | **2차 데이터 소스 없음** | V5 교차검증이 WARN에 머묾 | Phase 3(실자본) 전 필수 |
| 3 | **이벤트 캘린더 미연결** | FOMC/CPI/실적 미반영 | Phase 2 |
| 4 | **레짐 파라미터 미검증** | `regime.yaml` 전 수치가 초기 가설값 | `validate_regime` PASS 시 |
| 5 | **구성종목이 오늘 기준** | 과거 breadth에 생존편향 | Phase 2에서 유료 데이터 |
| 6 | Numeric Guard가 0~12 정수 통과 | 문서화된 한계 | 브리핑 템플릿의 소수 표기로 완화 |
| 7 | yfinance는 시점별 구성종목 미제공 | 백테스트 불가 | Phase 2에서 유료 전환 |

---

## 7. 파라미터 변경 규칙

`config/*.yaml`을 바꿀 때는 반드시:

1. 커밋 메시지에 **변경 사유**를 쓴다
2. 변경 **전/후 `validate_regime.py` 결과**를 함께 남긴다
3. `version` 필드를 올린다

이유: 나중에 성과가 변했을 때 "언제 무엇을 왜 바꿨는지" 모르면 원인을 해석할 수 없다.
그리고 시도 횟수를 모르면 Deflated Sharpe를 계산할 수 없고, 계산할 수 없으면
과최적화 여부를 영영 알 수 없다.

**금지:** 성과가 좋아지는 방향으로 파라미터를 탐색하는 것.
조정은 오직 **단조성 회복**을 목표로만 한다.

---

## 8. Phase 2 진입 조건

아래 4개가 **전부** 충족되기 전에는 종목 선정 코드를 쓰지 않는다.

- [ ] `validate_regime.py` 실제 데이터에서 **Spearman ≥ 0.80**
- [ ] breadth compute 모드 정상 동작 (구성종목 수신율 90% 이상)
- [ ] 데이터 검증 게이트 **30일 연속 무오류**
- [ ] 브리핑 **30일 연속 자동 생성** (다운타임 0)

30일이 길게 느껴지겠지만, 검증 안 된 파이프라인 위에 전략을 쌓는 것보다 기댓값이 높다.
그리고 그 30일 동안 쌓이는 `reports/regime_history.csv`가 Phase 2에서 레짐 감사(L11-C)의
기준 데이터가 된다.

### 자동 실행 등록 (30일 카운트 시작용)

미국장 마감 16:30 ET = KST 05:30 (서머타임 기준)

**cron (macOS/Linux)**
```cron
30 5 * * 2-6 cd /path/to/us-market-system && .venv/bin/python scripts/handoff.py >> logs/daily.log 2>&1
```

**Windows 작업 스케줄러**
- 트리거: 매일 05:30
- 동작: `C:\path\us-market-system\.venv\Scripts\python.exe scripts\handoff.py`
- 시작 위치: `C:\path\us-market-system`

### Phase 2 착수 시 미리 준비할 것

| 항목 | 비고 |
|---|---|
| **시점별 구성종목 + 상장폐지 종목** | 유료. Sharadar / Norgate / Polygon. **없으면 백테스트 숫자 전체가 무효** |
| 실적 캘린더 API | Finnhub / FMP / Nasdaq |
| 매크로 캘린더 | FRED release calendar |

Phase 2의 첫 커밋은 전략이 아니라 **백테스트 엔진**이어야 한다.
엔진이 부정확하면 이후 몇 달이 전부 낭비된다.

---

## 9. 파일 지도

```
us-market-system/
├── README.md                    ← 이 문서. 집에서 읽는 유일한 진입점
├── fix_*.patch                  ← 원격이 보낸 패치. handoff.py가 자동 적용
├── config/
│   ├── data_sources.yaml        provider, 심볼, breadth 모드
│   ├── regime.yaml              ★ 4 Pillar 임계값 (전부 미검증 가설값)
│   ├── risk.yaml                Phase 3용
│   ├── universe.yaml            Phase 2용
│   └── sp500_constituents.csv   STEP 2에서 생성
├── usv2/
│   ├── data/       providers / ingest / validate
│   ├── engines/    indicators / regime / sector
│   ├── brief/      build / numeric_guard
│   └── validation/ regime_validation
├── scripts/
│   ├── handoff.py               ★ 이것만 실행하면 됨
│   ├── run_daily.py
│   ├── validate_regime.py
│   └── fetch_sp500_constituents.py
├── tests/                       47개
├── results/                     ★ 원격에 전달되는 실행 결과 (커밋됨)
├── reports/                     로컬 산출물 (gitignore)
└── data/cache/                  parquet 캐시 (gitignore)
```

---

## 10. 협업 루프

```
[원격]  clone → 코드·결과 분석 → 패치 + README 갱신본 전달
   ↓
[집]    파일을 제자리에 저장
   ↓
[집]    python scripts/handoff.py
   ↓
[GitHub] 패치 커밋 + results/ 결과 커밋
   ↓
[원격]  clone → 다음 라운드
```

공개 저장소는 인증 없이 clone되므로 **집 → 원격 방향은 완전히 열려 있다.**
원격이 GitHub에 직접 쓰는 것만 자격증명 때문에 막히고, 그건 "패치를 받아 커밋"으로
대체된다. `handoff.py`가 그 한 단계를 자동화한다.
