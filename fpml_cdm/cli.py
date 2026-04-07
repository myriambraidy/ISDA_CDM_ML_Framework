from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .llm.base import get_llm_provider
from .mapping_agent.agent import MappingAgentConfig
from .parser import parse_fpml_fx
from .pipeline import convert_fpml_to_cdm
from .transformer import transform_to_cdm_v6
from .types import (
    NORMALIZED_KIND_FX_OPTION,
    NORMALIZED_KIND_FX_SWAP,
    NormalizedFxForward,
    NormalizedFxOption,
    NormalizedFxSwap,
    ParserError,
    ValidationIssue,
)
from .validator import validate_conversion_files, validate_schema_data


def _resolve_existing_input_file(raw: str) -> Path:
    """Resolve a user-supplied path to an absolute Path; raise FileNotFoundError if missing.

    Use this for CLI args so ``tmp\\res\\file.json`` is not mangled by the shell
    (e.g. Git Bash treats ``\\r`` as carriage return in unquoted paths). Forward
    slashes or quoting avoids that; resolving here still catches missing files early.
    """
    p = Path(raw).expanduser()
    p = p.resolve(strict=False)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    return p


def _write_json(data, output: str | None) -> None:
    text = json.dumps(data, indent=2)
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)


def _issues_to_dict(issues: list[ValidationIssue]) -> list[dict]:
    return [issue.to_dict() for issue in issues]


def cmd_parse(args: argparse.Namespace) -> int:
    try:
        model = parse_fpml_fx(args.input, strict=not args.no_strict)
    except ParserError as exc:
        _write_json({"ok": False, "errors": _issues_to_dict(exc.issues)}, args.output)
        return 1

    _write_json(model.to_dict(), args.output)
    return 0


def cmd_transform(args: argparse.Namespace) -> int:
    with open(args.input, "r", encoding="utf-8") as f:
        parsed = json.load(f)
    kind = parsed.get("normalizedKind")
    if kind == NORMALIZED_KIND_FX_SWAP:
        model = NormalizedFxSwap.from_dict(parsed)
    elif kind == NORMALIZED_KIND_FX_OPTION:
        model = NormalizedFxOption.from_dict(parsed)
    else:
        model = NormalizedFxForward.from_dict(parsed)
    cdm = transform_to_cdm_v6(model)
    _write_json(cdm, args.output)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    report = validate_conversion_files(args.fpml, args.cdm)
    _write_json(report.to_dict(), args.output)
    return 0 if report.valid else 1


def cmd_validate_schema(args: argparse.Namespace) -> int:
    """Validate a JSON file against a schema only (no FpML, no semantic check)."""
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)
    schema_name = "cdm_fx_forward.schema.json" if args.schema == "cdm" else "fpml_normalized_trade.schema.json"
    issues = validate_schema_data(schema_name, data)
    report = {"valid": len(issues) == 0, "errors": _issues_to_dict(issues)}
    _write_json(report, args.output)
    return 0 if report["valid"] else 1


_FAILURE_HINTS = {
    "TradeSettlementPayout": (
        "Trade is missing 'partyRole' entries with Buyer/Seller roles.\n"
        "  FIX: Add partyRole array to the Trade object, e.g.:\n"
        '       "partyRole": [\n'
        '         {"role": "Buyer", "partyReference": {"globalReference": "party1"}},\n'
        '         {"role": "Seller", "partyReference": {"globalReference": "party2"}}\n'
        "       ]"
    ),
    "SettlementDateBusinessDays": (
        "Settlement date uses a field name the validator doesn't recognize.\n"
        "  FIX: Use 'adjustableOrRelativeDate' (CDM v6) instead of 'adjustableOrAdjustedDate' (v5).\n"
        "       Or use 'valueDate' directly for simple dates."
    ),
    "UnderlierChoice": (
        "SettlementPayout.underlier is empty — must have 'Observable' or 'Product'.\n"
        "  FIX: For FX, set underlier.Observable to the FX rate observable, e.g.:\n"
        '       "underlier": {"Observable": {"currencyPair": {"currency1": "USD", "currency2": "EUR"}}}'
    ),
    "IdentifierIssuerChoice": (
        "TradeIdentifier is missing 'issuer' or 'issuerReference'.\n"
        "  FIX: Add issuer (e.g. LEI or party ID) to each tradeIdentifier."
    ),
}


def _print_diagnostic(result, input_path: str, verbose: bool) -> None:
    """Print human-readable diagnostic to stderr."""
    import sys
    err = sys.stderr

    err.write(f"\n{'='*60}\n")
    err.write(f"  Rosetta CDM Validator Report\n")
    err.write(f"{'='*60}\n\n")

    err.write(f"  Input:   {input_path}\n")
    err.write(f"  Status:  {'PASS' if result.valid else 'FAIL'}\n")
    err.write(f"  Failures: {len(result.failures)}\n")

    if result.error:
        err.write(f"\n  RUNTIME ERROR: {result.error}\n")

    if not result.failures:
        err.write(f"\n  All Rosetta type validations passed.\n")
        err.write(f"{'='*60}\n\n")
        return

    err.write(f"\n{'─'*60}\n")

    for i, f in enumerate(result.failures, 1):
        name = f.get("name", "?")
        rule_type = f.get("type", "UNKNOWN")
        path = f.get("path", "")
        definition = f.get("definition", "")
        message = f.get("failureMessage", "")

        err.write(f"\n  [{i}/{len(result.failures)}] {name}\n")
        err.write(f"  Type:    {rule_type}\n")
        err.write(f"  Path:    {path}\n")

        if message:
            err.write(f"  Error:   {message}\n")

        if verbose and definition:
            err.write(f"  Rule:    {definition}\n")

        hint = _FAILURE_HINTS.get(name)
        if hint:
            err.write(f"\n  Hint:\n")
            for line in hint.splitlines():
                err.write(f"    {line}\n")

        err.write(f"\n{'─'*60}\n")

    err.write(f"\nSummary: {len(result.failures)} failure(s) — fix the transformer output.\n")
    err.write(f"{'='*60}\n\n")


def cmd_validate_rosetta(args: argparse.Namespace) -> int:
    """Validate CDM JSON against the Rosetta type system (requires Java + built JAR)."""
    import sys
    from .rosetta_validator import validate_cdm_rosetta, find_jar, java_available

    err = sys.stderr
    verbose = args.verbose

    err.write("\n[1/4] Checking prerequisites...\n")
    if not java_available():
        err.write("  FAIL: Java not found on PATH. Install JDK 11+.\n")
        return 2
    err.write("  OK:  Java found\n")

    jar = find_jar()
    if jar is None:
        err.write("  FAIL: Rosetta validator JAR not found.\n")
        err.write("  Run:  cd rosetta-validator && mvn package -q\n")
        return 2
    err.write(f"  OK:  JAR found at {jar}\n")

    err.write(f"\n[2/4] Loading CDM JSON from {args.input}...\n")
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            cdm_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        err.write(f"  FAIL: {exc}\n")
        return 2

    trade = cdm_data.get("trade", {})
    top_keys = list(trade.keys()) if trade else []
    err.write(f"  OK:  Loaded ({len(json.dumps(cdm_data))} bytes)\n")
    if verbose:
        err.write(f"  Trade top-level keys: {top_keys}\n")

    err.write(f"\n[3/4] Running RosettaTypeValidator (Java)...\n")
    try:
        result = validate_cdm_rosetta(
            cdm_data,
            target_type=args.type,
            timeout_seconds=args.timeout,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        err.write(f"  FAIL: {exc}\n")
        _write_json({"valid": False, "error": str(exc)}, args.output)
        return 2

    err.write(f"  Done (exit code {result.exit_code})\n")

    err.write(f"\n[4/4] Results\n")
    _print_diagnostic(result, args.input, verbose)

    if args.output:
        _write_json(result.to_dict(), args.output)
        err.write(f"  JSON report written to {args.output}\n")
    elif not args.quiet:
        _write_json(result.to_dict(), None)

    return 0 if result.valid else 1


def cmd_validate_cdm_structure(args: argparse.Namespace) -> int:
    """Unified CDM v6 structural validation (envelope + JSON Schema + Rosetta + supplementary)."""
    import sys

    from .cdm_structure_validator import infra_blocked, validate_cdm_structure

    err = sys.stderr
    try:
        path = _resolve_existing_input_file(args.input)
    except FileNotFoundError as exc:
        err.write(f"{exc}\n")
        return 2

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        err.write(f"Invalid JSON: {exc}\n")
        return 2

    if getattr(args, "no_rosetta", False):
        err.write(
            "WARNING: --no-rosetta skips the Rosetta JVM layer; not a full structural gate.\n",
        )

    allow = bool(getattr(args, "allow_no_rosetta", False))
    report = validate_cdm_structure(
        data,
        target_type=args.target_type,
        run_schema=not getattr(args, "no_schema", False),
        run_rosetta=not getattr(args, "no_rosetta", False),
        supplementary=not getattr(args, "no_supplementary", False),
        rosetta_timeout_seconds=int(getattr(args, "timeout", 60)),
        allow_no_rosetta=allow if allow else None,
    )
    out_dict = report.to_dict()
    _write_json(out_dict, args.output)

    codes = [str(i.code) for i in report.issues]
    if not report.structure_ok:
        if infra_blocked(codes) and not allow:
            return 2
        return 1
    return 0


def _resolve_llm_provider(args: argparse.Namespace):
    provider_name = getattr(args, "llm_provider", "none") or "none"
    if provider_name == "none":
        return None
    return get_llm_provider(
        provider_name=provider_name,
        model=getattr(args, "llm_model", None),
        base_url=getattr(args, "llm_base_url", None),
    )


def _resolve_mapping_llm_client(args: argparse.Namespace):
    provider_name = getattr(args, "mapping_provider", "none") or "none"
    if provider_name == "none":
        return None
    if provider_name == "openai":
        try:
            import openai
        except ImportError:
            raise RuntimeError("openai package not installed. Run: pip install openai")
        return openai.OpenAI(
            api_key=getattr(args, "mapping_api_key", None) or os.environ.get("OPENAI_API_KEY"),
            base_url=getattr(args, "mapping_base_url", None) or None,
        )
    if provider_name == "openrouter":
        from .java_gen.openrouter_client import OpenRouterClient

        api_key = (getattr(args, "mapping_api_key", None) or os.environ.get("OPENROUTER_API_KEY", "")).strip()
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY missing for mapping-provider=openrouter")
        return OpenRouterClient(api_key=api_key)
    raise RuntimeError(f"Unknown mapping provider: {provider_name}")


def cmd_convert(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    llm_provider = _resolve_llm_provider(args)
    try:
        mapping_llm_client = _resolve_mapping_llm_client(args)
    except RuntimeError as exc:
        _write_json({"ok": False, "error": str(exc)}, args.output)
        return 2
    mapping_cfg = MappingAgentConfig(
        max_iterations=args.mapping_max_iterations,
        max_tool_calls=args.mapping_max_tool_calls,
        timeout_seconds=args.mapping_timeout,
        semantic_no_improve_limit=args.mapping_no_improve,
        enable_rosetta=True,
        rosetta_timeout_seconds=args.mapping_rosetta_timeout,
    )
    result = convert_fpml_to_cdm(
        args.input,
        strict=not args.no_strict,
        llm_provider=llm_provider,
        mapping_llm_client=mapping_llm_client,
        mapping_model=args.mapping_model,
        mapping_config=mapping_cfg,
    )

    payload = result.to_dict()
    _write_json(payload, args.output)

    if args.normalized_output and result.normalized is not None:
        _write_json(result.normalized.to_dict(), args.normalized_output)
    if args.cdm_output and result.cdm is not None:
        _write_json(result.cdm, args.cdm_output)
    if args.report_output and result.validation is not None:
        _write_json(result.validation.to_dict(), args.report_output)
    if args.review_ticket_output and result.review_ticket is not None:
        _write_json(result.review_ticket, args.review_ticket_output)

    if not result.ok and args.strict_ci:
        return 1
    return 0 if result.ok else 1


def cmd_generate_java(args: argparse.Namespace) -> int:
    """Generate Java code from CDM JSON using agent loop."""
    from dotenv import load_dotenv
    load_dotenv()

    from .java_gen.agent import run_agent, AgentConfig

    err = sys.stderr
    try:
        cdm_json_path = _resolve_existing_input_file(args.input)
    except FileNotFoundError as exc:
        err.write(f"\n  FAIL: CDM JSON not found: {args.input}\n")
        err.write(f"  Resolved to: {exc.args[0]}\n")
        err.write(
            "  Hint: On Git Bash, backslashes can break paths (\\\\r = carriage return). "
            "Use forward slashes, e.g. tmp/res/transformer-fx-ex8.json, or quote the path.\n"
        )
        return 2

    err.write(f"\n[1/2] Initializing agent for {cdm_json_path}...\n")

    if getattr(args, "debug_openrouter", False):
        import os
        os.environ["FPML_OPENROUTER_LOG_REQUEST_BYTES"] = "1"
        err.write("  OpenRouter debug: logging request JSON size each call (FPML_OPENROUTER_LOG_REQUEST_BYTES=1)\n")

    config = AgentConfig(
        max_iterations=args.max_iterations,
        max_tool_calls=args.max_tool_calls,
        timeout_seconds=args.timeout,
    )

    if getattr(args, "provider", "openrouter") == "openai":
        try:
            import openai
            client = openai.OpenAI(
                api_key=getattr(args, "api_key", None) or None,
                base_url=getattr(args, "base_url", None) or None,
            )
        except ImportError:
            err.write("  FAIL: openai package not installed. Run: pip install openai\n")
            return 2
    else:
        import os
        from .java_gen.openrouter_client import OpenRouterClient
        api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            err.write("  FAIL: OPENROUTER_API_KEY not set. Set it in .env or the environment.\n")
            return 2
        try:
            client = OpenRouterClient(api_key=api_key)
        except ValueError as e:
            err.write(f"  FAIL: {e}\n")
            return 2

    err.write(f"  Model: {args.model}\n")
    err.write(f"  Max iterations: {config.max_iterations}\n")
    err.write(f"\n[2/2] Running agent loop...\n")

    log_progress: bool | None = True if getattr(args, "verbose", False) else (False if getattr(args, "quiet", False) else None)
    java_class = getattr(args, "java_class", None)
    java_class = java_class.strip() if isinstance(java_class, str) and java_class.strip() else None

    result = run_agent(
        cdm_json_path=str(cdm_json_path),
        llm_client=client,
        model=args.model,
        config=config,
        log_progress=log_progress,
        java_class_name=java_class,
    )

    err.write(f"\nAgent completed in {result.duration_seconds:.1f}s\n")
    err.write(f"  Iterations:  {result.iterations}\n")
    err.write(f"  Tool calls:  {result.total_tool_calls}\n")
    err.write(f"  Match:       {result.match_percentage}%  (diff_json vs expected CDM when run_java stdout captured)\n")
    v = getattr(result, "verification", None)
    if isinstance(v, dict):
        llm_m = v.get("llm_reported_match_percentage")
        if llm_m is not None and float(llm_m) != float(result.match_percentage):
            err.write(f"  LLM claimed: {llm_m}% (informational only)\n")
        dj = v.get("diff_json")
        if isinstance(dj, dict) and dj.get("error"):
            err.write(f"  diff_json:   ERROR {dj.get('error')}\n")
        cs = v.get("cdm_structure")
        if isinstance(cs, dict) and cs.get("structure_ok") is not None:
            err.write(f"  structure_ok: {cs.get('structure_ok')}\n")
        if v.get("note") == "no_run_java_stdout_for_verification":
            err.write("  Verification: skipped (no successful run_java stdout in session)\n")
    err.write(f"  Status:      {'SUCCESS' if result.success else 'FAILURE'}\n")
    err.write(f"  Summary:     {result.summary}\n")

    if args.trace_output:
        _write_json({"trace": result.trace, "result": result.to_dict()}, args.trace_output)
        err.write(f"  Trace:       {args.trace_output}\n")

    if result.java_file:
        err.write(f"  Java file:   {result.java_file}\n")

    return 0 if result.success else 1


def cmd_generate_java_from_fpml(args: argparse.Namespace) -> int:
    """Generate Java code from FpML via mapping agent + existing Java-gen agent."""
    from dotenv import load_dotenv

    load_dotenv()

    from .fpml_to_cdm_java import generate_java_from_fpml
    from .mapping_agent.agent import MappingAgentConfig
    from .java_gen.agent import AgentConfig

    err = sys.stderr
    err.write(f"\n[1/3] Initializing agents for {args.input}...\n")

    mapping_model = args.mapping_model or args.model

    mapping_cfg = MappingAgentConfig(
        max_iterations=args.mapping_max_iterations,
        max_tool_calls=args.mapping_max_tool_calls,
        timeout_seconds=args.mapping_timeout,
    )
    java_cfg = AgentConfig(
        max_iterations=args.max_iterations,
        max_tool_calls=args.max_tool_calls,
        timeout_seconds=args.timeout,
    )

    if getattr(args, "provider", "openrouter") == "openai":
        try:
            import openai

            client = openai.OpenAI(
                api_key=getattr(args, "api_key", None) or None,
                base_url=getattr(args, "base_url", None) or None,
            )
        except ImportError:
            err.write("  FAIL: openai package not installed. Run: pip install openai\n")
            return 2
    else:
        import os
        from .java_gen.openrouter_client import OpenRouterClient

        api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            err.write("  FAIL: OPENROUTER_API_KEY not set. Set it in .env or the environment.\n")
            return 2
        try:
            client = OpenRouterClient(api_key=api_key)
        except ValueError as e:
            err.write(f"  FAIL: {e}\n")
            return 2

    err.write(f"  LLM model (java+mapping): {args.model}\n")
    err.write(f"  Mapping model: {mapping_model}\n")
    err.write(f"  Output dir: {args.output_dir}\n")

    log_progress: bool | None = True if getattr(args, "verbose", False) else (False if getattr(args, "quiet", False) else None)

    err.write("\n[2/3] Converting FpML to best CDM JSON...\n")
    java_class = getattr(args, "java_class", None)
    java_class = java_class.strip() if isinstance(java_class, str) and java_class.strip() else None

    java_result, mapping_result, cdm_json_path = generate_java_from_fpml(
        args.input,
        llm_client=client,
        mapping_model=mapping_model,
        java_model=args.model,
        mapping_enabled=not bool(args.no_mapping_agent),
        mapping_config=mapping_cfg,
        java_config=java_cfg,
        log_progress=log_progress,
        output_dir=args.output_dir,
        java_class_name=java_class,
    )

    # Write mapping artifacts.
    if mapping_result is not None:
        mapping_trace_path = Path(args.output_dir) / "mapping_trace.json"
        _write_json(
            {
                "mapping_result": mapping_result.to_dict(),
                "trace": mapping_result.trace,
                "best_cdm_json_path": str(cdm_json_path),
            },
            str(mapping_trace_path),
        )
        err.write(f"  Mapping trace: {mapping_trace_path}\n")

    err.write("\n[3/3] Running Java code generation agent...\n")

    # The Java agent already ran inside generate_java_from_fpml.
    err.write(f"\nAgent completed in {java_result.duration_seconds:.1f}s\n")
    err.write(f"  Iterations:  {java_result.iterations}\n")
    err.write(f"  Tool calls:  {java_result.total_tool_calls}\n")
    err.write(f"  Match:       {java_result.match_percentage}%  (diff_json vs expected CDM when run_java stdout captured)\n")
    jv = getattr(java_result, "verification", None)
    if isinstance(jv, dict):
        llm_m = jv.get("llm_reported_match_percentage")
        if llm_m is not None and float(llm_m) != float(java_result.match_percentage):
            err.write(f"  LLM claimed: {llm_m}% (informational only)\n")
        dj = jv.get("diff_json")
        if isinstance(dj, dict) and dj.get("error"):
            err.write(f"  diff_json:   ERROR {dj.get('error')}\n")
        cs = jv.get("cdm_structure")
        if isinstance(cs, dict) and cs.get("structure_ok") is not None:
            err.write(f"  structure_ok: {cs.get('structure_ok')}\n")
        if jv.get("note") == "no_run_java_stdout_for_verification":
            err.write("  Verification: skipped (no successful run_java stdout in session)\n")
    err.write(f"  Status:      {'SUCCESS' if java_result.success else 'FAILURE'}\n")
    err.write(f"  Summary:     {java_result.summary}\n")

    if args.trace_output:
        _write_json({"trace": java_result.trace, "result": java_result.to_dict()}, args.trace_output)
        err.write(f"  Trace:       {args.trace_output}\n")

    if java_result.java_file:
        err.write(f"  Java file:   {java_result.java_file}\n")

    return 0 if java_result.success else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic FpML FX Forward -> CDM v6 converter")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_parser = subparsers.add_parser("parse", help="Parse FpML XML into normalized JSON")
    parse_parser.add_argument("input", help="Input FpML XML file")
    parse_parser.add_argument("--output", "-o", help="Output normalized JSON file")
    parse_parser.add_argument("--no-strict", action="store_true", help="Reserved for compatibility; strict parsing is default")
    parse_parser.set_defaults(func=cmd_parse)

    transform_parser = subparsers.add_parser("transform", help="Transform normalized JSON into CDM JSON")
    transform_parser.add_argument("input", help="Input normalized JSON file")
    transform_parser.add_argument("--output", "-o", help="Output CDM JSON file")
    transform_parser.set_defaults(func=cmd_transform)

    validate_parser = subparsers.add_parser("validate", help="Validate CDM output against source FpML (schema + semantic)")
    validate_parser.add_argument("--fpml", required=True, help="Source FpML XML file")
    validate_parser.add_argument("--cdm", required=True, help="CDM JSON file")
    validate_parser.add_argument("--output", "-o", help="Validation report JSON file")
    validate_parser.set_defaults(func=cmd_validate)

    validate_schema_parser = subparsers.add_parser("validate-schema", help="Validate a JSON file against schema only (cdm or parsed)")
    validate_schema_parser.add_argument("input", help="Input JSON file (CDM or normalized)")
    validate_schema_parser.add_argument("--schema", choices=("cdm", "parsed"), default="cdm", help="Schema to use (default: cdm)")
    validate_schema_parser.add_argument("--output", "-o", help="Validation report JSON file")
    validate_schema_parser.set_defaults(func=cmd_validate_schema)

    rosetta_parser = subparsers.add_parser("validate-rosetta", help="Validate CDM JSON against Rosetta type system (requires Java)")
    rosetta_parser.add_argument("input", help="CDM JSON file to validate")
    rosetta_parser.add_argument("--type", choices=("trade", "tradeState"), default="trade", help="CDM root type (default: trade)")
    rosetta_parser.add_argument("--timeout", type=int, default=60, help="Timeout in seconds (default: 60)")
    rosetta_parser.add_argument("--output", "-o", help="Validation report JSON file")
    rosetta_parser.add_argument("--verbose", "-v", action="store_true", help="Show rule definitions and CDM structure details")
    rosetta_parser.add_argument("--quiet", "-q", action="store_true", help="Suppress JSON output to stdout (diagnostic still goes to stderr)")
    rosetta_parser.set_defaults(func=cmd_validate_rosetta)

    cdm_struct_parser = subparsers.add_parser(
        "validate-cdm-structure",
        help="Unified CDM v6 validation: envelope + JSON Schema + Rosetta + supplementary",
    )
    cdm_struct_parser.add_argument("input", help="CDM JSON file (object with top-level trade or tradeState)")
    cdm_struct_parser.add_argument("--output", "-o", help="Write full validation report JSON to this path")
    cdm_struct_parser.add_argument(
        "--target-type",
        choices=("trade", "tradeState"),
        default="trade",
        help="Top-level CDM key to validate (default: trade)",
    )
    cdm_struct_parser.add_argument("--timeout", type=int, default=60, help="Rosetta JVM timeout in seconds (default: 60)")
    cdm_struct_parser.add_argument(
        "--no-rosetta",
        action="store_true",
        help="Skip Rosetta JVM layer (debug only; exit code may still be non-zero)",
    )
    cdm_struct_parser.add_argument(
        "--no-schema",
        action="store_true",
        help="Skip official JSON Schema (Draft 04) layer",
    )
    cdm_struct_parser.add_argument(
        "--no-supplementary",
        action="store_true",
        help="Skip supplementary checker registry",
    )
    cdm_struct_parser.add_argument(
        "--allow-no-rosetta",
        action="store_true",
        help=f"Allow missing Java/JAR (unsafe; sets env-style skip; see FPML_CDM_ALLOW_NO_ROSETTA)",
    )
    cdm_struct_parser.set_defaults(func=cmd_validate_cdm_structure)

    convert_parser = subparsers.add_parser("convert", help="Run parse -> transform -> validate")
    convert_parser.add_argument("input", help="Input FpML XML file")
    convert_parser.add_argument("--output", "-o", help="Output full conversion result JSON")
    convert_parser.add_argument("--normalized-output", help="Optional normalized JSON output path")
    convert_parser.add_argument("--cdm-output", help="Optional CDM JSON output path")
    convert_parser.add_argument("--report-output", help="Optional validation report output path")
    convert_parser.add_argument("--no-strict", action="store_true", help="Reserved for compatibility; strict parsing is default")
    convert_parser.add_argument("--llm-provider", default="none", help="LLM provider for field recovery: none, gemini, openai_compat (default: none)")
    convert_parser.add_argument("--llm-base-url", default="http://localhost:11434/v1", help="Base URL for openai_compat provider (default: http://localhost:11434/v1)")
    convert_parser.add_argument("--llm-model", default="llama3.2", help="Model name for LLM provider (default: llama3.2)")
    convert_parser.add_argument("--mapping-provider", choices=("none", "openrouter", "openai"), default="none", help="Provider for mapping-agent refinement (default: none)")
    convert_parser.add_argument("--mapping-api-key", default=None, help="API key for mapping provider (optional; env vars supported)")
    convert_parser.add_argument("--mapping-base-url", default=None, help="Base URL for mapping-provider=openai")
    convert_parser.add_argument("--mapping-model", default="minimax/minimax-m2.7", help="Model for mapping-agent refinement")
    convert_parser.add_argument("--mapping-max-iterations", type=int, default=10, help="Mapping-agent max iterations")
    convert_parser.add_argument("--mapping-max-tool-calls", type=int, default=80, help="Mapping-agent max total tool calls")
    convert_parser.add_argument("--mapping-timeout", type=int, default=300, help="Mapping-agent timeout in seconds")
    convert_parser.add_argument("--mapping-no-improve", type=int, default=3, help="Mapping-agent semantic no-improvement threshold")
    convert_parser.add_argument("--mapping-rosetta-timeout", type=int, default=60, help="Rosetta timeout in seconds during mapping compliance checks")
    convert_parser.add_argument("--review-ticket-output", help="Optional review-ticket JSON output path when non-compliant")
    convert_parser.add_argument("--strict-ci", action="store_true", help="Return non-zero exit code for non-compliant outputs")
    convert_parser.set_defaults(func=cmd_convert)

    java_gen_parser = subparsers.add_parser(
        "generate-java",
        help="Generate Java code from CDM JSON using agent loop (OpenRouter by default)",
    )
    java_gen_parser.add_argument(
        "input",
        help="CDM JSON file path (prefer forward slashes on Git Bash, e.g. tmp/res/x.json)",
    )
    java_gen_parser.add_argument("--provider", choices=("openrouter", "openai"), default="openrouter", help="LLM provider (default: openrouter)")
    java_gen_parser.add_argument("--model", default="minimax/minimax-m2.7", help="LLM model name (default: minimax/minimax-m2.5)")
    java_gen_parser.add_argument("--api-key", default=None, help="API key (for --provider openai: OpenAI key; else ignored)")
    java_gen_parser.add_argument("--base-url", default=None, help="Base URL (for --provider openai only)")
    java_gen_parser.add_argument("--max-iterations", type=int, default=20, help="Max agent iterations (default: 20)")
    java_gen_parser.add_argument("--max-tool-calls", type=int, default=50, help="Max total tool calls (default: 50)")
    java_gen_parser.add_argument("--timeout", type=int, default=600, help="Agent timeout in seconds; increase for large CDM or slow models (default: 600)")
    java_gen_parser.add_argument("--trace-output", help="Write agent trace JSON to file")
    java_gen_parser.add_argument(
        "--java-class",
        dest="java_class",
        default=None,
        help="Public Java class name and generated/<Name>.java (default: derived from CDM JSON filename stem)",
    )
    java_gen_parser.add_argument("--verbose", "-v", action="store_true", help="Always show per-tool and LLM timing logs")
    java_gen_parser.add_argument("--quiet", "-q", action="store_true", help="Suppress per-tool and LLM timing logs (default: show when stderr is a TTY)")
    java_gen_parser.add_argument(
        "--debug-openrouter",
        action="store_true",
        help=(
            "Log each OpenRouter request size (messages count + JSON UTF-8 bytes) to stderr; "
            "on HTTP errors the API response body is always printed. "
            "Same as env FPML_OPENROUTER_LOG_REQUEST_BYTES=1 for request size."
        ),
    )
    java_gen_parser.set_defaults(func=cmd_generate_java)

    fpml_to_java_parser = subparsers.add_parser(
        "generate-java-from-fpml",
        help="Generate Java code from FpML using mapping agent + Java codegen agent",
    )
    fpml_to_java_parser.add_argument("input", help="Input FpML XML file")
    fpml_to_java_parser.add_argument(
        "--provider",
        choices=("openrouter", "openai"),
        default="openrouter",
        help="LLM provider for mapping agent + Java codegen (default: openrouter)",
    )
    fpml_to_java_parser.add_argument(
        "--model",
        default="minimax/minimax-m2.5",
        help="LLM model name for both mapping + Java codegen (default: minimax/minimax-m2.5)",
    )
    fpml_to_java_parser.add_argument(
        "--mapping-model",
        default=None,
        help="Optional LLM model override for mapping agent (default: use --model)",
    )
    fpml_to_java_parser.add_argument(
        "--api-key",
        default=None,
        help="API key (for --provider openai: OpenAI key; else ignored)",
    )
    fpml_to_java_parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL (for --provider openai only)",
    )
    fpml_to_java_parser.add_argument(
        "--max-iterations",
        type=int,
        default=20,
        help="Java codegen max agent iterations (default: 20)",
    )
    fpml_to_java_parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=50,
        help="Java codegen max total tool calls (default: 50)",
    )
    fpml_to_java_parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Java codegen agent timeout in seconds (default: 600)",
    )
    fpml_to_java_parser.add_argument(
        "--mapping-max-iterations",
        type=int,
        default=10,
        help="Mapping agent max iterations (default: 10)",
    )
    fpml_to_java_parser.add_argument(
        "--mapping-max-tool-calls",
        type=int,
        default=80,
        help="Mapping agent max total tool calls (default: 80)",
    )
    fpml_to_java_parser.add_argument(
        "--mapping-timeout",
        type=int,
        default=300,
        help="Mapping agent timeout in seconds (default: 300)",
    )
    fpml_to_java_parser.add_argument(
        "--no-mapping-agent",
        action="store_true",
        help="Skip mapping agent; use deterministic CDM even if validation fails",
    )
    fpml_to_java_parser.add_argument(
        "--output-dir",
        default="tmp",
        help="Directory for intermediate artifacts (best CDM, traces) (default: tmp)",
    )
    fpml_to_java_parser.add_argument(
        "--java-class",
        dest="java_class",
        default=None,
        help="Public Java class name and generated/<Name>.java (default: derived from FpML filename stem)",
    )
    fpml_to_java_parser.add_argument(
        "--trace-output",
        help="Optional Java agent trace JSON output file (same as generate-java)",
    )
    fpml_to_java_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Always show per-tool and LLM timing logs",
    )
    fpml_to_java_parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress per-tool and LLM timing logs",
    )
    fpml_to_java_parser.set_defaults(func=cmd_generate_java_from_fpml)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
