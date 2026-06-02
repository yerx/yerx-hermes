import pytest
from agent.transports.claude_code_spawn import (
    assert_trusted_context,
    UntrustedContextError,
)


def test_local_interactive_context_is_allowed():
    assert_trusted_context({"source": "cli", "interactive": True})


@pytest.mark.parametrize("ctx", [
    {"source": "channel"},
    {"source": "gateway"},
    {"delegated": True},
    {"source": "remote_trigger"},
])
def test_untrusted_contexts_are_refused(ctx):
    with pytest.raises(UntrustedContextError):
        assert_trusted_context(ctx)
