from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
# Make imports work even if the working directory isn't the repo root
# (common when running from Git Bash / different shells).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

from fpml_cdm.mapping_agent.agent import MappingAgentConfig, run_mapping_agent
from fpml_cdm.parser_enrichment import ParserEnrichmentConfig, run_parser_enrichment
from fpml_cdm.java_gen.agent import AgentConfig, run_agent
from fpml_cdm.java_gen.tools import json_stem_to_java_class_name


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _build_llm_client(provider: str, *, api_key: Optional[str], base_url: Optional[str], timeout: float) -> object:
    if provider == "openrouter":
        from fpml_cdm.java_gen.openrouter_client import OpenRouterClient

        resolved_key = api_key or os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not resolved_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for provider=openrouter (set .env or env var)")
        return OpenRouterClient(api_key=resolved_key, timeout=timeout)

    if provider == "openai":
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError("openai package not installed. Run: pip install openai") from exc

        # openai SDK reads api_key/base_url from args/env; pass explicitly if given.
        return openai.OpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url,
        )

    raise ValueError(f"Unknown provider: {provider}")


def _force_disable_rosetta_if_requested(  # returns a context manager via patch
    rosetta_enabled: bool,
) -> Any:
    """
    The mapping agent registers a `validate_best_effort` tool which can (optionally)
    call the Rosetta Java validator. If `rosetta_enabled=False`, we monkeypatch
    the tool handler to force `enable_rosetta=False` regardless of what the LLM requests.
    """
    from unittest.mock import patch

    if rosetta_enabled:
        return nullcontext()

    from fpml_cdm.mapping_agent import tools as mapping_tools

    original = mapping_tools.validate_best_effort

    def no_rosetta(fpml_path: str, cdm_json: object, **kwargs: object):
        kwargs.pop("enable_rosetta", None)
        kwargs.pop("rosetta_timeout_seconds", None)
        return original(fpml_path=fpml_path, cdm_json=cdm_json, enable_rosetta=False, rosetta_timeout_seconds=60, **kwargs)

    return patch("fpml_cdm.mapping_agent.tools.validate_best_effort", side_effect=no_rosetta)


class nullcontext:  # py<3.7 fallback
    def __init__(self) -> None:
        pass

    def __enter__(self) -> None:  # noqa: D401
        return None

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def main(argv: list[str] | None = None) -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Whole pipeline runner: mapping agent -> Java codegen agent for ISDA CDM Java."
    )
    parser.add_argument("input", help="Input FpML XML file")
    parser.add_argument("--output-dir", default="tmp/run_pipeline", help="Output directory for artifacts")
    parser.add_argument("--provider", choices=("openrouter", "openai"), default="openrouter", help="LLM provider")
    parser.add_argument("--model", default="minimax/minimax-m2.5", help="LLM model for both mapping + Java agents")
    parser.add_argument("--mapping-model", default=None, help="Optional mapping-agent model override")
    parser.add_argument("--java-model", default=None, help="Optional Java-codegen model override (default: --model)")

    parser.add_argument("--api-key", default=None, help="Provider API key (openai only; openrouter uses OPENROUTER_API_KEY)")
    parser.add_argument("--base-url", default=None, help="OpenAI base URL (openai only)")

    parser.add_argument("--mapping-max-iterations", type=int, default=10)
    parser.add_argument("--mapping-max-tool-calls", type=int, default=80)
    parser.add_argument("--mapping-timeout", type=int, default=300)
    parser.add_argument("--mapping-no-improve", type=int, default=3)
    parser.add_argument("--enrich-parser", action="store_true", help="Run LLM parser enrichment before mapping.")
    parser.add_argument("--enrich-parser-model", default=None, help="Optional parser-enrichment model override.")
    parser.add_argument("--enrich-parser-max-attempts", type=int, default=1, help="Parser enrichment attempts.")

    parser.add_argument("--java-max-iterations", type=int, default=20)
    parser.add_argument("--java-max-tool-calls", type=int, default=50)
    parser.add_argument("--java-timeout", type=int, default=600)
    parser.add_argument("--java-trace-output", default=None, help="Optional file to write Java agent trace JSON")

    parser.add_argument(
        "--rosetta",
        action="store_true",
        help="Allow mapping agent to call Rosetta (requires the rosetta-validator JAR). Default: disabled.",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose agent tool timing logs")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress agent progress logs (still prints high-level phase markers).",
    )

    args = parser.parse_args(argv)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    llm_timeout = float(os.environ.get("FPML_CDM_LLM_TIMEOUT", "120"))
    llm_client = _build_llm_client(
        args.provider,
        api_key=args.api_key,
        base_url=args.base_url,
        timeout=llm_timeout,
    )

    mapping_model = args.mapping_model or args.model
    java_model = args.java_model or args.model

    mapping_cfg = MappingAgentConfig(
        max_iterations=args.mapping_max_iterations,
        max_tool_calls=args.mapping_max_tool_calls,
        timeout_seconds=args.mapping_timeout,
        semantic_no_improve_limit=args.mapping_no_improve,
        enable_rosetta=bool(args.rosetta),
        rosetta_timeout_seconds=int(os.environ.get("FPML_CDM_ROSETTA_TIMEOUT", "120")),
    )
    java_cfg = AgentConfig(
        max_iterations=args.java_max_iterations,
        max_tool_calls=args.java_max_tool_calls,
        timeout_seconds=args.java_timeout,
    )

    expected_cdm_json_path = out_dir / "generated_expected_cdm.json"
    mapping_trace_json_path = out_dir / "mapping_trace.json"
    parser_enrichment_trace_json_path = out_dir / "parser_enrichment_trace.json"
    parser_enriched_normalized_json_path = out_dir / "parser_enriched_normalized.json"

    log_progress = not args.quiet
    enrich_model = args.enrich_parser_model or args.model

    parser_enrichment_result = None
    if bool(args.enrich_parser):
        print(f"\n[0/3] Parser enrichment start: input={args.input}", flush=True)
        parser_enrichment_result = run_parser_enrichment(
            fpml_path=args.input,
            llm_client=llm_client,
            model=enrich_model,
            config=ParserEnrichmentConfig(
                max_attempts=int(args.enrich_parser_max_attempts),
                enable_rosetta=bool(args.rosetta),
                rosetta_timeout_seconds=int(os.environ.get("FPML_CDM_ROSETTA_TIMEOUT", "120")),
            ),
            log_progress=log_progress and bool(args.verbose or not args.quiet),
        )
        _write_json(parser_enrichment_trace_json_path, parser_enrichment_result.to_dict())
        _write_json(parser_enriched_normalized_json_path, parser_enrichment_result.enriched_normalized)
        print(
            f"[0/3] Parser enrichment done: accepted={parser_enrichment_result.accepted} "
            f"baseline_score={list(parser_enrichment_result.baseline_score)} "
            f"enriched_score={list(parser_enrichment_result.enriched_score)}",
            flush=True,
        )

    if int(args.mapping_max_iterations) <= 0:
        from types import SimpleNamespace
        from fpml_cdm.validator import validate_normalized_and_cdm
        from fpml_cdm.types import NormalizedFxForward

        if parser_enrichment_result is not None:
            chosen_cdm = parser_enrichment_result.enriched_cdm
            chosen_normalized = NormalizedFxForward.from_dict(parser_enrichment_result.enriched_normalized)
            chosen_adapter = str(chosen_normalized.sourceProduct or "fxForward")
        else:
            from fpml_cdm.rulesets import get_base_ruleset
            from fpml_cdm.ruleset_engine import parse_fpml_fx_with_ruleset
            from fpml_cdm.transformer import transform_to_cdm_v6

            best_adapter = "fxForward"
            best_score = (10**9, 10**9)
            chosen_cdm = {}
            chosen_normalized = None
            for adapter_id in ("fxSingleLeg", "fxForward"):
                try:
                    normalized, _ = parse_fpml_fx_with_ruleset(
                        fpml_path=args.input,
                        adapter_id=adapter_id,
                        ruleset=get_base_ruleset(adapter_id),
                        strict=False,
                        recovery_mode=True,
                    )
                    cdm = transform_to_cdm_v6(normalized)
                    rep = validate_normalized_and_cdm(normalized, cdm)
                    score = (
                        sum(1 for e in rep.errors if e.code == "SCHEMA_VALIDATION_FAILED"),
                        sum(1 for e in rep.errors if e.code == "SEMANTIC_VALIDATION_FAILED"),
                    )
                    if score < best_score:
                        best_score = score
                        best_adapter = adapter_id
                        chosen_cdm = cdm
                        chosen_normalized = normalized
                except Exception:
                    continue
            chosen_adapter = best_adapter
            if chosen_normalized is None:
                raise RuntimeError("Failed to produce deterministic mapping candidate.")

        chosen_report = validate_normalized_and_cdm(chosen_normalized, chosen_cdm)
        map_result = SimpleNamespace(
            adapter_id=chosen_adapter,
            best_schema_error_count=sum(1 for e in chosen_report.errors if e.code == "SCHEMA_VALIDATION_FAILED"),
            best_semantic_error_count=sum(1 for e in chosen_report.errors if e.code == "SEMANTIC_VALIDATION_FAILED"),
            best_rosetta_failure_count=0,
            iterations=0,
            total_tool_calls=0,
            trace=[],
            best_cdm_json=chosen_cdm,
            to_dict=lambda: {
                "adapter_id": chosen_adapter,
                "best_schema_error_count": sum(
                    1 for e in chosen_report.errors if e.code == "SCHEMA_VALIDATION_FAILED"
                ),
                "best_semantic_error_count": sum(
                    1 for e in chosen_report.errors if e.code == "SEMANTIC_VALIDATION_FAILED"
                ),
                "iterations": 0,
                "total_tool_calls": 0,
                "duration_seconds": 0.0,
                "best_validation_report": chosen_report.to_dict(),
            },
        )
        print(
            f"\n[1/3] Mapping agent skipped (max iterations=0): adapter={map_result.adapter_id} "
            f"schema_errors={map_result.best_schema_error_count} "
            f"semantic_errors={map_result.best_semantic_error_count}",
            flush=True,
        )
    else:
        print(f"\n[1/3] Mapping agent start: input={args.input}", flush=True)
        with _force_disable_rosetta_if_requested(rosetta_enabled=bool(args.rosetta)):
            map_result = run_mapping_agent(
                fpml_path=args.input,
                llm_client=llm_client,
                model=mapping_model,
                config=mapping_cfg,
                log_progress=log_progress and bool(args.verbose or not args.quiet),
            )
        print(
            f"[1/3] Mapping agent done: adapter={map_result.adapter_id} "
            f"schema_errors={map_result.best_schema_error_count} "
            f"semantic_errors={map_result.best_semantic_error_count} "
            f"iterations={map_result.iterations} tool_calls={map_result.total_tool_calls}",
            flush=True,
        )

    print(f"[2/3] Writing mapping artifacts to: {out_dir}", flush=True)
    _write_json(
        expected_cdm_json_path,
        map_result.best_cdm_json,
    )
    _write_json(
        mapping_trace_json_path,
        {
            "adapter_id": map_result.adapter_id,
            "best_schema_error_count": map_result.best_schema_error_count,
            "best_semantic_error_count": map_result.best_semantic_error_count,
            "trace": map_result.trace,
            "best_cdm_json": map_result.best_cdm_json,
        },
    )

    # Deterministic Rosetta type validation output (so `--rosetta` is not
    # dependent on the LLM calling validate_best_effort).
    if bool(args.rosetta):
        from fpml_cdm.rosetta_validator import validate_cdm_rosetta

        rosetta_report = validate_cdm_rosetta(
            map_result.best_cdm_json,
            timeout_seconds=int(os.environ.get("FPML_CDM_ROSETTA_TIMEOUT", "120")),
            target_type="trade",
        )
        rosetta_report_path = out_dir / "rosetta_report.json"
        _write_json(
            rosetta_report_path,
            rosetta_report.to_dict(),
        )
        print(
            f"[2/3] Rosetta validation: valid={rosetta_report.valid} failures={len(rosetta_report.failures)}",
            flush=True,
        )

    # Java codegen agent.
    print(f"[2/3] Java codegen start: expected_cdm={expected_cdm_json_path}", flush=True)
    java_result = run_agent(
        cdm_json_path=str(expected_cdm_json_path),
        llm_client=llm_client,
        model=java_model,
        config=java_cfg,
        log_progress=log_progress and bool(args.verbose or not args.quiet),
        java_class_name=json_stem_to_java_class_name(Path(args.input).stem),
    )
    print(
        f"[2/3] Java codegen done: success={java_result.success} "
        f"match={java_result.match_percentage}% iterations={java_result.iterations} "
        f"tool_calls={java_result.total_tool_calls}",
        flush=True,
    )

    # Copy the generated Java file into the output dir for easy inspection.
    if java_result.java_file:
        java_src = Path(java_result.java_file)
        if java_src.exists():
            shutil.copy2(java_src, out_dir / java_src.name)

    if args.java_trace_output:
        _write_json(args.java_trace_output, {"trace": java_result.trace, "result": java_result.to_dict()})

    # A small summary file for humans.
    _write_json(
        out_dir / "pipeline_summary.json",
        {
            "parser_enrichment_enabled": bool(args.enrich_parser),
            "parser_enrichment_trace_json_path": str(parser_enrichment_trace_json_path) if bool(args.enrich_parser) else None,
            "parser_enriched_normalized_json_path": str(parser_enriched_normalized_json_path) if bool(args.enrich_parser) else None,
            "mapping": map_result.to_dict(),
            "java": java_result.to_dict(),
            "expected_cdm_json_path": str(expected_cdm_json_path),
            "mapping_trace_json_path": str(mapping_trace_json_path),
        },
    )

    print(f"[3/3] Wrote summary: {out_dir / 'pipeline_summary.json'}", flush=True)
    return 0 if java_result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
