from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from semiconductor_agent.models import (
    EvidenceItem,
    MarketResearchResult,
    PatentInnovationSignalResult,
    PatentSignalEntry,
    ReportArtifact,
    ReportValidationMetrics,
    StrategyPlanResult,
    StrategyRecommendation,
    SupervisorDecision,
    TechniqueResearchResult,
    TechnologyBrief,
    ThreatEntry,
    ThreatEvaluationResult,
    TRLAssessmentEntry,
    TRLAssessmentResult,
    ValidationIssue,
)
from semiconductor_agent.pdf_writer import write_simple_pdf
from semiconductor_agent.rag import CorpusRegistry
from semiconductor_agent.runtime import RuntimeConfig
from semiconductor_agent.search import WebSearchClient, build_balanced_search_plan, validate_search_balance
from semiconductor_agent.shared_standards import REPORT_SECTION_SEQUENCE
from semiconductor_agent.state import AgentState


@dataclass
class AgentDependencies:
    runtime: RuntimeConfig
    corpora: CorpusRegistry
    web_search: WebSearchClient


class BaseWorkflowAgent:
    agent_key = "base"

    def __init__(self, dependencies: AgentDependencies):
        self.dependencies = dependencies

    def append_issues(self, state: AgentState, new_issues: Sequence[ValidationIssue]) -> List[ValidationIssue]:
        merged = list(state.get("validation_issues", []))
        merged.extend(new_issues)
        return merged

    def _increment_search_count(self, state: AgentState, amount: int) -> int:
        return min(state.get("search_budget_limit", 5), state.get("search_count", 0) + amount)

    def _freshness_note(self, evidence: Sequence[EvidenceItem]) -> str:
        dated = [item for item in evidence if item.published_at]
        if not dated:
            return "날짜 정보가 부족하여 최신성은 보수적으로 해석해야 함"
        newest = max(item.published_at for item in dated if item.published_at)
        return "가장 최근 근거 기준일: %s" % newest.isoformat()

    def _citation(self, evidence: EvidenceItem) -> str:
        page = " p.%s" % evidence.page if evidence.page else ""
        return "[출처: %s%s]" % (Path(evidence.source_path).name, page)


class MarketResearchCollectorAgent(BaseWorkflowAgent):
    agent_key = "market_research"

    def run(self, state: AgentState) -> Dict[str, object]:
        technologies = state.get("target_technologies", [])
        companies = state.get("candidate_companies", [])
        search_plan = build_balanced_search_plan(
            topic="반도체 시장 조사 및 경쟁사 선정",
            scope_hint=", ".join(technologies),
        )

        company_findings = {}
        latest_articles = []
        for company in companies:
            company_evidence = []
            for technology in technologies:
                hits = self.dependencies.corpora.search(
                    "research",
                    "%s %s semiconductor market technical trend" % (company, technology),
                    top_k=2,
                )
                for hit in hits:
                    hit.company = company
                    hit.technology = technology
                company_evidence.extend(hits)
            if not company_evidence:
                fallback_hits = self.dependencies.corpora.search(
                    "research",
                    "%s semiconductor strategy" % company,
                    top_k=2,
                )
                for hit in fallback_hits:
                    hit.company = company
                company_evidence.extend(fallback_hits)
            company_findings[company] = company_evidence
            latest_articles.extend(company_evidence[:1])

        web_results = []
        if self.dependencies.runtime.enable_web_search and state.get("search_count", 0) < state.get("search_budget_limit", 5):
            web_results = self.dependencies.web_search.search(search_plan.objective_query, max_results=3)

        market_summary = (
            "대상 기술은 HBM4, PIM, CXL로 고정하고 경쟁사는 SK hynix, Samsung Electronics, Micron, NVIDIA 범위에서 비교한다. "
            "시장 조사는 후속 TRL/위협 평가가 동일 기술-기업 축으로 비교되도록 기준 기업군을 유지하는 데 초점을 둔다."
        )

        issues = validate_search_balance(web_results)
        return {
            "market_research": MarketResearchResult(
                selected_companies=companies,
                market_summary=market_summary,
                company_findings=company_findings,
                latest_articles=latest_articles,
                search_plan=search_plan,
            ),
            "selected_companies": companies,
            "search_count": self._increment_search_count(state, 1 if web_results else 0),
            "validation_issues": self.append_issues(state, issues),
            "last_completed_step": self.agent_key,
        }


class TechniqueResearchCollectorAgent(BaseWorkflowAgent):
    agent_key = "technique_research"

    def run(self, state: AgentState) -> Dict[str, object]:
        technologies = state.get("target_technologies", [])
        search_plan = build_balanced_search_plan(
            topic="반도체 최신 기술 조사",
            scope_hint=", ".join(technologies),
        )
        technology_briefs = {}
        validation_issues = []
        for technology in technologies:
            evidence = self.dependencies.corpora.search(
                "research",
                "%s architecture principle performance roadmap" % technology,
                top_k=4,
            )
            for item in evidence:
                item.technology = technology

            brief = TechnologyBrief(
                technology=technology,
                summary=self._compose_technology_summary(technology, evidence),
                key_points=self._build_key_points(technology, evidence),
                core_claims=self._build_claims(technology, evidence),
                supporting_evidence=evidence,
                expansion_keywords=[
                    technology,
                    "%s roadmap" % technology,
                    "%s validation" % technology,
                ],
                freshness_note=self._freshness_note(evidence),
            )
            issues = self._evidence_validation_node(brief)
            brief.validation_issues = issues
            validation_issues.extend(issues)
            technology_briefs[technology] = brief

        return {
            "technique_research": TechniqueResearchResult(
                technology_briefs=technology_briefs,
                evidence_validation_issues=validation_issues,
                search_plan=search_plan,
            ),
            "validation_issues": self.append_issues(state, validation_issues),
            "last_completed_step": self.agent_key,
        }

    def _compose_technology_summary(self, technology: str, evidence: Sequence[EvidenceItem]) -> str:
        if not evidence:
            return "[추정] %s 관련 직접 근거가 부족하여 reference 문서 기반의 보수적 요약만 제공한다." % technology
        first = evidence[0]
        return "%s는 %s %s를 통해 최신 구조와 기술 방향을 파악할 수 있다." % (
            technology,
            Path(first.source_path).name,
            self._citation(first),
        )

    def _build_key_points(self, technology: str, evidence: Sequence[EvidenceItem]) -> List[str]:
        if not evidence:
            return ["[추정] %s의 핵심 포인트를 확인할 추가 근거가 부족함" % technology]
        points = []
        for item in evidence[:3]:
            points.append("%s 관련 근거 요약: %s %s" % (technology, item.content[:120], self._citation(item)))
        return points

    def _build_claims(self, technology: str, evidence: Sequence[EvidenceItem]) -> List[str]:
        if not evidence:
            return ["[추정] %s는 공개 자료 부족으로 추가 검색이 필요함" % technology]
        return [
            "%s 핵심 주장 1: %s %s" % (technology, evidence[0].content[:100], self._citation(evidence[0])),
            "%s 핵심 주장 2: %s %s" % (technology, evidence[min(1, len(evidence) - 1)].content[:100], self._citation(evidence[min(1, len(evidence) - 1)])),
        ]

    def _evidence_validation_node(self, brief: TechnologyBrief) -> List[ValidationIssue]:
        issues = []
        unique_sources = {item.source_path for item in brief.supporting_evidence}
        unique_source_types = {item.source_type for item in brief.supporting_evidence}
        if len(brief.supporting_evidence) < 2:
            issues.append(
                ValidationIssue(
                    scope="Evidence Validation Node",
                    message="%s 근거 문서 수가 부족합니다." % brief.technology,
                    severity="high",
                    blocking=True,
                )
            )
        if len(unique_sources) < len(brief.supporting_evidence):
            issues.append(
                ValidationIssue(
                    scope="Evidence Validation Node",
                    message="%s 조사 결과에 중복 문서가 포함되어 있습니다." % brief.technology,
                    severity="medium",
                    blocking=False,
                )
            )
        if len(unique_source_types) < 2:
            issues.append(
                ValidationIssue(
                    scope="Evidence Validation Node",
                    message="%s 조사 결과의 출처 유형이 단조롭습니다." % brief.technology,
                    severity="medium",
                    blocking=False,
                )
            )
        return issues


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


class TRLAssessmentAgent(BaseWorkflowAgent):
    agent_key = "trl_assessment"

    def run(self, state: AgentState) -> Dict[str, object]:
        companies = state.get("selected_companies", []) or state.get("candidate_companies", [])
        techniques = state.get("technique_research")
        patent_signals = state.get("patent_innovation_signal")
        standards = state.get("shared_standards", {})
        entries = []

        for technology in state.get("target_technologies", []):
            tech_brief = techniques.technology_briefs.get(technology) if techniques else None
            trl_context = self.dependencies.corpora.search(
                "trl",
                "%s technology readiness level definition NASA IRDS" % technology,
                top_k=2,
            )
            for company in companies:
                matching_signals = []
                if patent_signals:
                    matching_signals = [
                        entry for entry in patent_signals.entries if entry.company == company and entry.technology == technology
                    ]
                trl_level, rule_range, confidence, estimated = self._determine_trl(
                    tech_brief.supporting_evidence if tech_brief else [],
                    matching_signals,
                )
                supporting_evidence = list(trl_context)
                if tech_brief:
                    supporting_evidence.extend(tech_brief.supporting_evidence[:2])
                if matching_signals:
                    supporting_evidence.extend(matching_signals[0].indirect_evidence[:2])
                reason = self._compose_reason(company, technology, trl_level, rule_range, supporting_evidence, estimated)
                entries.append(
                    TRLAssessmentEntry(
                        technology=technology,
                        company=company,
                        trl_level=trl_level,
                        reason=reason,
                        applied_rule_range=rule_range,
                        supporting_evidence=supporting_evidence[:4],
                        confidence=confidence,
                        estimated=estimated,
                    )
                )

        return {
            "trl_assessment": TRLAssessmentResult(entries=entries, shared_standards_used=standards),
            "last_completed_step": self.agent_key,
        }

    def _determine_trl(
        self,
        technique_evidence: Sequence[EvidenceItem],
        patent_signals: Sequence[PatentSignalEntry],
    ) -> tuple:
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
    ) -> str:
        qualifier = "[추정] " if estimated else ""
        if evidence:
            citation = self._citation(evidence[0])
        else:
            citation = ""
        return "%s%s의 %s는 %s 규칙을 적용해 TRL %d로 판정했다. %s" % (
            qualifier,
            company,
            technology,
            rule_range,
            trl_level,
            citation,
        )


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


class StrategyPlannerAgent(BaseWorkflowAgent):
    agent_key = "strategy_planner"

    def run(self, state: AgentState) -> Dict[str, object]:
        threats = state.get("threat_evaluation")
        internal_baseline = state.get("internal_baseline", {})
        recommendations = []
        highest_by_technology = {}
        for entry in threats.entries if threats else []:
            current = highest_by_technology.get(entry.technology)
            if current is None or _threat_rank(entry.threat_level) > _threat_rank(current.threat_level):
                highest_by_technology[entry.technology] = entry

        for technology in state.get("target_technologies", []):
            entry = highest_by_technology.get(technology)
            threat_level = entry.threat_level if entry else "Low"
            baseline = internal_baseline.get(technology)
            recommendations.append(
                StrategyRecommendation(
                    technology=technology,
                    priority=self._priority_from_threat(threat_level),
                    recommendation=self._strategy_text(technology, threat_level, baseline),
                    linked_threat_level=threat_level,
                    rationale=self._strategy_rationale(technology, threat_level, baseline),
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

    @staticmethod
    def _priority_from_threat(threat_level: str) -> str:
        if threat_level == "High":
            return "High"
        if threat_level == "Medium":
            return "Medium"
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


class ReportWriterAgent(BaseWorkflowAgent):
    agent_key = "report_writer"

    def run(self, state: AgentState) -> Dict[str, object]:
        output_dir = Path(state.get("output_dir", "."))
        output_dir.mkdir(parents=True, exist_ok=True)
        markdown = self._build_markdown_report(state)
        markdown_path = output_dir / "semiconductor_strategy_report.md"
        markdown_path.write_text(markdown, encoding="utf-8")

        metrics, issues = self._report_validate_node(state, markdown)
        pdf_path = self._formatting_node_pdf_generator(markdown, output_dir / "semiconductor_strategy_report.pdf")

        artifact = ReportArtifact(
            markdown=markdown,
            markdown_path=str(markdown_path),
            pdf_path=str(pdf_path),
            metrics=metrics,
            validation_issues=issues,
        )
        return {
            "report_artifact": artifact,
            "validation_issues": self.append_issues(state, issues),
            "last_completed_step": self.agent_key,
        }

    def _build_markdown_report(self, state: AgentState) -> str:
        market = state.get("market_research")
        techniques = state.get("technique_research")
        patent = state.get("patent_innovation_signal")
        trl = state.get("trl_assessment")
        threat = state.get("threat_evaluation")
        strategy = state.get("strategy_plan")
        highest_threat_by_technology = {}
        if threat:
            for entry in threat.entries:
                current = highest_threat_by_technology.get(entry.technology)
                if current is None or _threat_rank(entry.threat_level) > _threat_rank(current.threat_level):
                    highest_threat_by_technology[entry.technology] = entry

        lines = []
        lines.append("# SUMMARY")
        lines.append("기술별 현재 위치, 주요 경쟁사, 위협 수준, 전략 방향을 중심으로 정리한다.")
        if threat and strategy:
            for entry in threat.entries[:3]:
                citation = self._citation(entry.supporting_evidence[0]) if entry.supporting_evidence else ""
                lines.append(
                    "- %s / %s: %s 위협. %s %s"
                    % (entry.company, entry.technology, entry.threat_level, entry.rationale, citation)
                )

        lines.append("")
        lines.append("# 분석 배경")
        lines.append(state.get("user_query", ""))
        lines.append("시장 변화와 기술 중요성에 따라 HBM4, PIM, CXL을 동일 구조로 비교한다.")

        lines.append("")
        lines.append("# 핵심 기술 현황")
        if market:
            lines.append("## 시장 및 경쟁사 범위")
            lines.append(market.market_summary)
            for company in market.selected_companies:
                evidence = market.company_findings.get(company, [])
                if evidence:
                    lines.append("- %s: %s %s" % (company, evidence[0].content[:100], self._citation(evidence[0])))
        if techniques:
            for brief in techniques.technology_briefs.values():
                lines.append("## %s" % brief.technology)
                lines.append(brief.summary)
                for point in brief.key_points:
                    lines.append("- %s" % point)
        if patent:
            lines.append("## Patent & Innovation Signal 종합 해석")
            for entry in patent.entries[:6]:
                prefix = "[추정] " if entry.estimated else ""
                citation = self._citation(entry.indirect_evidence[0]) if entry.indirect_evidence else ""
                lines.append("- %s%s / %s: %s %s" % (prefix, entry.company, entry.technology, entry.signal_summary, citation))

        lines.append("")
        lines.append("# TRL 기반 기술 성숙도 분석")
        if trl:
            for entry in trl.entries:
                prefix = "[추정] " if entry.estimated else ""
                citation = self._citation(entry.supporting_evidence[0]) if entry.supporting_evidence else ""
                lines.append(
                    "- %s%s / %s: TRL %d, %s, confidence=%s %s"
                    % (
                        prefix,
                        entry.company,
                        entry.technology,
                        entry.trl_level,
                        entry.reason,
                        entry.confidence,
                        citation,
                    )
                )

        lines.append("")
        lines.append("# 경쟁 위협 수준 평가")
        if threat:
            for entry in threat.entries:
                citation = self._citation(entry.supporting_evidence[0]) if entry.supporting_evidence else ""
                lines.append(
                    "- %s / %s: %s, %s %s"
                    % (entry.company, entry.technology, entry.threat_level, entry.rationale, citation)
                )

        lines.append("")
        lines.append("# 전략적 방향 및 대응제안")
        if strategy:
            for recommendation in strategy.recommendations:
                threat_entry = highest_threat_by_technology.get(recommendation.technology)
                citation = self._citation(threat_entry.supporting_evidence[0]) if threat_entry and threat_entry.supporting_evidence else ""
                lines.append(
                    "- %s: priority=%s, threat=%s, action=%s %s"
                    % (
                        recommendation.technology,
                        recommendation.priority,
                        recommendation.linked_threat_level,
                        recommendation.recommendation,
                        citation,
                    )
                )
                lines.append("  rationale: %s" % recommendation.rationale)

        lines.append("")
        lines.append("# REFERENCE")
        references = self._collect_references(state)
        for reference in references:
            lines.append("- %s" % reference)

        return "\n".join(lines).strip() + "\n"

    def _report_validate_node(self, state: AgentState, markdown: str) -> tuple:
        lines = [line for line in markdown.splitlines() if line.strip()]
        claim_lines = [line for line in lines if line.startswith("- ")]
        citation_lines = [line for line in claim_lines if "[출처:" in line]
        references = self._collect_reference_items(state)
        unique_references = {}
        for item in references:
            unique_references.setdefault(item.source_path, item)
        recent_reference_count = 0
        cutoff = date.today().replace(day=1)
        for item in unique_references.values():
            if item.published_at:
                months = (cutoff.year - item.published_at.year) * 12 + cutoff.month - item.published_at.month
                if months <= 18:
                    recent_reference_count += 1
        evidence_rate = len(citation_lines) / float(len(claim_lines) or 1)
        freshness_rate = recent_reference_count / float(len(unique_references) or 1)
        completeness_hits = sum(1 for section in REPORT_SECTION_SEQUENCE if "# %s" % section in markdown)
        completeness_rate = completeness_hits / float(len(REPORT_SECTION_SEQUENCE))
        uncertainty_rate = markdown.count("[추정]") / float(len(claim_lines) or 1)

        passed = 0
        if evidence_rate >= 0.8:
            passed += 1
        if freshness_rate >= 0.4:
            passed += 1
        if completeness_rate >= 1.0:
            passed += 1
        if uncertainty_rate <= 0.5:
            passed += 1

        metrics = ReportValidationMetrics(
            evidence_rate=evidence_rate,
            freshness_rate=freshness_rate,
            completeness_rate=completeness_rate,
            uncertainty_rate=uncertainty_rate,
            passed_criteria=passed,
            total_criteria=4,
        )

        issues = []
        if passed <= 2:
            issues.append(
                ValidationIssue(
                    scope="Report Validate Node",
                    message="보고서 품질 기준 4개 중 2개 이상을 통과하지 못했습니다.",
                    severity="high",
                    blocking=True,
                )
            )
        return metrics, issues

    def _formatting_node_pdf_generator(self, markdown: str, output_path: Path) -> Path:
        return write_simple_pdf(markdown.splitlines(), output_path)

    def _collect_references(self, state: AgentState) -> List[str]:
        items = self._collect_reference_items(state)
        rendered = []
        seen = set()
        for item in items:
            key = (item.source_path, item.page)
            if key in seen:
                continue
            seen.add(key)
            page = " p.%s" % item.page if item.page else ""
            rendered.append("%s%s (%s)" % (Path(item.source_path).name, page, item.source_type))
        return rendered

    def _collect_reference_items(self, state: AgentState) -> List[EvidenceItem]:
        items = []
        if state.get("market_research"):
            for values in state["market_research"].company_findings.values():
                items.extend(values)
            items.extend(state["market_research"].latest_articles)
        if state.get("technique_research"):
            for brief in state["technique_research"].technology_briefs.values():
                items.extend(brief.supporting_evidence)
        if state.get("patent_innovation_signal"):
            for entry in state["patent_innovation_signal"].entries:
                items.extend(entry.indirect_evidence)
        if state.get("trl_assessment"):
            for entry in state["trl_assessment"].entries:
                items.extend(entry.supporting_evidence)
        if state.get("threat_evaluation"):
            for entry in state["threat_evaluation"].entries:
                items.extend(entry.supporting_evidence)
        return items


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
            if blocking and _can_retry("technique_research", state, retry_counts):
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
            if blocking and _can_retry("trl_assessment", state, retry_counts):
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
            if blocking and _can_retry("strategy_planner", state, retry_counts):
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
            if blocking and _can_retry("report_writer", state, retry_counts):
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
            if _threat_rank(threat.threat_level) > _threat_rank(highest_threat.get(threat.technology, "Low")):
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


def _can_retry(step_name: str, state: AgentState, retry_counts: Dict[str, int]) -> bool:
    limit = state.get("retry_limits", {}).get(step_name, 0)
    return retry_counts.get(step_name, 0) < limit


def _threat_rank(threat_level: str) -> int:
    return {"Low": 1, "Medium": 2, "High": 3}.get(threat_level, 0)
