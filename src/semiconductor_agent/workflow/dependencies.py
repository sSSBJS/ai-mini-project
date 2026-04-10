from __future__ import annotations

from semiconductor_agent.agent_nodes.base import AgentDependencies
from semiconductor_agent.rag import CorpusRegistry
from semiconductor_agent.runtime import RuntimeConfig
from semiconductor_agent.search import WebSearchClient


def build_agent_dependencies(runtime: RuntimeConfig) -> AgentDependencies:
    return AgentDependencies(
        runtime=runtime,
        corpora=CorpusRegistry(runtime),
        web_search=WebSearchClient(runtime.enable_web_search),
    )
