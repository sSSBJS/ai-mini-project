from __future__ import annotations

from typing import Dict

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent
from semiconductor_agent.models import PatentInnovationSignalResult, PatentSignalEntry
from semiconductor_agent.search import build_balanced_search_plan
from semiconductor_agent.state import AgentState


class PatentInnovationSignalAgent(BaseWorkflowAgent):
    agent_key = "patent_innovation_signal"

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
                evidence = self.dependencies.corpora.search(
                    "research",
                    "%s %s patent partnership investment commercialization" % (company, technology),
                    top_k=3,
                )
                for item in evidence:
                    item.company = company
                    item.technology = technology
                estimated = not evidence
                if estimated:
                    evidence = self.dependencies.corpora.search(
                        "research",
                        "%s indirect maturity signal" % technology,
                        top_k=2,
                    )
                    for item in evidence:
                        item.company = company
                        item.technology = technology
                        item.estimated = True
                    summary = "[추정] 직접 특허/투자/파트너십 근거가 부족하여 기술 문헌 기반 간접 신호만 반영함."
                    confidence = "low"
                else:
                    summary = "%s의 %s 관련 간접 지표를 수집했다." % (company, technology)
                    confidence = "medium" if len(evidence) < 3 else "high"
                entries.append(
                    PatentSignalEntry(
                        technology=technology,
                        company=company,
                        signal_summary=summary,
                        indirect_evidence=evidence,
                        confidence=confidence,
                        estimated=estimated,
                    )
                )

        return {
            "patent_innovation_signal": PatentInnovationSignalResult(entries=entries, search_plan=search_plan),
            "last_completed_step": self.agent_key,
        }
