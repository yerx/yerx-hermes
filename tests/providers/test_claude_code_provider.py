from providers import get_provider_profile


def test_claude_code_provider_registered():
    p = get_provider_profile("claude-code")
    assert p is not None
    assert p.api_mode == "claude_code_cli"
    assert p.supports_health_check is False
    assert p.fallback_models  # static catalog present
