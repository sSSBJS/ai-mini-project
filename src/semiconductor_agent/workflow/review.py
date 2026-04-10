from __future__ import annotations

import json
import os
from typing import Dict, Iterable, List, Optional, Sequence

from semiconductor_agent.models import SupervisorStageReview
from semiconductor_agent.runtime import RuntimeConfig
from semiconductor_agent.shared_standards import REPORT_SECTION_SEQUENCE
from semiconductor_agent.state import AgentState

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - optional runtime dependency
    ChatOpenAI = None


SUCCESS_CRITERIA = {
    "initial_research": [
        "시장 조사 결과가 HBM4, PIM, CXL 범위와 주요 경쟁사 비교 축을 명확히 유지한다.",
        "기술 조사 결과가 target_technologies 전체를 빠짐없이 다룬다.",
        "기술별 요약, 핵심 포인트, 핵심 주장, freshness note가 포함된다.",
        "Evidence Validation Node 관점에서 기술별 근거 수와 출처 다양성이 너무 약하지 않다.",
        "BalancedSearchPlan과 검색 예산 제약이 프로젝트 요구사항과 충돌하지 않는다.",
    ],
    "patent_innovation_signal": [
        "선정된 기업-기술 조합마다 간접 신호 엔트리가 존재한다.",
        "직접 근거 부족 시 [추정] 성격과 confidence가 명확히 드러난다.",
        "특허, 투자, 파트너십, 상용화 신호가 후속 TRL 판정에 쓸 수 있는 수준으로 정리된다.",
    ],
    "trl_assessment": [
        "기업-기술 조합마다 TRL 엔트리가 존재한다.",
        "reason, applied_rule_range, confidence, supporting evidence가 포함된다.",
        "SHARED_STANDARDS의 TRL evidence rule과 모순되지 않는다.",
        "간접 신호와 TRL 판정 사이의 명백한 충돌이 있으면 보수적으로 다뤄진다.",
    ],
    "threat_evaluation": [
        "TRL 결과를 기반으로 기업-기술 조합별 위협 수준이 빠짐없이 계산된다.",
        "threat_level, rationale, supporting_evidence가 후속 전략 수립에 충분한 형태다.",
        "위협 수준이 TRL 및 간접 신호와 완전히 동떨어지지 않는다.",
    ],
    "strategy_plan": [
        "기술별 전략 recommendation이 모두 존재한다.",
        "linked_threat_level과 priority가 위협 평가 결과와 정합적이다.",
        "Strategy Validate Node 기준을 어기지 않는다.",
        "내부 baseline이 있으면 rationale에 반영되고, 없으면 그 사실이 드러난다.",
    ],
    "report_artifact": [
        "보고서가 필수 섹션 순서를 대부분 충족한다: %s." % ", ".join(REPORT_SECTION_SEQUENCE),
        "전략, 위협 수준, TRL 판단, 참고 근거가 최종 보고서에 반영된다.",
        "Report Validate Node의 지표와 validation issues를 무시하지 않는다.",
        "출처와 추정 표시가 프로젝트의 PDF 요구사항과 크게 어긋나지 않는다.",
    ],
}


class SupervisorLLMReviewer:
    def __init__(self, runtime: RuntimeConfig):
        self.runtime = runtime
        self._structured_llm = None
        if runtime.use_llm_supervisor_review and ChatOpenAI is not None and os.getenv("OPENAI_API_KEY"):
            llm = ChatOpenAI(model=runtime.openai_model, temperature=0)
            self._structured_llm = llm.with_structured_output(SupervisorStageReview)

    @property
    def enabled(self) -> bool:
        return self._structured_llm is not None

    def review(
        self,
        stage_name: str,
        state: AgentState,
        allowed_retry_targets: Sequence[str],
    ) -> Optional[SupervisorStageReview]:
        if not self.enabled:
            return None

        prompt = (
            "You are the supervisor review layer for a PDF-faithful semiconductor analysis workflow.\n"
            "Review the current stage output against the project Success Criteria derived from the design PDF.\n"
            "Approve only when the output is materially sufficient for downstream stages.\n"
            "If rework is needed, choose one retry_target from the allowed list.\n"
            "Do not invent evidence. Be conservative when evidence is weak.\n"
            "Return concise Korean summaries and issues.\n\n"
            "Project constraints:\n"
            "- Workflow keeps a central supervisor and 7 specialist agents.\n"
            "- Target technologies are HBM4, PIM, CXL.\n"
            "- Search budget must stay within the configured limit.\n"
            "- TRL judgments must respect SHARED_STANDARDS.\n"
            "- Final report should preserve required report sections and citations.\n\n"
            "Stage: %s\n"
            "Allowed retry targets: %s\n"
            "Success Criteria:\n- %s\n\n"
            "Stage snapshot JSON:\n%s"
        ) % (
            stage_name,
            ", ".join(allowed_retry_targets),
            "\n- ".join(SUCCESS_CRITERIA.get(stage_name, [])),
            json.dumps(build_stage_snapshot(stage_name, state), ensure_ascii=False, indent=2),
        )
        review = self._structured_llm.invoke(prompt)
        if review.retry_target not in allowed_retry_targets:
            review.retry_target = "none"
        if review.approved:
            review.retry_target = "none"
        return review


def build_stage_snapshot(stage_name: str, state: AgentState) -> Dict[str, object]:
    if stage_name == "initial_research":
        return {
            "user_query": state.get("user_query"),
            "target_technologies": state.get("target_technologies", []),
            "candidate_companies": state.get("candidate_companies", []),
            "selected_companies": state.get("selected_companies", []),
            "search_budget_limit": state.get("search_budget_limit"),
            "search_count": state.get("search_count"),
            "market_research": _market_snapshot(state),
            "technique_research": _technique_snapshot(state),
        }
    if stage_name == "patent_innovation_signal":
        patent = state.get("patent_innovation_signal")
        return {
            "selected_companies": state.get("selected_companies", []),
            "target_technologies": state.get("target_technologies", []),
            "entries": [
                {
                    "company": entry.company,
                    "technology": entry.technology,
                    "confidence": entry.confidence,
                    "estimated": entry.estimated,
                    "signal_summary": entry.signal_summary,
                    "evidence_count": len(entry.indirect_evidence),
                    "evidence_sources": _collect_source_types(entry.indirect_evidence),
                }
                for entry in (patent.entries if patent else [])
            ],
        }
    if stage_name == "trl_assessment":
        trl = state.get("trl_assessment")
        return {
            "entries": [
                {
                    "company": entry.company,
                    "technology": entry.technology,
                    "trl_level": entry.trl_level,
                    "reason": entry.reason,
                    "confidence": entry.confidence,
                    "supporting_evidence_count": len(entry.supporting_evidence),
                }
                for entry in (trl.entries if trl else [])
            ],
        }
    if stage_name == "threat_evaluation":
        threat = state.get("threat_evaluation")
        return {
            "entries": [
                {
                    "company": entry.company,
                    "technology": entry.technology,
                    "threat_level": entry.threat_level,
                    "rationale": entry.rationale,
                    "supporting_evidence_count": len(entry.supporting_evidence),
                }
                for entry in (threat.entries if threat else [])
            ],
            "trl_entries": build_stage_snapshot("trl_assessment", state).get("entries", []),
        }
    if stage_name == "strategy_plan":
        strategy = state.get("strategy_plan")
        threat = state.get("threat_evaluation")
        return {
            "recommendations": [
                {
                    "technology": item.technology,
                    "priority": item.priority,
                    "linked_threat_level": item.linked_threat_level,
                    "recommendation": item.recommendation,
                    "rationale": item.rationale,
                }
                for item in (strategy.recommendations if strategy else [])
            ],
            "validation_issues": [
                issue.model_dump() for issue in (strategy.validation_issues if strategy else [])
            ],
            "threat_entries": build_stage_snapshot("threat_evaluation", state).get("entries", []),
        }
    if stage_name == "report_artifact":
        artifact = state.get("report_artifact")
        strategy = state.get("strategy_plan")
        threat = state.get("threat_evaluation")
        return {
            "metrics": artifact.metrics.model_dump() if artifact else None,
            "validation_issues": [issue.model_dump() for issue in (artifact.validation_issues if artifact else [])],
            "report_excerpt": (artifact.markdown[:2500] if artifact else ""),
            "strategy_technologies": [item.technology for item in (strategy.recommendations if strategy else [])],
            "threat_levels": [entry.threat_level for entry in (threat.entries if threat else [])],
        }
    return {"state_keys": sorted(state.keys())}


def _market_snapshot(state: AgentState) -> Dict[str, object]:
    market = state.get("market_research")
    if not market:
        return {}
    company_findings = {}
    for company, items in market.company_findings.items():
        company_findings[company] = {
            "evidence_count": len(items),
            "source_types": _collect_source_types(items),
            "sample_titles": [item.title for item in items[:2]],
        }
    return {
        "selected_companies": market.selected_companies,
        "market_summary": market.market_summary,
        "search_plan": market.search_plan.model_dump(),
        "latest_articles_count": len(market.latest_articles),
        "company_findings": company_findings,
    }


def _technique_snapshot(state: AgentState) -> Dict[str, object]:
    techniques = state.get("technique_research")
    if not techniques:
        return {}
    briefs = {}
    for technology, brief in techniques.technology_briefs.items():
        briefs[technology] = {
            "summary": brief.summary,
            "key_points_count": len(brief.key_points),
            "core_claims_count": len(brief.core_claims),
            "freshness_note": brief.freshness_note,
            "supporting_evidence_count": len(brief.supporting_evidence),
            "source_types": _collect_source_types(brief.supporting_evidence),
            "validation_issues": [issue.model_dump() for issue in brief.validation_issues],
        }
    return {
        "technology_briefs": briefs,
        "evidence_validation_issues": [
            issue.model_dump() for issue in techniques.evidence_validation_issues
        ],
        "search_plan": techniques.search_plan.model_dump(),
    }


def _collect_source_types(items: Iterable[object]) -> List[str]:
    source_types = []
    for item in items:
        source_type = getattr(item, "source_type", None)
        if source_type and source_type not in source_types:
            source_types.append(source_type)
    return source_types
