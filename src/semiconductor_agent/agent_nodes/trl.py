from __future__ import annotations

import concurrent.futures
import json
import os
from typing import Dict, List, Optional, Sequence

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent
from semiconductor_agent.models import (
    EvidenceItem,
    MarketResearchResult,
    PatentSignalEntry,
    TechnologyBrief,
    TRLAssessmentEntry,
    TRLAssessmentResult,
    TRLLLMAssessment,
)
from semiconductor_agent.state import AgentState

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - optional runtime dependency
    ChatOpenAI = None


class TRLAssessmentAgent(BaseWorkflowAgent):
    agent_key = "trl_assessment"

    def __init__(self, dependencies):
        super().__init__(dependencies)
        self._structured_llm = None
        self.last_run_debug = {}
        # OpenAI structured output이 가능하면 LLM 판정을 쓰고, 아니면 보수적 fallback 규칙으로 간다.
        if ChatOpenAI is not None and os.getenv("OPENAI_API_KEY"):
            llm = ChatOpenAI(model=dependencies.runtime.openai_model, temperature=0)
            self._structured_llm = llm.with_structured_output(TRLLLMAssessment)

    def run(self, state: AgentState) -> Dict[str, object]:
        companies = state.get("selected_companies", []) or state.get("candidate_companies", [])
        market = state.get("market_research")
        techniques = state.get("technique_research")
        patent_signals = state.get("patent_innovation_signal")
        standards = state.get("shared_standards", {})
        rulebase = standards.get("trl_evidence_rules", {})
        jobs = []
        self.last_run_debug = {
            "llm_enabled": self.llm_enabled,
            "llm_success_count": 0,
            "llm_errors": [],
        }

        for technology in state.get("target_technologies", []):
            tech_brief = techniques.technology_briefs.get(technology) if techniques else None
            for company in companies:
                jobs.append(
                    {
                        "company": company,
                        "technology": technology,
                        "market": market,
                        "tech_brief": tech_brief,
                        "patent_signals": patent_signals.entries if patent_signals else [],
                        "shared_rulebase": rulebase,
                    }
                )
        entries = self._run_assessments(jobs)

        return {
            "trl_assessment": TRLAssessmentResult(
                entries=entries,
                shared_standards_used=rulebase,
            ),
            "last_completed_step": self.agent_key,
        }

    def _run_assessments(self, jobs: Sequence[Dict[str, object]]) -> List[TRLAssessmentEntry]:
        if not jobs:
            return []
        if not self.llm_enabled or len(jobs) == 1:
            return [self._assess_entry(**job) for job in jobs]

        max_workers = self._resolve_max_workers(len(jobs))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            return list(executor.map(self._assess_job, jobs))

    def _assess_job(self, job: Dict[str, object]) -> TRLAssessmentEntry:
        return self._assess_entry(**job)

    def _resolve_max_workers(self, job_count: int) -> int:
        raw = os.getenv("TRL_LLM_MAX_WORKERS", "4").strip()
        try:
            configured = int(raw)
        except ValueError:
            configured = 4
        return max(1, min(job_count, configured))

    def _assess_entry(
        self,
        company: str,
        technology: str,
        market: Optional[MarketResearchResult],
        tech_brief: Optional[TechnologyBrief],
        patent_signals: Sequence[PatentSignalEntry],
        shared_rulebase: Dict[str, object],
    ) -> TRLAssessmentEntry:
        market_evidence = self._collect_market_evidence(market, company, technology)
        matching_signals = [
            entry for entry in patent_signals if entry.company == company and entry.technology == technology
        ]
        trl_context = self._retrieve_external_trl_guidance(
            company=company,
            technology=technology,
            market_evidence=market_evidence,
            tech_brief=tech_brief,
            patent_signals=matching_signals,
        )
        default_supporting_evidence = self._collect_supporting_evidence(
            market_evidence=market_evidence,
            trl_context=trl_context,
            tech_brief=tech_brief,
            matching_signals=matching_signals,
        )
        llm_assessment = self._assess_with_llm(
            company=company,
            technology=technology,
            market_evidence=market_evidence,
            tech_brief=tech_brief,
            patent_signals=matching_signals,
            trl_context=trl_context,
            shared_rulebase=shared_rulebase,
        )

        if llm_assessment is None:
            trl_level, rule_range, confidence, estimated = self._determine_trl(
                tech_brief.supporting_evidence if tech_brief else [],
                matching_signals,
            )
            return TRLAssessmentEntry(
                technology=technology,
                company=company,
                trl_level=trl_level,
                reason=self._compose_reason(
                    company=company,
                    technology=technology,
                    trl_level=trl_level,
                    rule_range=rule_range,
                    evidence=default_supporting_evidence,
                    estimated=estimated,
                ),
                applied_rule_range=rule_range,
                supporting_evidence=list(default_supporting_evidence),
                confidence=confidence,
                estimated=estimated,
            )

        selected_supporting_evidence = self._select_supporting_evidence(
            market_evidence=market_evidence,
            tech_brief=tech_brief,
            patent_signals=matching_signals,
            trl_context=trl_context,
            selected_ids=llm_assessment.key_evidence_ids,
        )
        return TRLAssessmentEntry(
            technology=technology,
            company=company,
            trl_level=llm_assessment.trl_level,
            reason=self._compose_reason(
                company=company,
                technology=technology,
                trl_level=llm_assessment.trl_level,
                rule_range=llm_assessment.applied_rule_range,
                evidence=selected_supporting_evidence,
                estimated=llm_assessment.estimated,
                llm_reason=llm_assessment.reason,
            ),
            applied_rule_range=llm_assessment.applied_rule_range,
            supporting_evidence=list(selected_supporting_evidence),
            confidence=llm_assessment.confidence,
            estimated=llm_assessment.estimated,
        )

    @property
    def llm_enabled(self) -> bool:
        return self._structured_llm is not None

    def _assess_with_llm(
        self,
        company: str,
        technology: str,
        market_evidence: Sequence[EvidenceItem],
        tech_brief: Optional[TechnologyBrief],
        patent_signals: Sequence[PatentSignalEntry],
        trl_context: Sequence[EvidenceItem],
        shared_rulebase: Dict[str, object],
    ) -> Optional[TRLLLMAssessment]:
        if not self.llm_enabled:
            return None

        evidence_map = self._build_evidence_map(
            market_evidence=market_evidence,
            tech_brief=tech_brief,
            patent_signals=patent_signals,
            trl_context=trl_context,
        )
        prompt = self._build_llm_prompt(
            company=company,
            technology=technology,
            market_evidence=market_evidence,
            tech_brief=tech_brief,
            patent_signals=patent_signals,
            trl_context=trl_context,
            shared_rulebase=shared_rulebase,
            evidence_map=evidence_map,
        )

        try:
            raw = self._structured_llm.invoke(prompt)
        except Exception as exc:
            self.last_run_debug.setdefault("llm_errors", []).append(str(exc))
            return None

        assessment = self._normalize_llm_assessment(raw, evidence_map)
        if assessment is None:
            return None
        self.last_run_debug["llm_success_count"] = self.last_run_debug.get("llm_success_count", 0) + 1
        return assessment

    def _normalize_llm_assessment(
        self,
        assessment: TRLLLMAssessment,
        evidence_map: Dict[str, EvidenceItem],
    ) -> Optional[TRLLLMAssessment]:
        if assessment is None:
            return None

        trl_level = max(1, min(9, int(assessment.trl_level)))
        applied_rule_range = assessment.applied_rule_range
        if applied_rule_range not in {"range_1_3", "range_4_6", "range_7_9"}:
            applied_rule_range = self._infer_rule_range(trl_level)

        low, high = self._rule_bounds(applied_rule_range)
        if trl_level < low:
            trl_level = low
        if trl_level > high:
            trl_level = high

        confidence = assessment.confidence.strip().lower() if assessment.confidence else "low"
        if confidence not in {"low", "medium", "high"}:
            confidence = "low"

        return TRLLLMAssessment(
            trl_level=trl_level,
            applied_rule_range=applied_rule_range,
            confidence=confidence,
            estimated=assessment.estimated,
            reason=(assessment.reason or "").strip(),
            key_evidence_ids=[evidence_id for evidence_id in assessment.key_evidence_ids if evidence_id in evidence_map],
        )

    def _build_llm_prompt(
        self,
        company: str,
        technology: str,
        market_evidence: Sequence[EvidenceItem],
        tech_brief: Optional[TechnologyBrief],
        patent_signals: Sequence[PatentSignalEntry],
        trl_context: Sequence[EvidenceItem],
        shared_rulebase: Dict[str, object],
        evidence_map: Dict[str, EvidenceItem],
    ) -> str:
        # state에 누적된 조사 결과를 그대로 보여주고, 내부 rule base를 우선 규칙으로 적용하게 한다.
        payload = {
            "company": company,
            "technology": technology,
            "market_research": self._serialize_market_evidence(market_evidence),
            "technique_research": self._serialize_tech_brief(tech_brief),
            "patent_innovation_signal": self._serialize_patent_signals(patent_signals),
            "trl_rulebase": shared_rulebase,
            "external_trl_rag": self._serialize_evidence_refs(trl_context, evidence_map, prefix="R"),
            "evidence_catalog": self._serialize_full_evidence_catalog(evidence_map),
        }
        return (
            "You are the TRL assessment specialist for a semiconductor workflow.\n"
            "Determine TRL primarily from the current workflow state.\n"
            "The main basis must be:\n"
            "1. market_research in state,\n"
            "2. technique_research in state,\n"
            "3. patent_innovation_signal in state,\n"
            "4. the internal trl_rulebase.\n"
            "Use external_trl_rag only as a supplement when it helps interpret whether the current evidence "
            "looks like early research, relevant-environment validation, system prototype, operational use, "
            "product transition, manufacturing readiness, or commercialization.\n"
            "Do not invent additional rules beyond the provided trl_rulebase.\n"
            "If the state evidence is weak or indirect, keep the judgment conservative.\n"
            "Return concise Korean reasoning and list the evidence ids you actually relied on.\n\n"
            "Assessment payload JSON:\n%s"
        ) % json.dumps(payload, ensure_ascii=False, indent=2)

    def _collect_market_evidence(
        self,
        market: Optional[MarketResearchResult],
        company: str,
        technology: str,
    ) -> Sequence[EvidenceItem]:
        if market is None:
            return []

        matched = []
        for item in market.company_findings.get(company, []):
            if item.technology in (None, technology):
                matched.append(self._normalize_evidence_item(item))
        return matched[:3]

    def _retrieve_external_trl_guidance(
        self,
        company: str,
        technology: str,
        market_evidence: Sequence[EvidenceItem],
        tech_brief: Optional[TechnologyBrief],
        patent_signals: Sequence[PatentSignalEntry],
    ) -> Sequence[EvidenceItem]:
        queries = self._build_trl_queries(
            company=company,
            technology=technology,
            market_evidence=market_evidence,
            tech_brief=tech_brief,
            patent_signals=patent_signals,
        )
        gathered = []
        for query in queries:
            gathered.extend(self.dependencies.corpora.search("trl", query, top_k=2))
        return self._dedupe_evidence(self._normalize_evidence_items(gathered))[:6]

    def _build_trl_queries(
        self,
        company: str,
        technology: str,
        market_evidence: Sequence[EvidenceItem],
        tech_brief: Optional[TechnologyBrief],
        patent_signals: Sequence[PatentSignalEntry],
    ) -> List[str]:
        # 외부 TRL 문서는 state 근거를 해석하는 데 필요한 기준만 찾도록 단순 쿼리로 가져온다.
        signal_summary = patent_signals[0].signal_summary if patent_signals else ""
        technique_summary = tech_brief.summary if tech_brief else ""
        market_summary = " ".join(item.content[:120] for item in market_evidence[:2])

        queries = [
            "technology readiness level verification validation relevant environment operational environment",
            "product maturity product transition end-to-end validation system prototype operational use",
            "%s %s manufacturing readiness commercialization product deployment %s" % (
                company,
                technology,
                signal_summary[:120],
            ),
        ]

        if technique_summary or market_summary:
            queries.append(
                "%s %s %s %s" % (
                    technology,
                    technique_summary[:100],
                    market_summary[:100],
                    "technology gap validation production",
                )
            )
        return queries[:4]

    def _collect_supporting_evidence(
        self,
        market_evidence: Sequence[EvidenceItem],
        trl_context: Sequence[EvidenceItem],
        tech_brief: Optional[TechnologyBrief],
        matching_signals: Sequence[PatentSignalEntry],
    ) -> Sequence[EvidenceItem]:
        # 결과와 보고서에 재사용할 근거를 시장/기술/간접지표/RAG에서 균형 있게 묶는다.
        supporting_evidence = list(market_evidence[:1])
        if tech_brief:
            supporting_evidence.extend(self._normalize_evidence_items(tech_brief.supporting_evidence[:1]))
        if matching_signals:
            supporting_evidence.extend(self._normalize_evidence_items(matching_signals[0].indirect_evidence[:1]))
        supporting_evidence.extend(trl_context[:2])
        return self._dedupe_evidence(supporting_evidence)[:4]

    def _select_supporting_evidence(
        self,
        market_evidence: Sequence[EvidenceItem],
        tech_brief: Optional[TechnologyBrief],
        patent_signals: Sequence[PatentSignalEntry],
        trl_context: Sequence[EvidenceItem],
        selected_ids: Sequence[str],
    ) -> Sequence[EvidenceItem]:
        # LLM이 고른 근거 id를 우선 반영하되, 비면 기본 evidence 세트를 그대로 사용한다.
        evidence_map = self._build_evidence_map(
            market_evidence=market_evidence,
            tech_brief=tech_brief,
            patent_signals=patent_signals,
            trl_context=trl_context,
        )
        selected = [evidence_map[evidence_id] for evidence_id in selected_ids if evidence_id in evidence_map]
        if not selected:
            return self._collect_supporting_evidence(
                market_evidence=market_evidence,
                trl_context=trl_context,
                tech_brief=tech_brief,
                matching_signals=patent_signals,
            )

        baseline = self._collect_supporting_evidence(
            market_evidence=market_evidence,
            trl_context=trl_context,
            tech_brief=tech_brief,
            matching_signals=patent_signals,
        )
        return self._dedupe_evidence([*selected, *baseline])[:4]

    def _build_evidence_map(
        self,
        market_evidence: Sequence[EvidenceItem],
        tech_brief: Optional[TechnologyBrief],
        patent_signals: Sequence[PatentSignalEntry],
        trl_context: Sequence[EvidenceItem],
    ) -> Dict[str, EvidenceItem]:
        # 프롬프트와 응답에서 같은 근거를 안정적으로 가리키도록 id를 붙인다.
        evidence_map = {}
        for index, item in enumerate(market_evidence[:3], start=1):
            evidence_map["M%s" % index] = self._normalize_evidence_item(item)
        for index, item in enumerate(tech_brief.supporting_evidence[:3] if tech_brief else [], start=1):
            evidence_map["T%s" % index] = self._normalize_evidence_item(item)
        if patent_signals:
            for index, item in enumerate(patent_signals[0].indirect_evidence[:3], start=1):
                evidence_map["P%s" % index] = self._normalize_evidence_item(item)
        for index, item in enumerate(trl_context[:4], start=1):
            evidence_map["R%s" % index] = self._normalize_evidence_item(item)
        return evidence_map

    def _serialize_market_evidence(self, market_evidence: Sequence[EvidenceItem]) -> Sequence[Dict[str, object]]:
        return self._serialize_evidence_refs(
            market_evidence[:3],
            self._build_simple_evidence_map("M", market_evidence[:3]),
            prefix="M",
        )

    def _serialize_tech_brief(self, tech_brief: Optional[TechnologyBrief]) -> Dict[str, object]:
        if tech_brief is None:
            return {}

        items = tech_brief.supporting_evidence[:3]
        return {
            "summary": tech_brief.summary,
            "key_points": tech_brief.key_points[:3],
            "core_claims": tech_brief.core_claims[:3],
            "freshness_note": tech_brief.freshness_note,
            "evidence": self._serialize_evidence_refs(
                items,
                self._build_simple_evidence_map("T", items),
                prefix="T",
            ),
        }

    def _serialize_patent_signals(self, patent_signals: Sequence[PatentSignalEntry]) -> Sequence[Dict[str, object]]:
        payload = []
        for entry in patent_signals[:1]:
            items = entry.indirect_evidence[:3]
            payload.append(
                {
                    "signal_summary": entry.signal_summary,
                    "confidence": entry.confidence,
                    "estimated": entry.estimated,
                    "evidence": self._serialize_evidence_refs(
                        items,
                        self._build_simple_evidence_map("P", items),
                        prefix="P",
                    ),
                }
            )
        return payload

    def _serialize_evidence_refs(
        self,
        items: Sequence[EvidenceItem],
        evidence_map: Dict[str, EvidenceItem],
        prefix: str,
    ) -> Sequence[Dict[str, object]]:
        refs = []
        for index, item in enumerate(items, start=1):
            evidence_id = "%s%s" % (prefix, index)
            mapped = evidence_map.get(evidence_id, item)
            refs.append(
                {
                    "id": evidence_id,
                    "title": mapped.title,
                    "source_type": mapped.source_type,
                }
            )
        return refs

    def _serialize_full_evidence_catalog(self, evidence_map: Dict[str, EvidenceItem]) -> Sequence[Dict[str, object]]:
        return [self._serialize_evidence_item(evidence_id, item) for evidence_id, item in evidence_map.items()]

    def _serialize_evidence_item(self, evidence_id: str, item: EvidenceItem) -> Dict[str, object]:
        return {
            "id": evidence_id,
            "title": item.title,
            "source_type": item.source_type,
            "content": item.content,
            "confidence": item.confidence,
            "estimated": item.estimated,
        }

    def _build_simple_evidence_map(self, prefix: str, items: Sequence[EvidenceItem]) -> Dict[str, EvidenceItem]:
        return {
            "%s%s" % (prefix, index): self._normalize_evidence_item(item)
            for index, item in enumerate(items, start=1)
        }

    def _normalize_evidence_items(self, items: Sequence[object]) -> List[EvidenceItem]:
        return [self._normalize_evidence_item(item) for item in items]

    def _normalize_evidence_item(self, item: object) -> EvidenceItem:
        if isinstance(item, EvidenceItem):
            return item
        if hasattr(item, "model_dump"):
            return EvidenceItem(**item.model_dump())
        if isinstance(item, dict):
            return EvidenceItem(**item)
        raise TypeError("Unsupported evidence item type: %s" % type(item).__name__)

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

    def _determine_trl(
        self,
        technique_evidence: Sequence[EvidenceItem],
        patent_signals: Sequence[PatentSignalEntry],
    ) -> tuple:
        # LLM을 쓸 수 없을 때를 위한 최소한의 보수적 fallback 규칙이다.
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
        llm_reason: str = "",
    ) -> str:
        qualifier = "[추정] " if estimated else ""
        citation = self._citation(evidence[0]) if evidence else ""
        if llm_reason:
            return "%s%s의 %s는 %s 규칙을 기준으로 TRL %d로 판정했다. %s %s" % (
                qualifier,
                company,
                technology,
                rule_range,
                trl_level,
                llm_reason,
                citation,
            )
        return "%s%s의 %s는 %s 규칙을 적용해 TRL %d로 판정했다. %s" % (
            qualifier,
            company,
            technology,
            rule_range,
            trl_level,
            citation,
        )

    def _build_overview(self, entries: Sequence[TRLAssessmentEntry]) -> str:
        if not entries:
            return "TRL 판정 결과 없음"
        rendered = []
        for entry in entries:
            rendered.append("%s/%s=TRL %d" % (entry.company, entry.technology, entry.trl_level))
        return "; ".join(rendered)

    @staticmethod
    def _infer_rule_range(trl_level: int) -> str:
        if trl_level >= 7:
            return "range_7_9"
        if trl_level >= 4:
            return "range_4_6"
        return "range_1_3"

    @staticmethod
    def _rule_bounds(rule_range: str) -> tuple:
        if rule_range == "range_7_9":
            return 7, 9
        if rule_range == "range_4_6":
            return 4, 6
        return 1, 3
