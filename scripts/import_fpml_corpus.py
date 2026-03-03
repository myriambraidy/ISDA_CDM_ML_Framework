#!/usr/bin/env python3
"""Download official FpML XML example corpora for local repeatable testing."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_SOURCES = [
    {
        "name": "fpml_5_12_4_confirmation",
        "index_url": "https://www.fpml.org/spec/fpml-5-12-4-rec-1/html/confirmation/fpml-5-12-examples.html",
    },
    {
        "name": "fpml_4_9_5",
        "index_url": "https://www.fpml.org/spec/fpml-4-9-5-rec-1/html/fpml-4-9-examples.html",
    },
]

HREF_XML_RE = re.compile(r"href=[\"']([^\"']+\.xml)[\"']", flags=re.IGNORECASE)
USER_AGENT = "fpml-corpus-importer/1.0"


@dataclass
class DownloadFailure:
    source: str
    href: str
    url: str
    error: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "source": self.source,
            "href": self.href,
            "url": self.url,
            "error": self.error,
        }


class ImportErrorSummary(Exception):
    pass


def http_get_text(url: str, timeout: float = 30.0) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        return response.read().decode("utf-8", errors="replace")


def http_get_bytes(url: str, timeout: float = 30.0) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        return response.read()


def extract_xml_links(html: str) -> List[str]:
    return sorted({match.group(1) for match in HREF_XML_RE.finditer(html)})


def normalized_url(index_url: str, href: str) -> str:
    joined = urljoin(index_url, href)
    split = urlsplit(joined)
    quoted_path = quote(split.path, safe="/-_.~()")
    quoted_query = quote(split.query, safe="=&-_.~")
    rebuilt = f"{split.scheme}://{split.netloc}{quoted_path}"
    if quoted_query:
        rebuilt += f"?{quoted_query}"
    return rebuilt


def safe_rel_path(href: str) -> Path:
    split = urlsplit(href)
    raw_path = split.path or href
    while raw_path.startswith("/"):
        raw_path = raw_path[1:]
    if not raw_path:
        raise ValueError("empty path")

    parts = []
    for part in raw_path.split("/"):
        if part in {"", ".", ".."}:
            continue
        parts.append(part)

    if not parts:
        raise ValueError("invalid relative path")
    return Path(*parts)


def download_with_retries(url: str, retries: int = 3, sleep_seconds: float = 0.25) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return http_get_bytes(url)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(sleep_seconds * attempt)
    raise ImportErrorSummary(str(last_error) if last_error else "unknown download error")


def iter_selected_sources(names: set[str] | None) -> Iterable[dict]:
    for source in DEFAULT_SOURCES:
        if not names or source["name"] in names:
            yield source


def run_import(dest: Path, force: bool, selected_sources: set[str] | None, limit_per_source: int | None) -> Dict:
    dest.mkdir(parents=True, exist_ok=True)

    failures: List[DownloadFailure] = []
    source_reports = []
    total_downloaded = 0
    total_skipped_existing = 0
    total_links = 0

    started_at = dt.datetime.now(dt.timezone.utc)

    for source in iter_selected_sources(selected_sources):
        source_name = source["name"]
        index_url = source["index_url"]
        source_dir = dest / source_name
        source_dir.mkdir(parents=True, exist_ok=True)

        html = http_get_text(index_url)
        links = extract_xml_links(html)
        if limit_per_source is not None:
            links = links[:limit_per_source]

        downloaded = 0
        skipped_existing = 0
        source_failures = 0

        for href in links:
            total_links += 1
            target_rel = safe_rel_path(href)
            target_path = source_dir / target_rel
            target_path.parent.mkdir(parents=True, exist_ok=True)

            if target_path.exists() and not force:
                skipped_existing += 1
                total_skipped_existing += 1
                continue

            url = normalized_url(index_url, href)
            try:
                payload = download_with_retries(url)
                target_path.write_bytes(payload)
                downloaded += 1
                total_downloaded += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    DownloadFailure(
                        source=source_name,
                        href=href,
                        url=url,
                        error=str(exc),
                    )
                )
                source_failures += 1

        source_reports.append(
            {
                "name": source_name,
                "index_url": index_url,
                "links_found": len(links),
                "downloaded": downloaded,
                "skipped_existing": skipped_existing,
                "failed": source_failures,
                "target_dir": str(source_dir),
            }
        )

    ended_at = dt.datetime.now(dt.timezone.utc)
    report = {
        "generated_at": ended_at.isoformat(),
        "started_at": started_at.isoformat(),
        "duration_seconds": (ended_at - started_at).total_seconds(),
        "destination": str(dest),
        "sources": source_reports,
        "totals": {
            "links": total_links,
            "downloaded": total_downloaded,
            "skipped_existing": total_skipped_existing,
            "failed": len(failures),
        },
        "failures": [f.to_dict() for f in failures],
    }

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import official FpML XML corpora into local folder")
    parser.add_argument(
        "--dest",
        default="data/corpus/fpml_official",
        help="Destination root directory for corpus download",
    )
    parser.add_argument(
        "--source",
        action="append",
        help=(
            "Restrict import to one source name (repeatable). "
            "Available: " + ", ".join(source["name"] for source in DEFAULT_SOURCES)
        ),
    )
    parser.add_argument(
        "--limit-per-source",
        type=int,
        default=None,
        help="Optional cap for number of links downloaded per source (for dry runs)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download files even if they already exist",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Output manifest path (default: <dest>/manifest.json)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dest = Path(args.dest)
    selected = set(args.source) if args.source else None
    manifest_path = Path(args.manifest) if args.manifest else (dest / "manifest.json")

    report = run_import(
        dest=dest,
        force=args.force,
        selected_sources=selected,
        limit_per_source=args.limit_per_source,
    )

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    totals = report["totals"]
    print(
        "Import complete: "
        f"links={totals['links']} downloaded={totals['downloaded']} "
        f"skipped={totals['skipped_existing']} failed={totals['failed']}"
    )
    print(f"Manifest: {manifest_path}")

    return 0 if totals["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
