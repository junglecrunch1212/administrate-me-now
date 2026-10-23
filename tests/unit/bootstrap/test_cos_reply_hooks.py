"""Owner-grant lifecycle and the pinned Gateway conversation capability."""

import copy

import pytest

from bootstrap.cos_reply_hooks import configure_reply_hook, require_reply_hook


def test_grant_reactivation_and_last_grant_revocation_preserve_other_policy() -> None:
    plugin = {"hooks": {"allowPromptInjection": False}, "enabled": True}
    original = copy.deepcopy(plugin)
    configure_reply_hook(plugin, ["a" * 64])
    require_reply_hook(plugin, ["a" * 64])
    assert plugin["hooks"] == {
        "allowPromptInjection": False,
        "allowConversationAccess": True,
    }
    configured = copy.deepcopy(plugin)
    configure_reply_hook(plugin, ["a" * 64])
    assert plugin == configured
    configure_reply_hook(plugin, [])
    require_reply_hook(plugin, [])
    assert plugin == {
        **original,
        "hooks": {"allowPromptInjection": False, "allowConversationAccess": False},
    }


@pytest.mark.parametrize("access", [None, False, "true", 1])
def test_granted_but_host_blocked_reply_is_not_ready(access: object) -> None:
    plugin = {} if access is None else {"hooks": {"allowConversationAccess": access}}
    with pytest.raises(ValueError, match="completion hook"):
        require_reply_hook(plugin, ["a" * 64])
    require_reply_hook(plugin, [])
