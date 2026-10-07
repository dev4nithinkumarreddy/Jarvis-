"""Command-line interface for Jarvis providing 'jarvis chat' and 'jarvis voice'."""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from jarvis.brain.anthropic_brain import AnthropicBrain
from jarvis.brain.base import Brain
from jarvis.brain.groq_brain import GroqBrain
from jarvis.core.channels import (
    CompositeInputChannel,
    CompositeOutputChannel,
    Confirmer,
    InputChannel,
    OutputChannel,
)
from jarvis.core.config import load_config
from jarvis.core.orchestrator import Orchestrator
from jarvis.memory.store import MemoryStore
from jarvis.routines import RoutinesManager
from jarvis.safety.audit import AuditLogger, verify_audit_log
from jarvis.safety.confirmers import CLIConfirmer, CompositeConfirmer
from jarvis.safety.killswitch import get_kill_switch
from jarvis.tools import ToolRegistry, create_all_tools
from jarvis.ui.server import HudChannel, HudServer
from jarvis.voice.audio_in import AudioCapture
from jarvis.voice.channel import VoiceChannel, generate_jarvis_greeting
from jarvis.voice.models import setup_piper_voice
from jarvis.voice.stt import FasterWhisperSTT
from jarvis.voice.triggers import OpenWakeWordDetector, PushToTalkTrigger, WakeWordTrigger
from jarvis.voice.tts import PiperTTS, Pyttsx3TTS
from jarvis.ui.widget import launch_widget
from jarvis.ui.autostart import (
    create_desktop_shortcut,
    disable_autostart,
    enable_autostart,
    is_autostart_enabled,
)
from jarvis.gui_control.screen import ScreenCapture

console = Console()


def create_orchestrator(
    config_path: str = "config.yaml",
    brain: Brain | None = None,
    dry_run: bool = False,
    confirmer: Confirmer | None = None,
) -> Orchestrator:
    """Initialize Orchestrator with all configured tools and safety modules."""
    config = load_config(config_path)

    # Brain initialization
    if brain is None:
        provider = config.brain.provider.lower()
        if provider == "anthropic":
            if not os.environ.get("ANTHROPIC_API_KEY"):
                console.print(
                    "[bold red]Error:[/] ANTHROPIC_API_KEY environment variable is not set.\n"
                    "Please set your Anthropic API key in your environment:\n"
                    "  [cyan]$env:ANTHROPIC_API_KEY = 'your-key'[/cyan] (PowerShell)\n"
                    "  [cyan]set ANTHROPIC_API_KEY=your-key[/cyan] (cmd)\n",
                    style="red",
                )
                sys.exit(1)
            brain = AnthropicBrain.from_config(config)
        elif provider == "groq":
            if not os.environ.get("GROQ_API_KEY"):
                console.print(
                    "[bold red]Error:[/] GROQ_API_KEY environment variable is not set.\n"
                    "Please set your Groq API key in your environment:\n"
                    "  [cyan]$env:GROQ_API_KEY = 'gsk_your_key'[/cyan] (PowerShell)\n"
                    "  [cyan]set GROQ_API_KEY=gsk_your_key[/cyan] (cmd)\n",
                    style="red",
                )
                sys.exit(1)
            brain = GroqBrain.from_config(config)
            try:
                brain.verify_model_exists()
            except Exception as exc:
                console.print(f"[bold red]Startup Check Failed:[/] {exc}", style="red")
                sys.exit(1)
        else:
            console.print(
                f"[bold red]Error:[/] Unsupported brain provider '{config.brain.provider}'. "
                "Supported providers: 'anthropic', 'groq'.",
                style="red",
            )
            sys.exit(1)

    supports_images = getattr(brain, "supports_images", True)

    # Tool registry with all read-only and state-altering tools
    registry = ToolRegistry()
    for tool_spec in create_all_tools(config, supports_images=supports_images):
        registry.register(tool_spec)

    kill_switch = get_kill_switch()
    kill_switch.start_hotkey_listener()

    audit_logger = AuditLogger(config.audit_log_path)
    if confirmer is None:
        confirmer = CLIConfirmer.from_config(config, console=console)

    return Orchestrator(
        brain=brain,
        tool_registry=registry,
        config=config,
        audit_logger=audit_logger,
        kill_switch=kill_switch,
        confirmer=confirmer,
        dry_run=dry_run,
    )


def chat_command(args: argparse.Namespace) -> None:
    """Handler for 'jarvis chat' command."""
    orchestrator = create_orchestrator(config_path=args.config, dry_run=args.dry_run)

    if args.dry_run:
        console.print("[bold yellow]Mode: DRY RUN (CONFIRM tools will describe actions without acting)[/bold yellow]")

    try:
        # Single-turn query mode if query argument was supplied
        if args.query:
            query_text = " ".join(args.query)
            response = orchestrator.run_turn(query_text)
            console.print(Markdown(response))
            return

        # Interactive chat session
        console.print(
            Panel(
                "[bold green]Jarvis Desktop Assistant[/bold green]\n"
                "Emergency Stop Hotkey: [bold yellow]Ctrl+Alt+Shift+K[/bold yellow]\n"
                "Type [bold cyan]exit[/bold cyan] or [bold cyan]quit[/bold cyan] to end the session.",
                title="Jarvis CLI",
                border_style="cyan",
            )
        )

        while True:
            try:
                user_input = console.input("\n[bold cyan]You > [/bold cyan]").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit", "q"):
                    console.print("[dim]Goodbye.[/dim]")
                    break

                response = orchestrator.run_turn(user_input)
                console.print("\n[bold green]Jarvis > [/bold green]")
                console.print(Markdown(response))
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Session terminated.[/dim]")
                break
    finally:
        if orchestrator.audit_logger is not None:
            orchestrator.audit_logger.log_event(
                event="session_shutdown",
                status="clean_shutdown",
            )
            final_hash = orchestrator.audit_logger.close()
            console.print(
                f"[dim]Audit chain closed cleanly. Final record_hash:[/] [bold cyan]{final_hash}[/]"
            )


def voice_command(args: argparse.Namespace) -> None:
    """Handler for 'jarvis voice' command with --ptt, --wake, setup-piper, and test modes."""
    if getattr(args, "voice_action", None) == "setup-piper":
        console.print("[bold cyan]Downloading Piper Neural TTS British Butler Voice (Paul Bettany Cadence)...[/]")
        onnx_path, json_path = setup_piper_voice()
        console.print(f"[bold green]Piper Voice ready:[/] {onnx_path}")
        console.print("[green]Testing audio synthesis...[/]")
        fallback_tts = Pyttsx3TTS()
        tts = PiperTTS(onnx_path, json_path, fallback_tts=fallback_tts)
        tts.speak("At your service, Sir. Neural audio synthesis is fully operational.")
        return

    if getattr(args, "voice_action", None) == "test":
        config = load_config(args.config)
        output_dev = getattr(config.voice, "output_device", None)
        fallback_tts = Pyttsx3TTS(device=output_dev)
        if config.voice.tts_engine.lower() == "piper" and config.voice.piper_model_path:
            tts = PiperTTS(
                model_path=config.voice.piper_model_path,
                config_path=config.voice.piper_config_path,
                device=output_dev,
                fallback_tts=fallback_tts,
            )
        else:
            tts = fallback_tts
        user_title = getattr(config.persona, "user_title", "Sir") if hasattr(config, "persona") else "Sir"
        greeting = generate_jarvis_greeting(user_title)
        console.print(f"[green]Speaking:[/] {greeting}")
        tts.speak(greeting)
        return

    config = load_config(args.config)
    capture = AudioCapture(
        sample_rate=config.voice.sample_rate,
        device=config.voice.input_device,
    )
    stt = FasterWhisperSTT(
        model_size=config.voice.stt_model,
        device=config.voice.stt_device,
        language=getattr(config.voice, "language", "en"),
    )
    output_dev = getattr(config.voice, "output_device", None)
    fallback_tts = Pyttsx3TTS(device=output_dev)
    if config.voice.tts_engine.lower() == "piper" and config.voice.piper_model_path:
        tts = PiperTTS(
            model_path=config.voice.piper_model_path,
            config_path=config.voice.piper_config_path,
            device=output_dev,
            fallback_tts=fallback_tts,
        )
    else:
        tts = fallback_tts

    is_wake_mode = bool(getattr(args, "wake", False))
    if is_wake_mode:
        detector = OpenWakeWordDetector(model_name=config.voice.wake_word)
        trigger = WakeWordTrigger(
            capture=capture,
            wake_word=config.voice.wake_word,
            wake_threshold=config.voice.wake_threshold,
            silence_timeout_seconds=config.voice.silence_timeout_seconds,
            detector=detector,
        )
    else:
        trigger = PushToTalkTrigger(
            capture=capture,
            key_name=config.voice.ptt_key,
        )

    voice_channel = VoiceChannel(
        stt=stt,
        tts=tts,
        trigger=trigger,
        capture=capture,
        config=config,
    )

    orchestrator = create_orchestrator(
        config_path=args.config,
        dry_run=args.dry_run,
        confirmer=voice_channel,
    )

    mode_label = "Wake Word" if is_wake_mode else f"Push-to-Talk [Hold '{config.voice.ptt_key}']"
    out_dev_label = config.voice.output_device or "Default"
    in_dev_label = config.voice.input_device or "Default"
    console.print(
        Panel(
            f"[bold green]Jarvis Voice Channel Online[/bold green]\n"
            f"Mode: [bold cyan]{mode_label}[/bold cyan]\n"
            f"STT: [yellow]{config.voice.stt_model}[/yellow] (lang: [yellow]{getattr(config.voice, 'language', 'en')}[/yellow])\n"
            f"TTS: [yellow]{config.voice.tts_engine}[/yellow] (Output: [yellow]{out_dev_label}[/yellow])\n"
            f"Input Device: [yellow]{in_dev_label}[/yellow]\n"
            f"Dual-factor key: [yellow]{config.voice.dual_confirm_key}[/yellow]\n"
            f"Emergency Stop: [bold yellow]Ctrl+Alt+Shift+K[/bold yellow]\n"
            f"Say [bold red]'exit'[/bold red] or press Ctrl+C to terminate.",
            title="Jarvis Voice",
            border_style="green",
        )
    )

    user_title = getattr(config.persona, "user_title", "Sir") if hasattr(config, "persona") else "Sir"
    greeting = generate_jarvis_greeting(user_title)
    voice_channel.say(greeting)

    while True:
        try:
            user_text = voice_channel.get_user_input()
            if not user_text:
                continue
            if user_text.lower() in ("exit", "quit", "goodbye", "bye"):
                voice_channel.say("Goodbye.")
                break

            response = orchestrator.run_turn(user_text)
            voice_channel.say(response)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Voice session terminated.[/dim]")
            break


def memory_command(args: argparse.Namespace) -> None:
    """Handle 'jarvis memory' commands: list, delete, export."""
    config = load_config(args.config)
    store = MemoryStore(db_path=config.memory.db_path, enabled=config.memory.enabled)

    if not store.enabled:
        console.print("[yellow]Memory store is disabled in configuration.[/yellow]")
        return

    if args.memory_action == "list":
        facts = store.list_facts()
        if not facts:
            console.print("[dim]No memories stored.[/dim]")
            return

        table = Table(title="Stored Memories")
        table.add_column("ID", justify="right", style="cyan", no_wrap=True)
        table.add_column("Fact", style="white")
        table.add_column("Tainted", justify="center")
        table.add_column("Created (UTC)", style="dim")

        for f in facts:
            is_tainted = bool(f.get("tainted", 0))
            tainted_str = "[bold yellow]Yes[/bold yellow]" if is_tainted else "[green]No[/green]"
            table.add_row(str(f["id"]), f["text"], tainted_str, f["created_at"])

        console.print(table)

    elif args.memory_action == "delete":
        if getattr(args, "all", False):
            count = store.clear_facts()
            console.print(f"[green]Deleted all memories ({count} total).[/green]")
        elif getattr(args, "id", None) is not None:
            fact_id = int(args.id)
            deleted = store.forget(fact_id)
            if deleted:
                console.print(f"[green]Successfully deleted memory fact [ID {fact_id}].[/green]")
            else:
                console.print(f"[red]Memory fact with ID {fact_id} not found.[/red]")
        else:
            console.print("[yellow]Please specify --id <ID> or --all to delete memories.[/yellow]")

    elif args.memory_action == "export":
        data = store.export_data()
        out_path = getattr(args, "output", None)
        if out_path:
            p = Path(out_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            console.print(f"[green]Exported memory data to '{p}'.[/green]")
        else:
            console.print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        console.print("[yellow]Please specify an action: list, delete, or export.[/yellow]")


def audit_command(args: argparse.Namespace) -> None:
    """Handle 'jarvis audit' commands: verify SHA-256 chain."""
    if getattr(args, "audit_action", None) == "verify":
        config = load_config(args.config)
        log_path = getattr(args, "log", None) or config.audit_log_path
        valid, count, err = verify_audit_log(log_path)
        if valid:
            console.print(
                f"[bold green]Audit log verified:[/] {count} records valid. SHA-256 hash chain intact."
            )
        else:
            console.print(f"[bold red]Audit log verification failed:[/] {err}")
            sys.exit(1)
    else:
        console.print("[yellow]Please specify an audit action, e.g. 'jarvis audit verify'.[/yellow]")


def run_command(args: argparse.Namespace) -> None:
    """Start Jarvis with all configured channels enabled (CLI, Voice, HUD)."""
    config = load_config(args.config)
    dry_run = getattr(args, "dry_run", False)

    console.print(
        Panel.fit(
            "[bold cyan]Jarvis Agent - Multi-Channel Session[/]\n"
            "[dim]Safety controls active. Kill switch hotkey enabled.[/]",
            border_style="cyan",
        )
    )

    kill_switch = get_kill_switch()
    kill_switch.start_hotkey_listener()

    active_confirmers: list[Confirmer] = []
    active_outputs: list[OutputChannel] = []

    # 1. CLI Confirmer and Output
    cli_confirmer = CLIConfirmer.from_config(config, console=console)
    active_confirmers.append(cli_confirmer)

    class CLIOutputChannel(OutputChannel):
        def say(self, message: str) -> None:
            console.print(f"\n[bold green]Jarvis:[/] {message}")

    active_outputs.append(CLIOutputChannel())

    # 2. Local Web HUD Channel
    hud_server: HudServer | None = None
    if config.hud.enabled and not getattr(args, "no_hud", False):
        user_title = getattr(config.persona, "user_title", "Sir") if hasattr(config, "persona") else "Sir"
        hud_server = HudServer(
            host=config.hud.host,
            port=config.hud.port,
            kill_switch=kill_switch,
            confirm_timeout_seconds=config.hud.confirm_timeout_seconds,
            user_title=user_title,
        )
        hud_server.start()
        active_confirmers.append(hud_server.channel)
        active_outputs.append(hud_server.channel)

        console.print(
            Panel(
                f"[bold green]Jarvis HUD is running at:[/] [bold cyan]{hud_server.url}[/]\n"
                "[dim]Open this URL in your browser to access the live dashboard.[/dim]",
                title="Local Web HUD",
                border_style="green",
            )
        )

    # 3. Voice Channel: Active by default (unless --no-voice is specified)
    voice_channel: VoiceChannel | None = None
    enable_voice = not getattr(args, "no_voice", False)
    if enable_voice:
        try:
            output_dev = getattr(config.voice, "output_device", None)
            fallback_tts = Pyttsx3TTS(device=output_dev)
            if config.voice.tts_engine == "piper" and config.voice.piper_model_path:
                tts = PiperTTS(
                    model_path=config.voice.piper_model_path,
                    config_path=config.voice.piper_config_path,
                    device=output_dev,
                    fallback_tts=fallback_tts,
                )
            else:
                tts = fallback_tts

            audio_capture = AudioCapture(
                sample_rate=config.voice.sample_rate,
                device=config.voice.input_device,
            )
            stt = FasterWhisperSTT(
                model_size=config.voice.stt_model,
                device=config.voice.stt_device,
                language=getattr(config.voice, "language", "en"),
            )
            trigger = PushToTalkTrigger(
                capture=audio_capture,
                key_name=config.voice.ptt_key,
            )
            voice_channel = VoiceChannel(
                stt=stt,
                tts=tts,
                trigger=trigger,
                capture=audio_capture,
                config=config,
            )
            active_confirmers.append(voice_channel)
            active_outputs.append(voice_channel)
            console.print("[green]Voice channel active: Speech output enabled & microphone listening [Hold Space to talk].[/green]")
        except Exception as exc:
            console.print(f"[yellow]Microphone capture skipped ({exc}). Audio speech output active.[/yellow]")
            try:
                out_dev = getattr(config.voice, "output_device", None)
                tts = Pyttsx3TTS(device=out_dev)
                class TTSOnlyChannel(OutputChannel):
                    def say(self, message: str) -> None:
                        tts.speak(message)
                active_outputs.append(TTSOnlyChannel())
            except Exception:
                pass

    # Composite Confirmer & Output Channel
    composite_confirmer = CompositeConfirmer(active_confirmers)
    composite_output = CompositeOutputChannel(active_outputs)

    # Audit Logger with live HUD streaming
    audit_logger = AuditLogger(config.audit_log_path)
    if hud_server is not None:
        audit_logger.set_listener(hud_server.channel.broadcast_audit)

    orchestrator = create_orchestrator(
        config_path=args.config,
        dry_run=dry_run,
        confirmer=composite_confirmer,
    )
    orchestrator.audit_logger = audit_logger

    # Start Scheduled Routines if configured
    routines_manager: RoutinesManager | None = None
    if config.routines.enabled:
        routines_manager = RoutinesManager(
            config=config,
            tool_registry=orchestrator.tool_registry,
            output_channel=composite_output,
            allowed_roots=config.allowed_roots,
        )
        routines_manager.start()

    # Proactive Iron Man Vocal Greeting on Startup!
    user_title = getattr(config.persona, "user_title", "Sir") if hasattr(config, "persona") else "Sir"
    greeting_text = generate_jarvis_greeting(user_title)
    composite_output.say(greeting_text)

    console.print("[bold cyan]Jarvis is ready! Type below, use the Web HUD, or hold Space for Voice. (Ctrl+C to exit)[/]\n")

    unified_input_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    stop_event = threading.Event()

    # 1. Voice worker thread
    if voice_channel is not None:
        def voice_worker() -> None:
            while not stop_event.is_set():
                try:
                    text = voice_channel.get_user_input()
                    if text and not stop_event.is_set():
                        unified_input_queue.put(("Voice", text))
                except Exception as exc:
                    time.sleep(0.1)

        t_voice = threading.Thread(target=voice_worker, daemon=True)
        t_voice.start()

    # 2. HUD worker thread
    if hud_server is not None:
        def hud_worker() -> None:
            while not stop_event.is_set():
                try:
                    text = hud_server.channel.input_queue.get(timeout=0.2)
                    if text and not stop_event.is_set():
                        unified_input_queue.put(("HUD", text))
                except queue.Empty:
                    continue
                except Exception:
                    break

        t_hud = threading.Thread(target=hud_worker, daemon=True)
        t_hud.start()

    # 3. CLI worker thread
    def cli_worker() -> None:
        while not stop_event.is_set():
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                text = line.strip()
                if text and not stop_event.is_set():
                    unified_input_queue.put(("CLI", text))
            except Exception:
                break

    t_cli = threading.Thread(target=cli_worker, daemon=True)
    t_cli.start()

    try:
        while True:
            if kill_switch.is_set():
                console.print("\n[bold red]Emergency Stop (Kill Switch) is active. Exiting.[/]")
                break

            try:
                source, user_input = unified_input_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if not user_input:
                continue

            if source != "CLI":
                console.print(f"\n[cyan]User (via {source}):[/] {user_input}")

            if user_input.lower() in ("exit", "quit", "q"):
                console.print("[yellow]Exiting Jarvis.[/yellow]")
                break

            if hud_server is not None:
                hud_server.broadcast_state("thinking")

            reply = orchestrator.run_turn(user_input)

            if hud_server is not None:
                hud_server.broadcast_state("idle")

            composite_output.say(reply)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
    finally:
        stop_event.set()
        if hud_server is not None:
            hud_server.stop()
        if routines_manager is not None:
            routines_manager.shutdown()
        kill_switch.stop_hotkey_listener()
        if orchestrator.audit_logger is not None:
            orchestrator.audit_logger.log_event(
                event="session_shutdown",
                status="clean_shutdown",
            )
            final_hash = orchestrator.audit_logger.close()
            console.print(
                f"[dim]Audit chain closed cleanly. Final record_hash:[/] [bold cyan]{final_hash}[/]"
            )


def brain_command(args: argparse.Namespace) -> None:
    """Handler for 'jarvis brain' command group."""
    if args.brain_action == "models":
        try:
            config = load_config(args.config)
            base_url = config.brain.base_url if config and config.brain else "https://api.groq.com/openai/v1"
        except Exception:
            base_url = "https://api.groq.com/openai/v1"

        try:
            models = GroqBrain.list_available_models(base_url=base_url)
        except ValueError as exc:
            console.print(f"[bold red]Error:[/] {exc}", style="red")
            sys.exit(1)
        except Exception as exc:
            console.print(f"[bold red]Error fetching models:[/] {exc}", style="red")
            sys.exit(1)

        table = Table(title="Available Groq Models", show_header=True, header_style="bold cyan")
        table.add_column("Model ID", style="bold green")
        table.add_column("Owned By", style="dim")
        table.add_column("Context Window", justify="right")
        table.add_column("Tool Use Support", justify="center")
        table.add_column("Vision Support", justify="center")

        for m in models:
            ctx = str(m["context_window"]) if m.get("context_window") else "N/A"
            tool_use = "[green]Yes[/green]" if m.get("supports_tool_use") else "[red]No[/red]"
            vision = "[green]Yes[/green]" if m.get("supports_vision") else "[dim]No[/dim]"
            table.add_row(m["id"], str(m.get("owned_by", "groq")), ctx, tool_use, vision)

        console.print(table)
    else:
        console.print("[yellow]Please specify a brain action (e.g. 'jarvis brain models').[/yellow]")


def screen_command(args: argparse.Namespace) -> None:
    """Capture screen and ask Jarvis to inspect it with computer vision."""
    query = " ".join(args.query).strip() if args.query else "Jarvis, please analyze my current desktop screen and describe what you see."
    orchestrator = create_orchestrator(config_path=args.config, dry_run=args.dry_run)
    console.print(Panel(f"[bold cyan]Directive:[/] {query}", title="Desktop Screen Vision", border_style="cyan"))
    with console.status("[bold green]Inspecting desktop workspace...[/]"):
        response = orchestrator.run_turn(f"Please inspect my current desktop screen and answer this: {query}")
    console.print("\n[bold green]J.A.R.V.I.S. > [/bold green]")
    console.print(Markdown(response))


def widget_command(args: argparse.Namespace) -> None:
    """Launch floating translucent desktop widget dock."""
    config = load_config(args.config)
    kill_switch = get_kill_switch()
    user_title = getattr(config.persona, "user_title", "Sir") if hasattr(config, "persona") else "Sir"

    hud_server = HudServer(
        host=config.hud.host,
        port=config.hud.port,
        kill_switch=kill_switch,
        confirm_timeout_seconds=config.hud.confirm_timeout_seconds,
        user_title=user_title,
    )
    hud_server.start()

    console.print(Panel(
        f"[bold green]J.A.R.V.I.S. Floating Translucent Dock Online[/bold green]\n"
        f"Server: [bold cyan]{hud_server.url}[/bold cyan]\n"
        "[dim]Press Ctrl+C in terminal or click Kill button on dock to close.[/dim]",
        title="Floating Widget",
        border_style="green",
    ))
    try:
        launch_widget(hud_server.url)
    finally:
        hud_server.stop()


def autostart_command(args: argparse.Namespace) -> None:
    """Manage Windows startup automation and desktop shortcut."""
    action = args.autostart_action
    if action == "enable":
        sc = enable_autostart()
        console.print(f"[bold green]Windows autostart enabled:[/] {sc}")
    elif action == "disable":
        if disable_autostart():
            console.print("[bold yellow]Windows autostart disabled.[/bold yellow]")
        else:
            console.print("[dim]Autostart was not active.[/dim]")
    elif action == "status":
        active = is_autostart_enabled()
        status_text = "[bold green]Active (starts on Windows login)[/bold green]" if active else "[yellow]Inactive[/yellow]"
        console.print(f"Autostart Status: {status_text}")
    elif action == "shortcut":
        sc = create_desktop_shortcut()
        console.print(f"[bold green]Desktop shortcut created:[/] {sc}")
    else:
        console.print("[yellow]Please specify action: enable, disable, status, shortcut[/yellow]")


def main() -> None:
    """Main CLI entry point for 'jarvis' command."""
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Jarvis - Local AI Desktop Agent",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # 'jarvis run' (multi-channel: CLI + HUD + Voice)
    run_parser = subparsers.add_parser("run", help="Start multi-channel Jarvis session (CLI, HUD, Voice)")
    run_parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to configuration YAML file (default: config.yaml)",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution without modifying system state",
    )
    run_parser.add_argument(
        "--voice",
        action="store_true",
        help="Enable voice input and output channel (active by default)",
    )
    run_parser.add_argument(
        "--no-voice",
        action="store_true",
        help="Disable voice synthesis and microphone input (runs silently)",
    )
    run_parser.add_argument(
        "--no-hud",
        action="store_true",
        help="Disable local web HUD server",
    )

    # 'jarvis chat'
    chat_parser = subparsers.add_parser("chat", help="Start chat session with Jarvis")
    chat_parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to configuration YAML file (default: config.yaml)",
    )
    chat_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution: CONFIRM tools describe actions without performing them",
    )
    chat_parser.add_argument(
        "query",
        nargs="*",
        help="Optional single query to execute non-interactively",
    )

    # 'jarvis voice'
    voice_parser = subparsers.add_parser("voice", help="Start voice session with Jarvis")
    voice_parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to configuration YAML file (default: config.yaml)",
    )
    voice_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution: CONFIRM tools describe actions without performing them",
    )
    voice_parser.add_argument(
        "--ptt",
        action="store_true",
        help="Push-to-talk mode (hold key to speak, default)",
    )
    voice_parser.add_argument(
        "--wake",
        action="store_true",
        help="Wake word detection mode",
    )
    voice_parser.add_argument(
        "voice_action",
        nargs="?",
        choices=["setup-piper", "test"],
        help="Voice action ('setup-piper' to download neural model, 'test' to speak greeting)",
    )

    # 'jarvis screen'
    screen_parser = subparsers.add_parser("screen", help="Capture desktop screen and analyze with Claude Vision")
    screen_parser.add_argument("--config", "-c", default="config.yaml")
    screen_parser.add_argument("--dry-run", action="store_true")
    screen_parser.add_argument("query", nargs="*", help="Question or directive about the screen")

    # 'jarvis widget'
    widget_parser = subparsers.add_parser("widget", help="Launch floating translucent Arc Reactor desktop dock")
    widget_parser.add_argument("--config", "-c", default="config.yaml")

    # 'jarvis autostart'
    autostart_parser = subparsers.add_parser("autostart", help="Manage Windows startup and desktop shortcuts")
    autostart_parser.add_argument(
        "autostart_action",
        choices=["enable", "disable", "status", "shortcut"],
        help="Autostart action: enable, disable, status, shortcut",
    )

    # 'jarvis memory'
    memory_parser = subparsers.add_parser("memory", help="Inspect and manage persistent memories")
    memory_parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to configuration YAML file (default: config.yaml)",
    )
    memory_sub = memory_parser.add_subparsers(dest="memory_action", help="Memory actions")

    # 'jarvis memory list'
    memory_sub.add_parser("list", help="List all stored memories")

    # 'jarvis memory delete'
    mem_del_parser = memory_sub.add_parser("delete", help="Delete memories")
    mem_del_parser.add_argument("--id", type=int, help="Delete specific memory by ID")
    mem_del_parser.add_argument("--all", action="store_true", help="Delete all stored memories")

    # 'jarvis memory export'
    mem_exp_parser = memory_sub.add_parser("export", help="Export memories and conversations to JSON")
    mem_exp_parser.add_argument("--output", "-o", help="File path to save JSON export")

    # 'jarvis audit'
    audit_parser = subparsers.add_parser("audit", help="Verify and manage cryptographic audit log")
    audit_parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to configuration YAML file (default: config.yaml)",
    )
    audit_sub = audit_parser.add_subparsers(dest="audit_action", help="Audit actions")
    audit_verify_parser = audit_sub.add_parser("verify", help="Verify cryptographic SHA-256 chain of audit log")
    audit_verify_parser.add_argument("--log", "-l", help="Path to audit log file (default from config)")

    # 'jarvis brain'
    brain_parser = subparsers.add_parser("brain", help="Inspect and manage brain models and providers")
    brain_parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to configuration YAML file (default: config.yaml)",
    )
    brain_sub = brain_parser.add_subparsers(dest="brain_action", help="Brain actions")
    brain_sub.add_parser("models", help="List available models and tool/vision capabilities from Groq endpoint")

    args = parser.parse_args()

    if args.command == "run":
        run_command(args)
    elif args.command == "chat":
        chat_command(args)
    elif args.command == "voice":
        voice_command(args)
    elif args.command == "screen":
        screen_command(args)
    elif args.command == "widget":
        widget_command(args)
    elif args.command == "autostart":
        autostart_command(args)
    elif args.command == "memory":
        memory_command(args)
    elif args.command == "audit":
        audit_command(args)
    elif args.command == "brain":
        brain_command(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
