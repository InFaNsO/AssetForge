"""The golden-mesh end-to-end test (DEVELOPMENT_PLAN.md §3).

One mesh runs the WHOLE 13-stage chain as stubs. This is the Phase 0 ship gate and the
regression guard every later phase builds on.
"""
import os
import unittest

from assetforge.core.adapter import RunContext
from assetforge.core.asset_state import AssetState, SourceKind, StageStatus
from assetforge.core.backends.stubs import build_stub_registry
from assetforge.core.pipeline import Mode, Pipeline, ValidationResult
from assetforge.core.secrets import DictSecretStore
from assetforge.core.stages import STAGES, AssetType

GOLDEN = os.path.join(os.path.dirname(__file__), "golden", "cube.obj")


class TestGoldenChain(unittest.TestCase):
    def setUp(self):
        self.reg = build_stub_registry()
        self.ctx = RunContext(secrets=DictSecretStore({"tripo": "sk-test"}))

    def _new_state(self, asset_type):
        self.assertTrue(os.path.exists(GOLDEN), "golden mesh missing")
        return AssetState(id="golden", source_kind=SourceKind.MESH,
                          source_ref=GOLDEN, asset_type=asset_type)

    def test_humanoid_runs_all_stages_to_done(self):
        state = self._new_state(AssetType.HUMANOID)
        report = Pipeline(self.reg, mode=Mode.GUIDED).run(state, self.ctx)
        self.assertTrue(report.ok, "\n" + report.summary())
        # Every applicable stage reaches DONE (or SKIPPED/NA where declared).
        terminal = {StageStatus.DONE, StageStatus.SKIPPED, StageStatus.NA}
        for s in STAGES:
            self.assertIn(state.status(s.key), terminal,
                          f"{s.key} ended {state.status(s.key)}\n{report.summary()}")
        # The chain actually produced an export artifact.
        self.assertIn("exported", state.artifacts)
        self.assertIn("mesh", state.artifacts)

    def test_static_asset_skips_rig_and_animate(self):
        state = self._new_state(AssetType.STATIC)
        report = Pipeline(self.reg, mode=Mode.GUIDED).run(state, self.ctx)
        self.assertTrue(report.ok, "\n" + report.summary())
        self.assertEqual(state.status("rig"), StageStatus.NA)
        self.assertEqual(state.status("animate"), StageStatus.NA)
        self.assertEqual(state.status("export"), StageStatus.DONE)

    def test_provenance_recorded_for_each_run_stage(self):
        state = self._new_state(AssetType.STATIC)
        Pipeline(self.reg, mode=Mode.GUIDED).run(state, self.ctx)
        run_stages = {p.stage for p in state.provenance}
        self.assertIn("generate", run_stages)
        self.assertIn("export", run_stages)
        # No secret leaked into provenance.
        for p in state.provenance:
            self.assertNotIn("sk-test", str(p.to_dict()))

    def test_skip_list_marks_stage_skipped(self):
        state = self._new_state(AssetType.STATIC)
        Pipeline(self.reg, mode=Mode.GUIDED).run(state, self.ctx, skip={"texture"})
        self.assertEqual(state.status("texture"), StageStatus.SKIPPED)

    def test_failed_validation_gate_stops_in_guided_mode(self):
        def fail_uv(state, key):
            return ValidationResult(False, "forced failure")

        state = self._new_state(AssetType.STATIC)
        report = Pipeline(self.reg, validators={"uv": fail_uv},
                          mode=Mode.GUIDED).run(state, self.ctx)
        self.assertFalse(report.ok)
        self.assertEqual(state.status("uv"), StageStatus.FAILED)
        # export is after uv, so guided mode must not have reached it
        self.assertEqual(state.status("export"), StageStatus.PENDING)


if __name__ == "__main__":
    unittest.main()
