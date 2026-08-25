"""
Numeric Guard (설계서 14-3).

목적
----
LLM이 작성한 브리핑 텍스트에 등장하는 **모든 숫자**를 추출해,
코드가 실제로 계산한 FACT/SIGNAL 값과 대조한다.
출처 불명 수치가 하나라도 있으면 브리핑 발행을 차단한다.

이 장치가 막는 것
-----------------
현행 국내장 시스템의 최대 취약점: LLM이 "VIX 14.89"라고 쓸 때 그게 실제 값인지
그럴듯하게 지어낸 값인지 검증할 방법이 없고, 그 숫자가 점수제 입력이 되어
환각이 매매 신호로 전파된다.

설계 원칙
---------
FAIL-CLOSED. 판단이 애매하면 통과가 아니라 차단이다.
"이 정도면 괜찮겠지"로 통과시키기 시작하면 장치 전체가 무의미해진다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 숫자 토큰: 1,234.56 / -3.2 / 45% / $1.2M 등에서 수치부만 뽑는다
_NUM_RE = re.compile(r"[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?|[-+]?\d+(?:\.\d+)?")

# 검증에서 면제되는 숫자.
# 날짜/시각/버전/서수/각주처럼 시장 데이터가 아닌 것들.
_EXEMPT_PATTERNS = [
    re.compile(r"\b(19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b"),   # 2026-08-25
    re.compile(r"\b\d{1,2}:\d{2}\s*(ET|KST|AM|PM)?\b", re.I),   # 09:30 ET
    re.compile(r"\bv?\d+\.\d+\.\d+\b"),                          # 1.0.0
    # 설계서 참조 라벨: Q1~Q13(브리핑 질문), S1~S5(전략), V1~V7(검증게이트),
    # L0~L12(아키텍처 레이어), F1~F6(팩터), G1~G5(하드게이트), R-047(규칙ID)
    re.compile(r"\b(Q\d{1,2}|S[1-9]|V[1-9]|L\d{1,2}|F[1-9]|G[1-5]|R-\d+)\b"),
    re.compile(r"\[\d+\]"),                                      # [1] 섹션 번호
    re.compile(r"#\d+"),
]

# 소규모 정수는 문장 구조상 불가피하게 나타난다 (섹터 3개, 상위 5개 등).
# 화이트리스트로 두되 범위를 좁게 유지한다.
_SMALL_INT_ALLOW = set(range(0, 13))


@dataclass
class GuardResult:
    passed: bool
    unknown: list[str] = field(default_factory=list)
    checked: int = 0
    exempt: int = 0

    def report(self) -> str:
        if self.passed:
            return f"PASS — 수치 {self.checked}건 전부 검증됨 (면제 {self.exempt}건)"
        return (
            f"FAIL — 미검증 수치 {len(self.unknown)}건: {self.unknown[:12]}\n"
            f"       브리핑 발행이 차단되었습니다. LLM이 데이터에 없는 숫자를 생성했습니다."
        )


def _round_variants(v: float) -> set[str]:
    """하나의 **실제 값**에 대해 LLM이 쓸 법한 표기 변형들을 생성.

    17.2345 -> {"17.2345", "17.235", "17.23", "17.2", "17", ...}
    0.803   -> 위 + 퍼센트 표기 {"80.3", "80", ...}

    !!! 이 함수는 오직 허용 집합(allowed) 생성에만 쓴다. !!!
    텍스트에서 뽑은 토큰을 이 함수로 확장해서 비교하면 안 된다.
    그렇게 하면 환각 수치 14.89가 반올림되어 실제값 15.0과 일치하면서
    통과해버린다. 확장은 소스 방향으로만, 비교는 정확히.
    """
    out: set[str] = set()
    if v is None:
        return out
    try:
        f = float(v)
    except (TypeError, ValueError):
        return out
    if f != f:  # NaN
        return out

    cands = [f]
    if abs(f) <= 1.0:
        cands.append(f * 100.0)      # 비율 -> 퍼센트 표기
    if abs(f) >= 1_000:
        cands.append(f / 1_000)
    if abs(f) >= 1_000_000:
        cands.append(f / 1_000_000)  # 백만 단위 축약

    for c in cands:
        for nd in range(0, 5):
            r = round(c, nd)
            out.add(f"{r:.{nd}f}")   # "71", "71.0", "71.00"
            out.add(f"{r:g}")        # "71", "71.5"
    return {s.lstrip("+") for s in out}


def build_allowed(*sources: dict) -> set[str]:
    """FACT/SIGNAL 딕셔너리들로부터 허용 수치 집합 구성.

    중첩 dict / list도 재귀 탐색한다.
    """
    allowed: set[str] = set()

    def walk(obj) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                walk(v)
        elif isinstance(obj, bool):
            return
        elif isinstance(obj, (int, float)):
            allowed.update(_round_variants(obj))

    for s in sources:
        walk(s)

    allowed.update(str(i) for i in _SMALL_INT_ALLOW)
    return allowed


def _mask_exempt(text: str) -> tuple[str, int]:
    """면제 패턴을 마스킹하고 마스킹 건수를 반환."""
    count = 0
    for pat in _EXEMPT_PATTERNS:
        text, n = pat.subn(" [EXEMPT] ", text)
        count += n
    return text, count


def check(text: str, *sources: dict, extra_allowed: set[str] | None = None) -> GuardResult:
    """브리핑 텍스트 검증.

    Parameters
    ----------
    text     : LLM이 생성한 브리핑 본문
    sources  : FACT/SIGNAL 딕셔너리들 (regime dict, sector dict 등)
    """
    allowed = build_allowed(*sources)
    if extra_allowed:
        allowed |= set(extra_allowed)

    masked, exempt_count = _mask_exempt(text)
    tokens = _NUM_RE.findall(masked)

    unknown: list[str] = []
    for tok in tokens:
        norm = tok.replace(",", "").lstrip("+")
        if norm in allowed:
            continue
        # 표기 정규화만 수행한다(후행 0 등). 반올림 확장은 하지 않는다.
        # 토큰 쪽을 반올림해서 비교하면 환각 수치가 실제값과 우연히 겹쳐 통과한다.
        try:
            f = float(norm)
        except ValueError:
            continue
        if f"{f:g}" in allowed:
            continue
        unknown.append(tok)

    return GuardResult(
        passed=len(unknown) == 0,
        unknown=unknown,
        checked=len(tokens),
        exempt=exempt_count,
    )


def enforce(text: str, *sources: dict, extra_allowed: set[str] | None = None) -> str:
    """검증 실패 시 예외를 던진다. 파이프라인에서는 이쪽을 쓴다."""
    res = check(text, *sources, extra_allowed=extra_allowed)
    if not res.passed:
        raise ValueError(res.report())
    return text
