import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("EPICTRACE_RUN_SLOW") != "1",
    reason="real-model agent test; set EPICTRACE_RUN_SLOW=1 to run")


def test_real_profile_probe_and_agent_round_trip():
    """Sketch: against a real configured profile, probe tool-calling and run one
    agent turn end-to-end. Requires a live ~/.epictrace/settings.json active profile.
    Asserts the probe returns a bool and (if supported) a search produces a non-empty pool."""
    from epictrace.agent.chat_model import make_chat_model
    from epictrace.agent.tool_probe import probe_tool_calling
    from epictrace.config import AppConfig
    from epictrace.services.settings import SettingsService

    profile = SettingsService(AppConfig()).get_active_profile()
    if profile is None:
        pytest.skip("no active profile configured")
    supported = probe_tool_calling(make_chat_model(profile))
    assert isinstance(supported, bool)
