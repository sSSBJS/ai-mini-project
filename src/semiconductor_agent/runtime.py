from __future__ import annotations

import os
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
    use_llm_supervisor_review: bool = True
    research_reference_dir: Optional[Path] = None
    trl_reference_dir: Optional[Path] = None
    embedding_model_name: str = "intfloat/multilingual-e5-large-instruct"

    @classmethod
    def from_env(cls, project_root: Path) -> "RuntimeConfig":
        load_dotenv_file(project_root / ".env")
        output_dir = Path(os.getenv("OUTPUT_DIR", project_root / "outputs")).expanduser()
        research_dir = os.getenv("RESEARCH_REFERENCE_DIR")
        trl_dir = os.getenv("TRL_REFERENCE_DIR")
        return cls(
            project_root=project_root,
            output_dir=output_dir,
            enable_web_search=_get_bool_env("ENABLE_WEB_SEARCH", False),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            use_llm_planning=_get_bool_env("USE_LLM_PLANNING", False),
            use_llm_supervisor_review=_get_bool_env("USE_LLM_SUPERVISOR_REVIEW", True),
            research_reference_dir=Path(research_dir).expanduser() if research_dir else None,
            trl_reference_dir=Path(trl_dir).expanduser() if trl_dir else None,
            embedding_model_name=os.getenv("EMBEDDING_MODEL_NAME", "intfloat/multilingual-e5-large-instruct"),
        )

    def resolve_reference_dir(self, corpus_name: str) -> Path:
        if corpus_name == "research":
            return self.research_reference_dir or self.project_root / "reference" / "research"
        if corpus_name == "trl":
            return self.trl_reference_dir or self.project_root / "reference" / "trl"
        raise ValueError("Unknown corpus: %s" % corpus_name)


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
