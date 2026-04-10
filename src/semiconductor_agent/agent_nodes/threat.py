from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Sequence

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent
from semiconductor_agent.models import (
    EvidenceItem,
    PatentSignalEntry,
    ThreatEntry,
    ThreatEvaluationResult,
    ThreatLLMAssessment,
    TRLAssessmentEntry,
)
from semiconductor_agent.state import AgentState

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - optional runtime dependency
    ChatOpenAI = None


class ThreatEvaluationAgent(BaseWorkflowAgent):
    agent_key = "threat_evaluation"

    def __init__(self, dependencies):
        super().__init__(dependencies)
        self._structured_llm = None
        if ChatOpenAI is not None and os.getenv("OPENAI_API_KEY"):
            llm = ChatOpenAI(model=dependencies.runtime.openai_model, temperature=0)
            self._structured_llm = llm.with_structured_output(ThreatLLMAssessment)

    def run(self, state: AgentState) -> Dict[str, object]:
        trl_result = state.get("trl_assessment")
        patent_result = state.get("patent_innovation_signal")
        entries = []
        for trl_entry in trl_result.entries if trl_result else []:
            matching_signal = self._find_matching_signal(
                patent_result.entries if patent_result else [],
                company=trl_entry.company,
                technology=trl_entry.technology,
            )
            entries.append(self._evaluate_entry(trl_entry, matching_signal))
        return {
            "threat_evaluation": ThreatEvaluationResult(entries=entries),
            "last_completed_step": self.agent_key,
        }

    def _evaluate_entry(
        self,
        trl_entry: TRLAssessmentEntry,
        matching_signal: Optional[PatentSignalEntry],
    ) -> ThreatEntry:
        supporting_evidence = self._build_supporting_evidence(trl_entry, matching_signal)
        llm_assessment = self._assess_with_llm(trl_entry, matching_signal, supporting_evidence)
        if llm_assessment is None:
            threat_level = self._determine_threat(trl_entry.trl_level, matching_signal)
            rationale = "%s의 %s는 TRL %d와 간접 지표를 종합해 %s 위협으로 분류했다." % (
                trl_entry.company,
                trl_entry.technology,
                trl_entry.trl_level,
                threat_level,
            )
            return ThreatEntry(
                technology=trl_entry.technology,
                company=trl_entry.company,
                threat_level=threat_level,
                rationale=rationale,
                supporting_evidence=supporting_evidence,
            )

        return ThreatEntry(
            technology=trl_entry.technology,
            company=trl_entry.company,
            threat_level=self._normalize_threat_level(llm_assessment.threat_level),
            rationale=llm_assessment.rationale.strip() or self._fallback_rationale(trl_entry, matching_signal),
            supporting_evidence=self._select_supporting_evidence(
                supporting_evidence,
                llm_assessment.key_evidence_ids,
            ),
        )

    def _assess_with_llm(
        self,
        trl_entry: TRLAssessmentEntry,
        matching_signal: Optional[PatentSignalEntry],
        supporting_evidence: Sequence[EvidenceItem],
    ) -> Optional[ThreatLLMAssessment]:
        if self._structured_llm is None:
            return None

        evidence_map = {
            "E%s" % index: item for index, item in enumerate(supporting_evidence, start=1)
        }
        payload = {
            "company": trl_entry.company,
            "technology": trl_entry.technology,
            "trl_level": trl_entry.trl_level,
            "trl_reason": trl_entry.reason,
            "trl_confidence": trl_entry.confidence,
            "patent_signal_summary": matching_signal.signal_summary if matching_signal else "",
            "patent_signal_confidence": matching_signal.confidence if matching_signal else "low",
            "evidence_catalog": [
                {
                    "id": evidence_id,
                    "title": item.title,
                    "source_type": item.source_type,
                    "content": item.content[:240],
                    "citation": self._citation(item),
                }
                for evidence_id, item in evidence_map.items()
            ],
        }
        prompt = (
            "You are the threat evaluation specialist for a semiconductor workflow.\n"
            "Assess competitor threat using TRL evidence first, then indirect patent and ecosystem signals.\n"
            "Return one of High, Medium, Low.\n"
            "Be conservative when evidence is weak.\n"
            "Return concise Korean rationale and the evidence ids you actually used.\n\n"
            "Threat payload JSON:\n%s"
        ) % json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            result = self._structured_llm.invoke(prompt)
        except Exception:
            return None
        return ThreatLLMAssessment(
            threat_level=self._normalize_threat_level(result.threat_level),
            rationale=(result.rationale or "").strip(),
            key_evidence_ids=[item for item in result.key_evidence_ids if item in evidence_map],
        )

    def _build_supporting_evidence(
        self,
        trl_entry: TRLAssessmentEntry,
        matching_signal: Optional[PatentSignalEntry],
    ) -> List[EvidenceItem]:
        supporting = list(trl_entry.supporting_evidence[:2])
        if matching_signal:
            supporting.extend(matching_signal.indirect_evidence[:2])
        return self._dedupe_evidence(supporting)[:4]

    def _select_supporting_evidence(
        self,
        baseline: Sequence[EvidenceItem],
        selected_ids: Sequence[str],
    ) -> List[EvidenceItem]:
        evidence_map = {
            "E%s" % index: item for index, item in enumerate(baseline, start=1)
        }
        selected = [evidence_map[evidence_id] for evidence_id in selected_ids if evidence_id in evidence_map]
        if not selected:
            return list(baseline)
        return self._dedupe_evidence([*selected, *baseline])[:4]

    def _dedupe_evidence(self, items: Sequence[EvidenceItem]) -> List[EvidenceItem]:
        deduped = []
        seen = set()
        for item in items:
            key = (item.source_path, item.page, item.content[:120])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _fallback_rationale(
        self,
        trl_entry: TRLAssessmentEntry,
        matching_signal: Optional[PatentSignalEntry],
    ) -> str:
        threat_level = self._determine_threat(trl_entry.trl_level, matching_signal)
        return "%s의 %s는 TRL %d와 간접 지표를 종합해 %s 위협으로 분류했다." % (
            trl_entry.company,
            trl_entry.technology,
            trl_entry.trl_level,
            threat_level,
        )

    @staticmethod
    def _find_matching_signal(
        entries: Sequence[PatentSignalEntry],
        company: str,
        technology: str,
    ) -> Optional[PatentSignalEntry]:
        for entry in entries:
            if entry.company == company and entry.technology == technology:
                return entry
        return None

    @staticmethod
    def _normalize_threat_level(value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized == "high":
            return "High"
        if normalized == "medium":
            return "Medium"
        return "Low"

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
