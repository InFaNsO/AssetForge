"""Verifies the Meshy + Kimodo backends are wired into the default registry.

These backends already exist; this test guards their registration (stage + name)
so the resolver can find them. No bpy, no network — just registry lookups.
"""
import unittest

from assetforge.core.backends.registry import build_default_registry


# (stage, backend name) -> expected .stage value on the resolved backend
EXPECTED = [
    ("generate", "meshy", "generate"),
    ("retopo", "meshy_remesh", "retopo"),
    ("texture", "meshy_retexture", "texture"),
    ("rig", "meshy_rigging", "rig"),
    ("animate", "meshy_animation", "animate"),
    ("animate", "kimodo", "animate"),
]


class TestMeshyWiring(unittest.TestCase):
    def setUp(self):
        self.reg = build_default_registry()

    def test_backends_registered_for_their_stages(self):
        for stage, name, expected_stage in EXPECTED:
            backend = self.reg.get(stage, name)
            self.assertIsNotNone(
                backend, f"{name!r} not registered for stage {stage!r}")
            self.assertEqual(
                backend.stage, expected_stage,
                f"{name!r} has stage {backend.stage!r}, expected {expected_stage!r}")

    def test_stubs_remain_as_fallbacks(self):
        # API backend + stub coexist on each downstream stage.
        for stage in ("retopo", "texture", "rig", "animate"):
            names = {b.name for b in self.reg.for_stage(stage)}
            self.assertTrue(
                len(names) >= 2,
                f"stage {stage!r} should keep a stub fallback alongside the API backend, "
                f"got {names}")


if __name__ == "__main__":
    unittest.main()
