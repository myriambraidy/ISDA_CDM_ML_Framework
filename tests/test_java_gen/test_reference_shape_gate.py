import json
import unittest


class ReferenceShapeGateTests(unittest.TestCase):
    def _gate(self, expected: object, actual: object) -> list[dict]:
        # Import the gate via finalize helper location to keep the test anchored.
        # NOTE: gate is nested; we validate behavior via _finalize_match_and_verification contract
        # by reimplementing the same minimal walk here would be pointless. Instead, we expose it
        # indirectly by calling the function and reading reference_shape_gate.
        from fpml_cdm.java_gen.agent import _finalize_match_and_verification  # type: ignore
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json.dumps(expected))
            exp_path = f.name
        try:
            mp, ver = _finalize_match_and_verification(
                cdm_json_path=exp_path,
                last_run_java_stdout=json.dumps(actual),
                llm_reported_match=0.0,
                status_success=True,
                artifacts_dir=None,
                enable_fixups=False,
            )
            self.assertIsInstance(ver, dict)
            gate = ver.get("reference_shape_gate")
            self.assertIsInstance(gate, dict)
            return list(gate.get("failures_sample") or []) if gate.get("ok") is False else []
        finally:
            Path(exp_path).unlink()

    def test_gate_fails_when_address_missing(self) -> None:
        expected = {"x": {"address": {"scope": "DOCUMENT", "value": "a"}}}
        actual = {"x": {"globalReference": "a"}}
        failures = self._gate(expected, actual)
        self.assertGreater(len(failures), 0)

    def test_gate_fails_when_extra_global_reference_present(self) -> None:
        expected = {"x": {"address": {"scope": "DOCUMENT", "value": "a"}}}
        actual = {"x": {"address": {"scope": "DOCUMENT", "value": "a"}, "globalReference": "a"}}
        failures = self._gate(expected, actual)
        self.assertGreater(len(failures), 0)

    def test_gate_passes_when_address_only_matches(self) -> None:
        expected = {"x": {"address": {"scope": "DOCUMENT", "value": "a"}}}
        actual = {"x": {"address": {"scope": "DOCUMENT", "value": "a"}}}
        failures = self._gate(expected, actual)
        self.assertEqual(failures, [])

