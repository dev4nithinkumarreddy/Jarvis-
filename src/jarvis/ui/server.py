"""FastAPI Local Web HUD server and WebSocket communication channel."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import queue
import secrets
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import psutil
import uvicorn

from jarvis.core.channels import Confirmer, InputChannel, OutputChannel
from jarvis.core.config import JarvisConfig
from jarvis.memory.store import redact_sensitive_text
from jarvis.safety.killswitch import KillSwitch, get_kill_switch

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def is_allowed_origin(origin: str | None, host: str, port: int) -> bool:
    """Verify that Origin header matches local server instance.

    Rejects requests whose Origin header is not the local server.
    """
    if not origin:
        return True

    allowed = {
        f"http://{host}:{port}",
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
        f"https://{host}:{port}",
        f"https://localhost:{port}",
        f"https://127.0.0.1:{port}",
    }
    return origin.rstrip("/") in allowed


def is_allowed_host(host_header: str | None, port: int, allow_testserver: bool = False) -> bool:
    """Verify that Host header matches local server instance.

    Accepts only 127.0.0.1:<port>, localhost:<port>, 127.0.0.1, or localhost.
    Synthetic testserver is permitted only when allow_testserver=True (test-only fixture or flag).
    """
    if not host_header:
        return False
    h = host_header.strip().lower()
    allowed = {
        f"127.0.0.1:{port}",
        f"localhost:{port}",
        "127.0.0.1",
        "localhost",
    }
    if allow_testserver:
        allowed.add("testserver")
        allowed.add(f"testserver:{port}")
    return h in allowed



class HudChannel(InputChannel, OutputChannel, Confirmer):
    """HUD communication channel implementing Input, Output, and Confirmation."""

    def __init__(self, server: HudServer, timeout_seconds: float = 30.0) -> None:
        self.server = server
        self.timeout_seconds = timeout_seconds
        self.input_queue: queue.Queue[str] = queue.Queue()
        self._pending_confirmations: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get_user_input(self, prompt: str = "") -> str:
        """Wait for user input sent from the HUD."""
        self.server.broadcast_state("listening")
        try:
            return self.input_queue.get()
        finally:
            self.server.broadcast_state("thinking")

    def say(self, message: str) -> None:
        """Deliver assistant message to HUD transcript."""
        # Redact secrets before sending to HUD
        safe_msg = redact_sensitive_text(message)
        self.server.broadcast({
            "type": "chat",
            "role": "assistant",
            "content": safe_msg,
        })

    def confirm(self, action_description: str, warning: str | None = None) -> bool:
        """Prompt user via HUD modal dialog for confirmation."""
        confirm_id = secrets.token_hex(8)
        event = threading.Event()

        safe_action = redact_sensitive_text(action_description)
        safe_warning = redact_sensitive_text(warning) if warning else None

        with self._lock:
            self._pending_confirmations[confirm_id] = {
                "event": event,
                "approved": False,
            }

        self.server.broadcast({
            "type": "confirm_request",
            "id": confirm_id,
            "action": safe_action,
            "warning": safe_warning,
            "timeout": int(self.timeout_seconds),
        })

        is_done = event.wait(timeout=self.timeout_seconds)

        with self._lock:
            info = self._pending_confirmations.pop(confirm_id, None)

        # Notify HUD to dismiss modal
        self.dismiss(confirm_id)

        if not is_done or info is None:
            return False

        return bool(info.get("approved", False))

    def resolve_confirmation(self, confirm_id: str, approved: bool) -> bool:
        """Resolve a pending confirmation by ID."""
        with self._lock:
            info = self._pending_confirmations.get(confirm_id)
            if info is not None:
                info["approved"] = approved
                info["event"].set()
                return True
        return False

    def dismiss(self, confirm_id: str | None = None) -> None:
        """Dismiss active confirmation modal on the HUD."""
        if confirm_id is not None:
            with self._lock:
                info = self._pending_confirmations.pop(confirm_id, None)
                if info is not None:
                    info["event"].set()
        else:
            with self._lock:
                for cid, info in list(self._pending_confirmations.items()):
                    info["event"].set()
                self._pending_confirmations.clear()

        self.server.broadcast({
            "type": "confirm_dismissed",
            "id": confirm_id,
        })

    def broadcast_audit(self, record: dict[str, Any]) -> None:
        """Broadcast audit record to HUD feed with secrets strictly redacted."""
        safe_record: dict[str, Any] = {}
        for k, v in record.items():
            if isinstance(v, str):
                safe_record[k] = redact_sensitive_text(v)
            elif isinstance(v, dict):
                safe_record[k] = {
                    dk: redact_sensitive_text(str(dv)) if isinstance(dv, str) else dv
                    for dk, dv in v.items()
                }
            else:
                safe_record[k] = v

        self.server.broadcast({
            "type": "audit",
            "record": safe_record,
        })

    def broadcast_state(self, state: str) -> None:
        """Broadcast state indicator change to HUD."""
        self.server.broadcast_state(state)


class HudServer:
    """FastAPI-based Local Web HUD server bound to 127.0.0.1."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        kill_switch: KillSwitch | None = None,
        session_token: str | None = None,
        confirm_timeout_seconds: float = 30.0,
        allow_testserver: bool = False,
        auth_timeout_seconds: float = 5.0,
        user_title: str = "Sir",
    ) -> None:
        # Enforce 127.0.0.1 binding only
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError(f"HUD must be bound to 127.0.0.1 only for local security, got '{host}'")

        self.host = "127.0.0.1"
        self.port = port
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()
        self.session_token = session_token or secrets.token_urlsafe(32)
        self.channel = HudChannel(self, timeout_seconds=confirm_timeout_seconds)
        self.allow_testserver = allow_testserver
        self.auth_timeout_seconds = auth_timeout_seconds
        self.user_title = user_title

        self._clients: set[WebSocket] = set()
        self._clients_lock = threading.Lock()
        self._running = False
        self._uvicorn_server: uvicorn.Server | None = None
        self._telemetry_thread: threading.Thread | None = None
        self._server_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self.app = self._build_fastapi_app()

    @property
    def url(self) -> str:
        """Full HUD access URL with session token in fragment."""
        return f"http://{self.host}:{self.port}/#token={self.session_token}"

    def _verify_token(self, request: Request) -> bool:
        """Validate session token from X-Session-Token header only.

        Query parameter tokens are strictly prohibited.
        """
        token = request.headers.get("X-Session-Token")
        return bool(token and token == self.session_token)

    def _build_fastapi_app(self) -> FastAPI:
        """Create and configure FastAPI application."""
        app = FastAPI(title="Jarvis HUD", docs_url=None, redoc_url=None)

        # 1. Security middleware: Host verification, Origin check, Query token rejection, CSP
        @app.middleware("http")
        async def security_middleware(request: Request, call_next: Any) -> Response:
            # Validate Host header
            host_header = request.headers.get("host")
            if not is_allowed_host(host_header, self.port, allow_testserver=self.allow_testserver):
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "Bad Request: Untrusted Host"},
                )

            # Validate Origin header if present
            origin = request.headers.get("origin")
            if not is_allowed_origin(origin, self.host, self.port):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Forbidden: Untrusted Origin"},
                )

            # Reject any token passed in a query string
            if request.query_params.get("token") is not None:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "Bad Request: Token in query string is prohibited"},
                )

            response = await call_next(request)

            # Add Content-Security-Policy header
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "connect-src 'self' ws://127.0.0.1:* ws://localhost:* http://127.0.0.1:* http://localhost:*; "
                "img-src 'self' data:; "
                "object-src 'none'; "
                "frame-ancestors 'none'; "
                "base-uri 'self';"
            )
            return response

        # 2. Static files
        if STATIC_DIR.exists():
            app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        # 3. Root HUD page
        @app.get("/", response_class=HTMLResponse)
        async def get_index(request: Request) -> Response:
            index_file = STATIC_DIR / "index.html"
            if index_file.exists():
                return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
            return HTMLResponse(content="<h1>Jarvis HUD (Static files not found)</h1>")

        # 4. WebSocket endpoint
        @app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket) -> None:
            # Check Host
            host_header = websocket.headers.get("host")
            if not is_allowed_host(host_header, self.port, allow_testserver=self.allow_testserver):
                await websocket.close(code=1008, reason="Forbidden: Untrusted Host")
                return

            # Check Origin
            origin = websocket.headers.get("origin")
            if not is_allowed_origin(origin, self.host, self.port):
                await websocket.close(code=1008, reason="Forbidden: Untrusted Origin")
                return

            # Reject token in query string
            if websocket.query_params.get("token") is not None:
                await websocket.close(code=1008, reason="Policy Violation: Token in query string is prohibited")
                return

            await websocket.accept()
            self._loop = asyncio.get_running_loop()

            # Wait for authentication in the first WebSocket message
            try:
                first_msg_raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=self.auth_timeout_seconds,
                )
                first_msg = json.loads(first_msg_raw)
                client_token = first_msg.get("token")
                if not client_token or client_token != self.session_token:
                    await websocket.close(code=1008, reason="Unauthorized: Invalid session token")
                    return
            except Exception:
                await websocket.close(code=1008, reason="Unauthorized: Missing authentication")
                return

            with self._clients_lock:
                self._clients.add(websocket)

            # Send initial state
            try:
                state_str = "halted" if self.kill_switch.is_set() else "idle"
                await websocket.send_json({"type": "state", "state": state_str, "user_title": self.user_title})
                # Send initial stats
                stats = self._gather_system_stats()
                await websocket.send_json({"type": "stats", **stats})

                while True:
                    text = await websocket.receive_text()
                    try:
                        data = json.loads(text)
                        msg_type = data.get("type")
                        if msg_type == "chat":
                            user_text = str(data.get("text", "")).strip()
                            if user_text:
                                self.channel.input_queue.put(user_text)
                        elif msg_type == "confirm_response":
                            cid = str(data.get("id", ""))
                            appr = bool(data.get("approved", False))
                            self.channel.resolve_confirmation(cid, appr)
                        elif msg_type == "kill":
                            self.kill_switch.trigger()
                            self.broadcast_state("halted")
                            self.broadcast({"type": "kill_state", "active": True})
                    except Exception as err:
                        logger.debug("Error processing HUD message: %s", err)
            except (WebSocketDisconnect, Exception):
                pass
            finally:
                with self._clients_lock:
                    self._clients.discard(websocket)

        # 5. REST API routes
        @app.post("/api/chat")
        async def api_chat(request: Request) -> JSONResponse:
            if not self._verify_token(request):
                raise HTTPException(status_code=401, detail="Unauthorized")
            body = await request.json()
            text = str(body.get("text", "")).strip()
            if not text:
                raise HTTPException(status_code=400, detail="Text cannot be empty")
            self.channel.input_queue.put(text)
            return JSONResponse({"status": "received", "text": text})

        @app.post("/api/confirm")
        async def api_confirm(request: Request) -> JSONResponse:
            if not self._verify_token(request):
                raise HTTPException(status_code=401, detail="Unauthorized")
            body = await request.json()
            cid = str(body.get("id", ""))
            approved = bool(body.get("approved", False))
            success = self.channel.resolve_confirmation(cid, approved)
            return JSONResponse({"status": "resolved" if success else "not_found", "id": cid})

        @app.post("/api/kill")
        async def api_kill(request: Request) -> JSONResponse:
            if not self._verify_token(request):
                raise HTTPException(status_code=401, detail="Unauthorized")
            self.kill_switch.trigger()
            self.broadcast_state("halted")
            self.broadcast({"type": "kill_state", "active": True})
            return JSONResponse({"status": "killed"})

        @app.get("/api/status")
        async def api_status(request: Request) -> JSONResponse:
            if not self._verify_token(request):
                raise HTTPException(status_code=401, detail="Unauthorized")
            return JSONResponse({
                "kill_switch": self.kill_switch.is_set(),
                "token_valid": True,
            })

        return app

    def _gather_system_stats(self) -> dict[str, Any]:
        """Collect current system telemetry."""
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").free // (1024 * 1024 * 1024)
            return {"cpu": cpu, "ram": ram, "disk": disk}
        except Exception:
            return {"cpu": 0, "ram": 0, "disk": 0}

    def broadcast(self, message: dict[str, Any]) -> None:
        """Broadcast a message payload to all connected WebSocket clients."""
        text = json.dumps(message, ensure_ascii=False)
        with self._clients_lock:
            clients_snapshot = list(self._clients)

        for client in clients_snapshot:
            try:
                # Use loop if available, else create task or send_text
                if self._loop is not None and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(client.send_text(text), self._loop)
            except Exception:
                pass

    def broadcast_state(self, state: str) -> None:
        """Broadcast state indicator."""
        self.broadcast({"type": "state", "state": state, "user_title": self.user_title})

    def _telemetry_loop(self) -> None:
        """Background thread broadcasting telemetry stats every 2 seconds."""
        while self._running:
            stats = self._gather_system_stats()
            self.broadcast({"type": "stats", **stats})
            time.sleep(2.0)

    def start(self) -> None:
        """Start Uvicorn server in a background daemon thread."""
        if self._running:
            return

        self._running = True
        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._uvicorn_server = uvicorn.Server(config)

        def run_server() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._uvicorn_server.serve())

        self._server_thread = threading.Thread(target=run_server, daemon=True)
        self._server_thread.start()

        # Start telemetry loop
        self._telemetry_thread = threading.Thread(target=self._telemetry_loop, daemon=True)
        self._telemetry_thread.start()

        logging.getLogger("uvicorn.access").disabled = True
        logger.info("Jarvis HUD server started on http://%s:%s/", self.host, self.port)

    def stop(self) -> None:
        """Stop HUD server and close connections."""
        self._running = False
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        with self._clients_lock:
            self._clients.clear()
