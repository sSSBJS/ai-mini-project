# Semiconductor LangGraph Agent

PDF 설계 문서의 구조를 그대로 옮긴 LangGraph 워크플로우입니다.

구현 범위

- 상단 아키텍처의 7개 Agent를 그대로 그래프 노드로 구성
- `Supervisor Agent` 중심의 중앙집중형 라우팅과 승인/반려
- `Technique Research Collector Agent` 내부의 `Evidence Validation Node`
- `Strategy Planner Agent` 내부의 `Strategy Validate Node`
- `Report Writer Agent` 내부의 `Report Validate Node`, `Formatting Node - PDF Generator`
- `reference/research`, `reference/trl`를 사용하는 Hybrid RAG
- PDF의 `SHARED_STANDARDS` 기반 TRL 판정 규칙

실행 예시

```bash
PYTHONPATH=src python3 - <<'PY'
from pathlib import Path
from semiconductor_agent.graph import build_agent_graph
from semiconductor_agent.runtime import RuntimeConfig
from semiconductor_agent.state import create_initial_state

runtime = RuntimeConfig(
    project_root=Path('.').resolve(),
    output_dir=Path('outputs').resolve(),
    enable_web_search=False,
)
graph = build_agent_graph(runtime)
state = create_initial_state(
    'HBM4, PIM, CXL 기술 전략 분석 보고서를 생성한다.',
    output_dir=runtime.output_dir,
)
result = graph.invoke(state)
print(result["report_artifact"].markdown_path)
print(result["report_artifact"].pdf_path)
PY
```

검증

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
