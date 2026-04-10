from __future__ import annotations

import copy
from typing import Dict, List, Sequence

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent, can_retry, threat_rank
from semiconductor_agent.models import SupervisorDecision, ValidationIssue
from semiconductor_agent.state import AgentState
from semiconductor_agent.workflow.review import SupervisorLLMReviewer


class SupervisorAgent(BaseWorkflowAgent):
    agent_key = "supervisor"

    def __init__(self, dependencies):
        super().__init__(dependencies)
        self.reviewer = SupervisorLLMReviewer(dependencies.runtime)

    def review_and_route(self, state: AgentState) -> Dict[str, object]:
        approvals = copy.deepcopy(state.get("approvals", {}))
        retry_counts = copy.deepcopy(state.get("retry_counts", {}))
        current_issues = list(state.get("validation_issues", []))
        decisions = list(state.get("supervisor_log", []))

        next_step = None
        reason = ""

        if state.get("market_research") is None or state.get("technique_research") is None:
            next_step = "end"
            reason = "초기 병렬 조사 결과가 아직 모두 합류하지 않아 supervisor 단계에서 중단"
        elif not approvals.get("coverage_review"):
            coverage_issues = self._merge_issues(
                self._check_market_research(state),
                self._check_technique_quality(state),
                self._check_coverage(state),
            )
            review = self._run_llm_review(
                "initial_research",
                state,
                allowed_retry_targets=("market_research", "technique_research", "none"),
            )
            review_issues = self._merge_issues(coverage_issues, *(review.issues,) if review else ())
            current_issues.extend(review_issues)
            retry_target = self._select_retry_target(
                review=review,
                fallback_target="technique_research",
                allowed_targets=("market_research", "technique_research"),
            )
            blocking = [issue for issue in review_issues if issue.blocking]
            if blocking and retry_target and can_retry(retry_target, state, retry_counts):
                retry_counts[retry_target] = retry_counts.get(retry_target, 0) + 1
                next_step = retry_target
                reason = self._compose_review_reason(
                    base_reason="초기 조사 결과가 Success Criteria를 충분히 만족하지 않아 재실행",
                    review=review,
                )
            else:
                approvals["coverage_review"] = True
                approvals["market_research"] = True
                approvals["technique_research"] = True
                next_step = "patent_innovation_signal"
                reason = self._compose_review_reason(
                    base_reason="시장 조사와 기술 조사 범위 검토 완료",
                    review=review,
                )
        elif state.get("patent_innovation_signal") is None:
            next_step = "patent_innovation_signal"
            reason = "간접 지표 수집 단계가 아직 없음"
        elif not approvals.get("patent_review"):
            patent_issues = self._check_patent_quality(state)
            review = self._run_llm_review(
                "patent_innovation_signal",
                state,
                allowed_retry_targets=("patent_innovation_signal", "none"),
            )
            reviewed_issues = self._merge_issues(patent_issues, *(review.issues,) if review else ())
            current_issues.extend(reviewed_issues)
            blocking = [issue for issue in reviewed_issues if issue.blocking]
            retry_target = self._select_retry_target(
                review=review,
                fallback_target="patent_innovation_signal",
                allowed_targets=("patent_innovation_signal",),
            )
            if blocking and retry_target and can_retry(retry_target, state, retry_counts):
                retry_counts[retry_target] = retry_counts.get(retry_target, 0) + 1
                next_step = retry_target
                reason = self._compose_review_reason(
                    base_reason="간접 지표 결과가 Success Criteria를 충분히 만족하지 않아 재실행",
                    review=review,
                )
            else:
                approvals["patent_review"] = True
                approvals["patent_innovation_signal"] = True
                next_step = "trl_assessment"
                reason = self._compose_review_reason(
                    base_reason="간접 지표 검토 완료",
                    review=review,
                )
        elif state.get("trl_assessment") is None:
            next_step = "trl_assessment"
            reason = "TRL 판정이 아직 없음"
        elif not approvals.get("trl_consistency_review"):
            consistency_issues = self._merge_issues(
                self._check_trl_quality(state),
                self._check_trl_consistency(state),
            )
            review = self._run_llm_review(
                "trl_assessment",
                state,
                allowed_retry_targets=("trl_assessment", "none"),
            )
            reviewed_issues = self._merge_issues(consistency_issues, *(review.issues,) if review else ())
            current_issues.extend(reviewed_issues)
            blocking = [issue for issue in reviewed_issues if issue.blocking]
            retry_target = self._select_retry_target(
                review=review,
                fallback_target="trl_assessment",
                allowed_targets=("trl_assessment",),
            )
            if blocking and retry_target and can_retry(retry_target, state, retry_counts):
                retry_counts[retry_target] = retry_counts.get(retry_target, 0) + 1
                next_step = "trl_assessment"
                reason = self._compose_review_reason(
                    base_reason="TRL 결과가 Success Criteria를 충분히 만족하지 않아 재판정",
                    review=review,
                )
            else:
                approvals["trl_consistency_review"] = True
                approvals["trl_assessment"] = True
                next_step = "threat_evaluation"
                reason = self._compose_review_reason(
                    base_reason="TRL과 간접 지표 일관성 검토 완료",
                    review=review,
                )
        elif state.get("threat_evaluation") is None:
            next_step = "threat_evaluation"
            reason = "위협 수준 평가가 아직 없음"
        elif not approvals.get("threat_review"):
            threat_issues = self._check_threat_quality(state)
            review = self._run_llm_review(
                "threat_evaluation",
                state,
                allowed_retry_targets=("threat_evaluation", "none"),
            )
            reviewed_issues = self._merge_issues(threat_issues, *(review.issues,) if review else ())
            current_issues.extend(reviewed_issues)
            blocking = [issue for issue in reviewed_issues if issue.blocking]
            retry_target = self._select_retry_target(
                review=review,
                fallback_target="threat_evaluation",
                allowed_targets=("threat_evaluation",),
            )
            if blocking and retry_target and can_retry(retry_target, state, retry_counts):
                retry_counts[retry_target] = retry_counts.get(retry_target, 0) + 1
                next_step = retry_target
                reason = self._compose_review_reason(
                    base_reason="위협 평가 결과가 Success Criteria를 충분히 만족하지 않아 재실행",
                    review=review,
                )
            else:
                approvals["threat_review"] = True
                approvals["threat_evaluation"] = True
                next_step = "strategy_planner"
                reason = self._compose_review_reason(
                    base_reason="위협 수준 평가 검토 완료",
                    review=review,
                )
        elif state.get("strategy_plan") is None:
            next_step = "strategy_planner"
            reason = "위협 결과를 전략으로 변환해야 함"
        elif not approvals.get("strategy_alignment_review"):
            alignment_issues = self._merge_issues(
                self._check_strategy_quality(state),
                self._check_strategy_alignment(state),
            )
            review = self._run_llm_review(
                "strategy_plan",
                state,
                allowed_retry_targets=("strategy_planner", "none"),
            )
            reviewed_issues = self._merge_issues(alignment_issues, *(review.issues,) if review else ())
            current_issues.extend(reviewed_issues)
            blocking = [issue for issue in reviewed_issues if issue.blocking]
            retry_target = self._select_retry_target(
                review=review,
                fallback_target="strategy_planner",
                allowed_targets=("strategy_planner",),
            )
            if blocking and retry_target and can_retry(retry_target, state, retry_counts):
                retry_counts[retry_target] = retry_counts.get(retry_target, 0) + 1
                next_step = "strategy_planner"
                reason = self._compose_review_reason(
                    base_reason="전략 결과가 Success Criteria를 충분히 만족하지 않아 재작성",
                    review=review,
                )
            else:
                approvals["strategy_alignment_review"] = True
                approvals["strategy_planner"] = True
                next_step = "report_writer"
                reason = self._compose_review_reason(
                    base_reason="전략 연결성 검토 완료",
                    review=review,
                )
        elif state.get("report_artifact") is None:
            next_step = "report_writer"
            reason = "최종 보고서가 아직 생성되지 않음"
        elif not approvals.get("report_alignment_review"):
            report_issues = self._merge_issues(
                self._check_report_alignment(state),
                self._check_report_quality(state),
            )
            review = self._run_llm_review(
                "report_artifact",
                state,
                allowed_retry_targets=("report_writer", "none"),
            )
            reviewed_issues = self._merge_issues(report_issues, *(review.issues,) if review else ())
            current_issues.extend(reviewed_issues)
            blocking = [issue for issue in reviewed_issues if issue.blocking]
            retry_target = self._select_retry_target(
                review=review,
                fallback_target="report_writer",
                allowed_targets=("report_writer",),
            )
            if blocking and retry_target and can_retry(retry_target, state, retry_counts):
                retry_counts[retry_target] = retry_counts.get(retry_target, 0) + 1
                next_step = "report_writer"
                reason = self._compose_review_reason(
                    base_reason="보고서가 Success Criteria를 충분히 만족하지 않아 재출력",
                    review=review,
                )
            else:
                approvals["report_alignment_review"] = True
                approvals["report_writer"] = True
                next_step = "end"
                reason = self._compose_review_reason(
                    base_reason="최종 보고서 채택",
                    review=review,
                )
        else:
            next_step = "end"
            reason = "모든 단계 완료"

        decisions.append(
            SupervisorDecision(
                step=state.get("last_completed_step") or "supervisor",
                decision=next_step,
                reason=reason,
            )
        )
        return {
            "approvals": approvals,
            "retry_counts": retry_counts,
            "validation_issues": current_issues,
            "supervisor_log": decisions,
            "next_step": next_step,
            "last_completed_step": self.agent_key,
        }

    def _run_llm_review(self, stage_name: str, state: AgentState, allowed_retry_targets: Sequence[str]):
        return self.reviewer.review(stage_name, state, allowed_retry_targets)

    def _select_retry_target(self, review, fallback_target: str, allowed_targets: Sequence[str]) -> str:
        if review and review.retry_target in allowed_targets:
            return review.retry_target
        if fallback_target in allowed_targets:
            return fallback_target
        return ""

    def _compose_review_reason(self, base_reason: str, review) -> str:
        if review and review.summary:
            return "%s / LLM review: %s" % (base_reason, review.summary)
        return base_reason

    def _merge_issues(self, *issue_groups: Sequence[ValidationIssue]) -> List[ValidationIssue]:
        merged = []
        seen = set()
        for group in issue_groups:
            for issue in group:
                key = (issue.scope, issue.message, issue.severity, issue.blocking)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(issue)
        return merged

    def _check_market_research(self, state: AgentState) -> List[ValidationIssue]:
        issues = []
        market = state.get("market_research")
        if not market:
            return issues
        if not market.selected_companies:
            issues.append(
                ValidationIssue(
                    scope="Supervisor",
                    message="시장 조사 결과에 선정 기업 목록이 비어 있습니다.",
                    severity="high",
                    blocking=True,
                )
            )
        if not market.market_summary:
            issues.append(
                ValidationIssue(
                    scope="Supervisor",
                    message="시장 조사 결과에 시장 요약이 비어 있습니다.",
                    severity="medium",
                    blocking=False,
                )
            )
        return issues

    def _check_technique_quality(self, state: AgentState) -> List[ValidationIssue]:
        issues = []
        techniques = state.get("technique_research")
        if not techniques:
            return issues
        for technology in state.get("target_technologies", []):
            brief = techniques.technology_briefs.get(technology)
            if not brief:
                issues.append(
                    ValidationIssue(
                        scope="Supervisor",
                        message="%s 기술 브리프가 없습니다." % technology,
                        severity="high",
                        blocking=True,
                    )
                )
                continue
            if not brief.core_claims:
                issues.append(
                    ValidationIssue(
                        scope="Supervisor",
                        message="%s 기술 브리프의 핵심 주장이 비어 있습니다." % technology,
                        severity="medium",
                        blocking=False,
                    )
                )
        return issues

    def _check_patent_quality(self, state: AgentState) -> List[ValidationIssue]:
        issues = []
        patent = state.get("patent_innovation_signal")
        if not patent:
            return issues
        companies = state.get("selected_companies", []) or state.get("candidate_companies", [])
        technologies = state.get("target_technologies", [])
        expected = len(companies) * len(technologies)
        if len(patent.entries) < expected:
            issues.append(
                ValidationIssue(
                    scope="Supervisor",
                    message="간접 지표 엔트리 수가 기업-기술 조합 수보다 적습니다.",
                    severity="high",
                    blocking=True,
                )
            )
        for entry in patent.entries:
            if not entry.signal_summary:
                issues.append(
                    ValidationIssue(
                        scope="Supervisor",
                        message="%s / %s 간접 지표 요약이 비어 있습니다." % (entry.company, entry.technology),
                        severity="medium",
                        blocking=False,
                    )
                )
        return issues

    def _check_trl_quality(self, state: AgentState) -> List[ValidationIssue]:
        issues = []
        trl = state.get("trl_assessment")
        if not trl:
            return issues
        if not trl.entries:
            issues.append(
                ValidationIssue(
                    scope="Supervisor",
                    message="TRL 판정 결과가 비어 있습니다.",
                    severity="high",
                    blocking=True,
                )
            )
        for entry in trl.entries:
            if not entry.reason:
                issues.append(
                    ValidationIssue(
                        scope="Supervisor",
                        message="%s / %s TRL 판정 사유가 비어 있습니다." % (entry.company, entry.technology),
                        severity="medium",
                        blocking=False,
                    )
                )
            if not entry.supporting_evidence:
                issues.append(
                    ValidationIssue(
                        scope="Supervisor",
                        message="%s / %s TRL supporting evidence가 부족합니다." % (entry.company, entry.technology),
                        severity="high",
                        blocking=True,
                    )
                )
        return issues

    def _check_threat_quality(self, state: AgentState) -> List[ValidationIssue]:
        issues = []
        threat = state.get("threat_evaluation")
        trl = state.get("trl_assessment")
        if not threat:
            return issues
        if trl and len(threat.entries) < len(trl.entries):
            issues.append(
                ValidationIssue(
                    scope="Supervisor",
                    message="위협 평가 엔트리 수가 TRL 엔트리 수보다 적습니다.",
                    severity="high",
                    blocking=True,
                )
            )
        for entry in threat.entries:
            if not entry.rationale:
                issues.append(
                    ValidationIssue(
                        scope="Supervisor",
                        message="%s / %s 위협 평가 rationale이 비어 있습니다." % (entry.company, entry.technology),
                        severity="medium",
                        blocking=False,
                    )
                )
        return issues

    def _check_coverage(self, state: AgentState) -> List[ValidationIssue]:
        issues = []
        market = state.get("market_research")
        techniques = state.get("technique_research")
        if not market or not techniques:
            return issues
        required_techs = set(state.get("target_technologies", []))
        actual_techs = set(techniques.technology_briefs.keys())
        missing_techs = required_techs - actual_techs
        if missing_techs:
            issues.append(
                ValidationIssue(
                    scope="Supervisor",
                    message="T2 조사 결과에서 누락된 기술이 있습니다: %s" % ", ".join(sorted(missing_techs)),
                    severity="high",
                    blocking=True,
                )
            )
        if set(market.selected_companies) != set(state.get("selected_companies", [])):
            issues.append(
                ValidationIssue(
                    scope="Supervisor",
                    message="T1에서 선정된 경쟁사 목록이 상태와 일치하지 않습니다.",
                    severity="medium",
                    blocking=False,
                )
            )
        return issues

    def _check_trl_consistency(self, state: AgentState) -> List[ValidationIssue]:
        issues = []
        trl = state.get("trl_assessment")
        patent = state.get("patent_innovation_signal")
        if not trl or not patent:
            return issues
        for entry in trl.entries:
            for signal in patent.entries:
                if signal.company == entry.company and signal.technology == entry.technology:
                    if entry.trl_level >= 7 and signal.confidence == "low":
                        issues.append(
                            ValidationIssue(
                                scope="Supervisor",
                                message="%s / %s는 TRL이 높지만 간접 지표 신뢰도가 낮아 모순 가능성이 있습니다." % (
                                    entry.company,
                                    entry.technology,
                                ),
                                severity="medium",
                                blocking=False,
                            )
                        )
        return issues

    def _check_strategy_alignment(self, state: AgentState) -> List[ValidationIssue]:
        issues = []
        threats = state.get("threat_evaluation")
        strategy = state.get("strategy_plan")
        if not threats or not strategy:
            return issues
        highest_threat = {}
        for threat in threats.entries:
            if threat_rank(threat.threat_level) > threat_rank(highest_threat.get(threat.technology, "Low")):
                highest_threat[threat.technology] = threat.threat_level
        for recommendation in strategy.recommendations:
            expected = highest_threat.get(recommendation.technology, "Low")
            if recommendation.linked_threat_level != expected:
                issues.append(
                    ValidationIssue(
                        scope="Supervisor",
                        message="%s 전략이 위협 평가 결과와 일치하지 않습니다." % recommendation.technology,
                        severity="high",
                        blocking=True,
                    )
                )
        return issues

    def _check_strategy_quality(self, state: AgentState) -> List[ValidationIssue]:
        issues = []
        strategy = state.get("strategy_plan")
        if not strategy:
            return issues
        issues.extend(strategy.validation_issues)
        if len(strategy.recommendations) < len(state.get("target_technologies", [])):
            issues.append(
                ValidationIssue(
                    scope="Supervisor",
                    message="전략 recommendation 수가 대상 기술 수보다 적습니다.",
                    severity="high",
                    blocking=True,
                )
            )
        return issues

    def _check_report_alignment(self, state: AgentState) -> List[ValidationIssue]:
        issues = []
        artifact = state.get("report_artifact")
        strategy = state.get("strategy_plan")
        threat = state.get("threat_evaluation")
        if not artifact:
            return issues
        markdown = artifact.markdown
        if strategy:
            for recommendation in strategy.recommendations:
                if recommendation.technology not in markdown:
                    issues.append(
                        ValidationIssue(
                            scope="Supervisor",
                            message="보고서에 %s 전략이 반영되지 않았습니다." % recommendation.technology,
                            severity="high",
                            blocking=True,
                        )
                    )
        if threat:
            for entry in threat.entries:
                if entry.threat_level not in markdown:
                    issues.append(
                        ValidationIssue(
                            scope="Supervisor",
                            message="보고서에 위협 등급 %s 반영이 누락되었을 수 있습니다." % entry.threat_level,
                            severity="medium",
                            blocking=False,
                        )
                    )
                    break
        issues.extend(artifact.validation_issues)
        return issues

    def _check_report_quality(self, state: AgentState) -> List[ValidationIssue]:
        issues = []
        artifact = state.get("report_artifact")
        if not artifact:
            return issues
        if artifact.metrics.passed_criteria < 3:
            issues.append(
                ValidationIssue(
                    scope="Supervisor",
                    message="보고서 품질 지표 통과 개수가 3 미만입니다.",
                    severity="high",
                    blocking=True,
                )
            )
        return issues
