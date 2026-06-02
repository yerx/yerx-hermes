"""Provider profile for the Claude Code CLI engine.

A distinct provider (not an openai_runtime sub-mode): auth is the local
`claude` login, the catalog is static, and no HTTP health probe applies.
"""

from __future__ import annotations

import providers as _providers_pkg
from providers import register_provider
from providers.base import ProviderProfile

_profile = ProviderProfile(
    name="claude-code",
    api_mode="claude_code_cli",
    display_name="Claude Code (CLI engine)",
    description="Runs the local `claude` CLI as the reasoning engine.",
    auth_type="api_key",
    env_vars=(),
    base_url="",
    supports_health_check=False,
    fallback_models=(
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ),
)

register_provider(_profile)

# The anthropic plugin registers "claude-code" as an alias pointing to
# "anthropic".  Since legacy providers/*.py modules are imported AFTER
# bundled plugins, that alias is already in _ALIASES when we get here.
# Override it so get_provider_profile("claude-code") resolves to this
# distinct profile rather than the anthropic one.
_providers_pkg._ALIASES["claude-code"] = "claude-code"
