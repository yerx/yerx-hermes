"""Provider profile for the Claude Code CLI engine.

A distinct provider (not an openai_runtime sub-mode): auth is the local
`claude` login, the catalog is static, and no HTTP health probe applies.

Named ``claude-cli`` rather than ``claude-code`` because ``claude-code`` is
already wired throughout the codebase as a synonym for the ``anthropic``
provider (auth, models, web_server, providers, auxiliary_client, and the
anthropic plugin alias). Reusing that name would clobber those paths.
"""

from __future__ import annotations

from providers import register_provider
from providers.base import ProviderProfile

_profile = ProviderProfile(
    name="claude-cli",
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
