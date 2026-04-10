from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import date
from pathlib import Path

from semiconductor_agent.agent_nodes.base import AgentDependencies
from semiconductor_agent.agent_nodes.trl import TRLAssessmentAgent
from semiconductor_agent.models import (
    BalancedSearchPlan,
    EvidenceItem,
    MarketResearchResult,
    PatentInnovationSignalResult,
    PatentSignalEntry,
    TechniqueResearchResult,
    TechnologyBrief,
)
from semiconductor_agent.rag import CorpusRegistry
from semiconductor_agent.runtime import RuntimeConfig
from semiconductor_agent.shared_standards import SHARED_STANDARDS
from semiconductor_agent.search import WebSearchClient


def _search_plan() -> BalancedSearchPlan:
    return BalancedSearchPlan(
        hypothesis="PIM의 현재 기술 성숙도는 파일럿 검증 구간에 가까울 수 있다.",
        confirming_query="PIM pilot validation official announcement",
        opposing_query="PIM limitation delay bottleneck",
        objective_query="PIM roadmap benchmark specification",
    )


class RecordingCorpora:
    def __init__(self, inner: CorpusRegistry):
        self.inner = inner
        self.queries = []

    def search(self, corpus_name: str, query: str, top_k: int = 4):
        self.queries.append((corpus_name, query, top_k))
        return self.inner.search(corpus_name, query, top_k=top_k)


def build_demo_state() -> dict:
    technique_evidence = [
        EvidenceItem(
            title="PIM roadmap",
            content="PIM architecture validation with benchmark and prototype performance results, including system integration tests.",
            source_path="reference/research/PIM.pdf",
            source_type="paper",
            published_at=date(2025, 1, 1),
        ),
        EvidenceItem(
            title="PIM standard",
            content="PIM standardization progress and interoperability requirements with memory-system level validation needs.",
            source_path="reference/research/PIM_표준.pdf",
            source_type="standard",
            published_at=date(2024, 7, 1),
        ),
    ]
    signal_evidence = [
        EvidenceItem(
            title="Vendor partnership",
            content="Acme announced a packaging partnership and pilot validation milestone for PIM.",
            source_path="reference/research/PIM.pdf",
            source_type="company",
            published_at=date(2025, 2, 1),
        ),
        EvidenceItem(
            title="Commercialization note",
            content="Pilot customer evaluation and commercialization preparation are underway for Acme PIM.",
            source_path="reference/research/PIM.pdf",
            source_type="report",
            published_at=date(2025, 3, 1),
        ),
        EvidenceItem(
            title="Pilot investment",
            content="Acme increased pilot line investment for PIM validation, packaging, and yield learning.",
            source_path="reference/research/PIM.pdf",
            source_type="report",
            published_at=date(2025, 4, 1),
        ),
    ]

    return {
        "selected_companies": ["Acme"],
        "target_technologies": ["PIM"],
        "shared_standards": SHARED_STANDARDS,
        "market_research": MarketResearchResult(
            selected_companies=["Acme"],
            market_summary="Acme는 PIM 상용화 가능성을 검토 중이며 고객 평가 단계 진입을 준비한다.",
            company_findings={"Acme": signal_evidence[:1]},
            latest_articles=signal_evidence[:1],
            search_plan=_search_plan(),
        ),
        "technique_research": TechniqueResearchResult(
            technology_briefs={
                "PIM": TechnologyBrief(
                    technology="PIM",
                    summary="PIM은 최근 프로토타입 및 검증 흐름이 확인되며 시스템 통합 관점의 성숙도 판단이 필요하다.",
                    key_points=["메모리 내 연산 구조 검증", "시스템 통합 실험 진행"],
                    core_claims=["실험실 단계를 넘는 파일럿 검증 신호가 있다."],
                    supporting_evidence=technique_evidence,
                    expansion_keywords=["PIM", "PIM roadmap"],
                    freshness_note="가장 최근 근거 기준일: 2025-02-01",
                )
            },
            evidence_validation_issues=[],
            search_plan=_search_plan(),
        ),
        "patent_innovation_signal": PatentInnovationSignalResult(
            entries=[
                PatentSignalEntry(
                    technology="PIM",
                    company="Acme",
                    signal_summary="Acme의 PIM 파일럿 검증과 파트너십 신호를 수집했다.",
                    indirect_evidence=signal_evidence,
                    confidence="high",
                    estimated=False,
                )
            ],
            search_plan=_search_plan(),
        ),
    }


def build_real_agent(project_root: Path) -> tuple[TRLAssessmentAgent, RecordingCorpora]:
    # 데모 실행 시 LangSmith 네트워크 경고가 결과를 가리지 않도록 tracing을 끈다.
    os.environ["LANGSMITH_TRACING"] = "false"
    runtime = RuntimeConfig.from_env(project_root)
    runtime.output_dir = Path(tempfile.mkdtemp(prefix="semiconductor-trl-demo-"))
    corpora = RecordingCorpora(CorpusRegistry(runtime))
    dependencies = AgentDependencies(
        runtime=runtime,
        corpora=corpora,
        web_search=WebSearchClient(enabled=False),
    )
    return TRLAssessmentAgent(dependencies), corpora


def run_demo() -> dict:
    project_root = Path(__file__).resolve().parents[1]
    agent, corpora = build_real_agent(project_root)
    state = build_demo_state()

    if not agent.llm_enabled:
        raise RuntimeError(
            "실제 LLM 경로가 활성화되지 않았습니다. .env 또는 환경 변수에 OPENAI_API_KEY가 있는지 확인하세요."
        )

    result = agent.run(state)
    return {
        "mode": "real_llm",
        "llm_debug": agent.last_run_debug,
        "input_state_summary": {
            "selected_companies": state["selected_companies"],
            "target_technologies": state["target_technologies"],
            "market_summary": state["market_research"].market_summary,
            "technique_summary": state["technique_research"].technology_briefs["PIM"].summary,
            "patent_signal_summary": state["patent_innovation_signal"].entries[0].signal_summary,
        },
        "trl_queries": corpora.queries,
        "trl_assessment_result": result["trl_assessment"].model_dump(mode="json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the TRL agent with temporary demo state and a real OpenAI LLM."
    )
    parser.parse_args()

    payload = run_demo()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
