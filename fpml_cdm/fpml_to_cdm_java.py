from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Tuple

from .java_gen.tools import json_stem_to_java_class_name
from .mapping_agent.agent import MappingAgentConfig, MappingAgentResult, run_mapping_agent
from .pipeline import convert_fpml_to_cdm
from .transformer import transform_to_cdm_v6
from .parser import parse_fpml_fx
from .types import ConversionResult


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def generate_java_from_fpml(
    fpml_path: str,
    *,
    llm_client: object,
    mapping_model: str,
    java_model: str,
    mapping_enabled: bool = True,
    mapping_config: Optional[MappingAgentConfig] = None,
    java_config: Optional[object] = None,
    log_progress: Optional[bool] = None,
    output_dir: str = "tmp",
    java_class_name: Optional[str] = None,
) -> Tuple[object, Optional[MappingAgentResult], Path]:
    """
    End-to-end:
      FpML XML → deterministic parse/transform → validate
      If not valid: run mapping-agent loop to get best CDM JSON
      CDM JSON → Java codegen agent → ``generated/<ClassName>.java`` (class from FpML
      filename stem unless ``java_class_name`` is set).

    Returns:
      (java_agent_result, mapping_agent_result|None, cdm_json_path)
    """
    # Phase A: deterministic first pass.
    conv: ConversionResult = convert_fpml_to_cdm(fpml_path, strict=True, llm_provider=None)
    mapping_result: Optional[MappingAgentResult] = None

    if conv.ok and conv.cdm is not None:
        best_cdm_json = conv.cdm
    else:
        if not mapping_enabled:
            if conv.cdm is None:
                # Fall back to strict=False parse; if this fails, let it raise.
                normalized = parse_fpml_fx(fpml_path, strict=False)
                best_cdm_json = transform_to_cdm_v6(normalized)
            else:
                best_cdm_json = conv.cdm
        else:
            mapping_result = run_mapping_agent(
                fpml_path,
                llm_client=llm_client,
                model=mapping_model,
                config=mapping_config,
                log_progress=log_progress,
            )
            best_cdm_json = mapping_result.best_cdm_json

    output_path = Path(output_dir)
    cdm_json_path = output_path / "generated_expected_cdm.json"
    _write_json(cdm_json_path, best_cdm_json)

    # Phase C: Java codegen with existing agent.
    from .java_gen.agent import AgentConfig, run_agent

    if java_config is None:
        java_cfg = AgentConfig()
    else:
        java_cfg = java_config

    resolved_java_class = (
        java_class_name.strip()
        if java_class_name is not None and java_class_name.strip()
        else json_stem_to_java_class_name(Path(fpml_path).stem)
    )

    java_result = run_agent(
        cdm_json_path=str(cdm_json_path),
        llm_client=llm_client,
        model=java_model,
        config=java_cfg,
        log_progress=log_progress,
        java_class_name=resolved_java_class,
    )

    return java_result, mapping_result, cdm_json_path

