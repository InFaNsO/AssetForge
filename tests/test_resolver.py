import unittest

from assetforge.core.adapter import RunContext, RunMode
from assetforge.core.asset_state import AssetState
from assetforge.core.backends.stubs import build_stub_registry
from assetforge.core.resolver import resolve
from assetforge.core.secrets import DictSecretStore
from assetforge.core.stages import AssetType


class TestResolver(unittest.TestCase):
    def setUp(self):
        self.reg = build_stub_registry()
        self.state = AssetState(id="t", asset_type=AssetType.STATIC)

    def test_generation_prefers_free_automation_when_no_key(self):
        ctx = RunContext(secrets=DictSecretStore())   # no API keys configured
        res = resolve("generate", self.reg, ctx, self.state)
        self.assertTrue(res.ok)
        self.assertEqual(res.backend.name, "copilot3d")
        self.assertEqual(res.mode, RunMode.AUTOMATION)

    def test_api_unavailable_without_key(self):
        ctx = RunContext(secrets=DictSecretStore())
        # The API stub needs the 'tripo' secret; with none it must not be chosen.
        res = resolve("generate", self.reg, ctx, self.state)
        self.assertNotEqual(res.backend.name, "gen_api")

    def test_user_choice_overrides_when_available(self):
        ctx = RunContext(
            secrets=DictSecretStore({"tripo": "sk-test"}),
            user_choice={"generate": "gen_api"},
        )
        res = resolve("generate", self.reg, ctx, self.state)
        self.assertEqual(res.backend.name, "gen_api")
        self.assertEqual(res.mode, RunMode.API)
        self.assertIn("user selected", res.reason)

    def test_reason_is_always_present(self):
        ctx = RunContext(secrets=DictSecretStore())
        res = resolve("retopo", self.reg, ctx, self.state)
        self.assertTrue(res.reason)


if __name__ == "__main__":
    unittest.main()
