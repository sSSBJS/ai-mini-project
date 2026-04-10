from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Tuple

from semiconductor_agent.agent_nodes.base import AgentDependencies
from semiconductor_agent.agent_nodes.market import MarketResearchCollectorAgent
from semiconductor_agent.agent_nodes.patent import PatentInnovationSignalAgent
from semiconductor_agent.agent_nodes.report import ReportWriterAgent
from semiconductor_agent.agent_nodes.strategy import StrategyPlannerAgent
from semiconductor_agent.agent_nodes.supervisor import SupervisorAgent
from semiconductor_agent.agent_nodes.technique import TechniqueResearchCollectorAgent
from semiconductor_agent.agent_nodes.threat import ThreatEvaluationAgent
from semiconductor_agent.agent_nodes.trl import TRLAssessmentAgent
from semiconductor_agent.state import AgentState

GraphNodeCallable = Callable[[AgentState], Dict[str, object]]


@dataclass(frozen=True)
class SupervisorTeam:
    supervisor: SupervisorAgent
    market_research: MarketResearchCollectorAgent
    technique_research: TechniqueResearchCollectorAgent
    patent_innovation_signal: PatentInnovationSignalAgent
    trl_assessment: TRLAssessmentAgent
    threat_evaluation: ThreatEvaluationAgent
    strategy_planner: StrategyPlannerAgent
    report_writer: ReportWriterAgent

    @classmethod
    def create(cls, dependencies: AgentDependencies) -> "SupervisorTeam":
        return cls(
            supervisor=SupervisorAgent(dependencies),
            market_research=MarketResearchCollectorAgent(dependencies),
            technique_research=TechniqueResearchCollectorAgent(dependencies),
            patent_innovation_signal=PatentInnovationSignalAgent(dependencies),
            trl_assessment=TRLAssessmentAgent(dependencies),
            threat_evaluation=ThreatEvaluationAgent(dependencies),
            strategy_planner=StrategyPlannerAgent(dependencies),
            report_writer=ReportWriterAgent(dependencies),
        )

    def graph_nodes(self) -> Dict[str, GraphNodeCallable]:
        return {
            "supervisor": self.supervisor.review_and_route,
            "market_research": self.market_research.run,
            "technique_research": self.technique_research.run,
            "patent_innovation_signal": self.patent_innovation_signal.run,
            "trl_assessment": self.trl_assessment.run,
            "threat_evaluation": self.threat_evaluation.run,
            "strategy_planner": self.strategy_planner.run,
            "report_writer": self.report_writer.run,
        }

    def worker_names(self) -> Tuple[str, ...]:
        return tuple(name for name in self.graph_nodes() if name != "supervisor")
