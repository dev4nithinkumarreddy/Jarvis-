"""Tests for core types, data contracts, and channel abstractions."""

import pytest
from jarvis.core.channels import Confirmer, InputChannel, OutputChannel
from jarvis.core.types import (
    PermissionDecision,
    RiskTier,
    ToolCall,
    ToolResult,
    ToolSpec,
)


def test_risk_tier_enum():
    """Verify RiskTier has expected members and values."""
    assert RiskTier.AUTO == "AUTO"
    assert RiskTier.CONFIRM == "CONFIRM"
    assert RiskTier.BLOCKED == "BLOCKED"
    assert set(RiskTier) == {RiskTier.AUTO, RiskTier.CONFIRM, RiskTier.BLOCKED}


def test_tool_spec_contract():
    """Verify ToolSpec creation and attributes."""
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    handler_fn = lambda query: f"result: {query}"

    spec = ToolSpec(
        name="web_search",
        description="Search the web",
        input_schema=schema,
        risk_tier=RiskTier.AUTO,
        handler=handler_fn,
        untrusted_output=True,
    )
    assert spec.name == "web_search"
    assert spec.description == "Search the web"
    assert spec.input_schema == schema
    assert spec.risk_tier == RiskTier.AUTO
    assert spec.handler == handler_fn
    assert spec.untrusted_output is True

    # Test default handler and untrusted_output
    spec2 = ToolSpec(
        name="echo",
        description="Echo string",
        input_schema={},
        risk_tier=RiskTier.CONFIRM,
    )
    assert spec2.handler is None
    assert spec2.untrusted_output is False


def test_tool_call_contract():
    """Verify ToolCall contract."""
    call = ToolCall(id="call_123", name="read_file", arguments={"path": "test.txt"})
    assert call.id == "call_123"
    assert call.name == "read_file"
    assert call.arguments == {"path": "test.txt"}

    # Default arguments
    call_no_args = ToolCall(id="call_456", name="list_dir")
    assert call_no_args.arguments == {}


def test_tool_result_contract():
    """Verify ToolResult contract."""
    res_ok = ToolResult(call_id="call_123", ok=True, content="File contents")
    assert res_ok.call_id == "call_123"
    assert res_ok.ok is True
    assert res_ok.content == "File contents"
    assert res_ok.error is None

    res_err = ToolResult(call_id="call_123", ok=False, error="File not found")
    assert res_err.call_id == "call_123"
    assert res_err.ok is False
    assert res_err.content is None
    assert res_err.error == "File not found"


def test_permission_decision_contract():
    """Verify PermissionDecision contract."""
    decision_allowed = PermissionDecision(
        allowed=True,
        reason="Path is within allowed roots",
        needs_confirmation=False,
    )
    assert decision_allowed.allowed is True
    assert decision_allowed.reason == "Path is within allowed roots"
    assert decision_allowed.needs_confirmation is False

    decision_confirm = PermissionDecision(
        allowed=True,
        reason="Risky write operation requires confirmation",
        needs_confirmation=True,
    )
    assert decision_confirm.allowed is True
    assert decision_confirm.needs_confirmation is True


def test_channels_abc_cannot_instantiate_directly():
    """Verify that channel ABCs cannot be instantiated without implementations."""
    with pytest.raises(TypeError):
        InputChannel()  # type: ignore

    with pytest.raises(TypeError):
        OutputChannel()  # type: ignore

    with pytest.raises(TypeError):
        Confirmer()  # type: ignore


def test_channels_concrete_implementations():
    """Verify channels can be implemented as specified."""

    class MockInputChannel(InputChannel):
        def get_user_input(self, prompt: str = "") -> str:
            return f"user: {prompt}"

    class MockOutputChannel(OutputChannel):
        def __init__(self):
            self.messages: list[str] = []

        def say(self, message: str) -> None:
            self.messages.append(message)

    class MockConfirmer(Confirmer):
        def __init__(self, response: bool = True):
            self.response = response
            self.last_query = None

        def confirm(self, action_description: str, warning: str | None = None) -> bool:
            self.last_query = (action_description, warning)
            return self.response

    inp = MockInputChannel()
    assert inp.get_user_input("hello") == "user: hello"

    out = MockOutputChannel()
    out.say("Jarvis ready")
    assert out.messages == ["Jarvis ready"]

    conf = MockConfirmer(response=True)
    assert conf.confirm("Delete directory", warning="Permanent deletion") is True
    assert conf.last_query == ("Delete directory", "Permanent deletion")
