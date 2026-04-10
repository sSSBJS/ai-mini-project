from __future__ import annotations

import os
import re
import urllib.parse
import urllib.request
from datetime import date
from email.utils import parsedate_to_datetime
from typing import List, Optional

from semiconductor_agent.models import BalancedSearchPlan, SearchResult, ValidationIssue

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - optional runtime dependency
    ChatOpenAI = None


def build_balanced_search_plan(topic: str, scope_hint: str) -> BalancedSearchPlan:
    prompt = (
        "반도체 기술 전략 분석을 위한 검색 계획을 만듭니다. "
        "확증 편향을 피하기 위해 확인/반박/중립 검색어를 분리합니다."
    )
    if ChatOpenAI is not None and os.getenv("OPENAI_API_KEY"):
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        structured_llm = llm.with_structured_output(BalancedSearchPlan)
        return structured_llm.invoke(
            prompt
            + "\n주제: %s\n범위: %s\n"
            % (topic, scope_hint)
        )

    return BalancedSearchPlan(
        hypothesis="%s와 관련된 최근 기술 성숙도와 경쟁 위협을 검증한다." % topic,
        confirming_query="%s %s official announcement OR technical validation" % (topic, scope_hint),
        opposing_query="%s %s limitation OR delay OR bottleneck" % (topic, scope_hint),
        objective_query="%s %s roadmap OR specification OR benchmark" % (topic, scope_hint),
    )


class WebSearchClient:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def search(self, query: str, max_results: int = 3) -> List[SearchResult]:
        if not self.enabled:
            return []

        encoded = urllib.parse.quote(query)
        url = "https://duckduckgo.com/html/?q=%s" % encoded
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            html = response.read().decode("utf-8", errors="ignore")

        items = []
        link_pattern = re.compile(
            r'nofollow" class="result__a" href="(?P<url>.*?)".*?>(?P<title>.*?)</a>.*?<a class="result__snippet".*?>(?P<snippet>.*?)</a>',
            re.DOTALL,
        )
        for match in link_pattern.finditer(html):
            title = _strip_html(match.group("title"))
            snippet = _strip_html(match.group("snippet"))
            result_url = urllib.parse.unquote(match.group("url"))
            source_type = _guess_source_type(result_url)
            items.append(
                SearchResult(
                    title=title,
                    snippet=snippet,
                    url=result_url,
                    source_type=source_type,
                )
            )
            if len(items) >= max_results:
                break
        return items


def validate_search_balance(results: List[SearchResult]) -> List[ValidationIssue]:
    issues = []
    source_types = {result.source_type for result in results}
    if len(source_types) < 2:
        issues.append(
            ValidationIssue(
                scope="search_balance",
                message="서로 다른 출처 유형이 부족합니다. 단일 출처 의존 위험이 있습니다.",
                severity="high",
                blocking=True,
            )
        )
    if results and not any(result.source_type == "company" for result in results):
        issues.append(
            ValidationIssue(
                scope="search_balance",
                message="기업 자료가 없어 발표/로드맵 검증 근거가 약합니다.",
                severity="medium",
                blocking=False,
            )
        )
    return issues


def parse_http_date(header_value: Optional[str]) -> Optional[date]:
    if not header_value:
        return None
    try:
        return parsedate_to_datetime(header_value).date()
    except Exception:
        return None


def _strip_html(text: str) -> str:
    text = re.sub(r"<.*?>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _guess_source_type(url: str) -> str:
    lowered = url.lower()
    if any(token in lowered for token in ["ieee", "acm", "arxiv", "springer"]):
        return "paper"
    if any(token in lowered for token in ["jedec", "consortium", "specification"]):
        return "standard"
    if any(token in lowered for token in ["investor", "newsroom", "press", "nvidia.com", "samsung.com", "micron.com", "skhynix"]):
        return "company"
    if "patent" in lowered:
        return "patent"
    return "news"
