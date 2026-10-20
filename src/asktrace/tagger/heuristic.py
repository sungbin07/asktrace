"""Offline tagger. Deterministic, no network, Korean + English aware.

It is intentionally simple: it exists so the pipeline works without an API key and so
tests are reproducible. The Claude tagger produces much better concepts.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..models import Tag
from .base import Tagger

# (pattern, depth, question_type) checked in order; first match wins.
_DEPTH_RULES: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"(설계|아키텍처|architect|design|trade[- ]?off|확장성|scal)", re.I), 5, "design"),
    (
        re.compile(
            r"(에러|오류|실패|안\s?(돼|되)|깨[졌지]|error|fail|exception|traceback|crash|bug|왜 안)",
            re.I,
        ),
        4,
        "troubleshoot",
    ),
    (
        re.compile(
            r"(차이|비교|왜|이유|vs\.?|versus|why|difference|compare|better|나은|장단점)", re.I
        ),
        3,
        "conceptual",
    ),
    (
        re.compile(
            r"(어떻게|방법|how (do|to|can|should)|구현|만들|설정|해줘|해봐|추가|수정|implement|set ?up|write|build)",
            re.I,
        ),
        2,
        "howto",
    ),
    (re.compile(r"(뭐야|무엇|이란|what (is|are|does)|정의|의미|explain|설명)", re.I), 1, "factual"),
]

_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ml": (
        "model",
        "모델",
        "llm",
        "prompt",
        "프롬프트",
        "fine-tun",
        "파인튜닝",
        "token",
        "토큰",
        "gpu",
        "vllm",
        "embedding",
        "rag",
        "학습",
        "train",
        "eval",
        "평가",
        "데이터셋",
        "dataset",
    ),
    "backend": (
        "api",
        "server",
        "서버",
        "endpoint",
        "fastapi",
        "django",
        "flask",
        "http",
        "rest",
        "grpc",
        "queue",
        "큐",
    ),
    "data": (
        "sql",
        "sqlite",
        "postgres",
        "database",
        "db",
        "schema",
        "스키마",
        "쿼리",
        "query",
        "pandas",
        "csv",
        "json",
    ),
    "devops": (
        "docker",
        "kubernetes",
        "k8s",
        "deploy",
        "배포",
        "ci",
        "github actions",
        "nginx",
        "ssh",
        "tunnel",
        "터널",
        "linux",
        "cron",
    ),
    "frontend": (
        "react",
        "css",
        "html",
        "컴포넌트",
        "component",
        "브라우저",
        "browser",
        "ui",
        "화면",
        "next.js",
        "vue",
    ),
    "git": ("git", "commit", "커밋", "branch", "브랜치", "rebase", "merge", "pr", "pull request"),
    "python": ("python", "파이썬", "pip", "uv", "pytest", "pydantic", "asyncio", "venv"),
    "product": (
        "기획",
        "사용자",
        "유저",
        "persona",
        "페르소나",
        "로드맵",
        "roadmap",
        "지표",
        "metric",
        "제품",
        "product",
    ),
    "writing": ("문서", "doc", "readme", "글", "write-up", "요약", "summar", "번역", "translate"),
}

_STOP = set(
    """the a an is are was were be to of in on for and or with that this it as at by from
    how what why which when where who do does did can could should would will my me our
    i you we they he she your its about into over than then there here just like
    좀 그 이 저 것 수 등 및 를 을 이 가 은 는 에 의 로 으로 도 와 과 한 하는 해줘 해봐 있는 없는 뭐야
    지금 다시 한번 그거 이거 저거 거기 여기""".split()
)
_TOKEN_RE = re.compile(r"`([^`]+)`|([A-Za-z][A-Za-z0-9_.+-]{2,})|([가-힣]{2,})")
# Korean particles (josa) glued to the end of a noun: "관점에서" -> "관점", "모드랑" -> "모드".
_JOSA = (
    "에서는",
    "에서",
    "으로",
    "이랑",
    "에게",
    "한테",
    "까지",
    "부터",
    "처럼",
    "마다",
    "로",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "도",
    "와",
    "과",
    "랑",
    "에",
)


def _strip_josa(tok: str) -> str:
    for suffix in _JOSA:
        if tok.endswith(suffix) and len(tok) - len(suffix) >= 2:
            return tok[: -len(suffix)]
    return tok


def _concepts(text: str) -> list[str]:
    scored: dict[str, int] = {}
    for m in _TOKEN_RE.finditer(text):
        backtick, latin, hangul = m.groups()
        if backtick:
            scored[backtick.strip()] = scored.get(backtick.strip(), 0) + 3
            continue
        tok = _strip_josa(hangul) if hangul else latin.lower()
        if tok in _STOP or len(tok) < 3 and not hangul:
            continue
        scored[tok] = scored.get(tok, 0) + 1
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
    return [tok for tok, _ in ranked[:5]]


def _domain(text: str) -> str:
    low = text.lower()
    best, best_hits = "general", 0
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in low)
        if hits > best_hits:
            best, best_hits = domain, hits
    return best


def _depth_and_type(text: str) -> tuple[int, str]:
    for pattern, depth, qtype in _DEPTH_RULES:
        if pattern.search(text):
            return depth, qtype
    return 2, "other"


class HeuristicTagger(Tagger):
    name = "heuristic"

    def tag(self, questions: Sequence[tuple[str, str]]) -> list[Tag]:
        out = []
        for qid, text in questions:
            depth, qtype = _depth_and_type(text)
            out.append(
                Tag(
                    question_id=qid,
                    concepts=_concepts(text),
                    domain=_domain(text),
                    depth=depth,
                    question_type=qtype,
                    tagger=self.name,
                    confidence=0.4,
                )
            )
        return out
