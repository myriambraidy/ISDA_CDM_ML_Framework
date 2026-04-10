"""Mapping agent: FpML -> CDM v6 via LLM-driven ruleset patches or LLM-native CDM submit."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .classifier import ClassifierResult, classify_fpml
from .prompt_builder import build_bootstrap_user_message, build_system_prompt
from .registry import ToolRegistry, ToolSpec
from .skill_store import SkillMeta, load_skill_catalog, get_skill_by_id
from .coverage import fpml_coverage_report
from .tools import (
    get_active_ruleset_summary,
    inspect_fpml_trade,
    list_supported_fx_adapters,
    run_conversion_with_patch,
    submit_llm_cdm,
    validate_best_effort,
)


@dataclass
class MappingAgentConfig:
    max_iterations: int = 10
    max_tool_calls: int = 80
    timeout_seconds: int = 300
    semantic_no_improve_limit: int = 3
    enable_rosetta: bool = False
    rosetta_timeout_seconds: int = 60
    skills_dir: Optional[str] = None
    #: ``ruleset`` = patch deterministic parser/ruleset; ``llm_native`` = model authors CDM via submit_llm_cdm.
    mapping_mode: str = "ruleset"


@dataclass
class MappingAgentResult:
    best_cdm_json: Dict[str, Any]
    best_normalized: Dict[str, Any]
    best_validation_report: Dict[str, Any]
    best_schema_error_count: int
    best_semantic_error_count: int
    adapter_id: str
    iterations: int = 0
    total_tool_calls: int = 0
    duration_seconds: float = 0.0
    trace: List[Dict[str, Any]] = field(default_factory=list)
    best_rosetta_failure_count: int = 0
    best_coverage_gaps: int = -1
    skill_id: Optional[str] = None
    skill_version: Optional[str] = None
    classifier_result: Optional[Dict[str, Any]] = None
    finish_summary: Optional[str] = None
    mapping_mode: str = "ruleset"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "best_schema_error_count": self.best_schema_error_count,
            "best_semantic_error_count": self.best_semantic_error_count,
            "best_rosetta_failure_count": self.best_rosetta_failure_count,
            "best_coverage_gaps": self.best_coverage_gaps,
            "adapter_id": self.adapter_id,
            "iterations": self.iterations,
            "total_tool_calls": self.total_tool_calls,
            "duration_seconds": round(self.duration_seconds, 2),
            "best_validation_report": self.best_validation_report,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "classifier_result": self.classifier_result,
            "finish_summary": self.finish_summary,
            "mapping_mode": self.mapping_mode,
        }


def _validation_still_failing(
    schema_err: int, sem_err: int, ros_fail: int, *, enable_rosetta: bool
) -> bool:
    if schema_err > 0 or sem_err > 0:
        return True
    if enable_rosetta and ros_fail > 0:
        return True
    return False


def _stall_nudge_message(
    *,
    schema_err: int,
    sem_err: int,
    ros_fail: int,
    enable_rosetta: bool,
) -> str:
    return (
        "Validation is still failing — keep iterating. Do not treat the last tool result as final.\n"
        f"Current best scores: schema_errors={schema_err}, semantic_errors={sem_err}, "
        f"rosetta_failures={ros_fail if enable_rosetta else 'n/a (Rosetta off)'}.\n"
        "Read `feedback_for_model.human_readable` from submit_llm_cdm / validate_best_effort, fix the CDM `trade`, "
        "and call submit_llm_cdm again until all counts are zero"
        + (" (and Rosetta passes)" if enable_rosetta else "")
        + ", or call finish with status partial/failed if you cannot fix it."
    )


def _score_from_validation_summary(result: Dict[str, Any]) -> Tuple[int, int, int]:
    summary = result.get("validation_summary") or {}
    schema_err = int(summary.get("schema_error_count", 0))
    semantic_err = int(summary.get("semantic_error_count", 0))
    rosetta_fail = int(summary.get("rosetta_failure_count", 0))
    return (schema_err, semantic_err, rosetta_fail)


def _format_problem_statement(best_report: Dict[str, Any]) -> str:
    errors = best_report.get("errors") or []
    schema = [e for e in errors if e.get("code") == "SCHEMA_VALIDATION_FAILED"]
    semantic = [e for e in errors if e.get("code") == "SEMANTIC_VALIDATION_FAILED"]

    lines: List[str] = []
    lines.append(f"Current best has schema_errors={len(schema)}, semantic_errors={len(semantic)}")
    if schema:
        lines.append("Schema failures (first 5):")
        for e in schema[:5]:
            lines.append(f"- {e.get('path','')} :: {e.get('message','')}".strip())
    if semantic:
        lines.append("Semantic failures (first 5):")
        for e in semantic[:5]:
            lines.append(f"- {e.get('path','')} :: {e.get('message','')}".strip())
    return "\n".join(lines)


def _build_registry(mapping_mode: str) -> ToolRegistry:
    reg = ToolRegistry()

    reg.register(ToolSpec(
        name="inspect_fpml_trade",
        description="Inspect the XML <trade> subtree and summarize product candidates and relevant tag counts.",
        json_schema={
            "type": "object",
            "properties": {"fpml_path": {"type": "string"}},
            "required": ["fpml_path"],
            "additionalProperties": False,
        },
        handler=inspect_fpml_trade,
    ))
    reg.register(ToolSpec(
        name="list_supported_fx_adapters",
        description="List supported FX FpML adapter_ids (trade child local names), priority, and normalized_kind.",
        json_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda **_: list_supported_fx_adapters(),
    ))

    if mapping_mode == "ruleset":
        reg.register(ToolSpec(
            name="get_active_ruleset_summary",
            description="Return a compact ruleset summary (candidate XML paths) for adapter_id.",
            json_schema={
                "type": "object",
                "properties": {"adapter_id": {"type": "string"}},
                "required": ["adapter_id"],
                "additionalProperties": False,
            },
            handler=get_active_ruleset_summary,
        ))
        reg.register(ToolSpec(
            name="run_conversion_with_patch",
            description="Apply a structured ruleset patch deterministically, then parse→transform→validate and return CDM JSON plus validation summary.",
            json_schema={
                "type": "object",
                "properties": {
                    "fpml_path": {"type": "string"},
                    "adapter_id": {"type": "string"},
                    "patch": {"type": "object", "additionalProperties": True},
                    "enable_rosetta": {"type": "boolean"},
                    "rosetta_timeout_seconds": {"type": "integer", "minimum": 1},
                },
                "required": ["fpml_path", "adapter_id", "patch"],
                "additionalProperties": False,
            },
            handler=run_conversion_with_patch,
        ))
    else:
        reg.register(ToolSpec(
            name="submit_llm_cdm",
            description=(
                "Submit LLM-authored CDM v6 JSON as {\"trade\": {...}}. Validates schema, semantic match vs recovery FpML parse, and optional Rosetta. "
                "Returns validation_summary plus feedback_for_model (human_readable samples of schema, semantic, Rosetta, and structure issues). "
                "If any count is non-zero, revise trade and submit again — do not stop until zeros or finish(partial/failed)."
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "fpml_path": {"type": "string"},
                    "adapter_id": {"type": "string"},
                    "cdm_json": {"type": "object", "additionalProperties": True},
                    "enable_rosetta": {"type": "boolean"},
                    "rosetta_timeout_seconds": {"type": "integer", "minimum": 1},
                },
                "required": ["fpml_path", "adapter_id", "cdm_json"],
                "additionalProperties": False,
            },
            handler=submit_llm_cdm,
        ))

    reg.register(ToolSpec(
        name="validate_best_effort",
        description="Validate the given CDM JSON against the source FpML and optionally run Rosetta (best-effort).",
        json_schema={
            "type": "object",
            "properties": {
                "fpml_path": {"type": "string"},
                "cdm_json": {"type": "object", "additionalProperties": True},
                "enable_rosetta": {"type": "boolean"},
                "rosetta_timeout_seconds": {"type": "integer", "minimum": 1},
            },
            "required": ["fpml_path", "cdm_json"],
            "additionalProperties": False,
        },
        handler=validate_best_effort,
    ))
    reg.register(ToolSpec(
        name="fpml_coverage_report",
        description="Compute coverage of FpML leaf paths in the CDM output. Shows mapped, ignored, and unmapped paths.",
        json_schema={
            "type": "object",
            "properties": {
                "fpml_path": {"type": "string"},
                "cdm_json": {"type": "object", "additionalProperties": True},
            },
            "required": ["fpml_path", "cdm_json"],
            "additionalProperties": False,
        },
        handler=fpml_coverage_report,
    ))
    reg.register(ToolSpec(
        name="finish",
        description="Signal that you are done mapping. Call this when the mapping is satisfactory or you cannot improve further.",
        json_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["success", "partial", "failed"]},
                "summary": {"type": "string"},
            },
            "required": ["status", "summary"],
            "additionalProperties": False,
        },
        handler=lambda **kwargs: {"acknowledged": True, **kwargs},
    ))

    return reg


def _nudge_tool_list(mapping_mode: str) -> str:
    if mapping_mode == "llm_native":
        return (
            "You must call at least one tool (submit_llm_cdm, inspect_fpml_trade, list_supported_fx_adapters, "
            "validate_best_effort, fpml_coverage_report, or finish). Do not respond with only text."
        )
    return (
        "You must call at least one tool (run_conversion_with_patch, "
        "inspect_fpml_trade, get_active_ruleset_summary, list_supported_fx_adapters, "
        "validate_best_effort, fpml_coverage_report, or finish). Do not respond with only text."
    )


def _detect_supported_adapter_candidates(fpml_path: str) -> List[str]:
    from fpml_cdm.adapters.registry import SUPPORTED_FX_ADAPTER_IDS, iter_fx_adapter_ids_by_priority

    candidates = inspect_fpml_trade(fpml_path)
    if "error" in candidates:
        return []
    found: List[str] = []
    for p in candidates.get("product_candidates") or []:
        aid = p.get("adapter_id")
        if aid in SUPPORTED_FX_ADAPTER_IDS:
            found.append(str(aid))
    order = iter_fx_adapter_ids_by_priority()
    rank = {aid: i for i, aid in enumerate(order)}
    return sorted(dict.fromkeys(found), key=lambda x: (rank.get(x, 999), x))


def _initial_best(fpml_path: str, config: MappingAgentConfig) -> Tuple[str, Dict[str, Any], Dict[str, Any], int, int, int]:
    from fpml_cdm.rulesets import get_base_ruleset
    from fpml_cdm.ruleset_engine import parse_fpml_fx_with_ruleset
    from fpml_cdm.transformer import transform_to_cdm_v6
    from fpml_cdm.validator import validate_normalized_and_cdm

    adapter_candidates = _detect_supported_adapter_candidates(fpml_path)
    if not adapter_candidates:
        raise ValueError("No supported product candidates found in FpML <trade> subtree.")

    best: Optional[Tuple[Tuple[int, int, int], str, Dict[str, Any], Dict[str, Any]]] = None
    best_schema = 10**9
    best_sem = 10**9
    best_ros = 0

    for adapter_id in adapter_candidates:
        base = get_base_ruleset(adapter_id)
        normalized, _ = parse_fpml_fx_with_ruleset(
            fpml_path=fpml_path,
            adapter_id=adapter_id,
            ruleset=base,
            strict=False,
            recovery_mode=True,
        )
        cdm = transform_to_cdm_v6(normalized)
        report = validate_normalized_and_cdm(normalized, cdm)

        schema_err = sum(1 for e in report.errors if e.code == "SCHEMA_VALIDATION_FAILED")
        semantic_err = sum(1 for e in report.errors if e.code == "SEMANTIC_VALIDATION_FAILED")
        rosetta_fail = 0
        if config.enable_rosetta:
            from fpml_cdm.rosetta_validator import validate_cdm_rosetta

            ros = validate_cdm_rosetta(cdm, timeout_seconds=config.rosetta_timeout_seconds)
            rosetta_fail = 0 if ros.valid else max(1, len(ros.failures))

        if (schema_err, semantic_err, rosetta_fail) < (best_schema, best_sem, best_ros):
            best_schema = schema_err
            best_sem = semantic_err
            best_ros = rosetta_fail
            best = ((schema_err, semantic_err, rosetta_fail), adapter_id, cdm, report.to_dict())

    assert best is not None
    (_, adapter_id, cdm, report_dict) = best
    return adapter_id, {}, report_dict | {"cdm_json": cdm}, best_schema, best_sem, best_ros


def _strip_meta(obj: Any) -> Any:
    """Recursively remove ``meta``, ``address``, and ``location`` keys from a CDM dict for use as a reference template."""
    if isinstance(obj, dict):
        return {k: _strip_meta(v) for k, v in obj.items() if k not in ("meta",)}
    if isinstance(obj, list):
        return [_strip_meta(item) for item in obj]
    return obj


def _generate_reference_cdm(fpml_path: str, adapter_id: str) -> Optional[Dict[str, Any]]:
    """Run the deterministic pipeline to produce a reference CDM shape for the LLM-native prompt."""
    try:
        from fpml_cdm.rulesets import get_base_ruleset
        from fpml_cdm.ruleset_engine import parse_fpml_fx_with_ruleset
        from fpml_cdm.transformer import transform_to_cdm_v6

        base = get_base_ruleset(adapter_id)
        normalized, _ = parse_fpml_fx_with_ruleset(
            fpml_path=fpml_path,
            adapter_id=adapter_id,
            ruleset=base,
            strict=False,
            recovery_mode=True,
        )
        cdm = transform_to_cdm_v6(normalized)
        return _strip_meta(cdm)
    except Exception:
        return None


def _empty_llm_native_seed(
    best_adapter: str, enable_rosetta: bool
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Tuple[int, int, int], int, int, int]:
    """Empty CDM + synthetic report. ``best_key`` uses large sentinels so the first real submit always improves."""
    report: Dict[str, Any] = {
        "valid": False,
        "errors": [
            {
                "code": "SCHEMA_VALIDATION_FAILED",
                "message": 'No CDM yet. Call submit_llm_cdm with a JSON object containing top-level "trade" (CDM v6).',
                "path": "",
                "level": "error",
            }
        ],
        "warnings": [],
        "mapping_score": {"total_fields": 0, "matched_fields": 0, "accuracy_percent": 0.0},
    }
    big = 10**9
    ros_k = big if enable_rosetta else 0
    best_key = (big, big, ros_k)
    # Human-readable counters for logs until the first successful submit updates them
    disp_s, disp_m, disp_r = 1, 0, (1 if enable_rosetta else 0)
    return {}, {}, report, best_key, disp_s, disp_m, disp_r


def run_mapping_agent(
    fpml_path: str,
    llm_client: object,
    model: str,
    *,
    config: Optional[MappingAgentConfig] = None,
    log_progress: Optional[bool] = None,
) -> MappingAgentResult:
    """
    Agent loop:
      - Classify FpML, load skill
      - ruleset mode: seed best CDM from deterministic baseline; LLM patches rulesets
      - llm_native mode: no baseline; LLM submits CDM via submit_llm_cdm
    """
    config = config or MappingAgentConfig()
    mode = (config.mapping_mode or "ruleset").strip().lower().replace("-", "_")
    if mode not in ("ruleset", "llm_native"):
        raise ValueError(f"Unknown mapping_mode: {config.mapping_mode!r} (use ruleset or llm_native)")

    if log_progress is None:
        log_progress = sys.stderr.isatty()

    catalog = load_skill_catalog(config.skills_dir)
    cr = classify_fpml(fpml_path, catalog)
    skill: Optional[SkillMeta] = None
    if cr.skill_id:
        skill = get_skill_by_id(cr.skill_id, config.skills_dir)

    if log_progress:
        sys.stderr.write(f"  [mapping] mode: {mode}\n")
        sys.stderr.write(f"  [mapping] classifier: {cr.reason}\n")
        if skill:
            sys.stderr.write(f"  [mapping] skill: {skill.skill_id} v{skill.version}\n")

    tool_registry = _build_registry(mode)
    tool_specs = tool_registry.tool_definitions_for_llm()
    system_prompt = build_system_prompt(catalog, skill, mapping_mode=mode)

    start_time = time.time()
    trace: List[Dict[str, Any]] = []
    trace.append({"type": "classifier", **cr.to_dict()})

    adapter_ids = _detect_supported_adapter_candidates(fpml_path)
    if not adapter_ids:
        raise ValueError("No supported product candidates found in FpML <trade> subtree.")

    reference_cdm: Optional[Dict[str, Any]] = None

    if mode == "llm_native":
        best_adapter = str(cr.adapter_id or adapter_ids[0])
        (
            best_cdm_json,
            best_normalized_json,
            best_validation_report_dict,
            best_key,
            best_schema_err,
            best_sem_err,
            best_ros_fail,
        ) = _empty_llm_native_seed(best_adapter, config.enable_rosetta)
        reference_cdm = _generate_reference_cdm(fpml_path, best_adapter)
        trace.append(
            {
                "type": "initial_state",
                "mapping_mode": "llm_native",
                "adapter_id": best_adapter,
                "note": "No deterministic baseline; model must submit_llm_cdm.",
                "has_reference_cdm": reference_cdm is not None,
            }
        )
    else:
        best_adapter, _, best_report_init, best_schema_err, best_sem_err, best_ros_fail = _initial_best(fpml_path, config)
        best_cdm_json = best_report_init.get("cdm_json") or {}
        best_normalized_json = {}
        best_validation_report_dict = best_report_init
        best_key = (best_schema_err, best_sem_err, best_ros_fail)
        trace.append(
            {
                "type": "initial_best",
                "adapter_id": best_adapter,
                "schema_errors": best_schema_err,
                "semantic_errors": best_sem_err,
                "rosetta_failures": best_ros_fail,
            }
        )

    problem_statement = _format_problem_statement(best_validation_report_dict)
    messages: List[Any] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": build_bootstrap_user_message(
                fpml_path=fpml_path,
                classifier_result=cr,
                best_adapter=best_adapter,
                problem_statement=problem_statement,
                enable_rosetta=config.enable_rosetta,
                rosetta_timeout_seconds=config.rosetta_timeout_seconds,
                mapping_mode=mode,
                reference_cdm=reference_cdm,
            ),
        },
    ]

    no_improve_iters = 0
    total_tool_calls = 0
    consecutive_text_only = 0
    last_tool_key = ""
    repeat_count = 0
    finish_summary: Optional[str] = None
    last_iteration = 0

    for iteration in range(config.max_iterations):
        last_iteration = iteration + 1
        elapsed = time.time() - start_time
        if elapsed > config.timeout_seconds:
            break
        if total_tool_calls >= config.max_tool_calls:
            break

        if log_progress:
            sys.stderr.write(f"  [mapping] iter {iteration+1}: waiting for LLM...\n")

        t0 = time.perf_counter()
        response = llm_client.chat.completions.create(  # type: ignore[union-attr]
            model=model,
            messages=messages,
            tools=tool_specs,
            tool_choice="auto",
        )
        t1 = time.perf_counter()
        message = response.choices[0].message
        messages.append(message)

        num_calls = len(message.tool_calls) if message.tool_calls else 0
        if log_progress:
            sys.stderr.write(f"  [mapping] iter {iteration+1}: LLM returned {num_calls} tool call(s) ({t1-t0:.1f}s)\n")

        if not message.tool_calls:
            trace.append({"iteration": iteration, "type": "text", "content": (message.content or "")[:500]})
            consecutive_text_only += 1
            if consecutive_text_only == 1:
                messages.append({"role": "user", "content": _nudge_tool_list(mode)})
            continue

        consecutive_text_only = 0
        finished = False

        for tool_call in message.tool_calls:
            total_tool_calls += 1
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments)

            tool_key = f"{fn_name}:{json.dumps(fn_args, sort_keys=True)}"
            if tool_key == last_tool_key:
                repeat_count += 1
            else:
                repeat_count = 0
                last_tool_key = tool_key

            trace.append({"iteration": iteration, "type": "tool_call", "tool": fn_name, "args": fn_args})

            if fn_name == "finish":
                finish_summary = fn_args.get("summary", "")
                trace.append({"iteration": iteration, "type": "finish", "args": fn_args})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(
                            {"acknowledged": True, "status": fn_args.get("status"), "summary": finish_summary}
                        ),
                    }
                )
                finished = True
                break

            t_tool0 = time.perf_counter()
            result_dict = tool_registry.dispatch(fn_name, fn_args)
            t_tool1 = time.perf_counter()
            result_str = json.dumps(result_dict, default=str)

            if log_progress:
                sys.stderr.write(f"    [tool] {fn_name} -> {t_tool1 - t_tool0:.2f}s\n")

            if repeat_count >= 3:
                result_dict = {
                    "warning": "You have called the same tool with the same arguments 3+ times. Try a different approach.",
                    "original_result": result_dict,
                }
                result_str = json.dumps(result_dict, default=str)
                repeat_count = 0

            trace.append(
                {"iteration": iteration, "type": "tool_result", "tool": fn_name, "result_preview": result_str[:500]}
            )

            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result_str})

            if fn_name in ("run_conversion_with_patch", "submit_llm_cdm"):
                try:
                    if (
                        isinstance(result_dict, dict)
                        and not result_dict.get("error")
                        and "validation_summary" in result_dict
                    ):
                        new_key = _score_from_validation_summary(result_dict)
                        cdm_out = result_dict.get("cdm_json")
                        first_llm_cdm = (
                            mode == "llm_native"
                            and isinstance(cdm_out, dict)
                            and isinstance(cdm_out.get("trade"), dict)
                            and not best_cdm_json
                        )
                        improved = first_llm_cdm or (new_key < best_key)
                        if improved:
                            best_key = new_key
                            best_schema_err = new_key[0]
                            best_sem_err = new_key[1]
                            best_ros_fail = new_key[2]
                            best_adapter = str(result_dict.get("adapter_id", best_adapter))
                            best_cdm_json = cdm_out or best_cdm_json
                            best_normalized_json = result_dict.get("normalized") or best_normalized_json
                            best_validation_report_dict = (
                                result_dict.get("validation_report") or best_validation_report_dict
                            )
                            no_improve_iters = 0
                        else:
                            no_improve_iters += 1
                    else:
                        no_improve_iters += 1
                except Exception:
                    no_improve_iters += 1

        if finished:
            break

        if best_schema_err == 0 and best_sem_err == 0 and (not config.enable_rosetta or best_ros_fail == 0):
            break

        if no_improve_iters >= config.semantic_no_improve_limit:
            broken = _validation_still_failing(
                best_schema_err, best_sem_err, best_ros_fail, enable_rosetta=config.enable_rosetta
            )
            if broken:
                no_improve_iters = 0
                nudge = _stall_nudge_message(
                    schema_err=best_schema_err,
                    sem_err=best_sem_err,
                    ros_fail=best_ros_fail,
                    enable_rosetta=config.enable_rosetta,
                )
                messages.append({"role": "user", "content": nudge})
                trace.append({"iteration": iteration, "type": "stall_nudge", "reason": "validation_still_failing"})
            else:
                break

    elapsed = time.time() - start_time

    coverage_gaps = -1
    try:
        from .coverage import compute_coverage

        if best_cdm_json:
            cov = compute_coverage(fpml_path, best_cdm_json)
            coverage_gaps = cov.unmapped_count
            trace.append({"type": "coverage", **cov.to_dict()})
    except Exception:
        pass

    return MappingAgentResult(
        best_cdm_json=best_cdm_json,
        best_normalized=best_normalized_json,
        best_validation_report=best_validation_report_dict,
        best_schema_error_count=best_schema_err,
        best_semantic_error_count=best_sem_err,
        best_rosetta_failure_count=best_ros_fail,
        best_coverage_gaps=coverage_gaps,
        adapter_id=str(best_adapter),
        iterations=last_iteration,
        total_tool_calls=total_tool_calls,
        duration_seconds=elapsed,
        trace=trace,
        skill_id=cr.skill_id,
        skill_version=skill.version if skill else None,
        classifier_result=cr.to_dict(),
        finish_summary=finish_summary,
        mapping_mode=mode,
    )
