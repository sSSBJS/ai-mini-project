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
    # CHANGED: Report Validate Node 실패 시 최대 2회까지 재출력을 시도하도록 제한.
    _MAX_REPORT_RETRIES = 2

    def run(self, state: AgentState) -> Dict[str, object]:
        output_dir = Path(state.get("output_dir", "."))
        output_dir.mkdir(parents=True, exist_ok=True)
        # CHANGED: 보고서 생성 -> 검증 -> 재출력의 bounded loop를 report.py 내부에서 수행.
        retry_round = 0
        markdown = self._build_markdown_report(state, retry_round=retry_round, validation_feedback=[])
        metrics, issues = self._report_validate_node(state, markdown)
        while self._should_retry_report(issues, retry_round):
            retry_round += 1
            markdown = self._build_markdown_report(
                state,
                retry_round=retry_round,
                validation_feedback=[issue.message for issue in issues],
            )
            metrics, issues = self._report_validate_node(state, markdown)
        markdown_path = output_dir / "semiconductor_strategy_report.md"
        markdown_path.write_text(markdown, encoding="utf-8")

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

    def _build_markdown_report(
        self,
        state: AgentState,
        retry_round: int = 0,
        validation_feedback: List[str] | None = None,
    ) -> str:
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
        # CHANGED: 임원용 요약을 표 중심으로 재구성하고 위협/전략/TRL을 한눈에 보도록 정리.
        lines.append("# SUMMARY")
        lines.append("임원용 요약: 핵심기술의 현재 위치, 주요 기업, 위협 수준, 전략 방향을 한눈에 파악할 수 있도록 정리한다.")
        if retry_round > 0:
            lines.append("보고서 품질 보강 라운드 %d: 근거율, 신선도율, 완결율, 불확실율을 재점검해 재출력했다." % retry_round)
            for feedback in (validation_feedback or [])[:4]:
                lines.append("- 품질 보강 포인트: %s" % feedback)
        lines.append("")
        lines.extend(self._executive_summary_table(state, highest_threat_by_technology))

        lines.append("")
        lines.append("# 분석 배경")
        lines.append(state.get("user_query", ""))
        lines.append("시장·기술·특허·혁신 신호·TRL·위협 수준·전략 제안을 하나의 흐름으로 통합 해석한다.")

        lines.append("")
        lines.append("# 핵심 기술 현황")
        if market:
            # CHANGED: 시장 및 기업 분석 섹션을 표 중심으로 재구성.
            lines.append("## 시장 및 기업 분석 섹션")
            lines.append(market.market_summary)
            lines.append("")
            lines.extend(self._market_company_table(market))
        if techniques:
            # CHANGED: 기술별 분석 섹션을 기술 정의/핵심 포인트/발전 방향 형태로 정리.
            lines.append("## 기술별 분석 섹션")
            for brief in techniques.technology_briefs.values():
                lines.append("### %s" % brief.technology)
                lines.append(brief.summary)
                if brief.core_claims:
                    lines.append("- 핵심 주장: %s" % "; ".join(brief.core_claims[:3]))
                if brief.expansion_keywords:
                    lines.append("- 발전 방향 및 최신 동향: %s" % ", ".join(brief.expansion_keywords[:5]))
                for point in brief.key_points:
                    lines.append("- %s" % point)
                if brief.supporting_evidence:
                    lines.append("- 대표 근거: %s" % self._citation(brief.supporting_evidence[0]))
        if patent:
            # CHANGED: Patent & Innovation Signal을 세부 요약 필드 기준으로 종합 해석.
            lines.append("## Patent & Innovation Signal 종합 해석")
            lines.append("")
            lines.extend(self._patent_signal_table(patent))
            for entry in patent.entries[:6]:
                prefix = "[추정] " if entry.estimated else ""
                citation = self._citation(entry.indirect_evidence[0]) if entry.indirect_evidence else ""
                lines.append("### %s%s / %s" % (prefix, entry.company, entry.technology))
                lines.append("- 통합 해석: %s %s" % (entry.signal_summary, citation))
                lines.append(
                    "- 특허 activity: %s"
                    % self._safe_getattr(
                        entry,
                        "patent_activity_summary",
                        "특허 activity 요약이 제공되지 않아 간접 근거 중심으로 해석함.",
                    )
                )
                lines.append(
                    "- 특허-논문 연결: %s"
                    % self._safe_getattr(
                        entry,
                        "patent_paper_link_summary",
                        "NPL, 동일 발명자, 시간차 관련 근거는 제한적으로 확인됨.",
                    )
                )
                lines.append(
                    "- 생태계·사업화 신호: %s"
                    % self._safe_getattr(
                        entry,
                        "ecosystem_signal_summary",
                        "파트너십·투자·사업화 관련 신호를 간접 evidence로 보완함.",
                    )
                )

        lines.append("")
        lines.append("# TRL 기반 기술 성숙도 분석")
        if trl:
            # CHANGED: TRL 결과를 표로 먼저 요약하고 근거를 이어서 제공.
            lines.extend(self._trl_table(trl))
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
            # CHANGED: Threat Evaluation 결과를 표 형식으로 정리.
            lines.extend(self._threat_table(threat))
            for entry in threat.entries:
                citation = self._citation(entry.supporting_evidence[0]) if entry.supporting_evidence else ""
                lines.append(
                    "- %s / %s: %s, %s %s"
                    % (entry.company, entry.technology, entry.threat_level, entry.rationale, citation)
                )

        lines.append("")
        lines.append("# 전략적 방향 및 대응제안")
        if strategy:
            # CHANGED: Strategy Recommendation을 실행 가능한 액션 중심의 표로 제공.
            lines.extend(self._strategy_table(strategy))
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
        lines.append("## Evidence & References")
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
                    message="보고서 품질 기준 4개 중 2개 이상을 통과하지 못했습니다. 재출력이 필요합니다.",
                    severity="high",
                    blocking=True,
                )
            )
        # CHANGED: 각 품질 기준에 대한 세부 실패 사유를 추가해 최종 점검 노드 역할을 강화.
        if evidence_rate < 0.8:
            issues.append(
                ValidationIssue(
                    scope="Report Validate Node",
                    message="근거율이 기준(0.8) 미만입니다. 출처 부착이 더 필요합니다.",
                    severity="medium",
                    blocking=False,
                )
            )
        if freshness_rate < 0.4:
            issues.append(
                ValidationIssue(
                    scope="Report Validate Node",
                    message="신선도율이 기준(0.4) 미만입니다. 18개월 이내 최신 출처 보강이 필요합니다.",
                    severity="medium",
                    blocking=False,
                )
            )
        if completeness_rate < 1.0:
            issues.append(
                ValidationIssue(
                    scope="Report Validate Node",
                    message="완결율이 기준(1.0) 미만입니다. 필수 섹션 누락 여부를 점검해야 합니다.",
                    severity="high",
                    blocking=False,
                )
            )
        if uncertainty_rate > 0.5:
            issues.append(
                ValidationIssue(
                    scope="Report Validate Node",
                    message="불확실율이 기준(0.5)을 초과했습니다. [추정] 태그 비중을 낮추거나 근거를 강화해야 합니다.",
                    severity="medium",
                    blocking=False,
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
            confidence_tag = "confidence=%s" % getattr(item, "confidence", "unknown")
            rendered.append("%s%s (%s, %s)" % (Path(item.source_path).name, page, item.source_type, confidence_tag))
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

    # CHANGED: blocking 품질 이슈가 있고 최대 재시도 횟수(2회)를 넘지 않으면 재출력을 수행.
    def _should_retry_report(self, issues: List[ValidationIssue], retry_round: int) -> bool:
        has_blocking_issue = any(issue.blocking for issue in issues if issue.scope == "Report Validate Node")
        return has_blocking_issue and retry_round < self._MAX_REPORT_RETRIES

    # CHANGED: 임원용 요약 표를 생성.
    def _executive_summary_table(self, state: AgentState, highest_threat_by_technology: Dict[str, object]) -> List[str]:
        strategy = state.get("strategy_plan")
        trl = state.get("trl_assessment")
        lines = [
            "| 기술 | 현재 위치 | 주요 기업 | 최고 위협 수준 | 전략 방향 |",
            "| --- | --- | --- | --- | --- |",
        ]
        strategy_by_tech = {item.technology: item for item in strategy.recommendations} if strategy else {}
        trl_by_tech = {}
        if trl:
            for entry in trl.entries:
                current = trl_by_tech.get(entry.technology)
                if current is None or entry.trl_level > current.trl_level:
                    trl_by_tech[entry.technology] = entry
        for technology in state.get("target_technologies", []):
            companies = state.get("selected_companies", []) or state.get("candidate_companies", [])
            company_text = ", ".join(companies[:3]) if companies else "-"
            trl_entry = trl_by_tech.get(technology)
            threat_entry = highest_threat_by_technology.get(technology)
            recommendation = strategy_by_tech.get(technology)
            current_position = "TRL %s" % trl_entry.trl_level if trl_entry else "판정 전"
            threat_level = threat_entry.threat_level if threat_entry else "-"
            action = recommendation.recommendation[:50] + "..." if recommendation and len(recommendation.recommendation) > 50 else (recommendation.recommendation if recommendation else "-")
            lines.append("| %s | %s | %s | %s | %s |" % (technology, current_position, company_text, threat_level, action))
        return lines

    # CHANGED: 시장 및 기업 분석 표를 생성.
    def _market_company_table(self, market) -> List[str]:
        lines = [
            "| 기업 | 주요 활동 | 투자/경쟁 구도 단서 | 출처 |",
            "| --- | --- | --- | --- |",
        ]
        for company in market.selected_companies:
            evidence = market.company_findings.get(company, [])
            if evidence:
                item = evidence[0]
                activity = item.content[:70].replace("|", "/")
                investment_hint = evidence[1].content[:50].replace("|", "/") if len(evidence) > 1 else item.source_type
                citation = self._citation(item)
            else:
                activity = "-"
                investment_hint = "-"
                citation = "-"
            lines.append("| %s | %s | %s | %s |" % (company, activity, investment_hint, citation))
        return lines

    # CHANGED: Patent & Innovation Signal 표를 생성.
    def _patent_signal_table(self, patent) -> List[str]:
        lines = [
            "| 기업 | 기술 | 특허 activity | 특허-논문 연결 | 생태계·사업화 신호 | confidence |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for entry in patent.entries[:6]:
            lines.append(
                "| %s | %s | %s | %s | %s | %s |"
                % (
                    entry.company,
                    entry.technology,
                    self._shorten(self._safe_getattr(entry, "patent_activity_summary", entry.signal_summary), 80),
                    self._shorten(self._safe_getattr(entry, "patent_paper_link_summary", "제한적"), 80),
                    self._shorten(self._safe_getattr(entry, "ecosystem_signal_summary", "제한적"), 80),
                    entry.confidence,
                )
            )
        return lines

    # CHANGED: TRL 표를 생성.
    def _trl_table(self, trl) -> List[str]:
        lines = [
            "| 기업 | 기술 | TRL | 판정 근거 | confidence |",
            "| --- | --- | --- | --- | --- |",
        ]
        for entry in trl.entries:
            lines.append(
                "| %s | %s | %s | %s | %s |"
                % (entry.company, entry.technology, entry.trl_level, self._shorten(entry.reason, 90), entry.confidence)
            )
        return lines

    # CHANGED: Threat Evaluation 표를 생성.
    def _threat_table(self, threat) -> List[str]:
        lines = [
            "| 기업 | 기술 | 위협 수준 | 근거 요약 |",
            "| --- | --- | --- | --- |",
        ]
        for entry in threat.entries:
            lines.append("| %s | %s | %s | %s |" % (entry.company, entry.technology, entry.threat_level, self._shorten(entry.rationale, 100)))
        return lines

    # CHANGED: Strategy Recommendation 표를 생성.
    def _strategy_table(self, strategy) -> List[str]:
        lines = [
            "| 기술 | 우선순위 | 연계 위협 수준 | 실행 전략 |",
            "| --- | --- | --- | --- |",
        ]
        for item in strategy.recommendations:
            lines.append(
                "| %s | %s | %s | %s |"
                % (item.technology, item.priority, item.linked_threat_level, self._shorten(item.recommendation, 100))
            )
        return lines

    # CHANGED: 동적 모델/기존 모델 양쪽 모두에서 안전하게 필드를 읽는다.
    def _safe_getattr(self, entry: object, field_name: str, default: str) -> str:
        value = getattr(entry, field_name, default)
        return value if value else default

    # CHANGED: 표 셀 길이를 제한해 보고서 가독성을 유지한다.
    def _shorten(self, text: str, limit: int) -> str:
        normalized = " ".join((text or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3] + "..."
