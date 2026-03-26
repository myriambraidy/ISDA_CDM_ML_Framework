"""Bridge to the Rosetta CDM type validator (Java).

Calls the rosetta-validator fat JAR via subprocess to run
RosettaTypeValidator against CDM JSON output.  This is an
*out-of-band* validation layer — it is NOT in the hot conversion path.
Use it in CI / corpus checks for authoritative CDM compliance checking.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .types import ErrorCode, ValidationIssue

JAR_NAME = "rosetta-validator-1.0.0.jar"
_JAR_SEARCH_PATHS = [
    Path(__file__).resolve().parent.parent / "rosetta-validator" / "target" / JAR_NAME,
    Path(__file__).resolve().parent.parent / "rosetta-validator" / JAR_NAME,
]


@dataclass
class RosettaValidationResult:
    """Result from the Rosetta type validator."""
    valid: bool
    failures: List[Dict[str, str]] = field(default_factory=list)
    error: Optional[str] = None
    exit_code: int = 0

    def to_issues(self) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        if self.error:
            issues.append(ValidationIssue(
                code=ErrorCode.SCHEMA_VALIDATION_FAILED.value,
                message=f"Rosetta validator error: {self.error}",
                path="<rosetta>",
            ))
        for f in self.failures:
            rule_type = f.get("type", "UNKNOWN")
            name = f.get("name", "")
            msg = f.get("failureMessage", "") or f.get("definition", "")
            path = f.get("path", "")
            issues.append(ValidationIssue(
                code=ErrorCode.SCHEMA_VALIDATION_FAILED.value,
                message=f"[{rule_type}] {name}: {msg}".strip(),
                path=path,
                level="error",
            ))
        return issues

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "failureCount": len(self.failures),
            "failures": self.failures,
            "error": self.error,
        }


def find_jar() -> Optional[Path]:
    for p in _JAR_SEARCH_PATHS:
        if p.exists():
            return p
    return None


def java_available() -> bool:
    return shutil.which("java") is not None


def validate_cdm_rosetta(
    cdm_data: Dict[str, Any],
    *,
    jar_path: Optional[Path] = None,
    target_type: str = "trade",
    timeout_seconds: int = 60,
) -> RosettaValidationResult:
    """
    Validate a CDM dict against the Rosetta type system.

    Args:
        cdm_data: The CDM JSON dict (e.g. {"trade": {...}}).
        jar_path: Override path to the fat JAR.
        target_type: "trade" or "tradeState".
        timeout_seconds: Max time for the JVM process.

    Returns:
        RosettaValidationResult with structured failures.

    Raises:
        FileNotFoundError: If the JAR is not found.
        RuntimeError: If Java is not installed.
    """
    jar = jar_path or find_jar()
    if jar is None:
        raise FileNotFoundError(
            f"Rosetta validator JAR not found. Build it with: "
            f"cd rosetta-validator && mvn package -q"
        )
    if not java_available():
        raise RuntimeError(
            "Java not found on PATH. Install JDK 11+ to use Rosetta validation."
        )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(cdm_data, tmp)
        tmp_path = tmp.name

    try:
        proc = subprocess.run(
            ["java", "-jar", str(jar), tmp_path, "--type", target_type],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return RosettaValidationResult(
            valid=False,
            error=f"Rosetta validator timed out after {timeout_seconds}s",
            exit_code=-1,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if not proc.stdout.strip():
        return RosettaValidationResult(
            valid=False,
            error=f"No output from validator. stderr: {proc.stderr[:500]}",
            exit_code=proc.returncode,
        )

    try:
        output = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return RosettaValidationResult(
            valid=False,
            error=f"Invalid JSON from validator: {proc.stdout[:500]}",
            exit_code=proc.returncode,
        )

    return RosettaValidationResult(
        valid=output.get("valid", False),
        failures=output.get("failures", []),
        error=output.get("error"),
        exit_code=proc.returncode,
    )


def validate_cdm_rosetta_with_retry(
    cdm_data: Dict[str, Any],
    *,
    jar_path: Optional[Path] = None,
    target_type: str = "trade",
    timeout_seconds: int = 60,
    max_attempts: int = 2,
    retry_delay_seconds: float = 0.2,
) -> RosettaValidationResult:
    """
    Retry Rosetta validation once for transient runtime failures/timeouts.

    Infra-not-available conditions (missing JAR/Java) are returned as errors
    without retrying.
    """
    if max_attempts < 1:
        max_attempts = 1

    # Fail fast for infrastructure unavailability.
    jar = jar_path or find_jar()
    if jar is None:
        return RosettaValidationResult(
            valid=False,
            error="Rosetta validator JAR not found. Build it with: cd rosetta-validator && mvn package -q",
            exit_code=2,
        )
    if not java_available():
        return RosettaValidationResult(
            valid=False,
            error="Java not found on PATH. Install JDK 11+ to use Rosetta validation.",
            exit_code=2,
        )

    last: Optional[RosettaValidationResult] = None
    for attempt in range(1, max_attempts + 1):
        result = validate_cdm_rosetta(
            cdm_data,
            jar_path=jar,
            target_type=target_type,
            timeout_seconds=timeout_seconds,
        )
        last = result

        # Retry only transient execution failures.
        is_transient = bool(result.error) and (
            "timed out" in result.error.lower()
            or "no output from validator" in result.error.lower()
            or "invalid json from validator" in result.error.lower()
        )
        if not is_transient or attempt >= max_attempts:
            return result
        time.sleep(retry_delay_seconds)

    assert last is not None
    return last
