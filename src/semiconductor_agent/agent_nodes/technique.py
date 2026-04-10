from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Dict, List, Sequence

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent
from semiconductor_agent.models import EvidenceItem, SearchResult, TechniqueResearchResult, TechnologyBrief, ValidationIssue
from semiconductor_agent.search import (
    SearchPromptBundle,
    SearchResearchBlueprint,
    SearchTask,
    SearchVerificationReport,
    build_balanced_search_plan,
)
from semiconductor_agent.state import AgentState


def build_technique_research_blueprint(
    technologies: Sequence[str],
) -> SearchResearchBlueprint:
    technology_scope = ", ".join(technologies)
    search_plan = build_balanced_search_plan(
        topic="반도체 최신 기술 조사",
        scope_hint=technology_scope,
    )
    tasks = [
        SearchTask(
            task_id="paper-review",
            title="논문 및 학술 근거 조사",
            objective="핵심 기술의 원리, 성능 병목, 구현 난도를 학술 근거로 확보한다.",
            focus="paper",
            priority=1,
            queries=[
                "%s architecture performance bottleneck" % technology_scope,
                "%s peer reviewed benchmark" % technology_scope,
                search_plan.objective_query,
            ],
            required_source_types=["paper"],
            verification_questions=[
                "핵심 주장을 뒷받침하는 최근 논문 근거가 있는가.",
                "학술 근거가 실제 기술 성숙도 판단과 연결되는가.",
            ],
            deliverable="기술별 학술 근거와 병목 요약",
        ),
        SearchTask(
            task_id="standard-validation",
            title="표준 및 상호운용성 조사",
            objective="표준 문서, 인터페이스 제약, 상호운용성 이슈를 수집한다.",
            focus="paper",
            priority=2,
            queries=[
                "%s standard specification interoperability" % technology_scope,
                "%s validation benchmark standard" % technology_scope,
                search_plan.confirming_query,
            ],
            required_source_types=["standard", "paper"],
            verification_questions=[
                "표준 또는 사양 문서가 포함되었는가.",
                "구현 난도를 높이는 상호운용성 이슈가 정리되었는가.",
            ],
            deliverable="표준, 사양, 상호운용성 메모",
        ),
        SearchTask(
            task_id="technical-risk",
            title="기술 한계 및 반대 근거 검증",
            objective="성능, 전력, 열, 수율, 도입 비용 측면의 기술적 한계를 정리한다.",
            focus="risk",
            priority=3,
            queries=[
                "%s power thermal yield bottleneck" % technology_scope,
                "%s limitation deployment risk" % technology_scope,
                search_plan.opposing_query,
            ],
            required_source_types=["paper", "news"],
            verification_questions=[
                "반대 근거가 실제 검색 결과에 포함되어 있는가.",
                "기술 낙관론만 남기고 한계 자료를 놓치지 않았는가.",
            ],
            deliverable="기술 한계 및 위험 요인",
        ),
    ]
    prompts = SearchPromptBundle(
        planner_prompt=(
            "당신은 technique research planner다.\n"
            "기술 범위는 %s 이다.\n"
            "논문, 표준, 기술 한계 검증을 분리하고 OpenAlex를 학술 근거 우선 수집 채널로 사용하라.\n"
            "필요하면 내부 RAG에서 reference 논문과 표준 PDF를 다시 확인하라."
        ) % technology_scope,
        execution_prompt=(
            "당신은 technique research agent다.\n"
            "- 기술별 직접 근거를 우선 수집한다.\n"
            "- 원리, 성능, 병목, 표준, 한계를 각각 정리한다.\n"
            "- 근거가 약하면 [추정]으로 남기고 confidence를 낮춘다.\n"
            "- supervisor 전달 전에 Evidence Validation Node를 반드시 통과해야 한다."
        ),
        verification_prompt=(
            "당신은 Evidence Validation Node다.\n"
            "기술별 근거 수, 출처 다양성, 중복 여부, 최신성, 반대 근거 포함 여부를 확인하라.\n"
            "blocking issue가 있으면 technique agent에 재검색을 요청하라."
        ),
        supervisor_handoff_prompt=(
            "당신은 supervisor handoff reviewer다.\n"
            "기술 조사 결과가 후속 TRL 평가와 전략 수립에 바로 쓰일 수 있는지 확인하라.\n"
            "기술별 핵심 근거, 핵심 주장, freshness note가 없으면 반려하라."
        ),
    )
    rag_queries = []
    for technology in technologies:
        rag_queries.extend(
            [
                "%s architecture principle performance roadmap" % technology,
                "%s standard interoperability" % technology,
                "%s bottleneck validation limitation" % technology,
            ]
        )
    return SearchResearchBlueprint(
        goal="반도체 최신 기술 조사",
        search_plan=search_plan,
        hypothesis=search_plan.hypothesis,
        task_manager_notes=[
            "논문은 최근 5년을 우선 보되 고전 표준 문서는 예외적으로 허용한다.",
            "직접 근거가 부족한 기술은 reference corpus와 외부 논문을 함께 사용해 보수적으로 요약한다.",
            "기술 요약은 후속 TRL 판단에 바로 재사용될 수 있게 원리, 병목, 검증 근거를 함께 남긴다.",
        ],
        tasks=tasks,
        prompts=prompts,
        rag_queries=rag_queries,
        supervisor_gate_checklist=[
            "기술별 brief가 모두 존재하는가.",
            "기술별 supporting evidence가 2개 이상인가.",
            "논문 또는 표준 계열 근거가 포함되어 있는가.",
            "freshness note와 validation issue가 함께 남아 있는가.",
        ],
    )


class TechniqueResearchCollectorAgent(BaseWorkflowAgent):
    agent_key = "technique_research"
    _TARGET_EVIDENCE_PER_TECH = 10
    _WEB_SHARE = 0.7

    def run(self, state: AgentState) -> Dict[str, object]:
        technologies = state.get("target_technologies", [])
        blueprint = build_technique_research_blueprint(technologies)
        search_plan = blueprint.search_plan
        technology_briefs = {}
        validation_issues = []
        external_results_used = False

        for technology in technologies:
            rag_evidence = self._collect_internal_rag_evidence(technology, blueprint)
            for item in rag_evidence:
                item.technology = technology

            web_evidence = self._collect_external_evidence(state, technology)
            if web_evidence:
                external_results_used = True
            evidence = self._blend_evidence(
                rag_evidence=rag_evidence,
                web_evidence=web_evidence,
                total_target=self._TARGET_EVIDENCE_PER_TECH,
            )

            brief = TechnologyBrief(
                technology=technology,
                summary=self._compose_technology_summary(technology, evidence),
                key_points=self._build_key_points(technology, evidence),
                core_claims=self._build_claims(technology, evidence),
                supporting_evidence=evidence,
                expansion_keywords=[
                    technology,
                    "%s roadmap" % technology,
                    "%s validation" % technology,
                    "%s bottleneck" % technology,
                ],
                freshness_note=self._freshness_note(evidence),
            )
            issues = self._evidence_validation_node(brief)
            brief.validation_issues = issues
            validation_issues.extend(issues)
            technology_briefs[technology] = brief

        return {
            "technique_research": TechniqueResearchResult(
                technology_briefs=technology_briefs,
                evidence_validation_issues=validation_issues,
                search_plan=search_plan,
            ),
            "search_count": self._increment_search_count(state, 1 if external_results_used else 0),
            "validation_issues": self.append_issues(state, validation_issues),
        }

    def _collect_internal_rag_evidence(
        self,
        technology: str,
        blueprint: SearchResearchBlueprint,
    ) -> List[EvidenceItem]:
        queries = [
            "%s architecture principle performance roadmap" % technology,
            "%s standard interoperability" % technology,
            "%s bottleneck validation limitation" % technology,
        ]
        queries.extend(
            query for query in blueprint.rag_queries
            if technology.lower() in query.lower()
        )

        evidence: List[EvidenceItem] = []
        for query in queries[:5]:
            evidence.extend(self.dependencies.corpora.search("research", query, top_k=2))

        # TRL corpus is not the primary source for technique research,
        # but its system-level readiness guidance can reinforce limitation notes.
        evidence.extend(
            self.dependencies.corpora.search(
                "trl",
                "%s readiness validation system integration" % technology,
                top_k=1,
            )
        )
        return self._deduplicate_evidence(evidence)

    def _collect_external_evidence(self, state: AgentState, technology: str) -> List[EvidenceItem]:
        if not self.dependencies.runtime.enable_web_search:
            return []
        if state.get("search_count", 0) >= state.get("search_budget_limit", 5):
            return []

        paper_task = SearchTask(
            task_id="paper-review-%s" % technology.lower(),
            title="%s 논문 조사" % technology,
            objective="%s 관련 최근 논문과 기술 검증 근거를 찾는다." % technology,
            focus="paper",
            priority=1,
            queries=[
                "%s architecture performance bottleneck" % technology,
                "%s peer reviewed benchmark" % technology,
            ],
            required_source_types=["paper"],
            verification_questions=["최근 학술 근거가 존재하는가."],
            deliverable="%s 논문 근거" % technology,
        )
        risk_task = SearchTask(
            task_id="risk-review-%s" % technology.lower(),
            title="%s 한계 검증" % technology,
            objective="%s 관련 기술 한계와 반대 근거를 찾는다." % technology,
            focus="risk",
            priority=2,
            queries=[
                "%s limitation thermal power risk" % technology,
                "%s adoption bottleneck" % technology,
            ],
            required_source_types=["paper", "news"],
            verification_questions=["반대 근거가 존재하는가."],
            deliverable="%s 기술 리스크" % technology,
        )
        results: List[SearchResult] = self.dependencies.web_search.search_task(paper_task, max_results_per_query=4)
        results.extend(self.dependencies.web_search.search_task(risk_task, max_results_per_query=3))
        verification = self.dependencies.web_search.verify_handoff(
            results,
            required_source_types=("paper",),
        )
        return self._search_results_to_evidence(results, technology, verification)

    def _search_results_to_evidence(
        self,
        results: Sequence[SearchResult],
        technology: str,
        verification: SearchVerificationReport,
    ) -> List[EvidenceItem]:
        evidence = []
        estimated = not verification.approved
        confidence = "medium" if verification.approved else "low"
        for item in results:
            evidence.append(
                EvidenceItem(
                    title=item.title,
                    content=item.snippet,
                    source_path=item.url,
                    source_type=item.source_type,
                    technology=technology,
                    published_at=item.published_at,
                    confidence=confidence,
                    estimated=estimated,
                )
            )
        return evidence

    def _blend_evidence(
        self,
        rag_evidence: Sequence[EvidenceItem],
        web_evidence: Sequence[EvidenceItem],
        total_target: int,
    ) -> List[EvidenceItem]:
        web_target = max(1, int(round(total_target * self._WEB_SHARE)))
        rag_target = max(1, total_target - web_target)
        blended = list(web_evidence[:web_target])
        blended.extend(rag_evidence[:rag_target])
        if len(blended) < total_target:
            blended.extend(web_evidence[web_target:total_target])
        if len(blended) < total_target:
            blended.extend(rag_evidence[rag_target:total_target])
        return self._deduplicate_evidence(blended)[:total_target]

    def _deduplicate_evidence(self, evidence: Sequence[EvidenceItem]) -> List[EvidenceItem]:
        deduped = []
        seen = set()
        for item in evidence:
            key = (item.source_path, item.page, item.content[:120])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _compose_technology_summary(self, technology: str, evidence: Sequence[EvidenceItem]) -> str:
        if not evidence:
            return "[추정] %s 관련 직접 근거가 부족하여 reference 문서 기반의 보수적 요약만 제공한다." % technology
        first = evidence[0]
        return "%s는 %s %s를 통해 최신 구조와 기술 방향을 파악할 수 있다." % (
            technology,
            Path(first.source_path).name,
            self._citation(first),
        )

    def _build_key_points(self, technology: str, evidence: Sequence[EvidenceItem]) -> List[str]:
        if not evidence:
            return ["[추정] %s의 핵심 포인트를 확인할 추가 근거가 부족함" % technology]
        points = []
        for item in evidence[:3]:
            points.append("%s 관련 근거 요약: %s %s" % (technology, item.content[:120], self._citation(item)))
        return points

    def _build_claims(self, technology: str, evidence: Sequence[EvidenceItem]) -> List[str]:
        if not evidence:
            return ["[추정] %s는 공개 자료 부족으로 추가 검색이 필요함" % technology]
        return [
            "%s 핵심 주장 1: %s %s" % (technology, evidence[0].content[:100], self._citation(evidence[0])),
            "%s 핵심 주장 2: %s %s" % (technology, evidence[min(1, len(evidence) - 1)].content[:100], self._citation(evidence[min(1, len(evidence) - 1)])),
        ]

    def _evidence_validation_node(self, brief: TechnologyBrief) -> List[ValidationIssue]:
        issues = []
        unique_sources = {item.source_path for item in brief.supporting_evidence}
        unique_source_types = {item.source_type for item in brief.supporting_evidence}
        if len(brief.supporting_evidence) < 2:
            issues.append(
                ValidationIssue(
                    scope="Evidence Validation Node",
                    message="%s 근거 문서 수가 부족합니다." % brief.technology,
                    severity="high",
                    blocking=True,
                )
            )
        if len(unique_sources) < len(brief.supporting_evidence):
            issues.append(
                ValidationIssue(
                    scope="Evidence Validation Node",
                    message="%s 조사 결과에 중복 문서가 포함되어 있습니다." % brief.technology,
                    severity="medium",
                    blocking=False,
                )
            )
        if len(unique_source_types) < 2:
            issues.append(
                ValidationIssue(
                    scope="Evidence Validation Node",
                    message="%s 조사 결과의 출처 유형이 단조롭습니다." % brief.technology,
                    severity="medium",
                    blocking=False,
                )
            )
        if not any(item.source_type in {"paper", "standard"} for item in brief.supporting_evidence):
            issues.append(
                ValidationIssue(
                    scope="Evidence Validation Node",
                    message="%s 조사 결과에 논문 또는 표준 계열 근거가 없습니다." % brief.technology,
                    severity="high",
                    blocking=True,
                )
            )
        stale_count = 0
        dated_items = [item for item in brief.supporting_evidence if item.published_at]
        for item in dated_items:
            if date.today().year - item.published_at.year > 5:
                stale_count += 1
        if dated_items and stale_count == len(dated_items):
            issues.append(
                ValidationIssue(
                    scope="Evidence Validation Node",
                    message="%s 조사 결과가 전반적으로 오래되어 최신성 해석에 주의가 필요합니다." % brief.technology,
                    severity="medium",
                    blocking=False,
                )
            )
        return issues
