from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Dict, List

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent, threat_rank
from semiconductor_agent.models import EvidenceItem, ReportArtifact, ReportValidationMetrics, ValidationIssue
from semiconductor_agent.pdf_writer import write_simple_pdf
from semiconductor_agent.shared_standards import REPORT_SECTION_SEQUENCE
from semiconductor_agent.state import AgentState


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
                if current is None or threat_rank(entry.threat_level) > threat_rank(current.threat_level):
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
