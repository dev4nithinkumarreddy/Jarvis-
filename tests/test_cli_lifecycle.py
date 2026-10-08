"""Tests for Jarvis CLI lifecycle commands: status, stop, restart, and doctor."""

import argparse
from unittest.mock import MagicMock, patch
import pytest

from jarvis.ui.cli import (
    doctor_command,
    get_running_jarvis_processes,
    restart_command,
    status_command,
    stop_command,
)


def test_get_running_jarvis_processes_filters_properly():
    """Verify process finder filters out unrelated processes."""
    mock_p1 = MagicMock()
    mock_p1.pid = 9999
    mock_p1.info = {"cmdline": ["python", "random_script.py"]}
    mock_p1.net_connections.return_value = []

    mock_p2 = MagicMock()
    mock_p2.pid = 8888
    mock_p2.info = {"cmdline": ["pythonw", "jarvisw.pyw"]}
    mock_p2.net_connections.return_value = []

    with patch("psutil.process_iter", return_value=[mock_p1, mock_p2]), \
         patch("os.getpid", return_value=1234):
        found = get_running_jarvis_processes()
        assert len(found) == 1
        assert found[0].pid == 8888


def test_status_command_offline(capsys):
    """Verify status command output when no Jarvis instances are running."""
    args = argparse.Namespace()
    with patch("jarvis.ui.cli.get_running_jarvis_processes", return_value=[]):
        status_command(args)
    # Output rendered via rich console without crashing


def test_status_command_online():
    """Verify status command output when a Jarvis process is detected."""
    args = argparse.Namespace()
    mock_proc = MagicMock()
    mock_proc.pid = 4321
    mock_proc.name.return_value = "pythonw.exe"
    mock_proc.memory_info.return_value.rss = 150 * 1024 * 1024
    mock_proc.status.return_value = "running"

    with patch("jarvis.ui.cli.get_running_jarvis_processes", return_value=[mock_proc]):
        status_command(args)


def test_stop_command():
    """Verify stop terminates running processes cleanly."""
    args = argparse.Namespace()
    mock_proc = MagicMock()
    mock_proc.is_running.return_value = False

    with patch("jarvis.ui.cli.get_running_jarvis_processes", return_value=[mock_proc]), \
         patch("time.sleep"):
        stop_command(args)
        mock_proc.terminate.assert_called_once()


def test_restart_command():
    """Verify restart calls stop and launch."""
    args = argparse.Namespace()
    with patch("jarvis.ui.cli.stop_command") as mock_stop, \
         patch("jarvis.ui.cli.launch_command") as mock_launch, \
         patch("time.sleep"):
        restart_command(args)
        mock_stop.assert_called_once_with(args)
        mock_launch.assert_called_once_with(args)


def test_doctor_command():
    """Verify doctor command runs health checks without throwing exceptions."""
    args = argparse.Namespace(fix=False)
    with patch("sounddevice.query_devices", side_effect=Exception("No soundcard")):
        # Even if soundcard throws, doctor handles it gracefully
        doctor_command(args)
