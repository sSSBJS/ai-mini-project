from __future__ import annotations

import html
from datetime import date
from pathlib import Path
from typing import Dict, List

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent, threat_rank
from semiconductor_agent.models import EvidenceItem, ReportArtifact, ReportValidationMetrics, ValidationIssue
from semiconductor_agent.pdf_writer import write_html_pdf, write_simple_pdf
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
        # CHANGED: PDF 대신 바로 보기 좋은 HTML 결과물을 함께 생성.
        html_path = output_dir / "semiconductor_strategy_report.html"
        html_path.write_text(self._build_html_report(state, markdown, metrics, issues), encoding="utf-8")

        pdf_path = self._formatting_node_pdf_generator(
            markdown,
            html_path,
            output_dir / "semiconductor_strategy_report.pdf",
        )

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
        lines.append("# OVERVIEW")
        lines.append(self._overview_headline(state, highest_threat_by_technology))
        lines.append("")
        lines.extend(self._executive_summary_table(state, highest_threat_by_technology))
        overview_summary = self._overview_strategy_summary(strategy, highest_threat_by_technology)
        overview_takeaways = self._overview_takeaways(state, highest_threat_by_technology)
        if overview_summary or overview_takeaways:
            lines.append("")
            lines.append("## 핵심 인사이트")
            if overview_summary:
                lines.append("- %s" % overview_summary)
            for takeaway in overview_takeaways:
                lines.append("- %s" % takeaway)

        lines.append("")
        lines.append("# 분석 배경")
        lines.append(state.get("user_query", ""))
        lines.append("시장·기술·특허·혁신 신호·TRL·위협 수준·전략 제안을 하나의 흐름으로 통합 해석해 의사결정 관점으로 재정리했다.")

        lines.append("")
        lines.append("# 핵심 기술 현황")
        if market:
            lines.append("## 시장 및 기업 분석")
            lines.append(self._shorten(market.market_summary, 260))
            lines.append("")
            lines.extend(self._market_company_table(market))
        if techniques:
            lines.append("## 기술 스냅샷")
            lines.extend(self._technology_snapshot_table(techniques))
        if patent:
            lines.append("## Patent & Innovation Signal 종합 해석")
            lines.append("")
            lines.extend(self._patent_signal_table(patent))
            takeaways = self._patent_takeaways(state, patent)
            if takeaways:
                lines.append("")
                lines.append("### 핵심 해석")
                for takeaway in takeaways:
                    lines.append("- %s" % takeaway)

        lines.append("")
        lines.append("# TRL 기반 기술 성숙도 분석")
        if trl:
            lines.extend(self._trl_table(trl))
            takeaways = self._trl_takeaways(state, trl)
            if takeaways:
                lines.append("")
                lines.append("## SK hynix 중심 해석")
                for takeaway in takeaways:
                    lines.append("- %s" % takeaway)

        lines.append("")
        lines.append("# 경쟁 위협 수준 평가")
        if threat:
            lines.extend(self._threat_table(threat))
            takeaways = self._threat_takeaways(state, threat)
            if takeaways:
                lines.append("")
                lines.append("## 주요 경쟁 위협")
                for takeaway in takeaways:
                    lines.append("- %s" % takeaway)

        lines.append("")
        lines.append("# 전략적 방향 및 대응제안")
        if strategy:
            lines.extend(self._strategy_table(strategy))
            actions = self._strategy_action_plan(strategy)
            if actions:
                lines.append("")
                lines.append("## 실행 권고")
                for action in actions:
                    lines.append("- %s" % action)

        lines.append("")
        lines.append("# REFERENCE")
        lines.append("## Evidence & References")
        references = self._collect_references(state)
        for reference in references:
            lines.append("- %s" % reference)

        return "\n".join(lines).strip() + "\n"

    # CHANGED: 보고서를 한글 친화적이고 스타일이 적용된 HTML로도 생성.
    def _build_html_report(
        self,
        state: AgentState,
        markdown: str,
        metrics: ReportValidationMetrics,
        issues: List[ValidationIssue],
    ) -> str:
        sections = self._split_markdown_sections(markdown)
        section_html = "".join(
            '<section class="report-section"><h2>%s</h2>%s</section>'
            % (html.escape(title), self._render_markdown_block_to_html(body_lines))
            for title, body_lines in sections
        )
        technologies = ", ".join(state.get("target_technologies", [])[:5]) or "N/A"
        primary_company = self._primary_company(state)
        comparison_companies = ", ".join(self._comparison_companies(state)[:3]) or "N/A"
        template = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SK hynix 기술 전략 분석 보고서</title>
  <style>
    :root {
      --ink: #1f2937;
      --muted: #6b7280;
      --line: #d1d5db;
      --panel: #ffffff;
      --panel-alt: #f9fafb;
      --brand: #1f3a68;
      --brand-soft: #eef3fb;
      --warn: #9a5b13;
      --warn-soft: #fff7ed;
      --bg: #f5f6f8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.65;
    }
    .wrap {
      width: min(980px, calc(100vw - 40px));
      margin: 24px auto 40px;
    }
    .hero {
      background: var(--panel);
      padding: 24px 28px;
      border-radius: 16px;
      border: 1px solid var(--line);
    }
    .eyebrow {
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 8px;
    }
    h1 {
      margin: 0;
      font-size: 32px;
      line-height: 1.2;
      color: var(--brand);
    }
    .subtitle {
      margin-top: 10px;
      font-size: 14px;
      color: var(--muted);
    }
    .badges {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 16px;
    }
    .badge {
      background: var(--brand-soft);
      border: 1px solid #c8d6ea;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      color: var(--brand);
    }
    .report-section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 20px 22px 16px;
      margin-top: 16px;
    }
    .report-section h2 {
      margin: 0 0 14px 0;
      font-size: 22px;
      color: var(--brand);
      padding-bottom: 10px;
      border-bottom: 2px solid var(--brand-soft);
    }
    .report-section h3 {
      margin: 18px 0 8px;
      font-size: 17px;
      color: #0f4c81;
    }
    p { margin: 8px 0 0; }
    ul { margin: 10px 0 0 18px; padding: 0; }
    li { margin: 6px 0; }
    blockquote {
      margin: 10px 0 0;
      padding: 10px 14px;
      background: #f8fafc;
      border-left: 4px solid #94a3b8;
      border-radius: 8px;
      color: var(--muted);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
      overflow: hidden;
      border-radius: 12px;
      font-size: 14px;
      table-layout: fixed;
    }
    thead th {
      background: var(--brand-soft);
      color: var(--brand);
      font-weight: 700;
      text-align: left;
      padding: 11px 12px;
      border-bottom: 1px solid #cfd9ea;
    }
    td {
      padding: 10px 12px;
      border-bottom: 1px solid #e5e7eb;
      vertical-align: top;
      white-space: normal;
      word-break: break-word;
      overflow-wrap: anywhere;
    }
    tbody tr:nth-child(even) td {
      background: var(--panel-alt);
    }
    code {
      background: #eef2f7;
      padding: 2px 6px;
      border-radius: 6px;
      font-size: 0.95em;
    }
    hr {
      border: 0;
      border-top: 1px solid #e5e7eb;
      margin: 18px 0 14px;
    }
    @media (max-width: 840px) {
      .wrap { width: min(100vw - 24px, 1100px); }
      .hero { padding: 20px 18px; }
      h1 { font-size: 28px; }
      .report-section { padding: 18px 16px 14px; }
      table { font-size: 12px; table-layout: fixed; }
      th, td { white-space: normal; word-break: break-word; overflow-wrap: anywhere; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <div class="eyebrow">Technology Strategy Report</div>
      <h1>SK hynix 기술 전략 분석 보고서</h1>
      <div class="badges">
        <span class="badge">대상 기업: __PRIMARY_COMPANY__</span>
        <span class="badge">비교군: __COMPARISON_COMPANIES__</span>
        <span class="badge">대상 기술: __TECHNOLOGIES__</span>
      </div>
    </header>
    __SECTION_HTML__
  </div>
</body>
</html>
"""
        return (
            template.replace("__TECHNOLOGIES__", html.escape(technologies))
            .replace("__PRIMARY_COMPANY__", html.escape(primary_company))
            .replace("__COMPARISON_COMPANIES__", html.escape(comparison_companies))
            .replace("__SECTION_HTML__", section_html)
        )

    # CHANGED: 마크다운 텍스트를 단순 HTML 블록으로 변환.
    def _split_markdown_sections(self, markdown: str) -> List[tuple[str, List[str]]]:
        sections: List[tuple[str, List[str]]] = []
        current_title = "Overview"
        current_lines: List[str] = []
        for raw_line in markdown.splitlines():
            if raw_line.startswith("# "):
                if current_lines:
                    sections.append((current_title, current_lines))
                current_title = raw_line[2:].strip()
                current_lines = []
                continue
            current_lines.append(raw_line)
        if current_lines:
            sections.append((current_title, current_lines))
        return sections

    # CHANGED: 보고서 미리보기를 위한 최소 마크다운-HTML 렌더러.
    def _render_markdown_block_to_html(self, lines: List[str]) -> str:
        html_lines: List[str] = []
        in_list = False
        in_table = False
        table_buffer: List[str] = []
        paragraph_buffer: List[str] = []

        def flush_paragraph() -> None:
            nonlocal paragraph_buffer
            if paragraph_buffer:
                html_lines.append("<p>%s</p>" % html.escape(" ".join(paragraph_buffer)))
                paragraph_buffer = []

        def flush_list() -> None:
            nonlocal in_list
            if in_list:
                html_lines.append("</ul>")
                in_list = False

        def flush_table() -> None:
            nonlocal in_table, table_buffer
            if not in_table or not table_buffer:
                return
            rows = [row.strip().strip("|").split("|") for row in table_buffer if row.strip().startswith("|")]
            rows = [[cell.strip() for cell in row] for row in rows]
            if len(rows) >= 2:
                header = rows[0]
                body = rows[2:] if len(rows) > 2 else []
                html_lines.append("<table><thead><tr>%s</tr></thead><tbody>" % "".join("<th>%s</th>" % html.escape(cell) for cell in header))
                for row in body:
                    html_lines.append("<tr>%s</tr>" % "".join("<td>%s</td>" % html.escape(cell) for cell in row))
                html_lines.append("</tbody></table>")
            table_buffer = []
            in_table = False

        for raw in lines:
            stripped = raw.strip()
            if not stripped:
                flush_paragraph()
                flush_list()
                flush_table()
                continue
            if stripped.startswith("|"):
                flush_paragraph()
                flush_list()
                in_table = True
                table_buffer.append(stripped)
                continue
            flush_table()
            if stripped.startswith("### "):
                flush_paragraph()
                flush_list()
                html_lines.append("<h3>%s</h3>" % html.escape(stripped[4:].strip()))
            elif stripped.startswith("## "):
                flush_paragraph()
                flush_list()
                html_lines.append("<h3>%s</h3>" % html.escape(stripped[3:].strip()))
            elif stripped.startswith("- "):
                flush_paragraph()
                if not in_list:
                    html_lines.append("<ul>")
                    in_list = True
                html_lines.append("<li>%s</li>" % html.escape(stripped[2:].strip()))
            elif stripped.startswith("> "):
                flush_paragraph()
                flush_list()
                html_lines.append("<blockquote>%s</blockquote>" % html.escape(stripped[2:].strip()))
            elif stripped.startswith("**") and stripped.endswith("**"):
                flush_paragraph()
                flush_list()
                html_lines.append("<p><strong>%s</strong></p>" % html.escape(stripped.strip("*")))
            elif stripped == "---":
                flush_paragraph()
                flush_list()
                html_lines.append("<hr />")
            else:
                paragraph_buffer.append(stripped)

        flush_paragraph()
        flush_list()
        flush_table()
        return "".join(html_lines)

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

    def _formatting_node_pdf_generator(self, markdown: str, html_path: Path, output_path: Path) -> Path:
        # CHANGED: 가능하면 HTML 기반으로 PDF를 만들고, 실패하면 기존 단순 PDF 생성기로 폴백.
        html_pdf = write_html_pdf(html_path, output_path)
        if html_pdf:
            return html_pdf
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

    def _overview_strategy_summary(self, strategy, highest_threat_by_technology: Dict[str, object]) -> str:
        if not strategy or not strategy.recommendations:
            return ""
        fragments = []
        for recommendation in strategy.recommendations[:3]:
            threat_entry = highest_threat_by_technology.get(recommendation.technology)
            threat_level = threat_entry.threat_level if threat_entry else recommendation.linked_threat_level
            fragments.append(
                "%s는 %s 위협 기준으로 %s 우선순위를 적용하고 %s"
                % (
                    recommendation.technology,
                    threat_level,
                    recommendation.priority,
                    self._shorten(recommendation.recommendation, 44),
                )
            )
        return "우선 실행 방향은 %s이다." % " / ".join(fragments)

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
            action = self._table_text(recommendation.recommendation) if recommendation else "-"
            lines.append("| %s | %s | %s | %s | %s |" % (technology, current_position, company_text, threat_level, action))
        return lines

    def _overview_headline(self, state: AgentState, highest_threat_by_technology: Dict[str, object]) -> str:
        primary_company = self._primary_company(state)
        technologies = state.get("target_technologies", [])
        if not technologies:
            return "%s의 핵심 메모리 기술 포트폴리오에 대한 전략 관점을 정리했다." % primary_company
        top_threat = max(
            (threat_rank(entry.threat_level), entry.threat_level) for entry in highest_threat_by_technology.values()
        ) if highest_threat_by_technology else None
        if top_threat:
            return "%s의 %s 전략을 중심으로 기술 성숙도와 경쟁 위협을 압축 정리했다. 현재 최고 위협 수준은 %s로 평가된다." % (
                primary_company,
                ", ".join(technologies[:3]),
                top_threat[1],
            )
        return "%s의 %s 전략을 중심으로 기술 성숙도와 경쟁 구도를 압축 정리했다." % (
            primary_company,
            ", ".join(technologies[:3]),
        )

    def _overview_takeaways(self, state: AgentState, highest_threat_by_technology: Dict[str, object]) -> List[str]:
        strategy = state.get("strategy_plan")
        takeaways: List[str] = []
        if strategy and strategy.recommendations:
            high_priority = [item.technology for item in strategy.recommendations if item.priority == "High"]
            if high_priority:
                takeaways.append("우선 투자 축은 %s이며, 단기 제품화 또는 실증 확보가 핵심 과제로 정리된다." % ", ".join(high_priority[:3]))
        if highest_threat_by_technology:
            crowded = [
                technology
                for technology, entry in highest_threat_by_technology.items()
                if threat_rank(entry.threat_level) >= threat_rank("Medium")
            ]
            if crowded:
                takeaways.append("%s는 경쟁 신호가 두드러져 비교 기업의 상용화 속도와 생태계 결속을 함께 추적해야 한다." % ", ".join(crowded[:3]))
        if state.get("technique_research"):
            tech_count = len(state["technique_research"].technology_briefs)
            takeaways.append("기술 조사는 %s개 핵심 영역을 기준으로 시장, TRL, 전략 판단까지 동일 축으로 연결했다." % tech_count)
        return takeaways[:3]

    def _market_company_table(self, market) -> List[str]:
        lines = [
            "| 기업 | 주요 활동 | 투자/경쟁 구도 단서 | 출처 |",
            "| --- | --- | --- | --- |",
        ]
        for company in market.selected_companies:
            evidence = market.company_findings.get(company, [])
            if evidence:
                item = evidence[0]
                activity = self._table_text(item.content)
                investment_hint = self._table_text(evidence[1].content) if len(evidence) > 1 else item.source_type
                citation = self._citation(item)
            else:
                activity = "-"
                investment_hint = "-"
                citation = "-"
            lines.append("| %s | %s | %s | %s |" % (company, activity, investment_hint, citation))
        return lines

    def _technology_snapshot_table(self, techniques) -> List[str]:
        lines = [
            "| 기술 | 핵심 요약 | 주요 포인트 | 최신성 메모 |",
            "| --- | --- | --- | --- |",
        ]
        for brief in techniques.technology_briefs.values():
            point = brief.key_points[0] if brief.key_points else (brief.core_claims[0] if brief.core_claims else "-")
            lines.append(
                "| %s | %s | %s | %s |"
                % (
                    brief.technology,
                    self._table_text(brief.summary),
                    self._table_text(point),
                    self._table_text(brief.freshness_note),
                )
            )
        return lines

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
                    self._table_text(self._safe_getattr(entry, "patent_activity_summary", entry.signal_summary)),
                    self._table_text(self._safe_getattr(entry, "patent_paper_link_summary", "제한적")),
                    self._table_text(self._safe_getattr(entry, "ecosystem_signal_summary", "제한적")),
                    entry.confidence,
                )
            )
        return lines

    def _patent_takeaways(self, state: AgentState, patent) -> List[str]:
        primary_company = self._primary_company(state)
        takeaways: List[str] = []
        primary_entries = [entry for entry in patent.entries if entry.company == primary_company][:3]
        for entry in primary_entries:
            citation = self._citation(entry.indirect_evidence[0]) if entry.indirect_evidence else ""
            takeaways.append(
                "%s는 %s에서 %s %s"
                % (
                    primary_company,
                    entry.technology,
                    self._shorten(entry.signal_summary, 110),
                    citation,
                )
            )
        if not takeaways and patent.entries:
            top_entry = patent.entries[0]
            takeaways.append(
                "%s에서 %s"
                % (
                    top_entry.technology,
                    self._shorten(top_entry.signal_summary, 120),
                )
            )
        return takeaways[:3]

    def _trl_table(self, trl) -> List[str]:
        lines = [
            "| 기업 | 기술 | TRL | 판정 근거 | confidence |",
            "| --- | --- | --- | --- | --- |",
        ]
        for entry in trl.entries:
            lines.append(
                "| %s | %s | %s | %s | %s |"
                % (entry.company, entry.technology, entry.trl_level, self._table_text(entry.reason), entry.confidence)
            )
        return lines

    def _trl_takeaways(self, state: AgentState, trl) -> List[str]:
        primary_company = self._primary_company(state)
        entries = [entry for entry in trl.entries if entry.company == primary_company]
        takeaways: List[str] = []
        for entry in entries[:3]:
            citation = self._citation(entry.supporting_evidence[0]) if entry.supporting_evidence else ""
            takeaways.append(
                "%s의 %s는 TRL %s로 평가되며, %s %s"
                % (
                    primary_company,
                    entry.technology,
                    entry.trl_level,
                    self._shorten(entry.reason, 110),
                    citation,
                )
            )
        return takeaways

    def _threat_table(self, threat) -> List[str]:
        lines = [
            "| 기업 | 기술 | 위협 수준 | 근거 요약 |",
            "| --- | --- | --- | --- |",
        ]
        for entry in threat.entries:
            lines.append("| %s | %s | %s | %s |" % (entry.company, entry.technology, entry.threat_level, self._table_text(entry.rationale)))
        return lines

    def _threat_takeaways(self, state: AgentState, threat) -> List[str]:
        primary_company = self._primary_company(state)
        selected_entries = [entry for entry in threat.entries if entry.company != primary_company]
        selected_entries.sort(key=lambda item: (threat_rank(item.threat_level), item.technology), reverse=True)
        takeaways: List[str] = []
        for entry in selected_entries[:3]:
            citation = self._citation(entry.supporting_evidence[0]) if entry.supporting_evidence else ""
            takeaways.append(
                "%s의 %s는 %s 위협으로 분류되며, %s %s"
                % (
                    entry.company,
                    entry.technology,
                    entry.threat_level,
                    self._shorten(entry.rationale, 110),
                    citation,
                )
            )
        return takeaways

    def _strategy_table(self, strategy) -> List[str]:
        lines = [
            "| 기술 | 우선순위 | 연계 위협 수준 | 실행 전략 |",
            "| --- | --- | --- | --- |",
        ]
        for item in strategy.recommendations:
            lines.append(
                "| %s | %s | %s | %s |"
                % (item.technology, item.priority, item.linked_threat_level, self._table_text(item.recommendation))
            )
        return lines

    def _strategy_action_plan(self, strategy) -> List[str]:
        actions: List[str] = []
        for item in strategy.recommendations[:3]:
            actions.append(
                "%s는 %s 우선순위로 %s. 판단 배경은 %s"
                % (
                    item.technology,
                    item.priority,
                    self._shorten(item.recommendation, 90),
                    self._shorten(item.rationale, 90),
                )
            )
        return actions

    def _primary_company(self, state: AgentState) -> str:
        companies = state.get("selected_companies", []) or state.get("candidate_companies", [])
        for company in companies:
            if company.lower().replace(" ", "") in {"skhynix", "sk하이닉스"}:
                return company
        return companies[0] if companies else "SK hynix"

    def _comparison_companies(self, state: AgentState) -> List[str]:
        primary_company = self._primary_company(state)
        companies = state.get("selected_companies", []) or state.get("candidate_companies", [])
        return [company for company in companies if company != primary_company]

    def _safe_getattr(self, entry: object, field_name: str, default: str) -> str:
        value = getattr(entry, field_name, default)
        return value if value else default

    def _table_text(self, text: str) -> str:
        return " ".join((text or "").replace("|", "/").split())

    def _shorten(self, text: str, limit: int) -> str:
        return " ".join((text or "").split())
