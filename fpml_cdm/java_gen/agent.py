"""ReAct-style agent loop for CDM Java code generation."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

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
    store_large_payload,
    fetch_payload,
    compact_context,
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


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _append_tool_io_log(*, tool: str, raw: str, sent: str) -> None:
    """Append raw vs sent tool strings to tool_io.jsonl (debug)."""
    try:
        root = Path(__file__).resolve().parents[2]
    except Exception:
        root = Path(".")
    path = root / "tool_io.jsonl"
    try:
        ent = {
            "ts_ms": int(time.time() * 1000),
            "tool": tool,
            "raw_chars": len(raw),
            "sent_chars": len(sent),
            "raw": raw,
            "sent": sent,
        }
        path.open("a", encoding="utf-8").write(json.dumps(ent, ensure_ascii=False) + "\n")
    except Exception:
        return


def _max_tool_budget() -> Tuple[bool, int]:
    """(use_utf8_bytes, limit). If FPML_JAVA_GEN_MAX_TOOL_BYTES > 0, use UTF-8 byte length vs that limit."""
    mb = _env_int("FPML_JAVA_GEN_MAX_TOOL_BYTES", 0)
    if mb > 0:
        return (True, mb)
    return (False, _env_int("FPML_JAVA_GEN_MAX_TOOL_CHARS", 120_000))


def _tool_result_size(s: str, *, use_utf8_bytes: bool) -> int:
    return len(s.encode("utf-8")) if use_utf8_bytes else len(s)


def _tool_over_budget(s: str, *, use_utf8_bytes: bool, limit: int) -> bool:
    return _tool_result_size(s, use_utf8_bytes=use_utf8_bytes) > limit


def prepare_tool_result_for_llm(fn_name: str, result_str: str) -> Tuple[str, Dict[str, bool]]:
    """Cap tool JSON for the LLM: pass-through, inspect envelope+tree split, or full lossless stub.

    Returns (string_for_llm, meta) where meta may set tree_split, oversize_full.
    """
    meta: Dict[str, bool] = {"tree_split": False, "oversize_full": False}
    use_bytes, limit = _max_tool_budget()
    if not _tool_over_budget(result_str, use_utf8_bytes=use_bytes, limit=limit):
        _append_tool_io_log(tool=fn_name, raw=result_str, sent=result_str)
        return result_str, meta

    if fn_name == "inspect_cdm_json":
        try:
            data = json.loads(result_str)
        except (json.JSONDecodeError, TypeError, ValueError):
            data = None
        if isinstance(data, dict) and "tree" in data:
            envelope = {k: v for k, v in data.items() if k != "tree"}
            tree_payload = json.dumps(data["tree"], ensure_ascii=False, default=str)
            st_tree = store_large_payload(kind="inspect_cdm_json:tree", payload_json=tree_payload)
            if st_tree.get("success") is True:
                out_obj: Dict[str, object] = {
                    **envelope,
                    "tree_stored": True,
                    "tree_handle": st_tree.get("handle"),
                    "tree_total_chars": len(tree_payload),
                    "tree_sha256": st_tree.get("sha256"),
                    "stored": True,
                    "handle": st_tree.get("handle"),
                    "storage_mode": "inspect_tree_only",
                    "next_step": (
                        "Envelope (type_registry, warnings, reference hints, etc.) is inline above; "
                        "the structural `tree` JSON is stored under tree_handle / handle. "
                        "Call compact_context(tree_handle, offset, limit) or fetch_payload(tree_handle, offset, limit) "
                        "to page the tree (character offsets). Do not invent json_paths without reading it."
                    ),
                }
                out_str = json.dumps(out_obj, ensure_ascii=False, default=str)
                if not _tool_over_budget(out_str, use_utf8_bytes=use_bytes, limit=limit):
                    meta["tree_split"] = True
                    _append_tool_io_log(tool=fn_name, raw=result_str, sent=out_str)
                    return out_str, meta

    stored = store_large_payload(kind=f"{fn_name}:oversize", payload_json=result_str)
    meta["oversize_full"] = True
    cap_name = (
        "FPML_JAVA_GEN_MAX_TOOL_BYTES"
        if use_bytes
        else "FPML_JAVA_GEN_MAX_TOOL_CHARS"
    )
    out = json.dumps(
        {
            "tool": fn_name,
            "stored": True,
            "handle": stored.get("handle"),
            "sha256": stored.get("sha256"),
            "bytes": stored.get("bytes"),
            "next_step": (
                f"Payload exceeded {cap_name}; stored losslessly. "
                "Call compact_context(handle, offset, limit) or fetch_payload(handle, offset, limit) to read bytes."
            ),
        }
    )
    _append_tool_io_log(tool=fn_name, raw=result_str, sent=out)
    return out, meta


def maybe_store_oversized_tool_result_for_llm(fn_name: str, result_str: str) -> str:
    """Backward-compatible: cap tool output for LLM; returns string only."""
    out, _ = prepare_tool_result_for_llm(fn_name, result_str)
    return out


# Backward-compatible alias
compact_tool_result_for_llm = maybe_store_oversized_tool_result_for_llm


def _message_list_utf8_bytes(messages: List[Dict[str, object]]) -> int:
    total = 0
    for m in messages:
        for key in ("content", "name", "tool_call_id", "role"):
            v = m.get(key)
            if isinstance(v, str):
                total += len(v.encode("utf-8"))
        tc = m.get("tool_calls")
        if tc is not None:
            total += len(json.dumps(tc, default=str).encode("utf-8"))
    return total


def _stub_tool_content_for_prompt_budget(original: str) -> str:
    stored = store_large_payload(kind="presend:tool_message", payload_json=original)
    return json.dumps(
        {
            "stored": True,
            "context_stub": True,
            "handle": stored.get("handle"),
            "sha256": stored.get("sha256"),
            "bytes": stored.get("bytes"),
            "next_step": "Call compact_context or fetch_payload with this handle to recover exact bytes.",
        }
    )


def _tool_message_deprioritize_for_presend(content: str) -> bool:
    """True for bulky low-signal tool JSON we prefer to stub before inspect/enums."""
    try:
        d = json.loads(content)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(d, dict):
        return False
    if "content" in d and "path" in d and "lines" in d:
        return True
    if d.get("success") is False and d.get("errors"):
        return True
    return False


def _pick_presend_victim_index(messages: List[Dict[str, object]], protected: set[int]) -> int:
    tool_indices: List[int] = [
        i for i, m in enumerate(messages) if m.get("role") == "tool" and i not in protected
    ]
    if not tool_indices:
        return -1
    deprio = [
        i
        for i in tool_indices
        if isinstance(messages[i].get("content"), str)
        and _tool_message_deprioritize_for_presend(str(messages[i]["content"]))
    ]
    pool = deprio if deprio else tool_indices

    def _utf8_sz(i: int) -> int:
        c = messages[i].get("content")
        return len(c.encode("utf-8")) if isinstance(c, str) else 0

    return max(pool, key=_utf8_sz)


def _presend_compact_messages(messages: List[Dict[str, object]]) -> bool:
    """Shrink message list toward FPML_JAVA_GEN_MAX_PROMPT_CHARS (UTF-8). Returns True if any stub applied."""
    max_chars = _env_int("FPML_JAVA_GEN_MAX_PROMPT_CHARS", 0)
    if max_chars <= 0:
        return False
    headroom = _env_int("FPML_JAVA_GEN_PROMPT_HEADROOM_CHARS", 0)
    budget = max(0, max_chars - headroom)
    protect_n = max(0, _env_int("FPML_JAVA_GEN_PRESEND_PROTECT_LAST_TOOLS", 3))
    stubbed_any = False
    guard = 0
    while _message_list_utf8_bytes(messages) > budget:
        guard += 1
        if guard > max(len(messages) * 4, 4):
            if _env_flag("FPML_JAVA_GEN_LOG_PRESEND_ABORT"):
                sys.stderr.write(
                    f"  [java_gen] presend: abort after {guard - 1} pass(es); "
                    f"utf8_bytes={_message_list_utf8_bytes(messages)} budget={budget}\n"
                )
            break
        tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        if not tool_indices:
            break
        # Leave at least one tool message stubbable when multiple exist; with a single tool msg, allow stubbing it.
        n_protect = min(protect_n, max(0, len(tool_indices) - 1))
        protected = set(tool_indices[-n_protect:]) if n_protect > 0 else set()
        best_idx = _pick_presend_victim_index(messages, protected)
        if best_idx < 0:
            break
        orig = messages[best_idx]["content"]
        assert isinstance(orig, str)
        messages[best_idx]["content"] = _stub_tool_content_for_prompt_budget(orig)
        stubbed_any = True
    if stubbed_any and _message_list_utf8_bytes(messages) > budget and _env_flag(
        "FPML_JAVA_GEN_LOG_PRESEND_ABORT"
    ):
        sys.stderr.write(
            f"  [java_gen] presend: still over budget after stubbing; "
            f"utf8_bytes={_message_list_utf8_bytes(messages)} budget={budget}\n"
        )
    return stubbed_any


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
    "store_large_payload": store_large_payload,
    "fetch_payload": fetch_payload,
    "compact_context": compact_context,
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
        inspect_mode = os.environ.get("FPML_JAVA_GEN_INSPECT_DETAIL", "auto")
        full_max_nodes = _env_int("FPML_JAVA_GEN_FULL_INSPECT_MAX_NODES", 200)
        preflight = inspect_cdm_json(cdm_json_path, detail="full")
        total_nodes_raw = preflight.get("total_nodes", 0)
        total_nodes = int(total_nodes_raw) if isinstance(total_nodes_raw, int) else 0
        preflight["inspect_detail_mode"] = inspect_mode
        preflight["full_inspect_max_nodes"] = full_max_nodes
        preflight["preflight_large_trade"] = bool(total_nodes > full_max_nodes)
        if isinstance(total_nodes, int) and total_nodes:
            config = scale_java_gen_config_for_node_count(config, total_nodes)
            if log_progress:
                sys.stderr.write(
                    f"  [java_gen] total_nodes={total_nodes} "
                    f"preflight_large_trade={preflight['preflight_large_trade']} full_max_nodes={full_max_nodes} -> "
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
    try:
        trace.append(
            {
                "type": "preflight",
                "preflight_large_trade": preflight.get("preflight_large_trade"),
                "inspect_detail_mode": preflight.get("inspect_detail_mode"),
                "full_inspect_max_nodes": preflight.get("full_inspect_max_nodes"),
                "total_nodes": preflight.get("total_nodes"),
            }
        )
    except Exception:
        pass
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
        any_stored_this_turn = False
        turn_tree_split = False
        turn_oversize_full = False
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
        if _presend_compact_messages(messages):
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "NOTICE: Prompt budget (FPML_JAVA_GEN_MAX_PROMPT_CHARS) replaced one or more older "
                        "tool messages with stored handles (context_stub=true). Use compact_context or "
                        "fetch_payload with the handle to retrieve exact bytes before relying on that data."
                    ),
                }
            )
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
            raw_result_str = _execute_tool(fn_name, fn_args)
            result_str, prep_meta = prepare_tool_result_for_llm(fn_name, raw_result_str)
            if prep_meta.get("tree_split"):
                turn_tree_split = True
            if prep_meta.get("oversize_full"):
                turn_oversize_full = True
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
            try:
                if json.loads(result_str).get("stored") is True:
                    any_stored_this_turn = True
            except (json.JSONDecodeError, TypeError):
                pass

            if fn_name == "inspect_cdm_json" and prep_meta.get("tree_split"):
                auto_lim = _env_int("FPML_JAVA_GEN_AUTO_TREE_CHUNK_CHARS", 0)
                if auto_lim > 0:
                    try:
                        _ins = json.loads(result_str)
                        th = _ins.get("tree_handle") or _ins.get("handle")
                        if isinstance(th, str) and th:
                            _chunk = compact_context(th, offset=0, limit=auto_lim)
                            chunk_str = json.dumps(_chunk, ensure_ascii=False, default=str)
                            chunk_str, _ = prepare_tool_result_for_llm("compact_context", chunk_str)
                            syn_id = f"auto_tree_chunk_{total_tool_calls}"
                            messages.append(
                                {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "type": "function",
                                            "id": syn_id,
                                            "function": {
                                                "name": "compact_context",
                                                "arguments": json.dumps(
                                                    {"handle": th, "offset": 0, "limit": auto_lim}
                                                ),
                                            },
                                        }
                                    ],
                                }
                            )
                            messages.append(
                                {"role": "tool", "tool_call_id": syn_id, "content": chunk_str}
                            )
                            total_tool_calls += 1
                            trace.append(
                                {
                                    "iteration": iteration,
                                    "type": "tool_call",
                                    "tool": "compact_context",
                                    "args": {
                                        "handle": th,
                                        "offset": 0,
                                        "limit": auto_lim,
                                        "auto_injected": True,
                                    },
                                }
                            )
                            trace.append(
                                {
                                    "iteration": iteration,
                                    "type": "tool_result",
                                    "tool": "compact_context",
                                    "result_preview": chunk_str[:500],
                                }
                            )
                            if log_progress:
                                sys.stderr.write("    [deterministic] compact_context (auto tree chunk)\n")
                    except (json.JSONDecodeError, TypeError, KeyError):
                        pass
            if _env_flag("FPML_JAVA_GEN_LOG_TOOL_BYTES"):
                try:
                    raw_bytes = len(raw_result_str.encode("utf-8"))
                except Exception:
                    raw_bytes = -1
                try:
                    out_bytes = len(result_str.encode("utf-8"))
                except Exception:
                    out_bytes = -1
                stored_b = False
                try:
                    d = json.loads(result_str)
                    stored_b = bool(d.get("stored") is True)
                except Exception:
                    pass
                sys.stderr.write(
                    f"      [tool_bytes] {fn_name} raw={raw_bytes} out={out_bytes} stored={stored_b}\n"
                )

        if turn_tree_split:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "NOTICE: inspect_cdm_json returned with the structural tree externalized (tree_handle). "
                        "Type registry, reference hints, and warnings are inline in that tool result—page the tree "
                        "with compact_context(tree_handle, offset, limit) only as needed. Do not invent json_paths."
                    ),
                }
            )
        elif turn_oversize_full:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "NOTICE: A tool result exceeded the tool output cap (FPML_JAVA_GEN_MAX_TOOL_CHARS or "
                        "FPML_JAVA_GEN_MAX_TOOL_BYTES). Full payload is behind handle; use compact_context or "
                        "fetch_payload before writing code that depends on that data. Do not guess missing structure."
                    ),
                }
            )
        elif any_stored_this_turn:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "NOTICE: A tool result used external storage (stored=true). Retrieve exact bytes with "
                        "compact_context(handle, offset, limit) or fetch_payload before relying on that data."
                    ),
                }
            )

        if consecutive_single_patches >= 3 and not patch_loop_nudge_sent:
            messages.append({
                "role": "user",
                "content": (
                    "WARNING: You have called patch_java_file repeatedly with single-patch calls only. "
                    "This wastes iterations. STOP patching one fix at a time. Instead:\n"
                    "1. Call read_java_file to load the full generated file and find ALL occurrences of the same pattern.\n"
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
                    run_result_str, _ = prepare_tool_result_for_llm(
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
