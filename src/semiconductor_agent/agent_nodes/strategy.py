from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Sequence

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent, threat_rank
from semiconductor_agent.models import (
    StrategyLLMRecommendation,
    StrategyPlanResult,
    StrategyRecommendation,
    ValidationIssue,
)
from semiconductor_agent.state import AgentState

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - optional runtime dependency
    ChatOpenAI = None


class StrategyPlannerAgent(BaseWorkflowAgent):
    agent_key = "strategy_planner"

    def __init__(self, dependencies):
        super().__init__(dependencies)
        self._structured_llm = None
        if ChatOpenAI is not None and os.getenv("OPENAI_API_KEY"):
            llm = ChatOpenAI(model=dependencies.runtime.openai_model, temperature=0)
            self._structured_llm = llm.with_structured_output(StrategyLLMRecommendation)

    def run(self, state: AgentState) -> Dict[str, object]:
        threats = state.get("threat_evaluation")
        internal_baseline = state.get("internal_baseline", {})
        recommendations = []
        highest_by_technology = self._highest_threat_by_technology(threats.entries if threats else [])

        for technology in state.get("target_technologies", []):
            threat_entry = highest_by_technology.get(technology)
            threat_level = threat_entry.threat_level if threat_entry else "Low"
            baseline = internal_baseline.get(technology)
            recommendations.append(
                self._build_recommendation(
                    technology=technology,
                    threat_level=threat_level,
                    baseline=baseline,
                    threat_entry=threat_entry,
                )
            )

        issues = self._strategy_validate_node(recommendations)
        return {
            "strategy_plan": StrategyPlanResult(
                recommendations=recommendations,
                validation_issues=issues,
            ),
            "validation_issues": self.append_issues(state, issues),
            "last_completed_step": self.agent_key,
        }

    def _build_recommendation(
        self,
        technology: str,
        threat_level: str,
        baseline: Optional[int],
        threat_entry,
    ) -> StrategyRecommendation:
        llm_result = self._plan_with_llm(
            technology=technology,
            threat_level=threat_level,
            baseline=baseline,
            threat_entry=threat_entry,
        )
        if llm_result is None:
            return StrategyRecommendation(
                technology=technology,
                priority=self._priority_from_threat(threat_level),
                recommendation=self._strategy_text(technology, threat_level, baseline),
                linked_threat_level=threat_level,
                rationale=self._strategy_rationale(technology, threat_level, baseline),
            )

        priority = self._normalize_priority(llm_result.priority, threat_level)
        return StrategyRecommendation(
            technology=technology,
            priority=priority,
            recommendation=(llm_result.recommendation or self._strategy_text(technology, threat_level, baseline)).strip(),
            linked_threat_level=threat_level,
            rationale=(llm_result.rationale or self._strategy_rationale(technology, threat_level, baseline)).strip(),
        )

    def _plan_with_llm(
        self,
        technology: str,
        threat_level: str,
        baseline: Optional[int],
        threat_entry,
    ) -> Optional[StrategyLLMRecommendation]:
        if self._structured_llm is None:
            return None
        payload = {
            "technology": technology,
            "linked_threat_level": threat_level,
            "internal_baseline_trl": baseline,
            "threat_rationale": threat_entry.rationale if threat_entry else "",
            "supporting_evidence": [
                {
                    "title": item.title,
                    "source_type": item.source_type,
                    "content": item.content[:220],
                    "citation": self._citation(item),
                }
                for item in (threat_entry.supporting_evidence[:3] if threat_entry else [])
            ],
        }
        prompt = (
            "You are the strategy planner for a semiconductor workflow.\n"
            "Write one actionable Korean recommendation for the technology.\n"
            "Use the linked threat level as the main constraint.\n"
            "If threat is High, priority must stay High.\n"
            "Keep recommendation specific and execution-oriented.\n\n"
            "Strategy payload JSON:\n%s"
        ) % json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            return self._structured_llm.invoke(prompt)
        except Exception:
            return None

    @staticmethod
    def _highest_threat_by_technology(entries: Sequence) -> Dict[str, object]:
        highest_by_technology = {}
        for entry in entries:
            current = highest_by_technology.get(entry.technology)
            if current is None or threat_rank(entry.threat_level) > threat_rank(current.threat_level):
                highest_by_technology[entry.technology] = entry
        return highest_by_technology

    @staticmethod
    def _priority_from_threat(threat_level: str) -> str:
        if threat_level == "High":
            return "High"
        if threat_level == "Medium":
            return "Medium"
        return "Low"

    def _normalize_priority(self, value: str, threat_level: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized == "high":
            return "High"
        if normalized == "medium":
            return "Medium"
        if threat_level == "High":
            return "High"
        return "Low"

    def _strategy_text(self, technology: str, threat_level: str, baseline: Optional[int]) -> str:
        if threat_level == "High":
            return "%s의 핵심 검증 항목을 우선 투자 대상으로 두고, 경쟁사 추격 리스크를 줄이기 위한 개발 우선순위를 상향한다." % technology
        if threat_level == "Medium":
            return "%s는 파일럿 수준 검증과 파트너십 신호를 추적하며 선택적 투자를 유지한다." % technology
        return "%s는 관찰을 유지하되, 공개 자료 기반의 기술 이해와 RAG 업데이트를 지속한다." % technology

    def _strategy_rationale(self, technology: str, threat_level: str, baseline: Optional[int]) -> str:
        if baseline is None:
            return "내부 기준선이 제공되지 않아 공개 정보 기반 위협 수준을 우선 반영했다."
        return "내부 기준선 TRL %d와 외부 위협 수준 %s를 함께 반영했다." % (baseline, threat_level)

    def _strategy_validate_node(self, recommendations: Sequence[StrategyRecommendation]) -> List[ValidationIssue]:
        issues = []
        if len(recommendations) < 3:
            issues.append(
                ValidationIssue(
                    scope="Strategy Validate Node",
                    message="전략 개수가 3개 미만입니다.",
                    severity="high",
                    blocking=True,
                )
            )
        for recommendation in recommendations:
            if recommendation.linked_threat_level == "High" and recommendation.priority != "High":
                issues.append(
                    ValidationIssue(
                        scope="Strategy Validate Node",
                        message="%s의 High 위협과 전략 우선순위가 일치하지 않습니다." % recommendation.technology,
                        severity="high",
                        blocking=True,
                    )
                )
        return issues
