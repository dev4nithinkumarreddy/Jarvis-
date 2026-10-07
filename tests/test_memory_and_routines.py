"""Tests for Phase 7: SQLite memory store, secret refusal, memory tools,

context injection, conversation history windowing, scheduled routines, and acceptance tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
import pytest

from jarvis.brain.base import BrainResponse
from jarvis.brain.fake_brain import FakeBrain
from jarvis.core.channels import Confirmer, OutputChannel
from jarvis.core.config import BrainConfig, JarvisConfig, MemoryConfig, RoutinesConfig
from jarvis.core.orchestrator import Orchestrator
from jarvis.core.types import RiskTier, ToolCall
from jarvis.memory.store import (
    MemoryStore,
    SecretPatternRefusalError,
    detect_sensitive_pattern,
)
from jarvis.routines import RoutinesManager, get_files_modified_today
from jarvis.safety.audit import AuditLogger
from jarvis.safety.killswitch import KillSwitch
from jarvis.safety.permissions import PermissionEngine
from jarvis.tools.gui import create_gui_tools
from jarvis.tools.memory import create_memory_tools
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.system_tools import create_current_time_tool, create_system_info_tool


# -------------------------------------------------------------------------
# Helpers & Mocks
# -------------------------------------------------------------------------


class MockConfirmer(Confirmer):
    """Tracking Confirmer for test verification."""

    def __init__(self, approve_all: bool = True) -> None:
        self.approve_all = approve_all
        self.prompts: list[str] = []

    def confirm(self, action_description: str, warning: str | None = None) -> bool:
        self.prompts.append(action_description)
        return self.approve_all


class MockOutputChannel(OutputChannel):
    """Output channel collecting sent messages for assertions."""

    def __init__(self) -> None:
        self.sent_messages: list[str] = []

    def say(self, message: str) -> None:
        self.sent_messages.append(message)


# -------------------------------------------------------------------------
# 1. MemoryStore CRUD Tests
# -------------------------------------------------------------------------


def test_memory_store_remember_recall_forget(tmp_path: Path) -> None:
    """MemoryStore stores, searches, lists, and deletes facts."""
    db_file = tmp_path / "memory.db"
    store = MemoryStore(db_path=db_file, enabled=True)

    # 1. Store facts
    f1_id, t1 = store.remember("User prefers Python over JavaScript.")
    f2_id, t2 = store.remember("Project folder is located at D:/Projects/Jarvis.")
    assert f1_id == 1
    assert f2_id == 2

    # 2. List facts
    all_facts = store.list_facts()
    assert len(all_facts) == 2
    assert all_facts[0]["text"] == t1
    assert all_facts[1]["text"] == t2

    # 3. Recall matching facts
    py_facts = store.recall("Python")
    assert len(py_facts) == 1
    assert py_facts[0]["id"] == 1

    jarvis_facts = store.recall("Jarvis")
    assert len(jarvis_facts) == 1
    assert jarvis_facts[0]["id"] == 2

    empty_facts = store.recall("Rust")
    assert len(empty_facts) == 0

    # 4. Forget
    deleted = store.forget(f1_id)
    assert deleted is True
    assert len(store.list_facts()) == 1
    assert store.recall("Python") == []

    # Forget nonexistent
    assert store.forget(999) is False

    store.close()


def test_memory_store_conversation_logging(tmp_path: Path) -> None:
    """MemoryStore records and retrieves conversation turns."""
    db_file = tmp_path / "conv.db"
    store = MemoryStore(db_path=db_file, enabled=True)

    store.log_conversation("user", "Hello Jarvis")
    store.log_conversation("assistant", "Greetings, how can I help you today?")
    store.log_conversation("user", "What is the time?")

    recent = store.get_recent_conversations(limit=2)
    assert len(recent) == 2
    assert recent[0]["role"] == "assistant"
    assert recent[1]["role"] == "user"

    export = store.export_data()
    assert "facts" in export
    assert len(export["conversations"]) == 3

    deleted_count = store.clear_conversations()
    assert deleted_count == 3
    assert len(store.get_recent_conversations()) == 0

    store.close()


# -------------------------------------------------------------------------
# 2. Secret Pattern Refusal Tests
# -------------------------------------------------------------------------


def test_secret_pattern_refusal_credit_cards() -> None:
    """Card numbers are detected and refused."""
    assert detect_sensitive_pattern("My card is 4111 2222 3333 4444") is not None
    assert detect_sensitive_pattern("Visa: 4111-2222-3333-4444") is not None
    assert detect_sensitive_pattern("1234567890123456") is not None


def test_secret_pattern_refusal_passwords() -> None:
    """Password assignments and phrases are detected and refused."""
    assert detect_sensitive_pattern("password: supersecretpassword123") is not None
    assert detect_sensitive_pattern("passwd=my_secret_pwd") is not None
    assert detect_sensitive_pattern("My password is secret123") is not None
    assert detect_sensitive_pattern("The secret is 1234") is not None
    assert detect_sensitive_pattern("token: abcd1234efgh5678") is not None


def test_secret_pattern_refusal_api_keys() -> None:
    """Anthropic, OpenAI, GitHub tokens are detected and refused."""
    assert detect_sensitive_pattern("My key is sk-ant-api03-1234567890abcdefghijklmnop") is not None
    assert detect_sensitive_pattern("OpenAI: sk-1234567890abcdefghijklmnopqrstuvwxyz") is not None
    assert detect_sensitive_pattern("ghp_123456789012345678901234567890123456") is not None


def test_secret_pattern_safe_text_allowed() -> None:
    """Normal personal facts and preferences pass validation."""
    assert detect_sensitive_pattern("My project directory is D:/Projects/Jarvis") is None
    assert detect_sensitive_pattern("I like drinking green tea in the morning") is None
    assert detect_sensitive_pattern("The server port is 8080") is None


def test_memory_store_refuses_secrets_on_store(tmp_path: Path) -> None:
    """MemoryStore.remember raises SecretPatternRefusalError for credentials."""
    store = MemoryStore(db_path=tmp_path / "mem.db", enabled=True)

    with pytest.raises(SecretPatternRefusalError, match="credit card"):
        store.remember("Remember my card 4111-2222-3333-4444")

    with pytest.raises(SecretPatternRefusalError, match="password"):
        store.remember("Remember that my password is Password123!")

    # Verify nothing was saved
    assert len(store.list_facts()) == 0
    store.close()


# -------------------------------------------------------------------------
# 3. Disabled Memory Store Tests
# -------------------------------------------------------------------------


def test_memory_disabled_no_db_created(tmp_path: Path) -> None:
    """When memory is disabled, no database file is created or accessed."""
    db_file = tmp_path / "never_created.db"
    store = MemoryStore(db_path=db_file, enabled=False)

    assert not db_file.exists()
    assert store.recall("test") == []
    assert store.list_facts() == []
    assert store.forget(1) is False
    store.log_conversation("user", "test")
    assert store.get_recent_conversations() == []

    # remember raises RuntimeError when disabled
    with pytest.raises(RuntimeError, match="disabled"):
        store.remember("test fact")

    # DB file was never created
    assert not db_file.exists()


# -------------------------------------------------------------------------
# 4. Memory Tools Suite Tests
# -------------------------------------------------------------------------


def test_memory_tools_specs_and_execution(tmp_path: Path) -> None:
    """Verify tool specs, risk tiers, and execution."""
    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        memory=MemoryConfig(enabled=True, db_path=tmp_path / "tools_mem.db"),
    )
    tools = create_memory_tools(cfg)
    tools_by_name = {t.name: t for t in tools}

    assert tools_by_name["remember"].risk_tier == RiskTier.CONFIRM
    assert tools_by_name["recall"].risk_tier == RiskTier.AUTO
    assert tools_by_name["list_memories"].risk_tier == RiskTier.AUTO
    assert tools_by_name["forget"].risk_tier == RiskTier.CONFIRM

    # 1. remember
    rem_res = tools_by_name["remember"].handler(
        ToolCall(id="c1", name="remember", arguments={"text": "User lives in Seattle."})
    )
    assert rem_res.ok is True
    assert "Remembered fact [ID 1]" in rem_res.content

    # 2. list_memories
    list_res = tools_by_name["list_memories"].handler(
        ToolCall(id="c2", name="list_memories", arguments={})
    )
    assert list_res.ok is True
    assert "Seattle" in list_res.content

    # 3. recall
    rec_res = tools_by_name["recall"].handler(
        ToolCall(id="c3", name="recall", arguments={"query": "Seattle"})
    )
    assert rec_res.ok is True
    assert "Seattle" in rec_res.content

    # 4. forget
    del_res = tools_by_name["forget"].handler(
        ToolCall(id="c4", name="forget", arguments={"id": 1})
    )
    assert del_res.ok is True
    assert "Successfully forgot" in del_res.content


# -------------------------------------------------------------------------
# 5. Context Injection & History Windowing in Orchestrator
# -------------------------------------------------------------------------


def test_orchestrator_injects_recalled_facts(tmp_path: Path) -> None:
    """Orchestrator injects stored memories into user turn prompt."""
    store = MemoryStore(db_path=tmp_path / "inject.db", enabled=True)
    store.remember("My favorite color is navy blue.")

    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        memory=MemoryConfig(enabled=True, db_path=tmp_path / "inject.db", max_context_chars=1000),
    )

    fake_brain = FakeBrain([
        BrainResponse(text="I noted your favorite color is navy blue."),
    ])

    registry = ToolRegistry()
    orchestrator = Orchestrator(
        brain=fake_brain,
        tool_registry=registry,
        config=cfg,
        memory_store=store,
        audit_logger=AuditLogger(tmp_path / "audit.log"),
    )

    resp = orchestrator.run_turn("What is my favorite color?")
    assert resp == "I noted your favorite color is navy blue."

    # Inspect messages recorded in FakeBrain call history
    call_messages = fake_brain.call_history[0]["messages"]
    user_msg = call_messages[0]["content"]
    assert "<user_provided_memory_context>" in user_msg
    assert "navy blue" in user_msg
    assert "What is my favorite color?" in user_msg


def test_orchestrator_memory_budget_capping(tmp_path: Path) -> None:
    """Memory context respects max_context_chars cap."""
    store = MemoryStore(db_path=tmp_path / "budget.db", enabled=True)
    for i in range(20):
        store.remember(f"Fact number {i}: This is an extended informational sentence for budget testing.")

    # Configure very tight character budget
    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        memory=MemoryConfig(enabled=True, db_path=tmp_path / "budget.db", max_context_chars=250),
    )

    fake_brain = FakeBrain([BrainResponse(text="Budget test response")])
    orchestrator = Orchestrator(
        brain=fake_brain,
        tool_registry=ToolRegistry(),
        config=cfg,
        memory_store=store,
        audit_logger=AuditLogger(tmp_path / "audit.log"),
    )

    orchestrator.run_turn("Testing budget")
    call_messages = fake_brain.call_history[0]["messages"]
    user_msg = call_messages[0]["content"]

    # Extract memory block
    start_tag = "<user_provided_memory_context>"
    end_tag = "</user_provided_memory_context>"
    start_idx = user_msg.find(start_tag)
    end_idx = user_msg.find(end_tag) + len(end_tag)
    memory_block = user_msg[start_idx:end_idx]

    assert len(memory_block) <= 250


def test_orchestrator_conversation_history_windowing(tmp_path: Path) -> None:
    """Orchestrator retains only the last N turns in context window."""
    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        memory=MemoryConfig(enabled=True, db_path=tmp_path / "history.db", history_turns=2),
    )

    fake_brain = FakeBrain([
        BrainResponse(text="Answer 1"),
        BrainResponse(text="Answer 2"),
        BrainResponse(text="Answer 3"),
    ])

    orchestrator = Orchestrator(
        brain=fake_brain,
        tool_registry=ToolRegistry(),
        config=cfg,
        audit_logger=AuditLogger(tmp_path / "audit.log"),
    )

    orchestrator.run_turn("Query 1")
    orchestrator.run_turn("Query 2")
    orchestrator.run_turn("Query 3")

    # In turn 3, FakeBrain should receive messages starting from Query 2 (last 2 turns)
    turn3_messages = fake_brain.call_history[2]["messages"]
    user_prompts = [m["content"] for m in turn3_messages if m["role"] == "user"]

    assert len(user_prompts) == 2
    assert "Query 2" in user_prompts[0]
    assert "Query 3" in user_prompts[1]


# -------------------------------------------------------------------------
# 6. Scheduled Routines Safety & Briefing Tests
# -------------------------------------------------------------------------


def test_routines_safety_prohibits_non_auto_tools() -> None:
    """Routines strictly fail if attempting to execute CONFIRM or BLOCKED tools."""
    registry = ToolRegistry()
    registry.register(create_current_time_tool())
    registry.register(create_system_info_tool())

    # Register a CONFIRM tool
    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        routines=RoutinesConfig(enabled=False, briefing_time="09:00"),
    )
    gui_tools = create_gui_tools(cfg)
    for t in gui_tools:
        registry.register(t)

    manager = RoutinesManager(config=cfg, tool_registry=registry)

    # 1. AUTO tool succeeds
    time_res = manager.execute_routine_tool("current_time")
    assert time_res.ok is True

    # 2. CONFIRM tool fails with PermissionError
    with pytest.raises(PermissionError, match="Safety violation: Routines can NEVER invoke non-AUTO tools"):
        manager.execute_routine_tool("gui_click", {"x": 100, "y": 200})

    with pytest.raises(PermissionError, match="Safety violation: Routines can NEVER invoke non-AUTO tools"):
        manager.execute_routine_tool("gui_type", {"text": "hello"})


def test_daily_briefing_execution_and_output_channel(tmp_path: Path) -> None:
    """Daily briefing gathers time, system info, modified files, and sends to output channel."""
    registry = ToolRegistry()
    registry.register(create_current_time_tool())
    registry.register(create_system_info_tool())

    # Create a test file modified today
    test_file = tmp_path / "modified_today.txt"
    test_file.write_text("Hello today", encoding="utf-8")

    out_channel = MockOutputChannel()
    cfg = JarvisConfig(
        allowed_roots=[tmp_path],
        brain=BrainConfig(provider="anthropic", model="test-model"),
        routines=RoutinesConfig(enabled=True, briefing_time="09:00"),
    )

    manager = RoutinesManager(
        config=cfg,
        tool_registry=registry,
        output_channel=out_channel,
        allowed_roots=[tmp_path],
    )

    briefing_text = manager.run_daily_briefing()

    assert "JARVIS DAILY BRIEFING" in briefing_text
    assert "Current Time:" in briefing_text
    assert "System Status:" in briefing_text
    assert "Files Modified Today" in briefing_text
    assert "modified_today.txt" in briefing_text

    # Output delivered to mock channel
    assert len(out_channel.sent_messages) == 1
    assert out_channel.sent_messages[0] == briefing_text

    manager.shutdown()


# -------------------------------------------------------------------------
# 7. Acceptance Tests
# -------------------------------------------------------------------------


def test_acceptance_remember_fact_persists_across_restarts(tmp_path: Path) -> None:
    """Acceptance: 'remember that my project folder is X' asks for confirmation and persists across restarts."""
    db_file = tmp_path / "acceptance_memory.db"
    audit_file = tmp_path / "audit.log"
    confirmer = MockConfirmer(approve_all=True)

    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        memory=MemoryConfig(enabled=True, db_path=db_file),
    )

    # ---------------- SESSION 1: Store memory ----------------
    registry1 = ToolRegistry()
    for t in create_memory_tools(cfg):
        registry1.register(t)

    # Script Brain to call remember
    fake_brain1 = FakeBrain([
        BrainResponse(tool_calls=[
            ToolCall(id="c1", name="remember", arguments={"text": "my project folder is D:/Projects/Jarvis"}),
        ]),
        BrainResponse(text="I have remembered that your project folder is D:/Projects/Jarvis."),
    ])

    orchestrator1 = Orchestrator(
        brain=fake_brain1,
        tool_registry=registry1,
        config=cfg,
        confirmer=confirmer,
        audit_logger=AuditLogger(audit_file),
    )

    res1 = orchestrator1.run_turn("remember that my project folder is D:/Projects/Jarvis")
    assert "I have remembered" in res1

    # Verify confirmation was asked for remember tool
    assert len(confirmer.prompts) == 1
    assert "Store fact in persistent memory" in confirmer.prompts[0]
    assert "D:/Projects/Jarvis" in confirmer.prompts[0]

    # Verify persistence on disk
    store_check = MemoryStore(db_path=db_file, enabled=True)
    facts = store_check.list_facts()
    assert len(facts) == 1
    assert "D:/Projects/Jarvis" in facts[0]["text"]
    store_check.close()

    # ---------------- SESSION 2: Restart agent with fresh instance ----------------
    registry2 = ToolRegistry()
    for t in create_memory_tools(cfg):
        registry2.register(t)

    # Brain answers user question directly using injected context
    fake_brain2 = FakeBrain([
        BrainResponse(text="Your project folder is D:/Projects/Jarvis."),
    ])

    orchestrator2 = Orchestrator(
        brain=fake_brain2,
        tool_registry=registry2,
        config=cfg,
        confirmer=MockConfirmer(),
        audit_logger=AuditLogger(audit_file),
    )

    res2 = orchestrator2.run_turn("Where is my project folder?")
    assert res2 == "Your project folder is D:/Projects/Jarvis."

    # Verify that Brain in Session 2 received the injected memory context
    session2_user_prompt = fake_brain2.call_history[0]["messages"][0]["content"]
    assert "<user_provided_memory_context>" in session2_user_prompt
    assert "my project folder is D:/Projects/Jarvis" in session2_user_prompt


def test_acceptance_daily_briefing_scheduled_configuration() -> None:
    """Acceptance: daily briefing is configured with APScheduler and cron trigger."""
    registry = ToolRegistry()
    registry.register(create_current_time_tool())
    registry.register(create_system_info_tool())

    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        routines=RoutinesConfig(enabled=True, briefing_time="08:30"),
    )

    out_channel = MockOutputChannel()
    manager = RoutinesManager(config=cfg, tool_registry=registry, output_channel=out_channel)

    # Verify job is scheduled with correct trigger
    jobs = manager.scheduler.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "daily_briefing"
    assert str(job.trigger) == "cron[hour='8', minute='30']"

    # Execute briefing now
    result = manager.run_daily_briefing()
    assert "JARVIS DAILY BRIEFING" in result
    assert len(out_channel.sent_messages) == 1

    manager.shutdown()


def test_cli_memory_commands(tmp_path: Path) -> None:
    """Test CLI commands: jarvis memory list, delete, and export."""
    import argparse
    import yaml
    from jarvis.ui.cli import memory_command

    # Create test config and memory database
    db_file = tmp_path / "cli_mem.db"
    store = MemoryStore(db_path=db_file, enabled=True)
    store.remember("Fact A: Python rules")
    store.remember("Fact B: Jarvis agent")

    cfg_data = {
        "allowed_roots": [str(tmp_path)],
        "brain": {"provider": "anthropic", "model": "test-model"},
        "memory": {"enabled": True, "db_path": str(db_file)},
    }
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(cfg_data), encoding="utf-8")

    # 1. test 'jarvis memory list'
    args_list = argparse.Namespace(config=str(cfg_file), memory_action="list")
    memory_command(args_list)

    # 2. test 'jarvis memory export --output <path>'
    export_file = tmp_path / "export.json"
    args_export = argparse.Namespace(config=str(cfg_file), memory_action="export", output=str(export_file))
    memory_command(args_export)
    assert export_file.exists()
    exported = json.loads(export_file.read_text(encoding="utf-8"))
    assert len(exported["facts"]) == 2

    # 3. test 'jarvis memory delete --id 1'
    args_del_id = argparse.Namespace(config=str(cfg_file), memory_action="delete", id=1, all=False)
    memory_command(args_del_id)
    assert len(store.list_facts()) == 1

    # 4. test 'jarvis memory delete --all'
    args_del_all = argparse.Namespace(config=str(cfg_file), memory_action="delete", id=None, all=True)
    memory_command(args_del_all)
    assert len(store.list_facts()) == 0
    store.close()


# -------------------------------------------------------------------------
# 8. Phase 7.1 Hardening Patch Tests
# -------------------------------------------------------------------------


def test_redaction_chat_secrets_not_in_db_or_export(tmp_path: Path) -> None:
    """Raw API key and card number typed in chat do not appear in memory.db or export."""
    import sqlite3
    db_file = tmp_path / "hardened_mem.db"
    store = MemoryStore(db_path=db_file, enabled=True)

    fake_brain = FakeBrain([
        BrainResponse(text="Received your message securely.")
    ])
    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        memory=MemoryConfig(enabled=True, db_path=db_file),
    )
    orchestrator = Orchestrator(
        brain=fake_brain,
        tool_registry=ToolRegistry(),
        config=cfg,
        audit_logger=AuditLogger(tmp_path / "audit.log"),
        memory_store=store,
    )

    secret_key = "sk-ant-api03-abcdef1234567890abcdef1234567890"
    card_number = "4111 2222 3333 4444"
    chat_prompt = f"Hello Jarvis, my API key is {secret_key} and my credit card is {card_number}."

    # Run turn
    res = orchestrator.run_turn(chat_prompt)
    assert res == "Received your message securely."

    # 1. Query sqlite database directly
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM conversations WHERE role = 'user';")
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) >= 1
    stored_text = rows[0][0]

    # Secrets MUST NOT appear anywhere in the database
    assert secret_key not in stored_text
    assert card_number not in stored_text
    assert "[REDACTED]" in stored_text

    # 2. Export data and ensure no secrets exist in the JSON export
    export_dict = store.export_data()
    export_json = json.dumps(export_dict)

    assert secret_key not in export_json
    assert card_number not in export_json
    assert "[REDACTED]" in export_json

    # 3. Test tool result logging redaction
    tool_secret = "ghp_123456789012345678901234567890123456"
    store.log_conversation("tool:read_text_file", f"Config contains github_token={tool_secret}")
    export_dict2 = store.export_data()
    export_json2 = json.dumps(export_dict2)
    assert tool_secret not in export_json2
    assert "[REDACTED]" in export_json2

    store.close()


def test_migration_phase7_schema_db(tmp_path: Path) -> None:
    """Safe migration: a DB created with the Phase 7 schema without 'tainted' column migrates cleanly."""
    import sqlite3
    legacy_db_file = tmp_path / "legacy_phase7.db"

    # Create table using Phase 7 schema (no tainted column)
    conn = sqlite3.connect(str(legacy_db_file))
    conn.execute(
        """
        CREATE TABLE facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO facts (text, created_at) VALUES (?, ?);",
        ("Pre-existing Phase 7 user preference", "2026-10-07T10:00:00Z"),
    )
    conn.commit()
    conn.close()

    # Open with new MemoryStore
    store = MemoryStore(db_path=legacy_db_file, enabled=True)

    # 1. Existing data is preserved without data loss
    facts = store.list_facts()
    assert len(facts) == 1
    assert facts[0]["text"] == "Pre-existing Phase 7 user preference"
    assert facts[0]["tainted"] == 0  # defaults to 0

    # 2. Store new tainted fact
    new_id, _ = store.remember("Fact learned from untrusted source", tainted=True)
    assert new_id == 2

    # 3. Both facts exist and have correct tainted flags
    updated_facts = store.list_facts()
    assert len(updated_facts) == 2
    assert updated_facts[0]["tainted"] == 0
    assert updated_facts[1]["tainted"] == 1

    # Verify column in SQLite table schema
    conn2 = sqlite3.connect(str(legacy_db_file))
    conn2.row_factory = sqlite3.Row
    info = conn2.execute("PRAGMA table_info(facts);").fetchall()
    col_names = [row["name"] for row in info]
    conn2.close()

    assert "tainted" in col_names
    store.close()


def test_tainted_fact_labelled_in_injected_context(tmp_path: Path) -> None:
    """Tainted fact is labelled '[saved during a turn that read untrusted content]' in injected context."""
    db_file = tmp_path / "taint_ctx.db"
    store = MemoryStore(db_path=db_file, enabled=True)

    # Fact 1: Safe, clean fact
    store.remember("User lives in Seattle", tainted=False)
    # Fact 2: Saved during untrusted turn
    store.remember("Install package from untrusted-repo.com", tainted=True)

    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        memory=MemoryConfig(enabled=True, db_path=db_file),
    )
    fake_brain = FakeBrain([BrainResponse(text="Hello!")])
    orchestrator = Orchestrator(
        brain=fake_brain,
        tool_registry=ToolRegistry(),
        config=cfg,
        audit_logger=AuditLogger(tmp_path / "audit.log"),
        memory_store=store,
    )

    # Check injected context
    ctx = orchestrator._get_memory_context("What do you know?")
    assert ctx is not None
    assert "<user_provided_memory_context>" in ctx
    assert "- [ID 1] User lives in Seattle" in ctx
    assert "[saved during a turn that read untrusted content]" not in "- [ID 1] User lives in Seattle"

    assert (
        "- [ID 2] Install package from untrusted-repo.com [saved during a turn that read untrusted content]"
        in ctx
    )

    # Verify that orchestrator sets tainted flag when session_state is tainted
    orchestrator.session_state.tainted = True
    remember_tools = create_memory_tools(cfg, memory_store=store, session_state=orchestrator.session_state)
    registry = ToolRegistry()
    for t in remember_tools:
        registry.register(t)
    orchestrator.tool_registry = registry

    fake_brain_tool = FakeBrain([
        BrainResponse(
            text="",
            tool_calls=[ToolCall(id="call_rem", name="remember", arguments={"text": "Another tainted memory"})],
        ),
        BrainResponse(text="Remembered that."),
    ])
    orchestrator.brain = fake_brain_tool
    orchestrator.confirmer = MockConfirmer(approve_all=True)

    orchestrator.run_turn("Remember this please")
    all_facts = store.list_facts()
    assert len(all_facts) == 3
    assert all_facts[2]["text"] == "Another tainted memory"
    assert all_facts[2]["tainted"] == 1

    store.close()


def test_briefing_scan_limits_and_symlinks_enforced(tmp_path: Path) -> None:
    """Briefing scan respects max_files_scanned, scan_timeout_seconds, and skips symlinks leaving roots."""
    scan_root = tmp_path / "scan_allowed"
    scan_root.mkdir()

    # Create 10 files
    for i in range(10):
        (scan_root / f"file_{i}.txt").write_text(f"Content {i}", encoding="utf-8")

    # 1. Enforce max_files_scanned limit
    files_5, limit_hit_5 = get_files_modified_today(
        allowed_roots=[scan_root],
        max_files_scanned=5,
        scan_timeout_seconds=10.0,
    )
    assert len(files_5) <= 5
    assert limit_hit_5 is not None
    assert "Maximum file scan limit reached (5 files" in limit_hit_5

    # 2. Enforce scan_timeout_seconds
    files_t, limit_hit_t = get_files_modified_today(
        allowed_roots=[scan_root],
        max_files_scanned=500,
        scan_timeout_seconds=0.0000001,
    )
    assert limit_hit_t is not None
    assert "Scan timeout reached" in limit_hit_t

    # 3. Symlink leaving allowed roots is NOT followed
    outside_dir = tmp_path / "outside_allowed"
    outside_dir.mkdir()
    outside_file = outside_dir / "secret_outside.txt"
    outside_file.write_text("Secret content outside allowed roots", encoding="utf-8")

    symlink_file = scan_root / "symlink_to_outside.txt"
    try:
        symlink_file.symlink_to(outside_file)
        has_symlink = True
    except (OSError, NotImplementedError):
        has_symlink = False

    if has_symlink:
        files_sym, _ = get_files_modified_today(
            allowed_roots=[scan_root],
            max_files_scanned=500,
            scan_timeout_seconds=10.0,
        )
        found_paths = [f["path"] for f in files_sym]
        # Must not contain outside_file
        assert str(outside_file) not in found_paths
        assert "secret_outside.txt" not in str(found_paths)

    # 4. Limit notice is present in daily briefing text
    registry = ToolRegistry()
    registry.register(create_current_time_tool())
    registry.register(create_system_info_tool())

    out_ch = MockOutputChannel()
    cfg = JarvisConfig(
        allowed_roots=[scan_root],
        brain=BrainConfig(provider="anthropic", model="test-model"),
        routines=RoutinesConfig(enabled=True, briefing_time="09:00", max_files_scanned=3),
    )
    manager = RoutinesManager(
        config=cfg,
        tool_registry=registry,
        output_channel=out_ch,
        allowed_roots=[scan_root],
    )

    briefing = manager.run_daily_briefing()
    assert "JARVIS DAILY BRIEFING" in briefing
    assert "Scan Notice: Maximum file scan limit reached (3 files" in briefing
    manager.shutdown()


