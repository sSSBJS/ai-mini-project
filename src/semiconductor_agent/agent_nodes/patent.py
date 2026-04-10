from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Dict

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent
from semiconductor_agent.models import EvidenceItem
from semiconductor_agent.search import build_balanced_search_plan
from semiconductor_agent.state import AgentState


# CHANGED: patent.py가 별도 모델 파일(models/models.py)의 구조화 출력 모델을 직접 로드하도록 연결.
_PATENT_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "models.py"
_PATENT_MODEL_SPEC = importlib.util.spec_from_file_location("semiconductor_agent_patent_models", _PATENT_MODEL_PATH)
if _PATENT_MODEL_SPEC is None or _PATENT_MODEL_SPEC.loader is None:
    raise ImportError("Failed to load patent output models from %s" % _PATENT_MODEL_PATH)
_PATENT_MODEL_MODULE = importlib.util.module_from_spec(_PATENT_MODEL_SPEC)
sys.modules[_PATENT_MODEL_SPEC.name] = _PATENT_MODEL_MODULE
_PATENT_MODEL_SPEC.loader.exec_module(_PATENT_MODEL_MODULE)
PatentInnovationSignalResult = _PATENT_MODEL_MODULE.PatentInnovationSignalResult
PatentSignalEntry = _PATENT_MODEL_MODULE.PatentSignalEntry
# CHANGED: 동적 로드된 Pydantic 모델의 forward reference 해석을 완료해 단독 실행 오류를 방지.
PatentSignalEntry.model_rebuild()
PatentInnovationSignalResult.model_rebuild()


class PatentInnovationSignalAgent(BaseWorkflowAgent):
    agent_key = "patent_innovation_signal"

    # CHANGED: SerpAPI / OpenAlex 연동에 필요한 엔드포인트와 제한값을 patent.py 내부에 추가.
    _SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
    _OPENALEX_WORKS_ENDPOINT = "https://api.openalex.org/works"
    _PATENT_RESULT_LIMIT = 3
    _WEB_RESULT_LIMIT = 5
    _PAPER_RESULT_LIMIT = 2

    def run(self, state: AgentState) -> Dict[str, object]:
        technologies = state.get("target_technologies", [])
        companies = state.get("selected_companies", []) or state.get("candidate_companies", [])
        search_plan = build_balanced_search_plan(
            topic="특허 및 혁신 신호 조사",
            scope_hint=", ".join(technologies),
        )

        entries = []
        for technology in technologies:
            for company in companies:
                # CHANGED: 최신성 확보를 위해 SerpAPI와 OpenAlex를 먼저 사용하고, 실패 시 기존 corpus 검색으로 폴백.
                evidence = self._collect_current_signal_evidence(company=company, technology=technology)
                estimated = not evidence
                if estimated:
                    evidence = self._fallback_to_local_corpus(company=company, technology=technology)
                    summary = "[추정] 실시간 특허/논문/웹 신호 확보가 부족하여 로컬 기술 문헌 기반 간접 신호를 반영함."
                    patent_activity_summary = "[추정] 실시간 특허 activity 근거가 부족하여 원천특허/구현특허 전환 패턴은 제한적으로 해석함."
                    patent_paper_link_summary = "[추정] 실시간 특허-논문 연결 근거가 부족하여 NPL 및 시간차 분석은 제한적으로 해석함."
                    ecosystem_signal_summary = "[추정] 실시간 파트너십·투자·채용·사업화 신호가 부족하여 생태계 참여는 보수적으로 해석함."
                    confidence = "low"
                else:
                    summary = self._build_signal_summary(company=company, technology=technology, evidence=evidence)
                    patent_activity_summary = self._build_patent_activity_summary(
                        company=company,
                        technology=technology,
                        evidence=evidence,
                    )
                    patent_paper_link_summary = self._build_patent_paper_link_summary(
                        company=company,
                        technology=technology,
                        evidence=evidence,
                    )
                    ecosystem_signal_summary = self._build_ecosystem_signal_summary(
                        company=company,
                        technology=technology,
                        evidence=evidence,
                    )
                    confidence = self._estimate_confidence(evidence)
                entries.append(
                    PatentSignalEntry(
                        technology=technology,
                        company=company,
                        signal_summary=summary,
                        patent_activity_summary=patent_activity_summary,
                        patent_paper_link_summary=patent_paper_link_summary,
                        ecosystem_signal_summary=ecosystem_signal_summary,
                        indirect_evidence=[item.model_dump() for item in evidence],
                        confidence=confidence,
                        estimated=estimated,
                    )
                )

        return {
            "patent_innovation_signal": PatentInnovationSignalResult(
                entries=entries,
                search_plan=search_plan.model_dump() if hasattr(search_plan, "model_dump") else search_plan,
            ),
            "last_completed_step": self.agent_key,
        }

    # CHANGED: patent.py 내부에서만 API를 직접 호출하기 위한 통합 수집 함수.
    def _collect_current_signal_evidence(self, company: str, technology: str) -> list[EvidenceItem]:
        evidence = []
        evidence.extend(self._search_patents(company=company, technology=technology))
        evidence.extend(self._search_papers(company=company, technology=technology))
        evidence.extend(self._search_web_signals(company=company, technology=technology))

        deduped = []
        seen = set()
        for item in evidence:
            key = (item.title, item.source_path)
            if key in seen:
                continue
            item.company = company
            item.technology = technology
            deduped.append(item)
            seen.add(key)
        # CHANGED: 특허와 논문 날짜를 비교해 12~24개월 실용화 전환 신호를 evidence에 추가.
        deduped.extend(self._build_patent_paper_bridge_evidence(company=company, technology=technology, evidence=deduped))
        return deduped

    # CHANGED: API 실패 시 기존 RAG 동작을 유지하도록 로컬 코퍼스 검색을 폴백으로 분리.
    def _fallback_to_local_corpus(self, company: str, technology: str) -> list[EvidenceItem]:
        evidence = self.dependencies.corpora.search(
            "research",
            "%s %s patent partnership investment commercialization" % (company, technology),
            top_k=3,
        )
        if not evidence:
            evidence = self.dependencies.corpora.search(
                "research",
                "%s indirect maturity signal" % technology,
                top_k=2,
            )
            for item in evidence:
                item.estimated = True
        for item in evidence:
            item.company = company
            item.technology = technology
        return evidence

    # CHANGED: SerpAPI Google Patents 검색으로 특허 activity 근거를 수집.
    def _search_patents(self, company: str, technology: str) -> list[EvidenceItem]:
        api_key = os.getenv("SERPAPI_API_KEY")
        if not api_key:
            return []

        query = '"%s" "%s" semiconductor patent' % (company, technology)
        payload = self._fetch_json(
            self._SERPAPI_ENDPOINT,
            {
                "engine": "google_patents",
                "q": query,
                "num": self._PATENT_RESULT_LIMIT,
                "api_key": api_key,
            },
        )
        organic_results = payload.get("organic_results", [])

        evidence = []
        for patent in organic_results[: self._PATENT_RESULT_LIMIT]:
            patent_id = patent.get("patent_id")
            details = self._fetch_serpapi_patent_details(patent_id, api_key) if patent_id else {}
            filing_date = self._parse_date(details.get("filing_date") or patent.get("filing_date"))
            publication_date = self._parse_date(details.get("publication_date") or patent.get("publication_date"))
            published_at = publication_date or filing_date
            title = details.get("title") or patent.get("title") or "%s 관련 특허" % technology
            assignees = details.get("assignees") or [patent.get("assignee")] if patent.get("assignee") else []
            inventors = details.get("inventors") or []
            inventor_names = ", ".join(
                inventor.get("name", "") for inventor in inventors if isinstance(inventor, dict) and inventor.get("name")
            )
            # CHANGED: 특허 제목/초록/청구항 키워드를 바탕으로 broad vs narrow 성격을 휴리스틱으로 추정.
            claim_style = self._classify_patent_claim_style(title=title, details=details)
            content_bits = [
                "특허 activity",
                "assignee=%s" % ", ".join(assignees) if assignees else None,
                "inventor=%s" % inventor_names if inventor_names else patent.get("inventor"),
                "filing_date=%s" % filing_date.isoformat() if filing_date else None,
                "publication_date=%s" % publication_date.isoformat() if publication_date else None,
                "claim_style=%s" % claim_style if claim_style else None,
                "prior_art_keywords=%s" % ", ".join(details.get("prior_art_keywords", [])[:5])
                if details.get("prior_art_keywords")
                else None,
            ]
            url = details.get("pdf") or patent.get("patent_link") or "https://patents.google.com/"
            evidence.append(
                EvidenceItem(
                    title=title,
                    content=" | ".join(bit for bit in content_bits if bit),
                    source_path=url,
                    source_type="patent",
                    published_at=published_at,
                    confidence="high" if details else "medium",
                )
            )
            # CHANGED: 특허 상세 응답에서 NPL/비특허문헌 인용 후보를 찾아 별도 evidence로 기록.
            evidence.extend(
                self._extract_npl_evidence(
                    company=company,
                    technology=technology,
                    patent_title=title,
                    patent_url=url,
                    patent_date=published_at,
                    details=details,
                )
            )
        return evidence

    # CHANGED: OpenAlex로 논문 발표일, 기관, 저자 기반의 특허-논문 연결 힌트를 수집.
    def _search_papers(self, company: str, technology: str) -> list[EvidenceItem]:
        query = '"%s" %s semiconductor' % (company, technology)
        params = {
            "search": query,
            "per-page": str(self._PAPER_RESULT_LIMIT),
            "mailto": os.getenv("OPENALEX_EMAIL", "support@example.com"),
        }
        api_key = os.getenv("OPENALEX_API_KEY")
        if api_key:
            params["api_key"] = api_key

        payload = self._fetch_json(self._OPENALEX_WORKS_ENDPOINT, params)
        works = payload.get("results", [])

        evidence = []
        for work in works[: self._PAPER_RESULT_LIMIT]:
            authorships = work.get("authorships", [])
            author_names = []
            institution_names = []
            for authorship in authorships[:4]:
                author = authorship.get("author") or {}
                if author.get("display_name"):
                    author_names.append(author["display_name"])
                for institution in authorship.get("institutions", [])[:2]:
                    if institution.get("display_name"):
                        institution_names.append(institution["display_name"])
            publication_date = self._parse_date(work.get("publication_date"))
            content_bits = [
                "논문-특허 연결 후보",
                "authors=%s" % ", ".join(author_names[:4]) if author_names else None,
                "institutions=%s" % ", ".join(dict.fromkeys(institution_names)) if institution_names else None,
                "cited_by=%s" % work.get("cited_by_count") if work.get("cited_by_count") is not None else None,
                "publication_date=%s" % publication_date.isoformat() if publication_date else None,
            ]
            evidence.append(
                EvidenceItem(
                    title=work.get("display_name", "%s 관련 논문" % technology),
                    content=" | ".join(bit for bit in content_bits if bit),
                    source_path=work.get("id", self._OPENALEX_WORKS_ENDPOINT),
                    source_type="paper",
                    published_at=publication_date,
                    confidence="medium",
                )
            )
        return evidence

    # CHANGED: SerpAPI 일반 웹검색으로 파트너십, 투자, 채용, 사업화 같은 최신 혁신 신호를 수집.
    def _search_web_signals(self, company: str, technology: str) -> list[EvidenceItem]:
        api_key = os.getenv("SERPAPI_API_KEY")
        if not api_key:
            return []

        evidence = []
        queries = [
            # CHANGED: 파트너십, 투자, 사업화, 공동개발, 생태계 참여를 더 넓게 잡도록 검색 쿼리를 분리.
            '"%s" "%s" partnership OR investment OR acquisition OR tapeout OR commercialization OR consortium'
            % (company, technology),
            # CHANGED: 채용 공고 키워드 변화 감지를 위해 별도 hiring 쿼리를 추가.
            '"%s" "%s" hiring OR careers OR "research scientist" OR "process engineer" OR "yield engineer" OR integration'
            % (company, technology),
        ]
        for query in queries:
            payload = self._fetch_json(
                self._SERPAPI_ENDPOINT,
                {
                    "engine": "google",
                    "q": query,
                    "num": self._WEB_RESULT_LIMIT,
                    "api_key": api_key,
                },
            )
            organic_results = payload.get("organic_results", [])
            for result in organic_results[: self._WEB_RESULT_LIMIT]:
                snippet = result.get("snippet") or "최신 혁신 신호 검색 결과"
                source_url = result.get("link") or result.get("redirect_link") or "https://www.google.com/"
                evidence.append(
                    EvidenceItem(
                        title=result.get("title", "%s 관련 웹 신호" % technology),
                        content=snippet,
                        source_path=source_url,
                        source_type="news",
                        confidence="medium",
                    )
                )
        return evidence

    # CHANGED: 특허 상세 메타데이터를 추가 조회해 filing/publication 날짜와 inventors를 보강.
    def _fetch_serpapi_patent_details(self, patent_id: str, api_key: str) -> dict:
        if not patent_id:
            return {}
        return self._fetch_json(
            self._SERPAPI_ENDPOINT,
            {
                "engine": "google_patents_details",
                "patent_id": patent_id,
                "api_key": api_key,
            },
        )

    # CHANGED: patent.py 단일 파일 내에서 재사용하는 공통 JSON 호출 함수.
    def _fetch_json(self, endpoint: str, params: dict) -> dict:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
        request = urllib.request.Request(
            "%s?%s" % (endpoint, query),
            headers={"User-Agent": "Mozilla/5.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            return {}

    # CHANGED: 요약 문장에 특허/논문/웹 신호 비중을 반영하도록 간단한 규칙을 추가.
    def _build_signal_summary(self, company: str, technology: str, evidence: list[EvidenceItem]) -> str:
        patent_count = sum(1 for item in evidence if item.source_type == "patent")
        paper_count = sum(1 for item in evidence if item.source_type == "paper")
        web_count = sum(1 for item in evidence if item.source_type in {"news", "company"})
        bridge_signals = [item for item in evidence if item.source_type == "analysis"]
        npl_count = sum(1 for item in evidence if item.source_type == "npl")
        hiring_stage = self._infer_hiring_stage(evidence)
        ecosystem_count = self._count_keyword_hits(
            evidence,
            ["partnership", "investment", "acquisition", "commercialization", "consortium", "tapeout", "joint development"],
        )
        parts = ["%s의 %s 관련 최신 간접 지표를 수집했다." % (company, technology)]
        if patent_count:
            parts.append("특허 activity %s건" % patent_count)
        if paper_count:
            parts.append("논문 연계 신호 %s건" % paper_count)
        if web_count:
            parts.append("파트너십·투자·채용 등 웹 신호 %s건" % web_count)
        if npl_count:
            parts.append("비특허문헌(NPL) 인용 후보 %s건" % npl_count)
        patent_transition = self._summarize_patent_transition(evidence)
        if patent_transition:
            parts.append(patent_transition)
        if bridge_signals:
            parts.append("논문 발표 후 12~24개월 내 특허 출원 가능성을 시사하는 연결 신호가 확인됐다")
        if ecosystem_count:
            parts.append("생태계 참여·사업화 정황이 포착됐다")
        if hiring_stage:
            parts.append(hiring_stage)
        parts.append("TRL 4~6 추정 구간의 간접 근거로 활용할 수 있다")
        return " ".join(parts)

    # CHANGED: 특허 activity를 독립된 구조화 필드로 출력하기 위한 요약 생성.
    def _build_patent_activity_summary(self, company: str, technology: str, evidence: list[EvidenceItem]) -> str:
        patent_items = [item for item in evidence if item.source_type == "patent"]
        broad_count = sum(1 for item in patent_items if "claim_style=broad_foundational_claim" in item.content)
        narrow_count = sum(1 for item in patent_items if "claim_style=narrow_implementation_claim" in item.content)
        if not patent_items:
            return "%s의 %s 관련 특허 activity 근거가 제한적이다." % (company, technology)
        if broad_count and narrow_count:
            return (
                "%s의 %s 관련 특허 activity는 총 %s건이며, 원천특허 %s건과 구현특허 %s건이 함께 관측되어 "
                "TRL 4 진입 전후의 성격 전환 가능성을 시사한다."
            ) % (company, technology, len(patent_items), broad_count, narrow_count)
        if narrow_count:
            return (
                "%s의 %s 관련 특허 activity는 총 %s건이며, 구현특허 성격이 우세해 narrow, specific claim 중심의 "
                "실용화 지향 단계로 해석할 수 있다."
            ) % (company, technology, len(patent_items))
        if broad_count:
            return (
                "%s의 %s 관련 특허 activity는 총 %s건이며, 원천특허 성격이 우세해 broad claim 중심의 "
                "초기 개념 보호 단계 가능성이 있다."
            ) % (company, technology, len(patent_items))
        return "%s의 %s 관련 특허 activity는 총 %s건으로 확인되며, 특허 성격 변화는 추가 해석이 필요하다." % (
            company,
            technology,
            len(patent_items),
        )

    # CHANGED: 특허-논문 연결, NPL, 시간차를 독립 필드로 출력하기 위한 요약 생성.
    def _build_patent_paper_link_summary(self, company: str, technology: str, evidence: list[EvidenceItem]) -> str:
        bridge_items = [item for item in evidence if item.source_type == "analysis"]
        npl_items = [item for item in evidence if item.source_type == "npl"]
        paper_items = [item for item in evidence if item.source_type == "paper"]
        overlap_found = any("발명자-저자 이름 중복" in item.content for item in bridge_items)
        month_gap_found = any("개월" in item.content for item in bridge_items)
        if bridge_items or npl_items:
            parts = [
                "%s의 %s 관련 특허-논문 연결 신호로 논문 %s건, NPL 후보 %s건이 확인됐다."
                % (company, technology, len(paper_items), len(npl_items))
            ]
            if overlap_found:
                parts.append("동일 발명자/저자 후보가 포착됐다.")
            if month_gap_found:
                parts.append("논문 발표 후 12~24개월 내 특허 출원 가능성을 시사하는 시간차 신호가 확인됐다.")
            parts.append("연구에서 실용화 단계로의 전환 가능성을 해석하는 간접 근거로 활용할 수 있다.")
            return " ".join(parts)
        if paper_items:
            return "%s의 %s 관련 논문은 확인되지만, 특허와 직접 연결되는 NPL 또는 시간차 신호는 제한적이다." % (
                company,
                technology,
            )
        return "%s의 %s 관련 특허-논문 연결 근거는 제한적이다." % (company, technology)

    # CHANGED: 생태계 참여, 투자, 채용, 사업화 신호를 독립 필드로 출력하기 위한 요약 생성.
    def _build_ecosystem_signal_summary(self, company: str, technology: str, evidence: list[EvidenceItem]) -> str:
        web_items = [item for item in evidence if item.source_type in {"news", "company"}]
        ecosystem_count = self._count_keyword_hits(
            web_items,
            ["partnership", "investment", "acquisition", "commercialization", "consortium", "tapeout", "joint development"],
        )
        hiring_stage = self._infer_hiring_stage(evidence)
        if web_items:
            parts = [
                "%s의 %s 관련 생태계·사업화 신호는 웹 근거 %s건으로 확인됐다." % (company, technology, len(web_items))
            ]
            if ecosystem_count:
                parts.append("공동개발, 투자, 사업화 또는 생태계 참여 정황이 포착됐다.")
            if hiring_stage:
                parts.append(hiring_stage)
            return " ".join(parts)
        return "%s의 %s 관련 생태계·사업화 웹 신호는 제한적이다." % (company, technology)

    # CHANGED: 출처 다양성과 특허 근거 포함 여부를 기준으로 confidence를 계산.
    def _estimate_confidence(self, evidence: list[EvidenceItem]) -> str:
        source_types = {item.source_type for item in evidence}
        has_patent = "patent" in source_types
        has_bridge = "analysis" in source_types
        has_npl = "npl" in source_types
        if has_patent and has_bridge and has_npl and len(source_types) >= 4 and len(evidence) >= 6:
            return "high"
        if has_patent or len(source_types) >= 2:
            return "medium"
        return "low"

    # CHANGED: 외부 API 응답의 날짜 문자열을 EvidenceItem 형식에 맞게 정규화.
    def _parse_date(self, raw_value: str | None) -> date | None:
        if not raw_value:
            return None
        try:
            return date.fromisoformat(raw_value[:10])
        except ValueError:
            return None

    # CHANGED: 특허 출원일과 논문 발표일의 시간차를 계산해 특허-논문 브리지 evidence를 생성.
    def _build_patent_paper_bridge_evidence(
        self, company: str, technology: str, evidence: list[EvidenceItem]
    ) -> list[EvidenceItem]:
        patents = [item for item in evidence if item.source_type == "patent" and item.published_at]
        papers = [item for item in evidence if item.source_type == "paper" and item.published_at]
        npl_candidates = [item for item in evidence if item.source_type == "npl"]
        bridge_evidence = []
        for patent in patents[:2]:
            for paper in papers[:2]:
                month_gap = self._month_gap(earlier=paper.published_at, later=patent.published_at)
                if month_gap is None or month_gap < 0:
                    continue
                author_overlap = self._find_person_overlap(patent.content, paper.content)
                npl_link = self._match_npl_to_paper(paper, npl_candidates)
                if 12 <= month_gap <= 24 or author_overlap or npl_link:
                    bridge_reason = []
                    if 12 <= month_gap <= 24:
                        bridge_reason.append("논문 발표 후 %s개월 뒤 특허 공개/출원이 이어짐" % month_gap)
                    if author_overlap:
                        bridge_reason.append("발명자-저자 이름 중복=%s" % ", ".join(author_overlap))
                    if npl_link:
                        bridge_reason.append("특허 내 NPL 후보와 논문 제목이 유사함")
                    bridge_evidence.append(
                        EvidenceItem(
                            title="%s %s 특허-논문 시간차 분석" % (company, technology),
                            content="%s | paper=%s | patent=%s"
                            % (" | ".join(bridge_reason), paper.title, patent.title),
                            source_path="%s#bridge" % patent.source_path,
                            source_type="analysis",
                            published_at=patent.published_at,
                            confidence="medium",
                        )
                    )
        return bridge_evidence

    # CHANGED: 채용 공고 키워드 변화로 TRL 단계 해석 문장을 생성.
    def _infer_hiring_stage(self, evidence: list[EvidenceItem]) -> str:
        research_hits = self._count_keyword_hits(evidence, ["research scientist", "research engineer", "scientist"])
        process_hits = self._count_keyword_hits(evidence, ["process engineer", "integration engineer", "integration"])
        yield_hits = self._count_keyword_hits(evidence, ["yield engineer", "quality engineer", "quality", "yield"])
        if yield_hits > max(research_hits, process_hits):
            return "Yield·Quality 직군 신호가 상대적으로 강해 TRL 7~8 진입 가능성을 시사한다"
        if process_hits >= research_hits and process_hits > 0:
            return "Process·Integration 직군 신호가 포착돼 TRL 4~6 구간 해석과 부합한다"
        if research_hits > 0:
            return "Research 직군 비중이 높아 TRL 1~3 초기 연구 단계 가능성이 남아 있다"
        return ""

    # CHANGED: evidence 본문과 제목에서 키워드 출현 횟수를 세는 보조 함수.
    def _count_keyword_hits(self, evidence: list[EvidenceItem], keywords: list[str]) -> int:
        hits = 0
        for item in evidence:
            haystack = ("%s %s" % (item.title, item.content)).lower()
            for keyword in keywords:
                if keyword.lower() in haystack:
                    hits += 1
        return hits

    # CHANGED: 논문 발표일과 특허 날짜의 월 차이를 계산하는 보조 함수.
    def _month_gap(self, earlier: date | None, later: date | None) -> int | None:
        if not earlier or not later:
            return None
        return (later.year - earlier.year) * 12 + (later.month - earlier.month)

    # CHANGED: 특허 상세 JSON에서 NPL, 논문, 학술 출처 관련 텍스트를 재귀 탐색해 evidence로 변환.
    def _extract_npl_evidence(
        self,
        company: str,
        technology: str,
        patent_title: str,
        patent_url: str,
        patent_date: date | None,
        details: dict,
    ) -> list[EvidenceItem]:
        candidates = self._collect_text_candidates(details)
        npl_items = []
        seen = set()
        for text in candidates:
            lowered = text.lower()
            if not self._looks_like_npl_reference(lowered):
                continue
            normalized = text.strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            npl_items.append(
                EvidenceItem(
                    title="%s NPL 후보" % technology,
                    content="patent=%s | npl=%s" % (patent_title, normalized[:400]),
                    source_path="%s#npl" % patent_url,
                    source_type="npl",
                    published_at=patent_date,
                    confidence="medium",
                    company=company,
                    technology=technology,
                )
            )
            if len(npl_items) >= 3:
                break
        return npl_items

    # CHANGED: 특허 제목/설명 기반으로 원천특허 vs 구현특허 성격을 거칠게 분류.
    def _classify_patent_claim_style(self, title: str, details: dict) -> str:
        text = " ".join(self._collect_text_candidates({"title": title, "details": details})[:40]).lower()
        broad_keywords = ["system", "method", "architecture", "framework", "general", "platform", "memory system"]
        narrow_keywords = ["fabrication", "process", "packaging", "controller", "interface", "yield", "stack", "device"]
        broad_score = sum(1 for keyword in broad_keywords if keyword in text)
        narrow_score = sum(1 for keyword in narrow_keywords if keyword in text)
        if narrow_score > broad_score:
            return "narrow_implementation_claim"
        if broad_score > 0:
            return "broad_foundational_claim"
        return "undetermined"

    # CHANGED: 특허 성격 변화 요약을 만들어 문서의 broad -> narrow 패턴 설명에 가깝게 맞춤.
    def _summarize_patent_transition(self, evidence: list[EvidenceItem]) -> str:
        patent_items = [item for item in evidence if item.source_type == "patent"]
        broad_count = sum(1 for item in patent_items if "claim_style=broad_foundational_claim" in item.content)
        narrow_count = sum(1 for item in patent_items if "claim_style=narrow_implementation_claim" in item.content)
        if broad_count and narrow_count:
            return "원천특허와 구현특허 성격이 함께 관측되어 TRL 4 전후의 전환 패턴 가능성을 시사한다"
        if narrow_count:
            return "구현특허 비중이 높아 실용화 지향 특허 activity로 해석할 수 있다"
        if broad_count:
            return "원천특허 성격이 우세해 아직 초기 개념 보호 단계일 가능성이 있다"
        return ""

    # CHANGED: inventor-author 이름 비교를 위해 사람 이름 토큰을 정규화한다.
    def _normalize_person_name(self, raw_name: str) -> str:
        lowered = raw_name.lower()
        lowered = re.sub(r"\([^)]*\)", " ", lowered)
        lowered = re.sub(r"[^a-z0-9 ]+", " ", lowered)
        tokens = [token for token in lowered.split() if len(token) > 1]
        return " ".join(tokens[:3])

    # CHANGED: 특허 evidence와 논문 evidence 사이 이름 중복을 찾아 inventor-author 연결 힌트로 사용.
    def _find_person_overlap(self, patent_text: str, paper_text: str) -> list[str]:
        patent_names = set()
        paper_names = set()
        for chunk in patent_text.split("|"):
            if "inventor=" in chunk.lower():
                for name in chunk.split("=", 1)[-1].split(","):
                    normalized = self._normalize_person_name(name)
                    if normalized:
                        patent_names.add(normalized)
        for chunk in paper_text.split("|"):
            if "authors=" in chunk.lower():
                for name in chunk.split("=", 1)[-1].split(","):
                    normalized = self._normalize_person_name(name)
                    if normalized:
                        paper_names.add(normalized)
        overlap = patent_names.intersection(paper_names)
        return sorted(overlap)[:3]

    # CHANGED: NPL 후보 문구와 논문 제목 간의 단순 유사도를 비교한다.
    def _match_npl_to_paper(self, paper: EvidenceItem, npl_candidates: list[EvidenceItem]) -> bool:
        paper_tokens = set(self._tokenize_for_match(paper.title))
        if not paper_tokens:
            return False
        for candidate in npl_candidates:
            candidate_tokens = set(self._tokenize_for_match(candidate.content))
            if len(paper_tokens.intersection(candidate_tokens)) >= 3:
                return True
        return False

    # CHANGED: 중첩된 JSON 응답에서 텍스트 후보를 폭넓게 수집하는 재귀 함수.
    def _collect_text_candidates(self, payload: Any) -> list[str]:
        results = []
        if isinstance(payload, dict):
            for value in payload.values():
                results.extend(self._collect_text_candidates(value))
        elif isinstance(payload, list):
            for item in payload:
                results.extend(self._collect_text_candidates(item))
        elif isinstance(payload, str):
            normalized = " ".join(payload.split())
            if len(normalized) >= 12:
                results.append(normalized)
        return results

    # CHANGED: 문자열이 NPL/학술 인용처럼 보이는지 판별하는 휴리스틱.
    def _looks_like_npl_reference(self, lowered_text: str) -> bool:
        npl_keywords = [
            "doi",
            "arxiv",
            "ieee",
            "acm",
            "journal",
            "conference",
            "proceedings",
            "non-patent",
            "literature",
            "paper",
            "et al",
        ]
        year_match = re.search(r"\b(19|20)\d{2}\b", lowered_text)
        return any(keyword in lowered_text for keyword in npl_keywords) and year_match is not None

    # CHANGED: 제목/본문 비교에 사용할 간단한 토큰화 함수.
    def _tokenize_for_match(self, text: str) -> list[str]:
        return [token for token in re.findall(r"[a-z0-9]{3,}", text.lower()) if token not in {"with", "from", "that"}]
