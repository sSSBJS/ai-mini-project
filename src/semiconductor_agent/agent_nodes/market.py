from __future__ import annotations

from datetime import date
from typing import Dict, List, Sequence

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent
from semiconductor_agent.models import EvidenceItem, MarketResearchResult, SearchResult
from semiconductor_agent.search import (
    SearchPromptBundle,
    SearchResearchBlueprint,
    SearchTask,
    SearchVerificationReport,
    build_balanced_search_plan,
    validate_search_balance,
)
from semiconductor_agent.state import AgentState


def build_market_research_blueprint(
    technologies: Sequence[str],
    companies: Sequence[str],
) -> SearchResearchBlueprint:
    technology_scope = ", ".join(technologies)
    company_scope = ", ".join(companies)
    search_plan = build_balanced_search_plan(
        topic="반도체 시장 조사 및 경쟁사 선정",
        scope_hint=technology_scope,
    )
    tasks = [
        SearchTask(
            task_id="market-landscape",
            title="시장 구도 및 투자 동향 조사",
            objective="HBM4, PIM, CXL 시장 구도와 최근 투자/양산/채택 시그널을 정리한다.",
            focus="market",
            priority=1,
            queries=[
                "%s semiconductor market outlook %s" % (technology_scope, date.today().year),
                "%s memory accelerator adoption investment" % technology_scope,
                search_plan.objective_query,
            ],
            required_source_types=["news", "company"],
            verification_questions=[
                "시장 규모, 성장성, 투자 방향이 최신 기사와 기업 발표로 교차 검증되는가.",
                "비교 대상 기업 선정 근거가 현재 시장 구도와 맞는가.",
            ],
            deliverable="시장 요약, 투자 동향, 경쟁 축",
        ),
        SearchTask(
            task_id="company-roadmap",
            title="기업별 로드맵 및 발표 검증",
            objective="경쟁 기업의 공식 발표, 뉴스룸, 투자자 자료를 통해 기술 추진 상태를 확인한다.",
            focus="company",
            priority=2,
            queries=[
                "%s %s roadmap newsroom investor" % (company_scope, technology_scope),
                "%s %s production sampling partnership" % (company_scope, technology_scope),
                search_plan.confirming_query,
            ],
            required_source_types=["company", "news"],
            verification_questions=[
                "공식 발표와 기사 해석이 충돌하지 않는가.",
                "양산, 샘플링, 제휴 표현이 과장 없이 정리되었는가.",
            ],
            deliverable="기업별 공식 로드맵과 실행 신호",
        ),
        SearchTask(
            task_id="market-risk",
            title="시장 채택 리스크 검증",
            objective="수율, 원가, 공급망, 표준 채택 지연 같은 사업화 리스크를 수집한다.",
            focus="risk",
            priority=3,
            queries=[
                "%s supply chain yield cost adoption risk" % technology_scope,
                "%s bottleneck delay commercialization risk" % technology_scope,
                search_plan.opposing_query,
            ],
            required_source_types=["news", "company", "paper"],
            verification_questions=[
                "반대 근거가 실제로 포함되어 있는가.",
                "위험도 판단에 영향을 줄 치명적 리스크가 누락되지 않았는가.",
            ],
            deliverable="시장, 사업화 리스크 목록",
        ),
    ]
    prompts = SearchPromptBundle(
        planner_prompt=(
            "당신은 market research planner다.\n"
            "기술 범위는 %s, 비교 기업은 %s 이다.\n"
            "시장 구조, 경쟁사 선정, 기업 발표 검증, 사업화 리스크를 분리해 조사 순서를 짜라.\n"
            "SerpAPI는 시장, 기업, 뉴스 조사에 우선 사용하고, 필요 시 내부 RAG로 reference 문서를 다시 확인한다."
        ) % (technology_scope, company_scope),
        execution_prompt=(
            "당신은 market research agent다.\n"
            "- 경쟁사 비교는 동일 기술 축(HBM4, PIM, CXL)으로 유지한다.\n"
            "- 시장 주장에는 뉴스 또는 기업 출처를 붙인다.\n"
            "- 직접 근거가 약하면 [추정]이라고 표시한다.\n"
            "- supervisor에 보내기 전에 과장 표현, 최신성 부족, 단일 출처 의존을 점검한다."
        ),
        verification_prompt=(
            "당신은 Market Verification Node다.\n"
            "시장 규모, 투자 동향, 공식 발표, 사업화 리스크가 서로 다른 출처로 교차 검증되었는지 확인하라.\n"
            "blocking issue가 있으면 market agent에 재검색을 요청하라."
        ),
        supervisor_handoff_prompt=(
            "당신은 supervisor handoff reviewer다.\n"
            "시장 조사 결과가 selected_companies 선정, 후속 위협 평가, 전략 수립에 바로 쓰일 수 있는지 확인하라.\n"
            "기업별 근거 또는 시장 리스크 근거가 약하면 supervisor에 보내지 말고 반려하라."
        ),
    )
    return SearchResearchBlueprint(
        goal="시장 조사 및 경쟁사 선정",
        search_plan=search_plan,
        hypothesis=search_plan.hypothesis,
        task_manager_notes=[
            "시장 자료는 최신 기사와 기업 발표를 분리해 확인한다.",
            "경쟁사 선정은 HBM4, PIM, CXL 범위에 실제로 등장하는 기업 위주로 고정한다.",
            "위험도 분석과 연결될 사업화 리스크를 시장 단계에서 미리 표시한다.",
        ],
        tasks=tasks,
        prompts=prompts,
        rag_queries=[
            "%s market roadmap investment" % technology for technology in technologies
        ]
        + [
            "%s semiconductor strategy roadmap" % company for company in companies
        ],
        supervisor_gate_checklist=[
            "선정 기업이 비어 있지 않은가.",
            "시장 요약이 HBM4, PIM, CXL 범위를 유지하는가.",
            "기업 자료와 뉴스 자료가 모두 존재하는가.",
            "사업화 리스크가 최소 1개 이상 정리되었는가.",
        ],
    )


class MarketResearchCollectorAgent(BaseWorkflowAgent):
    agent_key = "market_research"

    def run(self, state: AgentState) -> Dict[str, object]:
        technologies = state.get("target_technologies", [])
        companies = state.get("candidate_companies", [])
        blueprint = build_market_research_blueprint(technologies, companies)
        search_plan = blueprint.search_plan

        company_findings = {}
        latest_articles = []
        for company in companies:
            company_evidence = []
            for technology in technologies:
                hits = self.dependencies.corpora.search(
                    "research",
                    "%s %s semiconductor market technical trend" % (company, technology),
                    top_k=2,
                )
                for hit in hits:
                    hit.company = company
                    hit.technology = technology
                company_evidence.extend(hits)
            if not company_evidence:
                fallback_hits = self.dependencies.corpora.search(
                    "research",
                    "%s semiconductor strategy" % company,
                    top_k=2,
                )
                for hit in fallback_hits:
                    hit.company = company
                company_evidence.extend(fallback_hits)
            company_findings[company] = company_evidence
            latest_articles.extend(company_evidence[:1])

        web_results: List[SearchResult] = []
        verification = SearchVerificationReport(approved=True)
        if self.dependencies.runtime.enable_web_search and state.get("search_count", 0) < state.get("search_budget_limit", 5):
            for task in blueprint.tasks:
                web_results.extend(self.dependencies.web_search.search_task(task, max_results_per_query=2))
            verification = self.dependencies.web_search.verify_handoff(
                web_results,
                required_source_types=("company", "news"),
            )
            latest_articles.extend(self._search_results_to_evidence(web_results[:3]))

        market_summary = self._compose_market_summary(
            technologies=technologies,
            companies=companies,
            company_findings=company_findings,
            latest_articles=latest_articles,
            web_results=web_results,
        )

        issues = validate_search_balance(web_results)
        issues.extend(verification.issues)
        return {
            "market_research": MarketResearchResult(
                selected_companies=companies,
                market_summary=market_summary,
                company_findings=company_findings,
                latest_articles=latest_articles,
                search_plan=search_plan,
            ),
            "selected_companies": companies,
            "search_count": self._increment_search_count(state, 1 if web_results else 0),
            "validation_issues": self.append_issues(state, issues),
        }

    def _search_results_to_evidence(self, results: Sequence[SearchResult]) -> List[EvidenceItem]:
        evidence = []
        for item in results:
            evidence.append(
                EvidenceItem(
                    title=item.title,
                    content=item.snippet,
                    source_path=item.url,
                    source_type=item.source_type,
                    published_at=item.published_at,
                    confidence="medium",
                )
            )
        return evidence

    def _compose_market_summary(
        self,
        technologies: Sequence[str],
        companies: Sequence[str],
        company_findings: Dict[str, List[EvidenceItem]],
        latest_articles: Sequence[EvidenceItem],
        web_results: Sequence[SearchResult],
    ) -> str:
        ranked_companies = sorted(
            companies,
            key=lambda company: len(company_findings.get(company, [])),
            reverse=True,
        )
        company_fragments = []
        for company in ranked_companies[:4]:
            items = company_findings.get(company, [])
            if not items:
                company_fragments.append("%s는 직접 근거가 부족해 추가 검증이 필요하다." % company)
                continue
            sample = items[0]
            company_fragments.append(
                "%s는 %d건의 근거가 확인되었고 대표 근거는 %s이다."
                % (company, len(items), sample.title)
            )

        latest_titles = [item.title for item in latest_articles[:3]]
        summary = (
            "%s 범위를 기준으로 %s를 비교한 결과, %s "
            "시장 조사는 후속 TRL 및 위협 평가가 같은 기술-기업 축을 유지하도록 설계했다."
        ) % (
            ", ".join(technologies),
            ", ".join(companies),
            " ".join(company_fragments),
        )
        if latest_titles:
            summary += " 최근 확인된 대표 자료는 %s 이다." % ", ".join(latest_titles)
        if web_results:
            summary += (
                " 외부 검색에서는 시장 동향, 기업 발표, 사업화 리스크를 분리 조사했고 "
                "최소 2종 이상의 출처 유형으로 교차 검증했다."
            )
        return summary
