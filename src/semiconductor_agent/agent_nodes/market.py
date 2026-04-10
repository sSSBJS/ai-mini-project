from __future__ import annotations

from typing import Dict

from semiconductor_agent.agent_nodes.base import BaseWorkflowAgent
from semiconductor_agent.models import MarketResearchResult
from semiconductor_agent.search import build_balanced_search_plan, validate_search_balance
from semiconductor_agent.state import AgentState


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
        }
