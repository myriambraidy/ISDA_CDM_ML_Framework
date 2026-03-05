"""ReAct-style agent loop for CDM Java code generation."""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

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
    diff_json,
    validate_output,
    finish,
)

TOOLS_JSON = Path(__file__).parent / "tools.json"


def _partial_result_from_trace(
    trace: List[Dict[str, object]], summary_prefix: str
) -> tuple[float, Optional[str], str]:
    """Extract last diff_json match_percentage and write_java_file path from trace."""
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
        if tool == "diff_json":
            m = re.search(r'"match_percentage":\s*([\d.]+)', preview)
            if m:
                match_pct = float(m.group(1))
        elif tool == "write_java_file" and java_file is None:
            if '"success": true' in preview:
                m = re.search(r'"path":\s*"([^"]*)"', preview)
                if m:
                    java_file = m.group(1).replace("\\", "/")
        if match_pct > 0 and java_file is not None:
            break
    summary = summary_prefix
    if match_pct > 0 or java_file:
        parts = [f"last diff: {match_pct}% match" if match_pct > 0 else "", f"java_file: {java_file}" if java_file else ""]
        summary = f"{summary_prefix} ({'; '.join(p for p in parts if p)})"
    return (match_pct, java_file, summary)


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


SYSTEM_PROMPT = """\
You are a Java code generator for ISDA CDM (Common Domain Model) trades.

Your task: Given a CDM JSON file, generate executable Java code that reconstructs the
same trade using CDM Java builder patterns.

## Strategy

Follow this workflow:
1. Call `inspect_cdm_json` to understand the full structure.
   - The response includes a **type_registry** with pre-resolved Java imports, builder
     entries, and package names for EVERY type found. Use this directly — do NOT call
     `resolve_java_type` for types already in the registry.
   - The response also includes **java_type_warnings** listing fields where the actual
     Java type differs from the JSON schema type. PAY CLOSE ATTENTION to these.
2. Call `get_java_template` for the boilerplate.
3. For complex nested types, call `lookup_cdm_schema` to get property names and setter
   hints. Batch multiple lookups in a single turn when possible.
4. For enum values, call `list_enum_values` to get the correct Java constants.
5. Generate the complete Java code and call `write_java_file`.
6. Call `compile_java` — if errors, fix them using `patch_java_file` and recompile.
   Read the error hints carefully — they tell you exactly what type to use.
7. Call `run_java` — capture the output.
8. Call `diff_json` to compare output vs input.
9. If differences exist, fix the code and repeat from step 6.
10. Call `finish` with the result.

## CDM Java Builder Conventions
- Single values: `.setFieldName(value)`
- Array values: `.addFieldName(item)` (one call per item)
- Nested objects: `.setField(TypeName.builder()...build())`
- Strings wrapped in FieldWithMetaString: `FieldWithMetaString.builder().setValue("...").build()`
- Numbers: use `java.math.BigDecimal` for decimals
- Enums: use the enum class constant (e.g., `CounterpartyRoleEnum.PARTY_1`)

## CRITICAL: CDM Date Types
The JSON schemas represent dates as strings, but CDM Java uses typed date classes:
- **tradeDate**: use `FieldWithMetaDate.builder().setValue(Date.of(YYYY, MM, DD)).build()`
  Import: `com.rosetta.model.metafields.FieldWithMetaDate` and `com.rosetta.model.lib.records.Date`
- **valueDate, unadjustedDate**: use `Date.of(YYYY, MM, DD)`
  Import: `com.rosetta.model.lib.records.Date`
- **adjustedDate**: use `FieldWithMetaDate.builder().setValue(Date.of(YYYY, MM, DD)).build()`
- Do NOT use `java.time.LocalDate` or plain `String` for CDM date fields.

## CRITICAL: Underlier (e.g. FX Forward)
When the CDM has an `underlier` (e.g. FX forward with Asset.Cash and a currency), you MUST build
the full underlier structure from schema (Underlier → Observable → Asset → Cash → identifier),
not `ReferenceWithMetaObservable.builder().setValue(null)`. Use `lookup_cdm_schema` for Underlier,
Observable, Asset, Cash as needed and construct the full builder chain so the serialized JSON matches.

## Efficiency Rules
- **Batch lookups**: After `inspect_cdm_json`, call `lookup_cdm_schema` for ALL types
  you need in one turn (the LLM supports parallel tool calls).
- **Batch patches**: Use the `patches` parameter of `patch_java_file` to apply multiple
  independent fixes in a single call instead of one patch per call.
- **Use type_registry**: The `inspect_cdm_json` response already has imports and builder
  entries — use them directly. Only call `resolve_java_type` for types NOT in the registry.

## Rules
- ALWAYS look up schemas before assuming type names or method signatures
- ALWAYS look up enums before using them — don't guess Java constant names
- When compilation fails, read ALL errors and batch independent fixes together
- The generated code must be self-contained in a single Java file (no package statement)
- Use fully-qualified class names in the code OR add imports — never leave symbols unresolved
"""


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
    "diff_json": diff_json,
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
) -> AgentResult:
    """Main agent loop: LLM + tools until finish or limits reached."""
    config = config or AgentConfig()
    if log_progress is None:
        log_progress = sys.stderr.isatty()
    tool_specs = load_tool_specs()
    start_time = time.time()
    trace: List[Dict[str, object]] = []
    total_tool_calls = 0
    last_tool_key = ""
    repeat_count = 0
    total_tool_time = 0.0
    total_llm_time = 0.0

    messages: List[Dict[str, object]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Generate Java code that builds the CDM trade defined in: {cdm_json_path}\n"
                f"The code must compile against the CDM classpath and produce JSON output "
                f"matching the input file when executed."
            ),
        },
    ]

    for iteration in range(config.max_iterations):
        elapsed = time.time() - start_time
        if elapsed > config.timeout_seconds:
            match_pct, java_path, summary = _partial_result_from_trace(
                trace, f"Timeout after {config.timeout_seconds}s"
            )
            return AgentResult(
                success=False,
                java_file=java_path,
                match_percentage=match_pct,
                iterations=iteration,
                total_tool_calls=total_tool_calls,
                duration_seconds=elapsed,
                summary=summary,
                trace=trace,
            )

        if total_tool_calls >= config.max_tool_calls:
            match_pct, java_path, summary = _partial_result_from_trace(
                trace, f"Max tool calls ({config.max_tool_calls}) reached"
            )
            return AgentResult(
                success=False,
                java_file=java_path,
                match_percentage=match_pct,
                iterations=iteration,
                total_tool_calls=total_tool_calls,
                duration_seconds=time.time() - start_time,
                summary=summary,
                trace=trace,
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
            continue

        for tool_call in message.tool_calls:
            total_tool_calls += 1
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments)

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

        if log_progress:
            sys.stderr.write(f"  [iter {iteration+1}] {total_tool_calls} tool calls (total so far: {total_tool_time:.1f}s tools, {total_llm_time:.1f}s LLM)\n")

    duration = time.time() - start_time
    match_pct, java_path, summary = _partial_result_from_trace(
        trace, f"Max iterations ({config.max_iterations}) reached without finish"
    )
    return AgentResult(
        success=False,
        java_file=java_path,
        match_percentage=match_pct,
        iterations=config.max_iterations,
        total_tool_calls=total_tool_calls,
        duration_seconds=duration,
        summary=summary,
        trace=trace,
    )
