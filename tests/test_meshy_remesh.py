"""Tests for the Meshy Remesh backend (stage 4 retopology via API)."""
import os
import tempfile
import unittest

from assetforge.core.adapter import RunContext, RunMode
from assetforge.core.asset_state import AssetState, SourceKind
from assetforge.core.backends.remesh.meshy_remesh import MeshyRemeshBackend, MeshyRemeshError
from assetforge.core.secrets import DictSecretStore
from assetforge.core.stages import AssetType

GOLDEN_GLB = os.path.join(os.path.dirname(__file__), "golden", "sample_input.glb")


class _FakeMeshyRemeshHttp:
    def create_task(self, base_url, api_key, body):
        self.last_body = body
        return {"result": "remesh-task-1"}

    def get_task(self, base_url, api_key, task_id):
        return {"status": "SUCCEEDED",
                "model_urls": {"glb": "https://x/retopo.glb"}}

    def download(self, url, dest):
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(b"glb")
        return dest


def _mesh_state(work):
    state = AssetState(id="r", source_kind=SourceKind.IMAGE,
                       source_ref="img.png", asset_type=AssetType.STATIC)
    state.artifacts["mesh"] = GOLDEN_GLB
    return state


class TestMeshyRemeshBackend(unittest.TestCase):
    def test_full_flow_with_local_glb(self):
        with tempfile.TemporaryDirectory() as work:
            state = _mesh_state(work)
            ctx = RunContext(secrets=DictSecretStore({"meshy": "sk-m"}), work_dir=work)
            http = _FakeMeshyRemeshHttp()
            MeshyRemeshBackend(http_client=http, poll_interval=0).run_api(state, {}, ctx)
            self.assertTrue(os.path.exists(state.artifacts["mesh"]))
            self.assertEqual(state.artifacts["topology"], "quad")
            self.assertEqual(state.metadata["retopo"]["method"], "meshy_remesh")
            # Data URI path used (not input_task_id) since no Meshy generation
            self.assertIn("model_url", http.last_body)
            self.assertTrue(http.last_body["model_url"].startswith("data:"))

    def test_uses_task_id_when_mesh_from_meshy(self):
        with tempfile.TemporaryDirectory() as work:
            state = _mesh_state(work)
            state.metadata["generation"] = {"backend": "meshy", "task_id": "gen-task-9"}
            ctx = RunContext(secrets=DictSecretStore({"meshy": "sk-m"}), work_dir=work)
            http = _FakeMeshyRemeshHttp()
            MeshyRemeshBackend(http_client=http, poll_interval=0).run_api(state, {}, ctx)
            # Should use input_task_id, not model_url
            self.assertIn("input_task_id", http.last_body)
            self.assertEqual(http.last_body["input_task_id"], "gen-task-9")
            self.assertNotIn("model_url", http.last_body)

    def test_platform_target_passed_correctly(self):
        with tempfile.TemporaryDirectory() as work:
            state = _mesh_state(work)
            ctx = RunContext(secrets=DictSecretStore({"meshy": "sk"}), work_dir=work)
            http = _FakeMeshyRemeshHttp()
            MeshyRemeshBackend(http_client=http, poll_interval=0).run_api(
                state, {"platform": "mobile"}, ctx)
            self.assertEqual(http.last_body["target_polycount"], 5_000)

    def test_no_key_raises(self):
        with tempfile.TemporaryDirectory() as work:
            state = _mesh_state(work)
            ctx = RunContext(secrets=DictSecretStore(), work_dir=work)
            with self.assertRaises(MeshyRemeshError):
                MeshyRemeshBackend(http_client=_FakeMeshyRemeshHttp()).run_api(state, {}, ctx)

    def test_topology_always_quad(self):
        with tempfile.TemporaryDirectory() as work:
            state = _mesh_state(work)
            ctx = RunContext(secrets=DictSecretStore({"meshy": "sk"}), work_dir=work)
            MeshyRemeshBackend(http_client=_FakeMeshyRemeshHttp(), poll_interval=0).run_api(
                state, {}, ctx)
            self.assertEqual(state.artifacts["topology"], "quad")


if __name__ == "__main__":
    unittest.main()
