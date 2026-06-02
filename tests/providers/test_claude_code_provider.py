from providers import get_provider_profile


def test_claude_cli_provider_registered():
    p = get_provider_profile("claude-cli")
    assert p is not None
    assert p.api_mode == "claude_code_cli"
    assert p.supports_health_check is False
    assert p.fallback_models  # static catalog present


def test_claude_code_alias_still_resolves_to_anthropic():
    # The distinct CLI-engine provider must NOT hijack the existing
    # "claude-code" synonym for the anthropic provider.
    assert get_provider_profile("claude-code").name == "anthropic"
