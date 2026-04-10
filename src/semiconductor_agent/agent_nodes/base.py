from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from semiconductor_agent.models import EvidenceItem, ValidationIssue
from semiconductor_agent.rag import CorpusRegistry
from semiconductor_agent.runtime import RuntimeConfig
from semiconductor_agent.search import WebSearchClient
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


def can_retry(step_name: str, state: AgentState, retry_counts: dict) -> bool:
    limit = state.get("retry_limits", {}).get(step_name, 0)
    return retry_counts.get(step_name, 0) < limit


def threat_rank(threat_level: str) -> int:
    return {"Low": 1, "Medium": 2, "High": 3}.get(threat_level, 0)
