from __future__ import annotations

from pathlib import Path
from typing import Dict

from langgraph.graph import END, START, StateGraph

from semiconductor_agent.agents import (
    AgentDependencies,
    MarketResearchCollectorAgent,
    PatentInnovationSignalAgent,
    ReportWriterAgent,
    StrategyPlannerAgent,
    SupervisorAgent,
    TechniqueResearchCollectorAgent,
    ThreatEvaluationAgent,
    TRLAssessmentAgent,
)
from semiconductor_agent.rag import CorpusRegistry
from semiconductor_agent.runtime import RuntimeConfig
from semiconductor_agent.search import WebSearchClient
from semiconductor_agent.state import AgentState, create_initial_state


def build_agent_graph(runtime: RuntimeConfig):
    dependencies = AgentDependencies(
        runtime=runtime,
        corpora=CorpusRegistry(runtime),
        web_search=WebSearchClient(runtime.enable_web_search),
    )

    supervisor = SupervisorAgent(dependencies)
    market = MarketResearchCollectorAgent(dependencies)
    technique = TechniqueResearchCollectorAgent(dependencies)
    patent = PatentInnovationSignalAgent(dependencies)
    trl = TRLAssessmentAgent(dependencies)
    threat = ThreatEvaluationAgent(dependencies)
    strategy = StrategyPlannerAgent(dependencies)
    report = ReportWriterAgent(dependencies)

    builder = StateGraph(AgentState)
    builder.add_node("supervisor", supervisor.review_and_route)
    builder.add_node("market_research", market.run)
    builder.add_node("technique_research", technique.run)
    builder.add_node("patent_innovation_signal", patent.run)
    builder.add_node("trl_assessment", trl.run)
    builder.add_node("threat_evaluation", threat.run)
    builder.add_node("strategy_planner", strategy.run)
    builder.add_node("report_writer", report.run)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        lambda state: state.get("next_step", "end"),
        {
            "market_research": "market_research",
            "technique_research": "technique_research",
            "patent_innovation_signal": "patent_innovation_signal",
            "trl_assessment": "trl_assessment",
            "threat_evaluation": "threat_evaluation",
            "strategy_planner": "strategy_planner",
            "report_writer": "report_writer",
            "end": END,
        },
    )
    for node_name in [
        "market_research",
        "technique_research",
        "patent_innovation_signal",
        "trl_assessment",
        "threat_evaluation",
        "strategy_planner",
        "report_writer",
    ]:
        builder.add_edge(node_name, "supervisor")

    compiled = builder.compile()
    compiled.runtime = runtime
    return compiled


def create_default_state(user_query: str, project_root: Path, output_dir: Path) -> AgentState:
    return create_initial_state(user_query=user_query, output_dir=output_dir)
