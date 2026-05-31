import unittest

from assetforge.core.asset_state import AssetState, SourceKind, StageStatus
from assetforge.core.provenance import ProvenanceEntry, strip_secrets
from assetforge.core.stages import AssetType


class TestAssetState(unittest.TestCase):
    def test_static_asset_marks_rig_and_animate_na(self):
        state = AssetState(id="a1", asset_type=AssetType.STATIC)
        self.assertEqual(state.status("rig"), StageStatus.NA)
        self.assertEqual(state.status("animate"), StageStatus.NA)
        self.assertEqual(state.status("generate"), StageStatus.PENDING)

    def test_humanoid_keeps_rig_pending(self):
        state = AssetState(id="h1", asset_type=AssetType.HUMANOID)
        self.assertEqual(state.status("rig"), StageStatus.PENDING)

    def test_json_round_trip(self):
        state = AssetState(id="r1", source_kind=SourceKind.TEXT,
                           source_ref="a wooden barrel", asset_type=AssetType.STATIC)
        state.artifacts["mesh"] = "work/r1/mesh.glb"
        state.set_status("generate", StageStatus.DONE)
        state.record(ProvenanceEntry.create("generate", "gen_api", "api", {"seed": 7}))

        restored = AssetState.from_json(state.to_json())
        self.assertEqual(restored.id, "r1")
        self.assertEqual(restored.source_kind, SourceKind.TEXT)
        self.assertEqual(restored.asset_type, AssetType.STATIC)
        self.assertEqual(restored.artifacts["mesh"], "work/r1/mesh.glb")
        self.assertEqual(restored.status("generate"), StageStatus.DONE)
        self.assertEqual(restored.status("rig"), StageStatus.NA)
        self.assertEqual(len(restored.provenance), 1)
        self.assertEqual(restored.provenance[0].params["seed"], 7)


class TestSecretsNeverLeak(unittest.TestCase):
    def test_strip_secrets_redacts_keylike_names(self):
        clean = strip_secrets({"api_key": "sk-123", "seed": 7,
                               "nested": {"auth_token": "t", "scale": 1}})
        self.assertEqual(clean["api_key"], "<redacted>")
        self.assertEqual(clean["nested"]["auth_token"], "<redacted>")
        self.assertEqual(clean["seed"], 7)
        self.assertEqual(clean["nested"]["scale"], 1)

    def test_provenance_entry_redacts_on_create(self):
        entry = ProvenanceEntry.create("generate", "gen_api", "api",
                                       {"api_key": "sk-secret", "seed": 1})
        self.assertEqual(entry.params["api_key"], "<redacted>")
        self.assertNotIn("sk-secret", entry.to_dict()["params"].values())


if __name__ == "__main__":
    unittest.main()
