from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RuntimeConfig:
    project_root: Path
    output_dir: Path
    enable_web_search: bool = False
    openai_model: str = "gpt-4o-mini"
    use_llm_planning: bool = False
    research_reference_dir: Optional[Path] = None
    trl_reference_dir: Optional[Path] = None
    embedding_model_name: str = "intfloat/multilingual-e5-large-instruct"

    def resolve_reference_dir(self, corpus_name: str) -> Path:
        if corpus_name == "research":
            return self.research_reference_dir or self.project_root / "reference" / "research"
        if corpus_name == "trl":
            return self.trl_reference_dir or self.project_root / "reference" / "trl"
        raise ValueError("Unknown corpus: %s" % corpus_name)
