"""Tests for single instance handshake and summon listener in jarvisw."""

import socket
import threading
import time
from unittest.mock import MagicMock, patch

import jarvisw


def test_single_instance_handshake_and_summon():
    """Verify primary instance starts listener and secondary instance receives ACK and triggers summon."""
    stop_event = threading.Event()
    mock_app = MagicMock()

    with patch.object(jarvisw, "LOCK_PORT", 48995):
        # Reset module socket state
        if jarvisw._instance_lock_socket:
            try:
                jarvisw._instance_lock_socket.close()
            except Exception:
                pass
            jarvisw._instance_lock_socket = None

        # 1. Primary instance claims lock
        claimed = jarvisw.acquire_single_instance_or_summon()
        assert claimed is True
        assert jarvisw._instance_lock_socket is not None

        # Start summon listener
        jarvisw.start_summon_listener(mock_app, stop_event)

        # 2. Secondary instance tries to acquire lock
        # It should connect to primary, send SUMMON, get ACK_SUMMON, and return False
        secondary_claimed = jarvisw.acquire_single_instance_or_summon()
        assert secondary_claimed is False

        # Give summon listener a split second to execute
        time.sleep(0.3)
        mock_app.summon.assert_called_once()

        # Clean up
        stop_event.set()
        if jarvisw._instance_lock_socket:
            try:
                jarvisw._instance_lock_socket.close()
            except Exception:
                pass
            jarvisw._instance_lock_socket = None
