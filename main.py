#!/usr/bin/env python3
"""
Semiconductor strategy workflow runner.
Usage: python main.py [--query "your query"] [--output output_dir]
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from semiconductor_agent.runtime import RuntimeConfig
from semiconductor_agent.workflow.builder import build_agent_graph, create_default_state


def _to_jsonable(value):
    if hasattr(value, "model_dump"):
        return _to_jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def main():
    parser = argparse.ArgumentParser(
        description="Run the semiconductor strategy analysis workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --query "Analyze TSMC's AI chip strategy"
  python main.py --output ./results
        """,
    )
    parser.add_argument(
        "--query",
        default="반도체 산업의 기술 트렌드와 전략을 분석해주세요.",
        help="User query for the analysis (default: 반도체 산업의 기술 트렌드와 전략을 분석해주세요.)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output directory for results (default: outputs/)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed workflow progress",
    )

    args = parser.parse_args()

    # Setup output directory
    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = Path("outputs")
    
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n🚀 Semiconductor Strategy Workflow")
    print(f"{'=' * 60}")
    print(f"Query: {args.query}")
    print(f"Output: {output_dir.resolve()}")
    print(f"{'=' * 60}\n")

    # Build workflow
    print("Building workflow graph...")
    try:
        runtime = RuntimeConfig.from_env(Path.cwd())
        graph = build_agent_graph(runtime)
        print("✓ Workflow graph built\n")
    except Exception as e:
        print(f"✗ Failed to build workflow: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    # Create initial state
    print("Initializing workflow state...")
    try:
        state = create_default_state(
            user_query=args.query,
            project_root=Path.cwd(),
            output_dir=output_dir,
        )
        print("✓ State initialized\n")
    except Exception as e:
        print(f"✗ Failed to initialize state: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    # Run workflow
    print("Starting workflow execution...\n")
    try:
        result = graph.invoke(state)
        
        # Save results
        output_file = output_dir / "workflow_result.json"
        with open(output_file, "w", encoding="utf-8") as f:
            serializable_result = _to_jsonable(result)
            json.dump(serializable_result, f, ensure_ascii=False, indent=2)
        
        print(f"\n{'=' * 60}")
        print(f"✓ Workflow completed successfully!")
        print(f"Results saved to: {output_file}")
        print(f"{'=' * 60}\n")

        # Print summary
        if "report_artifact" in result and result["report_artifact"]:
            report = result["report_artifact"]
            print(f"📄 Report generated:")
            print(f"  - PDF: {report.pdf_path}")
            print(f"  - Markdown: {report.markdown_path}")
            if hasattr(report, "metrics") and report.metrics:
                print(f"  - Coverage rate: {report.metrics.completeness_rate:.1%}")

        return 0

    except KeyboardInterrupt:
        print(f"\n\n⚠️  Workflow interrupted by user")
        return 130
    except Exception as e:
        print(f"\n✗ Workflow failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
