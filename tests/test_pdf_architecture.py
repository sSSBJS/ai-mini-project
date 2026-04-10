import tempfile
import unittest
from pathlib import Path

from semiconductor_agent.agent_nodes.report import ReportWriterAgent
from semiconductor_agent.agent_nodes.strategy import StrategyPlannerAgent
from semiconductor_agent.agent_nodes.technique import TechniqueResearchCollectorAgent
from semiconductor_agent.runtime import RuntimeConfig
from semiconductor_agent.shared_standards import DEFAULT_TECHNOLOGIES, SHARED_STANDARDS
from semiconductor_agent.state import create_initial_state
from semiconductor_agent.workflow.builder import build_agent_graph


class PdfArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).resolve().parents[1]
        self.output_dir = Path(tempfile.mkdtemp(prefix="semiconductor-agent-tests-"))
        self.runtime = RuntimeConfig(
            project_root=self.project_root,
            output_dir=self.output_dir,
            enable_web_search=False,
        )

    def test_graph_matches_pdf_topology(self):
        graph = build_agent_graph(self.runtime)
        langgraph_graph = graph.get_graph()
        expected_nodes = {
            "__start__",
            "research_sync",
            "supervisor",
            "market_research",
            "technique_research",
            "patent_innovation_signal",
            "trl_assessment",
            "threat_evaluation",
            "strategy_planner",
            "report_writer",
            "__end__",
        }
        self.assertEqual(set(langgraph_graph.nodes.keys()), expected_nodes)

        edges = {(edge.source, edge.target) for edge in langgraph_graph.edges}
        for worker in [
            "patent_innovation_signal",
            "trl_assessment",
            "threat_evaluation",
            "strategy_planner",
            "report_writer",
        ]:
            self.assertIn(("supervisor", worker), edges)
            self.assertIn((worker, "supervisor"), edges)
        self.assertIn(("__start__", "market_research"), edges)
        self.assertIn(("__start__", "technique_research"), edges)
        self.assertIn(("market_research", "research_sync"), edges)
        self.assertIn(("technique_research", "research_sync"), edges)
        self.assertIn(("research_sync", "supervisor"), edges)

    def test_state_and_standards_follow_pdf_constraints(self):
        state = create_initial_state("test", output_dir=self.output_dir)
        self.assertEqual(state["target_technologies"], DEFAULT_TECHNOLOGIES)
        self.assertEqual(state["search_budget_limit"], 5)
        self.assertEqual(set(SHARED_STANDARDS["trl_scale"].keys()), set(range(1, 10)))
        self.assertIn("range_1_3", SHARED_STANDARDS["trl_evidence_rules"])
        self.assertIn("range_4_6", SHARED_STANDARDS["trl_evidence_rules"])
        self.assertIn("range_7_9", SHARED_STANDARDS["trl_evidence_rules"])

    def test_validation_nodes_are_embedded_in_agents(self):
        self.assertTrue(hasattr(TechniqueResearchCollectorAgent, "_evidence_validation_node"))
        self.assertTrue(hasattr(StrategyPlannerAgent, "_strategy_validate_node"))
        self.assertTrue(hasattr(ReportWriterAgent, "_report_validate_node"))
        self.assertTrue(hasattr(ReportWriterAgent, "_formatting_node_pdf_generator"))

    def test_workflow_runs_end_to_end_and_generates_report(self):
        graph = build_agent_graph(self.runtime)
        state = create_initial_state(
            "HBM4, PIM, CXL 기술 전략 분석 보고서를 생성한다.",
            output_dir=self.output_dir,
        )
        result = graph.invoke(state)

        self.assertEqual(result["next_step"], "end")
        self.assertTrue(Path(result["report_artifact"].markdown_path).exists())
        self.assertTrue(Path(result["report_artifact"].pdf_path).exists())
        self.assertGreaterEqual(result["report_artifact"].metrics.passed_criteria, 3)
        self.assertGreaterEqual(len(result["supervisor_log"]), 6)


if __name__ == "__main__":
    unittest.main()
