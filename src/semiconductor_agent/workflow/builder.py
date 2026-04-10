from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from semiconductor_agent.runtime import RuntimeConfig
from semiconductor_agent.state import AgentState, create_initial_state
from semiconductor_agent.workflow.dependencies import build_agent_dependencies
from semiconductor_agent.workflow.team import SupervisorTeam


def build_supervisor_team(runtime: RuntimeConfig) -> SupervisorTeam:
    dependencies = build_agent_dependencies(runtime)
    return SupervisorTeam.create(dependencies)


def build_agent_graph(runtime: RuntimeConfig):
    team = build_supervisor_team(runtime)
    builder = StateGraph(AgentState)

    for node_name, node_callable in team.graph_nodes().items():
        builder.add_node(node_name, node_callable)
    builder.add_node("research_sync", _research_sync)

    builder.add_edge(START, "market_research")
    builder.add_edge(START, "technique_research")
    builder.add_edge("market_research", "research_sync")
    builder.add_edge("technique_research", "research_sync")
    builder.add_edge("research_sync", "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        _next_step,
        {**{name: name for name in team.worker_names()}, "end": END},
    )
    for node_name in team.worker_names():
        if node_name in {"market_research", "technique_research"}:
            continue
        builder.add_edge(node_name, "supervisor")

    compiled = builder.compile()
    compiled.runtime = runtime
    compiled.supervisor_team = team
    return compiled


def create_default_state(user_query: str, project_root: Path, output_dir: Path) -> AgentState:
    return create_initial_state(user_query=user_query, output_dir=output_dir)


def _next_step(state: AgentState) -> str:
    return state.get("next_step", "end")


def _research_sync(state: AgentState) -> AgentState:
    return {
        "last_completed_step": "parallel_research",
    }
