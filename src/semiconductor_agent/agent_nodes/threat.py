from __future__ import annotations

from typing import Dict, Optional

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent
from semiconductor_agent.models import PatentSignalEntry, ThreatEntry, ThreatEvaluationResult
from semiconductor_agent.state import AgentState


class ThreatEvaluationAgent(BaseWorkflowAgent):
    agent_key = "threat_evaluation"

    def run(self, state: AgentState) -> Dict[str, object]:
        trl_result = state.get("trl_assessment")
        patent_result = state.get("patent_innovation_signal")
        entries = []
        if trl_result:
            for trl_entry in trl_result.entries:
                matching_signal = None
                if patent_result:
                    for signal in patent_result.entries:
                        if signal.company == trl_entry.company and signal.technology == trl_entry.technology:
                            matching_signal = signal
                            break
                threat_level = self._determine_threat(trl_entry.trl_level, matching_signal)
                rationale = "%s의 %s는 TRL %d와 간접 지표를 종합해 %s 위협으로 분류했다." % (
                    trl_entry.company,
                    trl_entry.technology,
                    trl_entry.trl_level,
                    threat_level,
                )
                supporting = list(trl_entry.supporting_evidence)
                if matching_signal:
                    supporting.extend(matching_signal.indirect_evidence[:2])
                entries.append(
                    ThreatEntry(
                        technology=trl_entry.technology,
                        company=trl_entry.company,
                        threat_level=threat_level,
                        rationale=rationale,
                        supporting_evidence=supporting[:4],
                    )
                )
        return {
            "threat_evaluation": ThreatEvaluationResult(entries=entries),
            "last_completed_step": self.agent_key,
        }

    @staticmethod
    def _determine_threat(trl_level: int, signal_entry: Optional[PatentSignalEntry]) -> str:
        confidence = signal_entry.confidence if signal_entry else "low"
        if trl_level >= 7:
            return "High"
        if trl_level >= 5 and confidence in ("medium", "high"):
            return "High"
        if trl_level >= 4:
            return "Medium"
        return "Low"
