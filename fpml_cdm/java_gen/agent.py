"""ReAct-style agent loop for CDM Java code generation."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .prompt_blocks import PREFLIGHT_ALL_BLOCKS, build_system_prompt
from .tools import (
    inspect_cdm_json,
    lookup_cdm_schema,
    resolve_java_type,
    list_enum_values,
    get_java_template,
    write_java_file,
    read_java_file,
    patch_java_file,
    compile_java,
    run_java,
    validate_output,
    finish,
    set_java_generation_target,
    reset_java_generation_target,
    get_active_java_class_name,
    get_active_java_filename,
)

TOOLS_JSON = Path(__file__).parent / "tools.json"


def _partial_result_from_trace(
    trace: List[Dict[str, object]], summary_prefix: str
) -> tuple[float, Optional[str], str]:
    """Extract last successful write_java_file path from trace (best-effort)."""
    match_pct = 0.0
    java_file: Optional[str] = None
    for i in range(len(trace) - 1, -1, -1):
        ent = trace[i]
        if ent.get("type") != "tool_result":
            continue
        tool = ent.get("tool")
        preview = ent.get("result_preview")
        if not isinstance(preview, str):
            continue
        if tool == "write_java_file" and java_file is None:
            if '"success": true' in preview:
                m = re.search(r'"path":\s*"([^"]*)"', preview)
                if m:
                    java_file = m.group(1).replace("\\", "/")
                    break
    summary = summary_prefix
    if java_file:
        summary = f"{summary_prefix} (java_file: {java_file})"
    return (match_pct, java_file, summary)


def _parse_tool_preview_bool(preview: str, key: str) -> Optional[bool]:
    try:
        d = json.loads(preview)
        v = d.get(key)
        if v is True or v is False:
            return bool(v)
    except (json.JSONDecodeError, TypeError):
        pass
    if key == "success":
        if '"success": true' in preview:
            return True
        if '"success": false' in preview:
            return False
    return None


def _trace_has_successful_run_java(trace: List[Dict[str, object]]) -> bool:
    """True if the latest run_java tool result in trace reports success and exit_code 0."""
    for i in range(len(trace) - 1, -1, -1):
        ent = trace[i]
        if ent.get("type") != "tool_result" or ent.get("tool") != "run_java":
            continue
        preview = ent.get("result_preview")
        if not isinstance(preview, str):
            continue
        ok = _parse_tool_preview_bool(preview, "success")
        if ok is not True:
            return False
        try:
            d = json.loads(preview)
            if int(d.get("exit_code", -1)) != 0:
                return False
        except (json.JSONDecodeError, TypeError, ValueError):
            if '"exit_code": 0' not in preview:
                return False
        return True
    return False


def _last_run_java_succeeded_this_iteration(
    trace: List[Dict[str, object]], iteration: int
) -> bool:
    """Whether the last run_java in this iteration (LLM or deterministic) succeeded."""
    last_ok: Optional[bool] = None
    for ent in trace:
        if ent.get("type") != "tool_result" or ent.get("tool") != "run_java":
            continue
        if ent.get("iteration") != iteration:
            continue
        preview = ent.get("result_preview")
        if not isinstance(preview, str):
            continue
        if _parse_tool_preview_bool(preview, "success") is not True:
            last_ok = False
            continue
        try:
            d = json.loads(preview)
            last_ok = int(d.get("exit_code", -1)) == 0
        except (json.JSONDecodeError, TypeError, ValueError):
            last_ok = '"exit_code": 0' in preview
    return last_ok is True


def _agent_result_exhausted(
    *,
    trace: List[Dict[str, object]],
    total_tool_calls: int,
    duration: float,
    iterations_recorded: int,
    reason_summary: str,
) -> AgentResult:
    """Build result when the main loop ends without an explicit finish tool."""
    match_pct, java_path, summary = _partial_result_from_trace(trace, reason_summary)
    if _trace_has_successful_run_java(trace):
        active_rel = f"rosetta-validator/generated/{get_active_java_filename()}"
        jf = java_path or active_rel
        return AgentResult(
            success=True,
            java_file=jf,
            match_percentage=match_pct,
            iterations=iterations_recorded,
            total_tool_calls=total_tool_calls,
            duration_seconds=duration,
            summary=(
                f"{reason_summary}; closing as success because run_java completed with exit 0 "
                f"(java_file: {jf})"
            ),
            trace=trace,
        )
    return AgentResult(
        success=False,
        java_file=java_path,
        match_percentage=match_pct,
        iterations=iterations_recorded,
        total_tool_calls=total_tool_calls,
        duration_seconds=duration,
        summary=summary,
        trace=trace,
    )


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def compact_tool_result_for_llm(fn_name: str, result_str: str) -> str:
    """Shrink tool JSON strings before they are appended to the LLM message list."""
    max_tool = _env_int("FPML_JAVA_GEN_MAX_TOOL_CHARS", 120_000)
    max_read = _env_int("FPML_JAVA_GEN_MAX_READ_JAVA_CHARS", 48_000)
    max_compile_msg = _env_int("FPML_JAVA_GEN_MAX_COMPILE_MSG_CHARS", 600)
    max_compile_errors = _env_int("FPML_JAVA_GEN_MAX_COMPILE_ERRORS", 35)
    max_stderr = _env_int("FPML_JAVA_GEN_MAX_COMPILE_STDERR_CHARS", 2_500)
    max_stdout = _env_int("FPML_JAVA_GEN_MAX_RUN_JAVA_STDOUT_CHARS", 48_000)
    max_lookup_props = _env_int("FPML_JAVA_GEN_MAX_LOOKUP_PROPERTIES", 80)

    try:
        payload = json.loads(result_str)
    except json.JSONDecodeError:
        if len(result_str) > max_tool:
            return json.dumps(
                {
                    "_truncated": True,
                    "_preview": result_str[: max_tool // 2],
                    "_hint": "Tool result exceeded FPML_JAVA_GEN_MAX_TOOL_CHARS (non-JSON).",
                }
            )
        return result_str

    if not isinstance(payload, dict):
        out = json.dumps(payload, default=str)
        if len(out) > max_tool:
            return json.dumps(
                {
                    "_truncated": True,
                    "_type": type(payload).__name__,
                    "_hint": "Serialized tool result exceeded FPML_JAVA_GEN_MAX_TOOL_CHARS.",
                }
            )
        return out

    if fn_name == "read_java_file" and isinstance(payload.get("content"), str):
        content = payload["content"]
        if len(content) > max_read:
            lines = content.splitlines()
            head_n, tail_n = 120, 120
            if len(lines) > head_n + tail_n:
                head = "\n".join(f"{i + 1:6d}|{lines[i]}" for i in range(head_n))
                ts = len(lines) - tail_n
                tail = "\n".join(f"{i + 1:6d}|{lines[i]}" for i in range(ts, len(lines)))
                omitted = len(lines) - head_n - tail_n
                payload["content"] = (
                    f"{head}\n... [{omitted} lines omitted] ...\n{tail}"
                )
                payload["truncated"] = True
        result_str = json.dumps(payload, default=str)

    elif fn_name == "get_java_template" and isinstance(payload.get("template"), str):
        tpl = payload["template"]
        if len(tpl) > max_read:
            lines = tpl.splitlines()
            head_n, tail_n = 80, 80
            if len(lines) > head_n + tail_n:
                head = "\n".join(f"{i + 1:6d}|{lines[i]}" for i in range(head_n))
                ts = len(lines) - tail_n
                tail = "\n".join(f"{i + 1:6d}|{lines[i]}" for i in range(ts, len(lines)))
                omitted = len(lines) - head_n - tail_n
                payload["template"] = f"{head}\n... [{omitted} lines omitted] ...\n{tail}"
                payload["truncated"] = True
        result_str = json.dumps(payload, default=str)

    elif fn_name == "compile_java":
        errs = payload.get("errors")
        if isinstance(errs, list):
            trimmed: List[object] = []
            for e in errs[:max_compile_errors]:
                if isinstance(e, dict):
                    e = dict(e)
                    msg = e.get("message")
                    if isinstance(msg, str) and len(msg) > max_compile_msg:
                        e["message"] = msg[:max_compile_msg] + "…"
                trimmed.append(e)
            payload["errors"] = trimmed
            if len(errs) > max_compile_errors:
                payload["errors_truncated"] = True
                payload["errors_omitted_count"] = len(errs) - max_compile_errors
        rs = payload.get("raw_stderr")
        if isinstance(rs, str) and len(rs) > max_stderr:
            payload["raw_stderr"] = rs[:max_stderr] + "…"
        result_str = json.dumps(payload, default=str)

    elif fn_name == "run_java":
        so = payload.get("stdout")
        if isinstance(so, str) and len(so) > max_stdout:
            payload["stdout"] = so[:max_stdout] + "…"
            payload["stdout_truncated"] = True
        se = payload.get("stderr")
        if isinstance(se, str) and len(se) > max_stderr:
            payload["stderr"] = se[:max_stderr] + "…"
        result_str = json.dumps(payload, default=str)

    elif fn_name == "lookup_cdm_schema" and "properties" in payload:
        props = payload.get("properties")
        if isinstance(props, dict) and len(props) > max_lookup_props:
            keys = list(props.keys())[:max_lookup_props]
            payload["properties"] = {k: props[k] for k in keys}
            payload["properties_truncated"] = True
            payload["properties_omitted_count"] = len(props) - max_lookup_props
        result_str = json.dumps(payload, default=str)

    elif fn_name == "validate_output":
        errs = payload.get("errors")
        if isinstance(errs, list) and len(errs) > max_compile_errors:
            payload["errors"] = errs[:max_compile_errors]
            payload["errors_truncated"] = True
            payload["errors_omitted_count"] = len(errs) - max_compile_errors
        result_str = json.dumps(payload, default=str)

    elif fn_name == "inspect_cdm_json" and len(result_str) > max_tool:
        # Belt-and-suspenders: drop registry values to one line each if still huge
        tr = payload.get("type_registry")
        if isinstance(tr, dict):
            slim: Dict[str, object] = {}
            for k, v in list(tr.items())[:200]:
                if isinstance(v, dict):
                    slim[k] = {
                        "java_class": v.get("java_class"),
                        "simple_name": v.get("simple_name"),
                        "import_statement": v.get("import_statement"),
                    }
                else:
                    slim[k] = v
            payload["type_registry"] = slim
            payload["type_registry_truncated"] = True
        result_str = json.dumps(payload, default=str)

    if len(result_str) > max_tool:
        return json.dumps(
            {
                "tool": fn_name,
                "_truncated": True,
                "_original_chars": len(result_str),
                "_preview": result_str[: max_tool // 2],
                "_hint": "Result exceeded FPML_JAVA_GEN_MAX_TOOL_CHARS after compaction.",
            }
        )
    return result_str


def _format_tool_call_short(fn_name: str, fn_args: Dict[str, object]) -> str:
    """Format tool name + short args for stderr (omit huge payloads)."""
    parts: List[str] = []
    for k, v in sorted(fn_args.items()):
        if k == "actual_json" and isinstance(v, str):
            parts.append(f"actual=<len {len(v)} chars>")
        elif k == "code" and isinstance(v, str):
            parts.append(f"code=<len {len(v)} chars>")
        elif isinstance(v, str) and len(v) > 60:
            parts.append(f"{k}=<len {len(v)} chars>")
        else:
            parts.append(f"{k}={v!r}" if " " in str(v) else f"{k}={v}")
    return f"{fn_name}({', '.join(parts)})" if parts else f"{fn_name}()"


@dataclass
class AgentConfig:
    max_iterations: int = 20
    max_tool_calls: int = 50
    timeout_seconds: int = 600
    match_threshold: float = 95.0


def scale_java_gen_config_for_node_count(config: AgentConfig, total_nodes: int) -> AgentConfig:
    if total_nodes > 400:
        return replace(
            config,
            max_iterations=max(config.max_iterations, 50),
            max_tool_calls=max(config.max_tool_calls, 150),
            timeout_seconds=max(config.timeout_seconds, 900),
        )
    if total_nodes > 150:
        return replace(
            config,
            max_iterations=max(config.max_iterations, 35),
            max_tool_calls=max(config.max_tool_calls, 100),
            timeout_seconds=max(config.timeout_seconds, 600),
        )
    return config


@dataclass
class AgentResult:
    success: bool
    java_file: Optional[str] = None
    match_percentage: float = 0.0
    iterations: int = 0
    total_tool_calls: int = 0
    duration_seconds: float = 0.0
    summary: str = ""
    trace: List[Dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "success": self.success,
            "java_file": self.java_file,
            "match_percentage": self.match_percentage,
            "iterations": self.iterations,
            "total_tool_calls": self.total_tool_calls,
            "duration_seconds": round(self.duration_seconds, 2),
            "summary": self.summary,
        }


# All optional prompt blocks enabled (for tests and callers that need the legacy string).
SYSTEM_PROMPT = build_system_prompt(PREFLIGHT_ALL_BLOCKS)


TOOL_DISPATCH: Dict[str, Callable[..., Dict[str, object]]] = {
    "inspect_cdm_json": inspect_cdm_json,
    "lookup_cdm_schema": lookup_cdm_schema,
    "resolve_java_type": resolve_java_type,
    "list_enum_values": list_enum_values,
    "get_java_template": get_java_template,
    "write_java_file": write_java_file,
    "read_java_file": read_java_file,
    "patch_java_file": patch_java_file,
    "compile_java": compile_java,
    "run_java": run_java,
    "validate_output": validate_output,
    "finish": finish,
}


def load_tool_specs() -> List[Dict[str, object]]:
    """Load tool definitions from tools.json for the LLM API."""
    data = json.loads(TOOLS_JSON.read_text(encoding="utf-8"))
    return [{"type": "function", "function": tool} for tool in data["tools"]]


def _execute_tool(fn_name: str, fn_args: Dict[str, object]) -> str:
    """Execute a tool by name, return JSON string result."""
    tool_fn = TOOL_DISPATCH.get(fn_name)
    if tool_fn is None:
        return json.dumps({"error": f"Unknown tool: {fn_name}"})
    try:
        result = tool_fn(**fn_args)
        return json.dumps(result, default=str)
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


def run_agent(
    cdm_json_path: str,
    llm_client: object,
    model: str = "gpt-4o",
    config: Optional[AgentConfig] = None,
    log_progress: Optional[bool] = None,
    java_class_name: Optional[str] = None,
) -> AgentResult:
    """Main agent loop: LLM + tools until finish or limits reached.

    Output class and ``rosetta-validator/generated/<Name>.java`` are derived from the CDM JSON filename
    stem unless ``java_class_name`` is set (e.g. FpML pipeline passes the FpML stem).
    """
    set_java_generation_target(cdm_json_path=cdm_json_path, class_name=java_class_name)
    try:
        return _run_agent_impl(
            cdm_json_path=cdm_json_path,
            llm_client=llm_client,
            model=model,
            config=config,
            log_progress=log_progress,
        )
    finally:
        reset_java_generation_target()


def _run_agent_impl(
    cdm_json_path: str,
    llm_client: object,
    model: str = "gpt-4o",
    config: Optional[AgentConfig] = None,
    log_progress: Optional[bool] = None,
) -> AgentResult:
    """Inner agent loop (expects ``set_java_generation_target`` already applied)."""
    config = config or AgentConfig()
    if log_progress is None:
        log_progress = sys.stderr.isatty()
    preflight: Dict[str, object] = {}
    try:
        preflight = inspect_cdm_json(cdm_json_path, detail="full")
        total_nodes_raw = preflight.get("total_nodes", 0)
        if isinstance(total_nodes_raw, int):
            config = scale_java_gen_config_for_node_count(config, total_nodes_raw)
            if log_progress:
                sys.stderr.write(
                    f"  [java_gen] total_nodes={total_nodes_raw} -> "
                    f"max_iterations={config.max_iterations} "
                    f"max_tool_calls={config.max_tool_calls} "
                    f"timeout_seconds={config.timeout_seconds}\n"
                )
    except Exception:
        preflight = dict(PREFLIGHT_ALL_BLOCKS)
    system_prompt_effective = build_system_prompt(preflight)
    tool_specs = load_tool_specs()
    start_time = time.time()
    trace: List[Dict[str, object]] = []
    total_tool_calls = 0
    consecutive_single_patches = 0
    patch_loop_nudge_sent = False
    last_tool_key = ""
    repeat_count = 0
    consecutive_text_only = 0
    total_tool_time = 0.0
    total_llm_time = 0.0
    active_cls = get_active_java_class_name()
    active_file = get_active_java_filename()
    # Ensure a clean starting point for patch-based generation.
    # The LLM often follows a "patch placeholders" strategy; if an old generated file
    # exists without placeholders, patching fails and the agent can loop until timeout.
    try:
        template = get_java_template().get("template", "")
        if isinstance(template, str) and template.strip():
            write_java_file(template, filename=active_file)
    except Exception:
        # Don't fail Java generation just because template write failed;
        # the agent can still choose to write a full file directly.
        pass

    messages: List[Dict[str, object]] = [
        {"role": "system", "content": system_prompt_effective},
        {
            "role": "user",
            "content": (
                f"Generate Java code that builds the CDM trade defined in: {cdm_json_path}\n"
                f"Use that exact path when calling tools (especially inspect_cdm_json).\n"
                f"Use a single public class named `{active_cls}` in file `{active_file}` "
                f"under the repository `rosetta-validator/generated/` directory (filename must match the class name).\n"
                f"The code must compile against the CDM classpath and print valid CDM trade JSON "
                f"to stdout when executed (use validate_output if unsure)."
            ),
        },
    ]

    for iteration in range(config.max_iterations):
        elapsed = time.time() - start_time
        if elapsed > config.timeout_seconds:
            return _agent_result_exhausted(
                trace=trace,
                total_tool_calls=total_tool_calls,
                duration=elapsed,
                iterations_recorded=iteration,
                reason_summary=f"Timeout after {config.timeout_seconds}s",
            )

        if total_tool_calls >= config.max_tool_calls:
            return _agent_result_exhausted(
                trace=trace,
                total_tool_calls=total_tool_calls,
                duration=time.time() - start_time,
                iterations_recorded=iteration,
                reason_summary=f"Max tool calls ({config.max_tool_calls}) reached",
            )

        if log_progress:
            sys.stderr.write(f"  [iter {iteration+1}] waiting for LLM...\n")
        t0_llm = time.perf_counter()
        response = llm_client.chat.completions.create(  # type: ignore[union-attr]
            model=model,
            messages=messages,
            tools=tool_specs,
            tool_choice="auto",
        )
        t1_llm = time.perf_counter()
        total_llm_time += t1_llm - t0_llm
        message = response.choices[0].message
        messages.append(message)
        num_calls = len(message.tool_calls) if message.tool_calls else 0
        if log_progress:
            sys.stderr.write(f"  [iter {iteration+1}] LLM responded in {t1_llm - t0_llm:.1f}s ({num_calls} tool calls)\n")

        if not message.tool_calls:
            trace.append({
                "iteration": iteration,
                "type": "text",
                "content": (message.content or "")[:500],
            })
            consecutive_text_only += 1
            if consecutive_text_only == 1:
                nudge = (
                    "You must respond with at least one tool call "
                    "(e.g. read_java_file, patch_java_file, compile_java, or finish). "
                    "Do not respond with only text."
                )
                messages.append({"role": "user", "content": nudge})
            continue

        consecutive_text_only = 0
        last_tool_name = ""
        last_tool_result_str = ""
        for tool_call in message.tool_calls:
            total_tool_calls += 1
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments)

            if fn_name == "patch_java_file":
                patches_arg = fn_args.get("patches")
                is_single_patch = patches_arg is None or len(patches_arg) == 1
                if is_single_patch:
                    consecutive_single_patches += 1
                else:
                    consecutive_single_patches = 0
            elif fn_name == "compile_java":
                consecutive_single_patches = 0
                patch_loop_nudge_sent = False

            # Loop detection
            tool_key = f"{fn_name}:{json.dumps(fn_args, sort_keys=True)}"
            if tool_key == last_tool_key:
                repeat_count += 1
            else:
                repeat_count = 0
                last_tool_key = tool_key

            trace.append({
                "iteration": iteration,
                "type": "tool_call",
                "tool": fn_name,
                "args": fn_args,
            })

            if fn_name == "finish":
                duration = time.time() - start_time
                return AgentResult(
                    success=fn_args.get("status") == "success",
                    java_file=str(fn_args.get("java_file", "")),
                    match_percentage=float(fn_args.get("match_percentage", 0.0)),
                    iterations=iteration + 1,
                    total_tool_calls=total_tool_calls,
                    duration_seconds=duration,
                    summary=str(fn_args.get("summary", "")),
                    trace=trace,
                )

            if log_progress:
                sys.stderr.write(f"    {_format_tool_call_short(fn_name, fn_args)}\n")
            t0_tool = time.perf_counter()
            result_str = _execute_tool(fn_name, fn_args)
            result_str = compact_tool_result_for_llm(fn_name, result_str)
            t1_tool = time.perf_counter()
            total_tool_time += t1_tool - t0_tool
            if log_progress:
                try:
                    res = json.loads(result_str)
                    ok = bool(res.get("success", res.get("match", '"error"' not in result_str)))
                except (json.JSONDecodeError, TypeError):
                    ok = '"error"' not in result_str
                sys.stderr.write(f"      → {t1_tool - t0_tool:.2f}s {'ok' if ok else 'error'}\n")

            # Inject nudge if looping
            if repeat_count >= 3:
                result_str = json.dumps({
                    "warning": "You have called the same tool with the same arguments 3+ times. Try a different approach.",
                    "original_result": json.loads(result_str),
                })
                repeat_count = 0

            trace.append({
                "iteration": iteration,
                "type": "tool_result",
                "tool": fn_name,
                "result_preview": result_str[:500],
            })

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_str,
            })
            last_tool_name = fn_name
            last_tool_result_str = result_str

        if consecutive_single_patches >= 3 and not patch_loop_nudge_sent:
            messages.append({
                "role": "user",
                "content": (
                    "WARNING: You have called patch_java_file repeatedly with single-patch calls only. "
                    "This wastes iterations. STOP patching one fix at a time. Instead:\n"
                    "1. Call read_java_file (optionally with line range) to find ALL occurrences of the same pattern.\n"
                    "2. Fix ALL instances in ONE patch_java_file call using the patches array.\n"
                    "3. Then call compile_java once.\n\n"
                    "If replacing setReference(Reference.builder()...) with setGlobalReference(...), "
                    "scan the entire file and fix every occurrence in one batch."
                ),
            })
            patch_loop_nudge_sent = True
            consecutive_single_patches = 0

        if last_tool_name == "compile_java":
            try:
                compile_res = json.loads(last_tool_result_str)
                if compile_res.get("success") is True:
                    det_class = get_active_java_class_name()
                    run_result = run_java(class_name=det_class, timeout=30)
                    run_result_str = compact_tool_result_for_llm(
                        "run_java", json.dumps(run_result, default=str)
                    )
                    id_run = "call_det_run"
                    messages.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"type": "function", "id": id_run, "function": {"name": "run_java", "arguments": json.dumps({"class_name": det_class, "timeout": 30})}},
                        ],
                    })
                    messages.append({"role": "tool", "tool_call_id": id_run, "content": run_result_str})
                    trace.append({"iteration": iteration, "type": "tool_call", "tool": "run_java", "args": {"class_name": det_class, "timeout": 30}})
                    trace.append({"iteration": iteration, "type": "tool_result", "tool": "run_java", "result_preview": run_result_str[:500]})
                    total_tool_calls += 1
                    if log_progress:
                        sys.stderr.write("    [deterministic] run_java\n")
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        if _last_run_java_succeeded_this_iteration(trace, iteration):
            messages.append({
                "role": "user",
                "content": (
                    "Java compiled and ran successfully (run_java exit 0). "
                    "Call `finish` now with status \"success\", a short summary, "
                    "and `java_file` set to the generated `.java` path "
                    "(from the last write_java_file / active target under rosetta-validator/generated/)."
                ),
            })

        if log_progress:
            sys.stderr.write(f"  [iter {iteration+1}] {total_tool_calls} tool calls (total so far: {total_tool_time:.1f}s tools, {total_llm_time:.1f}s LLM)\n")

    duration = time.time() - start_time
    return _agent_result_exhausted(
        trace=trace,
        total_tool_calls=total_tool_calls,
        duration=duration,
        iterations_recorded=config.max_iterations,
        reason_summary=f"Max iterations ({config.max_iterations}) reached without finish",
    )
