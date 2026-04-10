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

구조

- `src/semiconductor_agent/agents.py`: 호환용 얇은 export 레이어
- `src/semiconductor_agent/agent_nodes/base.py`: 공통 의존성, 공통 helper
- `src/semiconductor_agent/agent_nodes/market.py`: 시장/경쟁사 조사
- `src/semiconductor_agent/agent_nodes/technique.py`: 기술 조사 + Evidence Validation Node
- `src/semiconductor_agent/agent_nodes/patent.py`: 특허/혁신 신호
- `src/semiconductor_agent/agent_nodes/trl.py`: TRL 판정
- `src/semiconductor_agent/agent_nodes/threat.py`: 위협 평가
- `src/semiconductor_agent/agent_nodes/strategy.py`: 전략 수립 + Strategy Validate Node
- `src/semiconductor_agent/agent_nodes/report.py`: 보고서 작성 + Report Validate Node + PDF 생성
- `src/semiconductor_agent/agent_nodes/supervisor.py`: 중앙집중형 Supervisor

환경 변수

- 루트의 [.env.example](/Users/jisung/Documents/skala/ai-service/ai-mini-project/.env.example)를 복사해 `.env`로 사용하면 됩니다.
- `RuntimeConfig.from_env(...)`는 루트의 `.env`를 자동으로 읽습니다.
- 기본 실행만 할 경우 `OPENAI_API_KEY` 없이도 동작합니다.
- `OPENAI_API_KEY`는 `USE_LLM_PLANNING=true` 또는 OpenAI 기반 planning을 쓸 때 필요합니다.
- LangSmith 추적은 `.env`에 `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY=...`를 넣으면 바로 활성화됩니다.
- `LANGSMITH_PROJECT`로 프로젝트 이름을 지정할 수 있습니다.
- `USE_LLM_SUPERVISOR_REVIEW=true`이면 Supervisor가 각 단계 산출물을 LLM으로 다시 검토합니다. API 키가 없으면 규칙 기반 검토로 자동 fallback 됩니다.

실행 예시

```bash
PYTHONPATH=src python3 - <<'PY'
from pathlib import Path
from semiconductor_agent.graph import build_agent_graph
from semiconductor_agent.runtime import RuntimeConfig
from semiconductor_agent.state import create_initial_state

runtime = RuntimeConfig.from_env(Path('.').resolve())
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

LangSmith 사용 예시

```bash
cp .env.example .env
```

`.env`에서 아래 값만 채우면 LangGraph/LangChain 실행이 LangSmith에 추적됩니다.

```env
OPENAI_API_KEY=...
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=semiconductor-langgraph-agent
```
