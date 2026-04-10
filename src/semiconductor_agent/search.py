from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Dict, Iterable, List, Optional, Sequence

from semiconductor_agent.models import BalancedSearchPlan, SearchResult, ValidationIssue

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - optional runtime dependency
    ChatOpenAI = None


SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
OPENALEX_ENDPOINT = "https://api.openalex.org/works"
DEFAULT_SOURCE_REQUIREMENTS = ("paper", "company", "news")


@dataclass(frozen=True)
class SearchTask:
    task_id: str
    title: str
    objective: str
    focus: str
    priority: int
    queries: List[str]
    required_source_types: List[str]
    verification_questions: List[str]
    deliverable: str


@dataclass(frozen=True)
class SearchPromptBundle:
    planner_prompt: str
    execution_prompt: str
    verification_prompt: str
    supervisor_handoff_prompt: str


@dataclass(frozen=True)
class SearchVerificationReport:
    approved: bool
    issues: List[ValidationIssue] = field(default_factory=list)
    source_type_counts: Dict[str, int] = field(default_factory=dict)
    fresh_result_count: int = 0
    stale_result_count: int = 0
    blocking_issue_count: int = 0


@dataclass(frozen=True)
class SearchResearchBlueprint:
    goal: str
    search_plan: BalancedSearchPlan
    hypothesis: str
    task_manager_notes: List[str]
    tasks: List[SearchTask]
    prompts: SearchPromptBundle
    rag_queries: List[str]
    supervisor_gate_checklist: List[str]


def build_balanced_search_plan(topic: str, scope_hint: str) -> BalancedSearchPlan:
    prompt = (
        "반도체 기술 전략 분석을 위한 검색 계획을 만듭니다. "
        "확증 편향을 피하기 위해 확인/반박/중립 검색어를 분리하고, "
        "시장 조사/논문 조사/리스크 검증까지 모두 커버해야 합니다."
    )
    if (
        ChatOpenAI is not None
        and os.getenv("OPENAI_API_KEY")
        and os.getenv("USE_LLM_PLANNING", "").strip().lower() in {"1", "true", "yes", "on"}
    ):
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        structured_llm = llm.with_structured_output(BalancedSearchPlan)
        return structured_llm.invoke(
            prompt
            + "\n주제: %s\n범위: %s\n"
            % (topic, scope_hint)
        )

    return BalancedSearchPlan(
        hypothesis="%s와 관련된 최신 기술 성숙도, 경쟁 강도, 사업화 위험을 교차 검증한다." % topic,
        confirming_query="%s %s official roadmap OR benchmark OR production update" % (topic, scope_hint),
        opposing_query="%s %s limitation OR bottleneck OR delay OR adoption risk" % (topic, scope_hint),
        objective_query="%s %s specification OR market outlook OR peer reviewed" % (topic, scope_hint),
    )


class OpenAlexSearchClient:
    def __init__(self, enabled: bool, email: Optional[str] = None):
        self.enabled = enabled
        self.email = email or os.getenv("OPENALEX_EMAIL") or os.getenv("OPENALEX_MAILTO")

    def search(
        self,
        query: str,
        max_results: int = 5,
        from_publication_year: Optional[int] = None,
    ) -> List[SearchResult]:
        if not self.enabled:
            return []

        params = {
            "search": query,
            "per-page": max(1, min(max_results, 25)),
            "sort": "relevance_score:desc",
        }
        if from_publication_year is not None:
            params["filter"] = "from_publication_date:%s-01-01" % from_publication_year
        if self.email:
            params["mailto"] = self.email

        payload = _read_json("%s?%s" % (OPENALEX_ENDPOINT, urllib.parse.urlencode(params)))
        results = []
        for item in payload.get("results", []):
            title = _normalize_whitespace(item.get("display_name") or "")
            if not title:
                continue
            snippet = _build_openalex_snippet(item)
            url = (
                item.get("primary_location", {}).get("landing_page_url")
                or item.get("doi")
                or item.get("id")
                or ""
            )
            results.append(
                SearchResult(
                    title=title,
                    snippet=snippet,
                    url=url,
                    source_type="paper",
                    published_at=_parse_openalex_date(item),
                )
            )
            if len(results) >= max_results:
                break
        return results


class SerpAPISearchClient:
    def __init__(self, enabled: bool, api_key: Optional[str] = None):
        self.enabled = enabled
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY")

    def search(
        self,
        query: str,
        max_results: int = 5,
        search_type: str = "web",
    ) -> List[SearchResult]:
        if not self.enabled or not self.api_key:
            return []

        params = {
            "engine": "google",
            "q": query,
            "num": max(1, min(max_results, 10)),
            "api_key": self.api_key,
        }
        if search_type == "news":
            params["tbm"] = "nws"

        payload = _read_json("%s?%s" % (SERPAPI_ENDPOINT, urllib.parse.urlencode(params)))
        key = "news_results" if search_type == "news" else "organic_results"
        results = []
        for item in payload.get(key, []):
            title = _normalize_whitespace(item.get("title") or "")
            if not title:
                continue
            snippet = _normalize_whitespace(
                item.get("snippet")
                or item.get("snippet_highlighted_words", [""])[0]
                or item.get("source", "")
            )
            url = item.get("link") or item.get("url") or ""
            published_at = _parse_fuzzy_date(item.get("date"))
            results.append(
                SearchResult(
                    title=title,
                    snippet=snippet,
                    url=url,
                    source_type=_guess_source_type(url),
                    published_at=published_at,
                )
            )
            if len(results) >= max_results:
                break
        return results


class WebSearchClient:
    def __init__(
        self,
        enabled: bool,
        serpapi_api_key: Optional[str] = None,
        openalex_email: Optional[str] = None,
    ):
        self.enabled = enabled
        self.serpapi = SerpAPISearchClient(enabled=enabled, api_key=serpapi_api_key)
        self.openalex = OpenAlexSearchClient(enabled=enabled, email=openalex_email)

    def search(self, query: str, max_results: int = 3) -> List[SearchResult]:
        if not self.enabled:
            return []
        serp_results = self.serpapi.search(query=query, max_results=max_results, search_type="web")
        if serp_results:
            return serp_results
        return self._search_duckduckgo(query=query, max_results=max_results)

    def search_news(self, query: str, max_results: int = 5) -> List[SearchResult]:
        if not self.enabled:
            return []
        results = self.serpapi.search(query=query, max_results=max_results, search_type="news")
        if results:
            return results
        return self._search_duckduckgo(query="%s latest news" % query, max_results=max_results)

    def search_papers(
        self,
        query: str,
        max_results: int = 5,
        from_publication_year: Optional[int] = None,
    ) -> List[SearchResult]:
        if not self.enabled:
            return []
        results = self.openalex.search(
            query=query,
            max_results=max_results,
            from_publication_year=from_publication_year,
        )
        if results:
            return results
        return self._search_duckduckgo(query="%s site:arxiv.org OR site:ieeexplore.ieee.org" % query, max_results=max_results)

    def search_task(self, task: SearchTask, max_results_per_query: int = 3) -> List[SearchResult]:
        if not self.enabled:
            return []

        results = []
        for query in task.queries:
            if task.focus == "paper":
                hits = self.search_papers(query, max_results=max_results_per_query, from_publication_year=date.today().year - 5)
            elif task.focus in {"market", "risk", "company"}:
                hits = self.search_news(query, max_results=max_results_per_query)
                if not hits:
                    hits = self.search(query, max_results=max_results_per_query)
            else:
                hits = self.search(query, max_results=max_results_per_query)
            results.extend(hits)
        return deduplicate_search_results(results)

    def verify_handoff(
        self,
        results: Sequence[SearchResult],
        required_source_types: Sequence[str] = DEFAULT_SOURCE_REQUIREMENTS,
        freshness_year_cutoff: int = 4,
    ) -> SearchVerificationReport:
        return verify_search_results(
            results=results,
            required_source_types=required_source_types,
            freshness_year_cutoff=freshness_year_cutoff,
        )

    def _search_duckduckgo(self, query: str, max_results: int = 3) -> List[SearchResult]:
        encoded = urllib.parse.quote(query)
        url = "https://duckduckgo.com/html/?q=%s" % encoded
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                html = response.read().decode("utf-8", errors="ignore")
        except Exception:
            return []

        items = []
        link_pattern = re.compile(
            r'nofollow" class="result__a" href="(?P<url>.*?)".*?>(?P<title>.*?)</a>.*?<a class="result__snippet".*?>(?P<snippet>.*?)</a>',
            re.DOTALL,
        )
        for match in link_pattern.finditer(html):
            title = _strip_html(match.group("title"))
            snippet = _strip_html(match.group("snippet"))
            result_url = urllib.parse.unquote(match.group("url"))
            items.append(
                SearchResult(
                    title=title,
                    snippet=snippet,
                    url=result_url,
                    source_type=_guess_source_type(result_url),
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
    if results and not any(result.source_type == "paper" for result in results):
        issues.append(
            ValidationIssue(
                scope="search_balance",
                message="논문/학술 자료가 없어 기술 성숙도 판단 근거가 약합니다.",
                severity="medium",
                blocking=False,
            )
        )
    stale_results = _count_stale_results(results, freshness_year_cutoff=4)
    if results and stale_results == len(results):
        issues.append(
            ValidationIssue(
                scope="search_balance",
                message="검색 결과가 전반적으로 오래되어 최신 시장/기술 동향 반영이 부족합니다.",
                severity="medium",
                blocking=False,
            )
        )
    return issues


def verify_search_results(
    results: Sequence[SearchResult],
    required_source_types: Sequence[str] = DEFAULT_SOURCE_REQUIREMENTS,
    freshness_year_cutoff: int = 4,
) -> SearchVerificationReport:
    issues = []
    source_type_counts = _count_by_source_type(results)
    missing_types = [source_type for source_type in required_source_types if source_type_counts.get(source_type, 0) == 0]
    fresh_result_count = _count_fresh_results(results, freshness_year_cutoff)
    stale_result_count = _count_stale_results(results, freshness_year_cutoff)

    if not results:
        issues.append(
            ValidationIssue(
                scope="Search Verification Node",
                message="검색 결과가 비어 있어 supervisor 전달이 불가능합니다.",
                severity="high",
                blocking=True,
            )
        )
    if missing_types:
        issues.append(
            ValidationIssue(
                scope="Search Verification Node",
                message="필수 출처 유형이 부족합니다: %s" % ", ".join(missing_types),
                severity="high",
                blocking=True,
            )
        )
    if len(results) < 3:
        issues.append(
            ValidationIssue(
                scope="Search Verification Node",
                message="근거 수가 너무 적어 교차 검증이 어렵습니다.",
                severity="high",
                blocking=True,
            )
        )
    if stale_result_count and not fresh_result_count:
        issues.append(
            ValidationIssue(
                scope="Search Verification Node",
                message="최신 근거가 없어 supervisor 전달 전에 재검색이 필요합니다.",
                severity="medium",
                blocking=True,
            )
        )
    if not any(result.source_type in {"news", "company"} for result in results):
        issues.append(
            ValidationIssue(
                scope="Search Verification Node",
                message="시장/기업 동향 자료가 부족해 위험도 분석 연결성이 약합니다.",
                severity="medium",
                blocking=False,
            )
        )
    if not any(result.source_type in {"paper", "standard"} for result in results):
        issues.append(
            ValidationIssue(
                scope="Search Verification Node",
                message="기술 검증 자료가 부족해 TRL/기술 성숙도 판단에 취약합니다.",
                severity="medium",
                blocking=False,
            )
        )

    blocking_issue_count = sum(1 for issue in issues if issue.blocking)
    return SearchVerificationReport(
        approved=blocking_issue_count == 0,
        issues=issues,
        source_type_counts=source_type_counts,
        fresh_result_count=fresh_result_count,
        stale_result_count=stale_result_count,
        blocking_issue_count=blocking_issue_count,
    )


def deduplicate_search_results(results: Sequence[SearchResult]) -> List[SearchResult]:
    deduped = []
    seen = set()
    for result in results:
        key = (result.title.strip().lower(), result.url.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped


def parse_http_date(header_value: Optional[str]) -> Optional[date]:
    if not header_value:
        return None
    try:
        return parsedate_to_datetime(header_value).date()
    except Exception:
        return None


def _read_json(url: str) -> Dict[str, object]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return {}


def _strip_html(text: str) -> str:
    text = re.sub(r"<.*?>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _build_openalex_snippet(item: Dict[str, object]) -> str:
    abstract = _decode_inverted_index(item.get("abstract_inverted_index"))
    if abstract:
        return abstract[:280]
    venue = item.get("primary_location", {}).get("source", {}).get("display_name")
    pub_date = item.get("publication_date") or item.get("publication_year")
    cited = item.get("cited_by_count")
    parts = [part for part in [venue, str(pub_date) if pub_date else "", "cited_by=%s" % cited if cited is not None else ""] if part]
    return " / ".join(parts)


def _decode_inverted_index(inverted_index: object) -> str:
    if not isinstance(inverted_index, dict):
        return ""
    positions = []
    for token, indexes in inverted_index.items():
        if not isinstance(indexes, list):
            continue
        for index in indexes:
            if isinstance(index, int):
                positions.append((index, token))
    if not positions:
        return ""
    ordered = [token for _, token in sorted(positions)]
    return _normalize_whitespace(" ".join(ordered))


def _parse_openalex_date(item: Dict[str, object]) -> Optional[date]:
    raw = item.get("publication_date")
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    year = item.get("publication_year")
    if isinstance(year, int):
        return date(year, 1, 1)
    return None


def _parse_fuzzy_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    month_formats = ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d")
    for fmt in month_formats:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    year_match = re.search(r"\b(20\d{2})\b", raw)
    if year_match:
        return date(int(year_match.group(1)), 1, 1)
    return None


def _guess_source_type(url: str) -> str:
    lowered = url.lower()
    if any(token in lowered for token in ["openalex", "ieee", "acm", "arxiv", "springer", "sciencedirect", "nature.com", "mdpi"]):
        return "paper"
    if any(token in lowered for token in ["jedec", "consortium", "specification", "cxl", "khronos", "standards"]):
        return "standard"
    if any(token in lowered for token in ["investor", "newsroom", "press", "nvidia.com", "samsung.com", "micron.com", "skhynix", "samsungsemiconductor"]):
        return "company"
    if "patent" in lowered:
        return "patent"
    return "news"


def _unique_preserve_order(items: Iterable[str]) -> List[str]:
    deduped = []
    seen = set()
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _count_by_source_type(results: Sequence[SearchResult]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for result in results:
        counts[result.source_type] = counts.get(result.source_type, 0) + 1
    return counts


def _count_fresh_results(results: Sequence[SearchResult], freshness_year_cutoff: int) -> int:
    if not results:
        return 0
    current_year = date.today().year
    count = 0
    for result in results:
        if result.published_at and current_year - result.published_at.year <= freshness_year_cutoff:
            count += 1
    return count


def _count_stale_results(results: Sequence[SearchResult], freshness_year_cutoff: int) -> int:
    if not results:
        return 0
    current_year = date.today().year
    count = 0
    for result in results:
        if result.published_at and current_year - result.published_at.year > freshness_year_cutoff:
            count += 1
    return count
