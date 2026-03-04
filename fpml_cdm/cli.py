from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .llm.base import get_llm_provider
from .parser import parse_fpml_fx
from .pipeline import convert_fpml_to_cdm
from .transformer import transform_to_cdm_v6
from .types import NormalizedFxForward, ParserError, ValidationIssue
from .validator import validate_conversion_files, validate_schema_data


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
    schema_name = "cdm_fx_forward.schema.json" if args.schema == "cdm" else "fpml_fx_forward_parsed.schema.json"
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


def _resolve_llm_provider(args: argparse.Namespace):
    provider_name = getattr(args, "llm_provider", "none") or "none"
    if provider_name == "none":
        return None
    return get_llm_provider(
        provider_name=provider_name,
        model=getattr(args, "llm_model", None),
        base_url=getattr(args, "llm_base_url", None),
    )


def cmd_convert(args: argparse.Namespace) -> int:
    llm_provider = _resolve_llm_provider(args)
    result = convert_fpml_to_cdm(args.input, strict=not args.no_strict, llm_provider=llm_provider)

    payload = result.to_dict()
    _write_json(payload, args.output)

    if args.normalized_output and result.normalized is not None:
        _write_json(result.normalized.to_dict(), args.normalized_output)
    if args.cdm_output and result.cdm is not None:
        _write_json(result.cdm, args.cdm_output)
    if args.report_output and result.validation is not None:
        _write_json(result.validation.to_dict(), args.report_output)

    return 0 if result.ok else 1


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
    convert_parser.set_defaults(func=cmd_convert)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
