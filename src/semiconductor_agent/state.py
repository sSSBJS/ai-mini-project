from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, TypedDict

from semiconductor_agent.models import (
    MarketResearchResult,
    PatentInnovationSignalResult,
    ReportArtifact,
    StrategyPlanResult,
    SupervisorDecision,
    TechniqueResearchResult,
    ThreatEvaluationResult,
    TRLAssessmentResult,
    ValidationIssue,
)
from semiconductor_agent.shared_standards import (
    DEFAULT_CANDIDATE_COMPANIES,
    DEFAULT_TECHNOLOGIES,
    SHARED_STANDARDS,
)


class AgentState(TypedDict, total=False):
    user_query: str
    output_dir: str
    target_technologies: List[str]
    candidate_companies: List[str]
    selected_companies: List[str]
    internal_baseline: Dict[str, int]
    shared_standards: Dict[str, object]
    search_budget_limit: int
    search_count: int
    retry_limits: Dict[str, int]
    retry_counts: Dict[str, int]
    approvals: Dict[str, bool]
    next_step: Optional[str]
    last_completed_step: Optional[str]
    supervisor_log: List[SupervisorDecision]
    validation_issues: List[ValidationIssue]
    market_research: Optional[MarketResearchResult]
    technique_research: Optional[TechniqueResearchResult]
    patent_innovation_signal: Optional[PatentInnovationSignalResult]
    trl_assessment: Optional[TRLAssessmentResult]
    threat_evaluation: Optional[ThreatEvaluationResult]
    strategy_plan: Optional[StrategyPlanResult]
    report_artifact: Optional[ReportArtifact]


def create_initial_state(
    user_query: str,
    output_dir: Path,
    internal_baseline: Optional[Dict[str, int]] = None,
) -> AgentState:
    return AgentState(
        user_query=user_query,
        output_dir=str(output_dir),
        target_technologies=list(DEFAULT_TECHNOLOGIES),
        candidate_companies=list(DEFAULT_CANDIDATE_COMPANIES),
        selected_companies=[],
        internal_baseline=internal_baseline or {},
        shared_standards=SHARED_STANDARDS,
        search_budget_limit=5,
        search_count=0,
        retry_limits={
            "technique_research": 2,
            "market_research": 1,
            "strategy_planner": 1,
            "report_writer": 1,
        },
        retry_counts={},
        approvals={},
        next_step="supervisor",
        last_completed_step=None,
        supervisor_log=[],
        validation_issues=[],
        market_research=None,
        technique_research=None,
        patent_innovation_signal=None,
        trl_assessment=None,
        threat_evaluation=None,
        strategy_plan=None,
        report_artifact=None,
    )
