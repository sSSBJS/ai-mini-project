from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class BalancedSearchPlan(BaseModel):
    hypothesis: str = Field(description="에이전트가 검증하려는 현재 가설")
    confirming_query: str = Field(description="가설을 뒷받침하는 증거를 찾기 위한 검색어")
    opposing_query: str = Field(description="가설을 반박하거나 치명적인 한계를 찾기 위한 검색어")
    objective_query: str = Field(description="중립적인 사실이나 통계 데이터를 찾기 위한 검색어")


class SearchResult(BaseModel):
    title: str
    snippet: str
    url: str
    source_type: str
    published_at: Optional[date] = None


class EvidenceItem(BaseModel):
    title: str
    content: str
    source_path: str
    source_type: str
    technology: Optional[str] = None
    company: Optional[str] = None
    page: Optional[int] = None
    published_at: Optional[date] = None
    confidence: str = "medium"
    estimated: bool = False


class ValidationIssue(BaseModel):
    scope: str
    message: str
    severity: str = "medium"
    blocking: bool = False


class TechnologyBrief(BaseModel):
    technology: str
    summary: str
    key_points: List[str]
    core_claims: List[str]
    supporting_evidence: List[EvidenceItem]
    expansion_keywords: List[str]
    freshness_note: str
    validation_issues: List[ValidationIssue] = Field(default_factory=list)


class MarketResearchResult(BaseModel):
    selected_companies: List[str]
    market_summary: str
    company_findings: Dict[str, List[EvidenceItem]]
    latest_articles: List[EvidenceItem]
    search_plan: BalancedSearchPlan


class TechniqueResearchResult(BaseModel):
    technology_briefs: Dict[str, TechnologyBrief]
    evidence_validation_issues: List[ValidationIssue] = Field(default_factory=list)
    search_plan: BalancedSearchPlan


class PatentSignalEntry(BaseModel):
    technology: str
    company: str
    signal_summary: str
    patent_activity_summary: Optional[str] = None
    patent_paper_link_summary: Optional[str] = None
    ecosystem_signal_summary: Optional[str] = None
    indirect_evidence: List[EvidenceItem]
    confidence: str
    estimated: bool = False


class PatentInnovationSignalResult(BaseModel):
    entries: List[PatentSignalEntry]
    search_plan: BalancedSearchPlan


class TRLAssessmentEntry(BaseModel):
    technology: str
    company: str
    trl_level: int
    reason: str
    applied_rule_range: str
    supporting_evidence: List[EvidenceItem]
    confidence: str
    estimated: bool = False


class TRLAssessmentResult(BaseModel):
    entries: List[TRLAssessmentEntry]
    shared_standards_used: Dict[str, object]


class ThreatEntry(BaseModel):
    technology: str
    company: str
    threat_level: str
    rationale: str
    supporting_evidence: List[EvidenceItem]


class ThreatEvaluationResult(BaseModel):
    entries: List[ThreatEntry]


class StrategyRecommendation(BaseModel):
    technology: str
    priority: str
    recommendation: str
    linked_threat_level: str
    rationale: str


class StrategyPlanResult(BaseModel):
    recommendations: List[StrategyRecommendation]
    validation_issues: List[ValidationIssue] = Field(default_factory=list)


class ReportValidationMetrics(BaseModel):
    evidence_rate: float
    freshness_rate: float
    completeness_rate: float
    uncertainty_rate: float
    passed_criteria: int
    total_criteria: int


class ReportArtifact(BaseModel):
    markdown: str
    markdown_path: str
    pdf_path: str
    metrics: ReportValidationMetrics
    validation_issues: List[ValidationIssue] = Field(default_factory=list)


class SupervisorDecision(BaseModel):
    step: str
    decision: str
    reason: str


class SupervisorStageReview(BaseModel):
    approved: bool
    retry_target: str = "none"
    summary: str
    issues: List[ValidationIssue] = Field(default_factory=list)

