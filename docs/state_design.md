# State Design

PDF 요구사항을 기준으로 상태는 Supervisor가 모든 승인/반려 결정을 내릴 수 있게 최소하지만 충분한 정보만 담도록 설계했다.

핵심 필드

- `target_technologies`: PDF 범위에 고정된 `HBM4`, `PIM`, `CXL`
- `candidate_companies`: PDF에 명시된 주요 경쟁사 후보군
- `selected_companies`: T1 시장 조사 결과로 확정된 비교 대상
- `shared_standards`: PDF의 `SHARED_STANDARDS` 전체
- `search_budget_limit`, `search_count`: 검색 5회 이내 제약
- `retry_limits`, `retry_counts`: T2 재검색 최대 2회, T1 품질 검증 1회, 전략/보고서 재출력 1회
- `market_research`, `technique_research`, `patent_innovation_signal`, `trl_assessment`, `threat_evaluation`, `strategy_plan`, `report_artifact`: 각 Agent 산출물
- `approvals`: Supervisor가 승인한 단계와 교차 검증 완료 여부
- `validation_issues`: 내부 검증 노드와 Supervisor 검토에서 누적된 이슈
- `supervisor_log`: 승인/반려와 재실행 결정 이력

왜 이 State가 효과적인가

- 그래프 노드는 7개 Agent와 Supervisor만 두고, 하위 검증 노드는 Agent 내부 함수로만 유지해 PDF 아키텍처를 그대로 보존한다.
- Supervisor가 `coverage`, `TRL consistency`, `strategy alignment`, `report alignment`를 모두 상태만으로 판단할 수 있다.
- RAG 결과, 검증 이슈, 재시도 횟수가 한 상태에 있어 재실행 판단이 단순해진다.
- 보고서 품질 지표까지 상태에 남겨 최종 채택 여부를 중앙에서 결정할 수 있다.
