from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Sequence

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent
from semiconductor_agent.models import EvidenceItem, TechniqueResearchResult, TechnologyBrief, ValidationIssue
from semiconductor_agent.search import build_balanced_search_plan
from semiconductor_agent.state import AgentState


class TechniqueResearchCollectorAgent(BaseWorkflowAgent):
    agent_key = "technique_research"

    def run(self, state: AgentState) -> Dict[str, object]:
        technologies = state.get("target_technologies", [])
        search_plan = build_balanced_search_plan(
            topic="반도체 최신 기술 조사",
            scope_hint=", ".join(technologies),
        )
        technology_briefs = {}
        validation_issues = []
        for technology in technologies:
            evidence = self.dependencies.corpora.search(
                "research",
                "%s architecture principle performance roadmap" % technology,
                top_k=4,
            )
            for item in evidence:
                item.technology = technology

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
            "validation_issues": self.append_issues(state, validation_issues),
            "last_completed_step": self.agent_key,
        }

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
        return issues
