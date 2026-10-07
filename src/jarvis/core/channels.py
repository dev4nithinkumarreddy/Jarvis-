"""Abstract communication channel interfaces for Jarvis."""

from __future__ import annotations

from abc import ABC, abstractmethod


class InputChannel(ABC):
    """Abstract interface for receiving user input."""

    @abstractmethod
    def get_user_input(self, prompt: str = "") -> str:
        """Receive input from the user.

        Args:
            prompt: Optional prompt text to display to the user.

        Returns:
            The user's input string.
        """
        pass


class OutputChannel(ABC):
    """Abstract interface for sending messages to the user."""

    @abstractmethod
    def say(self, message: str) -> None:
        """Deliver a message to the user.

        Args:
            message: The message string to present.
        """
        pass


class Confirmer(ABC):
    """Abstract interface for prompting user confirmation of risky actions."""

    @abstractmethod
    def confirm(self, action_description: str, warning: str | None = None) -> bool:
        """Prompt the user to confirm or deny an action.

        Args:
            action_description: Clear description of the proposed action.
            warning: Optional warning detail regarding consequences of the action.

        Returns:
            True if the user explicitly approved the action, False otherwise.
        """
        pass

    def dismiss(self) -> None:
        """Optional hook to dismiss active confirmation dialog/prompt."""
        pass


class CompositeOutputChannel(OutputChannel):
    """Broadcasts messages to multiple output channels."""

    def __init__(self, channels: list[OutputChannel]) -> None:
        self.channels = [c for c in channels if c is not None]

    def say(self, message: str) -> None:
        for ch in self.channels:
            try:
                ch.say(message)
            except Exception:
                pass


class CompositeInputChannel(InputChannel):
    """Multiplexes user input from multiple input channels."""

    def __init__(self, channels: list[InputChannel]) -> None:
        self.channels = [c for c in channels if c is not None]

    def get_user_input(self, prompt: str = "") -> str:
        if not self.channels:
            return ""
        if len(self.channels) == 1:
            return self.channels[0].get_user_input(prompt)

        import queue
        import threading

        q: queue.Queue[str] = queue.Queue()
        stop_event = threading.Event()

        def worker(ch: InputChannel) -> None:
            try:
                val = ch.get_user_input(prompt)
                if not stop_event.is_set() and val:
                    q.put(val)
                    stop_event.set()
            except Exception:
                pass

        for ch in self.channels:
            t = threading.Thread(target=worker, args=(ch,), daemon=True)
            t.start()

        res = q.get()
        stop_event.set()
        return res

