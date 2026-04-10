from __future__ import annotations

from typing import Dict, Sequence

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent
from semiconductor_agent.models import EvidenceItem, PatentSignalEntry, TRLAssessmentEntry, TRLAssessmentResult
from semiconductor_agent.state import AgentState


class TRLAssessmentAgent(BaseWorkflowAgent):
    agent_key = "trl_assessment"

    def run(self, state: AgentState) -> Dict[str, object]:
        companies = state.get("selected_companies", []) or state.get("candidate_companies", [])
        techniques = state.get("technique_research")
        patent_signals = state.get("patent_innovation_signal")
        standards = state.get("shared_standards", {})
        entries = []

        for technology in state.get("target_technologies", []):
            tech_brief = techniques.technology_briefs.get(technology) if techniques else None
            trl_context = self.dependencies.corpora.search(
                "trl",
                "%s technology readiness level definition NASA IRDS" % technology,
                top_k=2,
            )
            for company in companies:
                matching_signals = []
                if patent_signals:
                    matching_signals = [
                        entry for entry in patent_signals.entries if entry.company == company and entry.technology == technology
                    ]
                trl_level, rule_range, confidence, estimated = self._determine_trl(
                    tech_brief.supporting_evidence if tech_brief else [],
                    matching_signals,
                )
                supporting_evidence = list(trl_context)
                if tech_brief:
                    supporting_evidence.extend(tech_brief.supporting_evidence[:2])
                if matching_signals:
                    supporting_evidence.extend(matching_signals[0].indirect_evidence[:2])
                reason = self._compose_reason(company, technology, trl_level, rule_range, supporting_evidence, estimated)
                entries.append(
                    TRLAssessmentEntry(
                        technology=technology,
                        company=company,
                        trl_level=trl_level,
                        reason=reason,
                        applied_rule_range=rule_range,
                        supporting_evidence=supporting_evidence[:4],
                        confidence=confidence,
                        estimated=estimated,
                    )
                )

        return {
            "trl_assessment": TRLAssessmentResult(entries=entries, shared_standards_used=standards),
            "last_completed_step": self.agent_key,
        }

    def _determine_trl(
        self,
        technique_evidence: Sequence[EvidenceItem],
        patent_signals: Sequence[PatentSignalEntry],
    ) -> tuple:
        source_types = {item.source_type for item in technique_evidence}
        signal_evidence_count = sum(len(entry.indirect_evidence) for entry in patent_signals)
        has_company_signal = "company" in source_types
        has_standard = "standard" in source_types
        has_paper = "paper" in source_types
        has_report = "report" in source_types

        if has_company_signal and signal_evidence_count >= 3:
            return 7, "range_7_9", "high", False
        if signal_evidence_count >= 3 or (has_standard and has_report):
            return 5, "range_4_6", "medium", False
        if has_paper or has_standard:
            return 3, "range_1_3", "low", True
        return 2, "range_1_3", "low", True

    def _compose_reason(
        self,
        company: str,
        technology: str,
        trl_level: int,
        rule_range: str,
        evidence: Sequence[EvidenceItem],
        estimated: bool,
    ) -> str:
        qualifier = "[추정] " if estimated else ""
        citation = self._citation(evidence[0]) if evidence else ""
        return "%s%s의 %s는 %s 규칙을 적용해 TRL %d로 판정했다. %s" % (
            qualifier,
            company,
            technology,
            rule_range,
            trl_level,
            citation,
        )
