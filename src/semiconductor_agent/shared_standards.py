from __future__ import annotations

SHARED_STANDARDS = {
    "trl_scale": {
        1: "기초 연구 — 원리 관찰 및 보고",
        2: "기술 개념 정립 — 응용 가능성 확인",
        3: "개념 검증 — 실험실 수준 PoC",
        4: "실험실 검증 — 소규모 프로토타입",
        5: "관련 환경 검증 — 파일럿 수준",
        6: "실증 환경 검증 — 시스템 프로토타입",
        7: "운용 환경 시연 — 초도 양산 준비",
        8: "시스템 완성 — 양산 검증",
        9: "실증 완료 — 상용 배포",
    },
    "trl_evidence_rules": {
        "range_1_3": {
            "label": "논문 · 학술발표 중심",
            "primary_sources": [
                "peer-reviewed 논문 (Nature, Science, IEEE 등)",
                "학술대회 발표 (DAC, MICRO, ASPLOS 등)",
                "arXiv 프리프린트",
                "대학 · 연구소 기술보고서",
            ],
            "secondary_sources": [],
            "excluded_sources": [
                "특허 (아직 출원 전이거나 연구 단계)",
                "제품 발표 · IR 자료",
                "채용 공고",
            ],
            "confidence_floor": "low",
            "note": "논문 실측 데이터 없이 이론만 있으면 TRL 1~2로 제한",
        },
        "range_4_6": {
            "label": "간접 지표 기반 추정",
            "primary_sources": [
                "특허 출원 패턴 (출원 건수 추이, 청구항 유형 변화)",
                "특허-논문 연결 (출원일-발표일 시간차)",
                "산업 학회 발표 (ISSCC, IEDM, Hot Chips) + 실측값 포함 여부",
                "채용 공고 키워드 (Research → Process → Yield 직군 이동)",
                "표준화 단체 참여 등급 (JEDEC, CXL Consortium 등)",
                "파트너십 · MOU · 공동 테이프아웃 공시",
                "스타트업 인수 · 전략적 투자",
            ],
            "secondary_sources": [
                "비특허문헌 (NPL) 인용 빈도",
                "학회 공동저자 기관 변화 (대학 → 기업)",
            ],
            "excluded_sources": [
                "제품 출시 발표",
                "고객 납품 공시",
                "양산 수율 데이터",
            ],
            "confidence_floor": "medium",
            "aggregation_rule": "primary_sources 중 3개 이상 일치 시 해당 구간 확정. 2개 이하면 confidence를 low로 하향",
            "conflict_rule": "동일 기술에 대해 출처 간 TRL 추정치 차이가 2 이상일 경우 상충 사실 명시 후 보수적 하한값 채택, 채택 근거 1줄 기재",
            "note": "직접 증거 없음을 전제로 추정임을 반드시 명시",
        },
        "range_7_9": {
            "label": "기업 발표 · 제품화 · 상용화 신호 중심",
            "primary_sources": [
                "기업 공식 제품 발표 (Press Release, IR)",
                "고객사 적용 사례 공시",
                "양산 개시 · 출하 공시",
                "상용 제품 스펙시트 공개",
            ],
            "secondary_sources": [
                "산업 분석 리포트 (TechInsights, IDC, Gartner)",
                "고객사 레퍼런스 언급",
                "매출 기여 공시 (실적발표)",
            ],
            "excluded_sources": [
                "특허 단독 (TRL 7+ 판정 불가)",
                "논문 단독 (TRL 7+ 판정 불가)",
                "채용 공고",
            ],
            "confidence_floor": "high",
            "note": "양산 공시 없이 TRL 8~9 판정 금지. 고객 적용 사례만 있으면 최대 TRL 7",
        },
    },
}

DEFAULT_TECHNOLOGIES = ["HBM4", "PIM", "CXL"]
DEFAULT_CANDIDATE_COMPANIES = [
    "SK hynix",
    "Samsung Electronics",
    "Micron",
    "NVIDIA",
]
REPORT_SECTION_SEQUENCE = [
    "OVERVIEW",
    "분석 배경",
    "핵심 기술 현황",
    "TRL 기반 기술 성숙도 분석",
    "경쟁 위협 수준 평가",
    "전략적 방향 및 대응제안",
    "REFERENCE",
]
