"""CLI-based Confirmer implementation using Rich and timed input."""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable
from rich.console import Console

from jarvis.core.channels import Confirmer
from jarvis.core.config import JarvisConfig


class CLIConfirmer(Confirmer):
    """Command-line confirmer with rich styling, default 'No', and configurable timeout."""

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        console: Console | None = None,
        input_fn: Callable[[str], str] | None = None,
    ) -> None:
        """Initialize CLIConfirmer.

        Args:
            timeout_seconds: Timeout in seconds before defaulting to No.
            console: Optional Rich Console instance for output rendering.
            input_fn: Optional input function to override built-in input (useful for testing).
        """
        self.timeout_seconds = timeout_seconds
        self.console = console if console is not None else Console()
        self._input_fn = input_fn

    @classmethod
    def from_config(
        cls,
        config: JarvisConfig,
        console: Console | None = None,
        input_fn: Callable[[str], str] | None = None,
    ) -> CLIConfirmer:
        """Create CLIConfirmer instance configured from JarvisConfig."""
        return cls(
            timeout_seconds=config.confirm_timeout_seconds,
            console=console,
            input_fn=input_fn,
        )

    def _read_input(self, prompt_text: str, timeout: float) -> str | None:
        """Read input with a timeout using non-blocking console input on Windows, or worker thread."""
        if (
            self._input_fn is None
            and sys.platform == "win32"
            and hasattr(sys.stdin, "isatty")
            and sys.stdin.isatty()
        ):
            try:
                import msvcrt

                self.console.print(prompt_text, end="")
                chars: list[str] = []
                start = time.monotonic()
                while time.monotonic() - start < timeout:
                    if msvcrt.kbhit():
                        ch = msvcrt.getwche()
                        if ch in ("\r", "\n"):
                            self.console.print()
                            return "".join(chars)
                        elif ch == "\b":
                            if chars:
                                chars.pop()
                                self.console.print(" \b", end="")
                        elif ch == "\x03":  # Ctrl+C
                            raise KeyboardInterrupt
                        else:
                            chars.append(ch)
                    time.sleep(0.05)
                self.console.print()
                return None
            except KeyboardInterrupt:
                raise
            except Exception:
                pass  # Fall back to thread-based input below

        result: list[str | None] = [None]
        event = threading.Event()

        def worker() -> None:
            try:
                if self._input_fn is not None:
                    result[0] = self._input_fn(prompt_text)
                else:
                    result[0] = input(prompt_text)
            except (EOFError, Exception):
                result[0] = None
            finally:
                event.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        is_done = event.wait(timeout=timeout)

        if not is_done:
            return None  # Timed out
        return result[0]

    def dismiss(self) -> None:
        """Dismiss pending confirmation."""
        # Sets dismiss flag if tracked
        pass

    def confirm(self, action_description: str, warning: str | None = None) -> bool:
        """Prompt user to confirm an action using rich terminal formatting.

        Defaults to 'No' if user enters empty string, 'n', or if timeout expires.

        Args:
            action_description: Detailed description of the requested action.
            warning: Optional warning text regarding consequences or session taint.

        Returns:
            True if user explicitly approved with 'y' or 'yes', False otherwise.
        """
        self.console.print("\n[bold cyan]Action Confirmation Required:[/]")
        self.console.print(f"  {action_description}")

        if warning:
            self.console.print(f"  [bold red]{warning}[/]")

        prompt_str = f"Proceed? [y/N] (timeout: {self.timeout_seconds:.1f}s): "

        raw_input = self._read_input(prompt_str, timeout=self.timeout_seconds)

        if raw_input is None:
            self.console.print(
                f"\n[bold yellow]Confirmation timed out after {self.timeout_seconds:.1f}s. "
                "Defaulting to 'No' (denied).[/]"
            )
            return False

        clean_input = raw_input.strip().lower()
        if clean_input in ("y", "yes"):
            self.console.print("[green]Confirmation granted.[/green]")
            return True

        self.console.print("[yellow]Confirmation denied.[/yellow]")
        return False


class CompositeConfirmer(Confirmer):
    """Multiplexes confirmation requests across multiple confirmers concurrently.

    The first confirmer to produce an answer (approval or denial) wins. All other
    confirmers are immediately dismissed.
    """

    def __init__(self, confirmers: list[Confirmer]) -> None:
        self.confirmers = [c for c in confirmers if c is not None]

    def dismiss(self) -> None:
        """Dismiss all registered confirmers."""
        for c in self.confirmers:
            if hasattr(c, "dismiss") and callable(c.dismiss):
                try:
                    c.dismiss()
                except Exception:
                    pass

    def confirm(self, action_description: str, warning: str | None = None) -> bool:
        """Prompt user across all active confirmers; first answer wins."""
        if not self.confirmers:
            return False
        if len(self.confirmers) == 1:
            return self.confirmers[0].confirm(action_description, warning=warning)

        winner: list[bool | None] = [None]
        event = threading.Event()
        lock = threading.Lock()

        def runner(confirmer: Confirmer) -> None:
            try:
                ans = confirmer.confirm(action_description, warning=warning)
            except Exception:
                ans = False
            with lock:
                if not event.is_set():
                    winner[0] = ans
                    event.set()

        threads = []
        for c in self.confirmers:
            t = threading.Thread(target=runner, args=(c,), daemon=True)
            threads.append(t)
            t.start()

        event.wait()
        # Dismiss remaining confirmers
        self.dismiss()
        return bool(winner[0])

