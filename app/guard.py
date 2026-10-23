"""입력 미들웨어(guard 체인): 관리자 입력이 LLM에 가기 전에 검사 단계 목록을 차례로 통과한다.

근거 문서: docs/guard_research.md. 검사 대상은 조사 비서 대화창의 자유 입력뿐이다(고객 문의 등록,
첫 질문 y/n, 승인 카드 y/n/e 같은 고정 입력에는 쓰지 않는다).

- 단계는 모두 같은 인터페이스(GuardStep.check(text, norm) -> Verdict | None)이고, 체인은 처음 차단한 단계에서 멈춘다.
- 1차 구현은 Python 규칙: 길이 상한 → 유형별 패턴(정규화한 문장 + 공백을 뺀 문장 + Base64 해독문).
- 나중에 LLM 판정 단계나 의도 라우터(무관 주제 거절)를 같은 인터페이스로 GUARD_CHAIN 뒤에 붙인다.
- guard는 첫 번째 거름망일 뿐 보안 경계가 아니다. 실제 보호는 Python이 정하는 도구 목록·위험 등급·승인 게이트다.
  그래서 규칙은 낱말 하나가 아니라 '동사 + 대상'처럼 둘이 함께 있을 때만 막아 정상 업무 입력의 오탐을 줄인다.
"""

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

MAX_INPUT_CHARS = 2000
BLOCK_MESSAGE = ("입력 검사에서 막힌 요청입니다(유형: {category}). 이 입력은 비서에게 보내지 않았습니다. "
                 "매핑·이슈 조사에 관한 질문으로 다시 입력해 주세요. 처리안 반영은 승인 카드로만 할 수 있습니다.")

# 폭 없는 문자·방향 표시 문자(정규화 때 지운다).
_INVISIBLE = re.compile(r"[​-‏⁠-⁤﻿­]")


@dataclass(frozen=True)
class Normalized:
    """검사용 사본: text = NFKC·숨은 문자 제거·소문자·공백 정리, compact = 공백까지 모두 뺀 것."""

    original: str
    text: str
    compact: str


@dataclass(frozen=True)
class Verdict:
    """차단 판정. category = 유형 이름(안내 문장에 보임), stage = 걸린 단계, rule = 걸린 규칙(기록용)."""

    category: str
    stage: str
    rule: str

    @property
    def message(self) -> str:
        return BLOCK_MESSAGE.format(category=self.category)


def normalize(text: str) -> Normalized:
    value = unicodedata.normalize("NFKC", text or "")
    value = _INVISIBLE.sub("", value)
    value = "".join(ch if unicodedata.category(ch)[0] != "C" or ch in "\n\t" else " " for ch in value)
    value = " ".join(value.lower().split())
    return Normalized(text or "", value, re.sub(r"\s+", "", value))


class GuardStep(Protocol):
    name: str

    def check(self, text: str, norm: Normalized) -> Verdict | None: ...


class LengthStep:
    """과도한 길이(토큰 채우기·적대적 접미사)."""

    name = "길이 검사"

    def check(self, text, norm):
        if len(text or "") > MAX_INPUT_CHARS:
            return Verdict("입력 길이 초과", self.name, f"{len(text)}자 > {MAX_INPUT_CHARS}자")
        return None


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


# 유형별 규칙: (유형 이름, 규칙 이름, 정규화한 문장에 쓰는 정규식 목록, 공백 뺀 문장에 쓰는 정규식 목록)
_OBJ_KO = r"(?:지시|지침|규칙|명령|프롬프트|설정|instructions?|rules?|prompts?)"
_VERB_KO = r"(?:무시|잊어|잊고|잊으|따르지\s*마|따르지\s*말|지키지\s*마|없던\s*걸로)"
_VERB_EN = r"(?:ignore|disregard|forget|bypass|override|skip|neglect|overlook)"
_OBJ_EN = r"(?:instructions?|rules?|prompts?|directives?|guidelines?|context|지시|지침|규칙|명령|프롬프트)"

PATTERN_RULES: list[tuple[str, str, list[re.Pattern], list[re.Pattern]]] = [
    ("지시 무시", "override", [
        # 한국어 어순: 대상 다음 세 낱말 안에 무시 동사 (예: '이전 지시는 모두 무시해')
        _rx(_OBJ_KO + r"\S*(?:\s+\S+){0,3}?\s*" + _VERB_KO),
        # 영어 어순: 동사 다음 네 낱말 안에 대상 (예: 'ignore all previous instructions', 'ignore 이전 지시')
        _rx(r"\b" + _VERB_EN + r"\b(?:\s+\S+){0,4}?\s+" + _OBJ_EN),
        _rx(r"\bdo\s*n[o']t\s+(?:follow|obey)\b(?:\s+\S+){0,3}?\s+" + _OBJ_EN),
    ], [
        _rx(r"(?:ignore|disregard|forget|bypass|override)(?:all|the|any|your|of|my)*"
            r"(?:previous|prior|above|preceding|earlier|initial|system|these|those)*(?:instructions?|rules?|prompts?|directives?)"),
        _rx(r"(?:이전|앞|위|기존|모든|지금까지|시스템)\S{0,3}?(?:지시|지침|규칙|명령|프롬프트)\S{0,4}?(?:무시|잊어|잊고|따르지마)"),
    ]),
    ("역할 탈취", "jailbreak", [
        _rx(r"\bjailbreak|탈옥|developer\s*mode|개발자\s*모드|god\s*mode|\bdan\b(?:\s+mode)?|do\s+anything\s+now"),
        _rx(r"(?:제한|제약|검열|규칙)\s*(?:이|가)?\s*없는\s*(?:ai|인공지능|모드|비서|챗봇)|unrestricted|no\s+restrictions"),
        _rx(r"\byou\s+are\s+now\b|\bfrom\s+now\s+on,?\s+you\b|\bpretend\s+(?:to\s+be|you\s+are|that\s+you)\b|\bact\s+as\b"
            r"|\brole-?play\b"),
        _rx(r"(?:너는|넌|당신은|니가|네가)\s*(?:이제|지금부터)|(?:이제|지금부터)\s*(?:너는|넌|당신은)|역할을?\s*(?:연기|맡아)"
            r"|(?:인\s*척|인척)\s*해"),
    ], [
        _rx(r"개발자모드|탈옥|developermode|jailbreak"),
    ]),
    ("프롬프트 유출", "prompt_leak", [
        _rx(r"(?:system\s*prompt|시스템\s*프롬프트|프롬프트|initial\s+instructions?|hidden\s+(?:rules|instructions)|초기\s*지시"
            r"|지시문|숨겨진\s*(?:규칙|지시|설정)|your\s+(?:instructions|rules|prompt))"
            r".*(?:보여|출력|알려|말해|공개|읊어|그대로|print|show|reveal|repeat|display|tell)"),
        _rx(r"(?:print|show|reveal|repeat|display|tell\s+me|what\s+(?:are|is))\b.*(?:system\s*prompt|initial\s+instructions?"
            r"|hidden\s+(?:rules|instructions)|your\s+(?:instructions|rules|prompt))"),
        _rx(r"repeat\s+(?:the\s+)?(?:text|words|everything)\s+above|위\s*(?:내용|문장|글)을?\s*그대로\s*(?:반복|출력)"),
    ], [
        _rx(r"시스템프롬프트|systemprompt"),
    ]),
    ("승인 우회", "approval_bypass", [
        _rx(r"(?:승인|이중\s*확인|확인\s*절차|확인|approval|confirmation)\S*(?:\s+\S+){0,2}?\s*(?:없이|생략|건너\s*뛰|skip|무시)"),
        _rx(r"\b(?:without|skip(?:ping)?|bypass(?:ing)?|no)\s+(?:the\s+|any\s+)?(?:approval|confirmation|confirm)"),
        _rx(r"묻지\s*말고|물어보지\s*말고|자동\s*승인|auto[\s-]*approv|승인(?:한|된|했다고)\s*(?:걸로|것으로|치고)"
            r"|\by\s*(?:를|을)?\s*(?:누른|입력한|친)\s*(?:걸로|것으로)"),
    ], []),
    ("SQL·명령 실행", "sql_command", [
        _rx(r"\b(?:drop|truncate|alter)\s+table\b|\bdelete\s+from\b|\bupdate\s+\w+\s+set\b|\binsert\s+into\b"
            r"|\bselect\b.*\bfrom\s+\w|\bunion\s+select\b|'\s*;|;\s*--|\bor\s+1\s*=\s*1\b|/\*.*\*/"),
        _rx(r"rm\s+-rf|os\.system|\bsubprocess\b|\bexec\s*\(|\beval\s*\(|__import__|powershell|\bcmd(?:\.exe)?\s+/c"
            r"|\bbash\s+-c|\bsqlite3\s"),
        _rx(r"(?:쿼리|sql|셸|쉘|shell|명령어|커맨드|command|파이썬|python|스크립트|script)\S*(?:\s+\S+){0,2}?\s*(?:실행|돌려|run|execute)"),
        _rx(r"\b(?:run|execute)\s+(?:this\s+|the\s+|a\s+)?(?:query|sql|command|script|code|shell)"),
        _rx(r"(?:db|데이터베이스|테이블)\S*\s*(?:에\s*)?직접\s*(?:수정|업데이트|변경|삭제|넣|입력|반영)"),
    ], []),
    ("인코딩 우회", "obfuscation", [
        _rx(r"(?:decode|디코딩|해독|복호화|base64)\S*(?:\s+\S+){0,3}?\s*(?:따라|실행|수행|follow|execute|run|obey)"),
    ], []),
    ("권한 사칭", "privilege", [
        _rx(r"\bsystem\s*(?:권한|으로\s*(?:처리|실행|반영)|override|mode)|\bas\s+system\b|처리자를\s*system"
            r"|권한\s*(?:상승|올려)|\bsudo\b|\bi\s*am\s+(?:the\s+)?(?:system|admin)"
            r"|나는\s*(?:system|시스템)(?:이야|이다|입니다|다)?"),
    ], []),
]

# 처리상태 이름은 규칙에서 미리 뺀다('확인필요 건 목록'이 승인 우회의 '확인'에 걸리지 않게).
_STATUS_WORDS = re.compile(r"확인\s*필요")
_BASE64 = re.compile(r"[A-Za-z0-9+/]{20,}={0,2}")


def _decoded_parts(original: str) -> list[str]:
    """Base64로 보이는 덩어리 중 UTF-8 글자로 해독되는 것."""
    out = []
    for chunk in _BASE64.findall(original or ""):
        try:
            raw = base64.b64decode(chunk + "=" * (-len(chunk) % 4), validate=True)
            text = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if text.isprintable() and any(ch.isalpha() for ch in text):
            out.append(text)
    return out


class PatternStep:
    """유형별 Python 패턴 규칙(docs/guard_research.md 3.1~3.7)."""

    name = "패턴 규칙"

    def _match(self, norm: Normalized) -> Verdict | None:
        text = _STATUS_WORDS.sub("상태", norm.text)
        compact = _STATUS_WORDS.sub("상태", norm.compact)
        for category, rule, patterns, compact_patterns in PATTERN_RULES:
            for i, p in enumerate(patterns):
                if p.search(text):
                    return Verdict(category, self.name, f"{rule}#{i}")
            for i, p in enumerate(compact_patterns):
                if p.search(compact):
                    return Verdict(category, self.name, f"{rule}/compact#{i}")
        return None

    def check(self, text, norm):
        if verdict := self._match(norm):
            return verdict
        for decoded in _decoded_parts(norm.original):
            if verdict := self._match(normalize(decoded)):
                return Verdict("인코딩 우회", self.name, f"base64→{verdict.rule}")
        return None


GUARD_CHAIN: list[GuardStep] = [LengthStep(), PatternStep()]


def run_guard(text: str, chain: list[GuardStep] | None = None) -> Verdict | None:
    """체인을 차례로 돌려 처음 차단한 단계의 판정을, 모두 통과하면 None을 돌려준다."""
    norm = normalize(text)
    for step in GUARD_CHAIN if chain is None else chain:
        if verdict := step.check(text, norm):
            return verdict
    return None
