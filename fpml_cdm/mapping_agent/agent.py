from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


from .registry import ToolRegistry, ToolSpec
from .tools import (
    get_active_ruleset_summary,
    inspect_fpml_trade,
    list_supported_fx_adapters,
    run_conversion_with_patch,
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "best_schema_error_count": self.best_schema_error_count,
            "best_semantic_error_count": self.best_semantic_error_count,
            "best_rosetta_failure_count": self.best_rosetta_failure_count,
            "adapter_id": self.adapter_id,
            "iterations": self.iterations,
            "total_tool_calls": self.total_tool_calls,
            "duration_seconds": round(self.duration_seconds, 2),
            "best_validation_report": self.best_validation_report,
        }


SYSTEM_PROMPT = """\
You are a deterministic mapping agent for FpML FX derivatives → CDM v6 trades.

Rules:
1) You are tool-constrained: you must call tools instead of returning final CDM JSON directly.
2) You must only propose structured ruleset patches (candidate ordering and derived-field toggles).
3) Never guess XML structures: use inspect_fpml_trade, list_supported_fx_adapters, and get_active_ruleset_summary to see candidate paths.
4) Prefer patches that reduce schema validation failures first, then semantic mismatches.
5) Every iteration should try a new patch; avoid repeating identical tool calls.
"""


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


def _build_registry() -> ToolRegistry:
    reg = ToolRegistry()

    reg.register(
        ToolSpec(
            name="inspect_fpml_trade",
            description="Inspect the XML <trade> subtree and summarize product candidates and relevant tag counts.",
            json_schema={
                "type": "object",
                "properties": {"fpml_path": {"type": "string"}},
                "required": ["fpml_path"],
                "additionalProperties": False,
            },
            handler=inspect_fpml_trade,
        )
    )
    reg.register(
        ToolSpec(
            name="get_active_ruleset_summary",
            description="Return a compact ruleset summary (candidate XML paths) for adapter_id.",
            json_schema={
                "type": "object",
                "properties": {"adapter_id": {"type": "string"}},
                "required": ["adapter_id"],
                "additionalProperties": False,
            },
            handler=get_active_ruleset_summary,
        )
    )
    reg.register(
        ToolSpec(
            name="list_supported_fx_adapters",
            description="List supported FX FpML adapter_ids (trade child local names), priority, and normalized_kind.",
            json_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=lambda **_: list_supported_fx_adapters(),
        )
    )
    reg.register(
        ToolSpec(
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
        )
    )
    reg.register(
        ToolSpec(
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
        )
    )

    return reg


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


def _initial_best(fpml_path: str) -> Tuple[str, Dict[str, Any], Dict[str, Any], int, int]:
    from fpml_cdm.rulesets import get_base_ruleset
    from fpml_cdm.ruleset_engine import parse_fpml_fx_with_ruleset
    from fpml_cdm.transformer import transform_to_cdm_v6
    from fpml_cdm.validator import validate_normalized_and_cdm

    adapter_candidates = _detect_supported_adapter_candidates(fpml_path)
    if not adapter_candidates:
        raise ValueError("No supported product candidates found in FpML <trade> subtree.")

    best: Optional[Tuple[Tuple[int, int], str, Dict[str, Any], Dict[str, Any]]] = None
    best_schema = 10**9
    best_sem = 10**9

    for adapter_id in adapter_candidates:
        base = get_base_ruleset(adapter_id)
        normalized, _parse_issues = parse_fpml_fx_with_ruleset(
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

        if (schema_err, semantic_err) < (best_schema, best_sem):
            best_schema = schema_err
            best_sem = semantic_err
            best = ((schema_err, semantic_err), adapter_id, cdm, report.to_dict())

    assert best is not None
    (_, adapter_id, cdm, report_dict) = best
    # We re-load normalized from patched parse to ensure the returned structure matches.
    from fpml_cdm.ruleset_engine import parse_fpml_fx_with_ruleset as _parse

    base = get_base_ruleset(adapter_id)
    normalized, _ = _parse(fpml_path=fpml_path, adapter_id=adapter_id, ruleset=base, strict=False, recovery_mode=True)
    return adapter_id, normalized.to_dict(), report_dict | {"cdm_json": cdm}, best_schema, best_sem


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
      - Find initial best CDM via deterministic base rulesets
      - If validation not good enough, iterate tool calls:
          LLM calls run_conversion_with_patch with a structured patch
          we update the best-so-far CDM by validation error counts
    """
    config = config or MappingAgentConfig()
    if log_progress is None:
        log_progress = bool(getattr(llm_client, "__bool__", None)) or False

    tool_registry = _build_registry()
    tool_specs = tool_registry.tool_definitions_for_llm()

    start_time = time.time()
    trace: List[Dict[str, Any]] = []
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    adapter_ids = _detect_supported_adapter_candidates(fpml_path)
    if not adapter_ids:
        raise ValueError("No supported product candidates found in FpML <trade> subtree.")

    # Seed best-so-far from deterministic conversions over base rulesets.
    # We keep this in Python so we can score without relying on the LLM.
    best_adapter, best_normalized, best_report, best_schema_err, best_sem_err = None, None, None, None, None  # type: ignore[assignment]
    best_cdm_json: Dict[str, Any] = {}
    best_normalized_json: Dict[str, Any] = {}
    best_validation_report_dict: Dict[str, Any] = {}

    from fpml_cdm.rulesets import get_base_ruleset
    from fpml_cdm.ruleset_engine import parse_fpml_fx_with_ruleset
    from fpml_cdm.transformer import transform_to_cdm_v6
    from fpml_cdm.validator import validate_normalized_and_cdm

    # Always keep a 3-tuple score so we can optionally include Rosetta failures.
    # When Rosetta is disabled, rosetta_fail_count is always 0.
    best_key: Tuple[int, int, int] = (10**9, 10**9, 10**9)
    best_rosetta_failure_count = 0
    for adapter_id in adapter_ids:
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

        rosetta_fail_count = 0
        if config.enable_rosetta:
            from fpml_cdm.rosetta_validator import validate_cdm_rosetta

            ros = validate_cdm_rosetta(
                cdm,
                timeout_seconds=config.rosetta_timeout_seconds,
            )
            rosetta_fail_count = 0 if ros.valid else max(1, len(ros.failures))

        if (schema_err, semantic_err, rosetta_fail_count) < best_key:
            best_key = (schema_err, semantic_err, rosetta_fail_count)
            best_adapter = adapter_id
            best_cdm_json = cdm
            best_normalized_json = normalized.to_dict()
            best_validation_report_dict = report.to_dict()
            best_schema_err = schema_err
            best_sem_err = semantic_err
            best_rosetta_failure_count = rosetta_fail_count

    assert best_adapter is not None
    assert best_validation_report_dict is not None

    problem_statement = _format_problem_statement(best_validation_report_dict)
    messages.append(
        {
            "role": "user",
            "content": (
                f"FpML input: {fpml_path}\n"
                f"Initial best adapter: {best_adapter}\n"
                f"{problem_statement}\n\n"
                "Propose a structured patch and call run_conversion_with_patch.\n"
                f"Set enable_rosetta={'true' if config.enable_rosetta else 'false'} and "
                f"rosetta_timeout_seconds={config.rosetta_timeout_seconds} in run_conversion_with_patch.\n"
                "If schema errors are 0 but semantic errors remain, focus on semantic mismatches.\n"
            ),
        }
    )

    no_improve_iters = 0
    last_best_sem = best_sem_err  # type: ignore[assignment]
    last_best_rosetta_failure_count = best_rosetta_failure_count

    total_tool_calls = 0
    total_llm_time = 0.0
    consecutive_text_only = 0
    last_tool_key = ""
    repeat_count = 0

    last_iteration = 0
    for iteration in range(config.max_iterations):
        last_iteration = iteration + 1
        elapsed = time.time() - start_time
        if elapsed > config.timeout_seconds:
            return MappingAgentResult(
                best_cdm_json=best_cdm_json,
                best_normalized=best_normalized_json,
                best_validation_report=best_validation_report_dict,
                best_schema_error_count=int(best_schema_err),
                best_semantic_error_count=int(best_sem_err),
                best_rosetta_failure_count=int(best_rosetta_failure_count),
                adapter_id=str(best_adapter),
                iterations=iteration,
                total_tool_calls=total_tool_calls,
                duration_seconds=elapsed,
                trace=trace,
            )

        if total_tool_calls >= config.max_tool_calls:
            elapsed = time.time() - start_time
            return MappingAgentResult(
                best_cdm_json=best_cdm_json,
                best_normalized=best_normalized_json,
                best_validation_report=best_validation_report_dict,
                best_schema_error_count=int(best_schema_err),
                best_semantic_error_count=int(best_sem_err),
                best_rosetta_failure_count=int(best_rosetta_failure_count),
                adapter_id=str(best_adapter),
                iterations=iteration,
                total_tool_calls=total_tool_calls,
                duration_seconds=elapsed,
                trace=trace,
            )

        if log_progress:
            print(f"[mapping] iter {iteration+1}: waiting for LLM...")

        t0 = time.perf_counter()
        response = llm_client.chat.completions.create(  # type: ignore[union-attr]
            model=model,
            messages=messages,
            tools=tool_specs,
            tool_choice="auto",
        )
        t1 = time.perf_counter()
        total_llm_time += t1 - t0
        message = response.choices[0].message
        # Keep the exact message object returned by the client (OpenRouter dataclass
        # or OpenAI SDK message), matching the approach in java_gen/agent.py.
        messages.append(message)

        num_calls = len(message.tool_calls) if message.tool_calls else 0
        if log_progress:
            print(f"[mapping] iter {iteration+1}: LLM tool calls={num_calls}")

        if not message.tool_calls:
            trace.append(
                {"iteration": iteration, "type": "text", "content": (message.content or "")[:500]}
            )
            consecutive_text_only += 1
            if consecutive_text_only == 1:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You must call at least one tool (run_conversion_with_patch, "
                            "list_supported_fx_adapters, get_active_ruleset_summary, inspect_fpml_trade, or validate_best_effort). "
                            "Do not respond with only text."
                        ),
                    }
                )
            continue

        consecutive_text_only = 0
        last_tool_name = ""
        last_tool_result_str = ""

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

            t_tool0 = time.perf_counter()
            result_dict = tool_registry.dispatch(fn_name, fn_args)
            t_tool1 = time.perf_counter()
            result_str = json.dumps(result_dict, default=str)
            if log_progress:
                print(f"  [tool] {fn_name} → {t_tool1 - t_tool0:.2f}s")

            if repeat_count >= 3:
                result_dict = {
                    "warning": "You have called the same tool with the same arguments 3+ times.",
                    "original_result": result_dict,
                }
                result_str = json.dumps(result_dict, default=str)
                repeat_count = 0

            trace.append(
                {
                    "iteration": iteration,
                    "type": "tool_result",
                    "tool": fn_name,
                    "result_preview": result_str[:500],
                }
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str,
                }
            )

            last_tool_name = fn_name
            last_tool_result_str = result_str

            if fn_name == "run_conversion_with_patch":
                # Update best-so-far if this tool call improved the score.
                try:
                    result_obj = result_dict
                    if not isinstance(result_obj, dict) or result_obj.get("error"):
                        continue
                    if "validation_summary" not in result_obj:
                        continue
                    new_key = _score_from_validation_summary(result_obj)
                    new_key_full = new_key
                    if new_key_full < best_key:
                        best_key = new_key_full
                        best_schema_err = new_key_full[0]
                        best_sem_err = new_key_full[1]
                        best_rosetta_failure_count = int(new_key_full[2])
                        best_adapter = result_obj.get("adapter_id", best_adapter)
                        best_cdm_json = result_obj.get("cdm_json") or best_cdm_json
                        best_normalized_json = result_obj.get("normalized") or best_normalized_json
                        best_validation_report_dict = result_obj.get("validation_report") or best_validation_report_dict

                        # Improvement in secondary dimensions resets no-improve counter.
                        if (best_sem_err, best_rosetta_failure_count) < (last_best_sem, last_best_rosetta_failure_count):
                            no_improve_iters = 0
                            last_best_sem = best_sem_err
                            last_best_rosetta_failure_count = best_rosetta_failure_count
                        else:
                            no_improve_iters += 1
                except Exception:
                    # Ignore scoring errors; keep best-so-far.
                    pass

        # Stop if we have a fully valid mapping.
        if int(best_schema_err) == 0 and int(best_sem_err) == 0 and (
            (not config.enable_rosetta) or int(best_rosetta_failure_count) == 0
        ):
            break

        if no_improve_iters >= config.semantic_no_improve_limit:
            break

    elapsed = time.time() - start_time
    return MappingAgentResult(
        best_cdm_json=best_cdm_json,
        best_normalized=best_normalized_json,
        best_validation_report=best_validation_report_dict,
        best_schema_error_count=int(best_schema_err),
        best_semantic_error_count=int(best_sem_err),
        best_rosetta_failure_count=int(best_rosetta_failure_count),
        adapter_id=str(best_adapter),
        iterations=last_iteration,
        total_tool_calls=total_tool_calls,
        duration_seconds=elapsed,
        trace=trace,
    )

