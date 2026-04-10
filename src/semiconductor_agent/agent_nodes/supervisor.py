from __future__ import annotations

import copy
from typing import Dict, List

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent, can_retry, threat_rank
from semiconductor_agent.models import SupervisorDecision, ValidationIssue
from semiconductor_agent.state import AgentState


class SupervisorAgent(BaseWorkflowAgent):
    agent_key = "supervisor"

    def review_and_route(self, state: AgentState) -> Dict[str, object]:
        approvals = copy.deepcopy(state.get("approvals", {}))
        retry_counts = copy.deepcopy(state.get("retry_counts", {}))
        current_issues = list(state.get("validation_issues", []))
        decisions = list(state.get("supervisor_log", []))

        next_step = None
        reason = ""

        if state.get("market_research") is None:
            next_step = "market_research"
            reason = "시장 조사와 경쟁사 범위가 아직 수집되지 않음"
        elif state.get("technique_research") is None:
            approvals["market_research"] = True
            next_step = "technique_research"
            reason = "기술 요약과 핵심 주장 수집이 아직 완료되지 않음"
        elif not approvals.get("coverage_review"):
            coverage_issues = self._check_coverage(state)
            current_issues.extend(coverage_issues)
            blocking = [issue for issue in coverage_issues if issue.blocking]
            if blocking and can_retry("technique_research", state, retry_counts):
                retry_counts["technique_research"] = retry_counts.get("technique_research", 0) + 1
                next_step = "technique_research"
                reason = "T1 경쟁사/기술 범위가 T2 조사 결과에 충분히 반영되지 않아 재실행"
            else:
                approvals["coverage_review"] = True
                approvals["market_research"] = True
                approvals["technique_research"] = True
                next_step = "patent_innovation_signal"
                reason = "시장 조사와 기술 조사 범위 검토 완료"
        elif state.get("patent_innovation_signal") is None:
            next_step = "patent_innovation_signal"
            reason = "간접 지표 수집 단계가 아직 없음"
        elif state.get("trl_assessment") is None:
            approvals["patent_innovation_signal"] = True
            next_step = "trl_assessment"
            reason = "TRL 판정이 아직 없음"
        elif not approvals.get("trl_consistency_review"):
            consistency_issues = self._check_trl_consistency(state)
            current_issues.extend(consistency_issues)
            blocking = [issue for issue in consistency_issues if issue.blocking]
            if blocking and can_retry("trl_assessment", state, retry_counts):
                retry_counts["trl_assessment"] = retry_counts.get("trl_assessment", 0) + 1
                next_step = "trl_assessment"
                reason = "TRL과 간접 지표의 모순이 있어 재판정"
            else:
                approvals["trl_consistency_review"] = True
                approvals["trl_assessment"] = True
                next_step = "threat_evaluation"
                reason = "TRL과 간접 지표 일관성 검토 완료"
        elif state.get("threat_evaluation") is None:
            next_step = "threat_evaluation"
            reason = "위협 수준 평가가 아직 없음"
        elif state.get("strategy_plan") is None:
            approvals["threat_evaluation"] = True
            next_step = "strategy_planner"
            reason = "위협 결과를 전략으로 변환해야 함"
        elif not approvals.get("strategy_alignment_review"):
            alignment_issues = self._check_strategy_alignment(state)
            current_issues.extend(alignment_issues)
            blocking = [issue for issue in alignment_issues if issue.blocking]
            if blocking and can_retry("strategy_planner", state, retry_counts):
                retry_counts["strategy_planner"] = retry_counts.get("strategy_planner", 0) + 1
                next_step = "strategy_planner"
                reason = "위협 수준과 전략 연결이 약해 전략을 재작성"
            else:
                approvals["strategy_alignment_review"] = True
                approvals["strategy_planner"] = True
                next_step = "report_writer"
                reason = "전략 연결성 검토 완료"
        elif state.get("report_artifact") is None:
            next_step = "report_writer"
            reason = "최종 보고서가 아직 생성되지 않음"
        elif not approvals.get("report_alignment_review"):
            report_issues = self._check_report_alignment(state)
            current_issues.extend(report_issues)
            blocking = [issue for issue in report_issues if issue.blocking]
            if blocking and can_retry("report_writer", state, retry_counts):
                retry_counts["report_writer"] = retry_counts.get("report_writer", 0) + 1
                next_step = "report_writer"
                reason = "보고서 품질 및 상위 결과 반영 상태를 기준으로 재출력"
            else:
                approvals["report_alignment_review"] = True
                approvals["report_writer"] = True
                next_step = "end"
                reason = "최종 보고서 채택"
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
