#!/usr/bin/env python3
"""Run deterministic converter across a local XML corpus and write summary report."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from collections import Counter
from pathlib import Path
import sys
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fpml_cdm import convert_fpml_to_cdm
from fpml_cdm.adapters.registry import SUPPORTED_FX_ADAPTER_IDS, fpml_trade_product_local_names


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run fpml_cdm conversion over a local corpus")
    parser.add_argument(
        "--corpus",
        default="data/corpus/fpml_official",
        help="Root folder containing XML files",
    )
    parser.add_argument(
        "--output",
        default="data/corpus/reports/latest.json",
        help="Where to write the summary report JSON",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Optional max number of XML files to process",
    )
    parser.add_argument(
        "--include-path",
        default=None,
        help="Optional substring filter: only process files with this substring in path",
    )
    parser.add_argument(
        "--sample-errors",
        type=int,
        default=50,
        help="How many failed-file entries to keep in report",
    )
    return parser.parse_args()


def list_xml_files(root: Path, include_path: str | None, max_files: int | None) -> List[Path]:
    files = sorted(root.rglob("*.xml"))
    if include_path:
        files = [f for f in files if include_path in str(f)]
    if max_files is not None:
        files = files[:max_files]
    return files


def build_report(corpus: Path, files: List[Path], sample_errors: int) -> Dict:
    started_at = dt.datetime.now(dt.timezone.utc)
    start = time.time()

    status_counts = Counter()
    error_code_counts = Counter()
    ok_by_source = Counter()
    failed_by_source = Counter()
    ok_by_adapter_id = Counter()
    failed_by_trade_child = Counter()
    mapping_scores: List[float] = []
    failed_samples = []

    for file_path in files:
        rel = file_path.relative_to(corpus)
        source_key = rel.parts[0] if rel.parts else "root"

        result = convert_fpml_to_cdm(str(file_path))
        if result.ok:
            status_counts["ok"] += 1
            ok_by_source[source_key] += 1
            if result.normalized is not None and getattr(result.normalized, "sourceProduct", None):
                ok_by_adapter_id[str(result.normalized.sourceProduct)] += 1
            if result.validation is not None:
                mapping_scores.append(result.validation.mapping_score.accuracy_percent)
            continue

        status_counts["failed"] += 1
        failed_by_source[source_key] += 1
        tags = fpml_trade_product_local_names(str(file_path))
        registered_hits = [t for t in tags if t in SUPPORTED_FX_ADAPTER_IDS]
        if registered_hits:
            failed_bucket = registered_hits[0]
        elif tags:
            failed_bucket = tags[0]
        else:
            failed_bucket = "_no_trade_product"
        failed_by_trade_child[failed_bucket] += 1
        code = result.errors[0].code if result.errors else "UNKNOWN"
        message = result.errors[0].message if result.errors else "Unknown failure"
        error_code_counts[code] += 1
        if len(failed_samples) < sample_errors:
            failed_samples.append(
                {
                    "file": str(rel),
                    "error_code": code,
                    "message": message,
                }
            )

    elapsed = time.time() - start
    ended_at = dt.datetime.now(dt.timezone.utc)

    avg_score = sum(mapping_scores) / len(mapping_scores) if mapping_scores else 0.0
    report = {
        "generated_at": ended_at.isoformat(),
        "started_at": started_at.isoformat(),
        "duration_seconds": elapsed,
        "corpus": str(corpus),
        "files_processed": len(files),
        "throughput_files_per_second": (len(files) / elapsed) if elapsed else 0.0,
        "status_counts": dict(status_counts),
        "error_code_counts": dict(error_code_counts),
        "ok_by_source": dict(ok_by_source),
        "failed_by_source": dict(failed_by_source),
        "ok_by_adapter_id": dict(ok_by_adapter_id),
        "failed_by_trade_child": dict(failed_by_trade_child),
        "mapping_score": {
            "average_accuracy_percent": avg_score,
            "ok_file_count_with_score": len(mapping_scores),
        },
        "failed_samples": failed_samples,
    }

    return report


def main() -> int:
    args = parse_args()
    corpus = Path(args.corpus)
    if not corpus.exists():
        print(f"Corpus path not found: {corpus}")
        return 2

    files = list_xml_files(corpus, args.include_path, args.max_files)
    if not files:
        print(f"No XML files found under: {corpus}")
        return 2

    report = build_report(corpus, files, sample_errors=args.sample_errors)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(
        "Corpus check complete: "
        f"processed={report['files_processed']} ok={report['status_counts'].get('ok', 0)} "
        f"failed={report['status_counts'].get('failed', 0)} "
        f"throughput={report['throughput_files_per_second']:.2f} files/s"
    )
    print(f"Report: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
