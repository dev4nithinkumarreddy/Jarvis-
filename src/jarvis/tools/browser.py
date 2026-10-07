"""Browser automation tools powered by Playwright with strict safety policies."""

from __future__ import annotations

import atexit
from datetime import datetime
import logging
from pathlib import Path
import re
import threading
from typing import Any
import urllib.parse
from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from jarvis.core.config import BrowserConfig, JarvisConfig
from jarvis.core.types import RiskTier, ToolSpec
from jarvis.safety.killswitch import get_kill_switch

logger = logging.getLogger(__name__)


# Blocked file extensions indicating download triggers
DOWNLOAD_EXTENSIONS = {
    ".exe", ".msi", ".zip", ".tar", ".gz", ".7z", ".rar",
    ".dmg", ".pkg", ".deb", ".rpm", ".iso", ".bin", ".apk",
    ".bat", ".cmd", ".ps1", ".sh",
}

# Blocked keywords indicating purchase, payment, or checkout triggers
PAYMENT_KEYWORDS = {
    "pay", "payment", "buy", "purchase", "checkout", "order",
    "subscribe", "creditcard", "cc-number", "cvv", "cvc", "cardnumber",
}


def validate_url(
    url: str,
    allowed_domains: list[str] | None = None,
    denied_domains: list[str] | None = None,
) -> tuple[bool, str]:
    """Validate URL scheme and domain constraints.

    Rules:
    - Only 'http' and 'https' schemes are accepted. All other schemes (file://,
      javascript:, data:, etc.) are rejected.
    - Disallows domains on the denied_domains list.
    - If allowed_domains is provided and non-empty, requires the domain to match.

    Returns:
        tuple (is_valid, error_message)
    """
    cleaned_url = url.strip()
    if not cleaned_url:
        return False, "URL cannot be empty."

    try:
        parsed = urllib.parse.urlparse(cleaned_url)
    except Exception as exc:
        return False, f"Malformed URL '{cleaned_url}': {exc}"

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return False, (
            f"Invalid URL scheme '{parsed.scheme}'. Only http and https schemes are permitted. "
            "file://, javascript:, data: and other schemes are strictly rejected."
        )

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False, f"Invalid URL '{cleaned_url}': Missing hostname."

    # Denylist enforcement
    if denied_domains:
        for denied in denied_domains:
            d = denied.lower().strip()
            if hostname == d or hostname.endswith("." + d):
                return False, f"Domain '{hostname}' is blocked by browser safety denylist."

    # Allowlist enforcement
    if allowed_domains:
        matched = False
        for allowed in allowed_domains:
            a = allowed.lower().strip()
            if hostname == a or hostname.endswith("." + a):
                matched = True
                break
        if not matched:
            return False, f"Domain '{hostname}' is not in the browser safety allowlist."

    return True, ""


class BrowserSession:
    """Manages a single persistent Playwright browser context with lazy initialization."""

    def __init__(self, config: BrowserConfig | None = None) -> None:
        self.config = config if config is not None else BrowserConfig()
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._lock = threading.Lock()
        self._is_closed = False

        # Register shutdown handlers
        atexit.register(self.close)
        get_kill_switch().add_on_trigger(self.close)

    def _ensure_session(self) -> tuple[BrowserContext, Page]:
        """Lazy-initialize Playwright and persistent browser context."""
        with self._lock:
            if self._is_closed:
                raise RuntimeError("Browser session has been closed.")

            if self._context is not None and self._page is not None:
                return self._context, self._page

            logger.info("Initializing Playwright browser session...")
            self._playwright = sync_playwright().start()

            profile_path = Path(self.config.profile_dir).resolve()
            profile_path.mkdir(parents=True, exist_ok=True)

            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                headless=self.config.headless,
                accept_downloads=False,
            )

            if self._context.pages:
                self._page = self._context.pages[0]
            else:
                self._page = self._context.new_page()

            self._page.set_default_timeout(self.config.timeout_ms)

            # Prevent file downloads
            def _block_download(download: Any) -> None:
                logger.warning(f"Download attempted and cancelled: {download.url}")
                try:
                    download.cancel()
                except Exception:
                    pass

            self._page.on("download", _block_download)
            self._context.on("page", lambda p: p.on("download", _block_download))

            return self._context, self._page

    def get_page(self) -> Page:
        """Return the active browser page, initializing the session if needed."""
        _, page = self._ensure_session()
        return page

    def close(self) -> None:
        """Cleanly close browser context, active pages, and Playwright driver."""
        with self._lock:
            if self._is_closed:
                return
            self._is_closed = True

            logger.info("Closing Playwright browser session...")
            if self._context is not None:
                try:
                    self._context.close()
                except Exception as exc:
                    logger.debug(f"Error closing browser context: {exc}")
                self._context = None
                self._page = None

            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception as exc:
                    logger.debug(f"Error stopping playwright: {exc}")
                self._playwright = None


def check_is_password_field(page: Page, selector: str) -> bool:
    """Check if the target input field is a password input."""
    try:
        is_pwd = page.eval_on_selector(
            selector,
            """el => {
                if (!el) return false;
                const type = (el.getAttribute('type') || '').toLowerCase();
                if (type === 'password') return true;
                const auto = (el.getAttribute('autocomplete') || '').toLowerCase();
                if (auto.includes('password')) return true;
                const name = (el.getAttribute('name') || '').toLowerCase();
                const id = (el.getAttribute('id') || '').toLowerCase();
                const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                return name.includes('password') || id.includes('password') ||
                       name.includes('passwd') || id.includes('passwd') ||
                       name.includes('pwd') || id.includes('pwd') ||
                       aria.includes('password');
            }"""
        )
        return bool(is_pwd)
    except Exception:
        # Fallback inspection on selector string itself
        sel_lower = selector.lower()
        return "password" in sel_lower or "passwd" in sel_lower or "pwd" in sel_lower


def check_is_payment_or_purchase(page: Page, selector: str) -> bool:
    """Check if the target element or its enclosing form triggers a purchase or payment."""
    try:
        is_pay = page.eval_on_selector(
            selector,
            """el => {
                if (!el) return false;
                const text = (el.innerText || el.textContent || el.value || '').toLowerCase();
                const keywords = ['pay', 'payment', 'buy', 'purchase', 'checkout', 'place order', 'subscribe'];
                for (const kw of keywords) {
                    if (text.includes(kw)) return true;
                }
                const attrs = [el.id, el.name, el.className, el.getAttribute('aria-label') || ''].join(' ').toLowerCase();
                for (const kw of keywords) {
                    if (attrs.includes(kw)) return true;
                }
                // Check if element is inside a form with credit card / payment inputs
                const form = el.closest('form');
                if (form) {
                    const inputs = Array.from(form.querySelectorAll('input'));
                    for (const inp of inputs) {
                        const iAttr = [inp.name, inp.id, inp.getAttribute('autocomplete') || ''].join(' ').toLowerCase();
                        if (iAttr.includes('cc-number') || iAttr.includes('cvv') || iAttr.includes('creditcard') || iAttr.includes('cardnumber')) {
                            return true;
                        }
                    }
                }
                return false;
            }"""
        )
        return bool(is_pay)
    except Exception:
        sel_lower = selector.lower()
        return any(k in sel_lower for k in ("pay", "buy", "purchase", "checkout", "cvv", "creditcard"))


def check_is_download_trigger(page: Page, selector: str) -> bool:
    """Check if the element triggers a file download."""
    try:
        is_dl = page.eval_on_selector(
            selector,
            """el => {
                if (!el) return false;
                if (el.hasAttribute('download')) return true;
                const href = (el.getAttribute('href') || '').toLowerCase();
                const extensions = ['.exe', '.msi', '.zip', '.tar', '.gz', '.dmg', '.pkg', '.iso', '.bin', '.bat', '.cmd', '.sh'];
                for (const ext of extensions) {
                    if (href.endsWith(ext) || href.includes(ext + '?')) return true;
                }
                return false;
            }"""
        )
        return bool(is_dl)
    except Exception:
        sel_lower = selector.lower()
        return any(ext in sel_lower for ext in DOWNLOAD_EXTENSIONS)


# =============================================================================
# Tool Handlers
# =============================================================================

def handle_open_url(session: BrowserSession, args: dict[str, Any]) -> str:
    """Navigate browser to an HTTP/HTTPS URL."""
    url = str(args.get("url", "")).strip()
    is_valid, err = validate_url(
        url,
        allowed_domains=session.config.allowed_domains,
        denied_domains=session.config.denied_domains,
    )
    if not is_valid:
        raise ValueError(f"Browser navigation rejected: {err}")

    page = session.get_page()
    page.goto(url, wait_until="load")
    return f"Navigated to '{page.url}'. Title: '{page.title()}'"


def handle_get_page_text(session: BrowserSession, args: dict[str, Any]) -> str:
    """Extract visible page text, truncated to a safe 20 KB limit."""
    page = session.get_page()
    try:
        text = page.locator("body").inner_text()
    except Exception:
        text = page.content()

    # Truncate to 20 KB (20,480 characters)
    max_chars = 20480
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n... [Output truncated to 20 KB limit]"

    return text.strip()


def handle_list_links(session: BrowserSession, args: dict[str, Any]) -> list[dict[str, str]]:
    """List links (href and text) present on the active browser page."""
    page = session.get_page()
    links: list[dict[str, str]] = page.evaluate(
        """() => {
            const anchors = Array.from(document.querySelectorAll('a[href]'));
            return anchors.slice(0, 100).map(a => ({
                text: (a.innerText || a.textContent || '').trim().replace(/\\s+/g, ' '),
                href: a.href
            })).filter(item => item.href);
        }"""
    )
    return links


def handle_click_element(session: BrowserSession, args: dict[str, Any]) -> str:
    """Click an element on the active page, enforcing safety blocks on purchases and downloads."""
    selector = str(args.get("selector") or args.get("description", "")).strip()
    if not selector:
        raise ValueError("Selector or description is required for click_element.")

    page = session.get_page()

    # Safety checks
    if check_is_payment_or_purchase(page, selector):
        raise PermissionError(
            "BLOCKED: Purchases, payments, and checkout actions are strictly prohibited by safety policy."
        )

    if check_is_download_trigger(page, selector):
        raise PermissionError(
            "BLOCKED: File downloads via browser are strictly prohibited by safety policy."
        )

    locator = page.locator(selector).first
    locator.click()
    return f"Successfully clicked element '{selector}'."


def handle_type_into(session: BrowserSession, args: dict[str, Any]) -> str:
    """Type text into an input field, strictly refusing password and payment fields."""
    selector = str(args.get("selector", "")).strip()
    text = str(args.get("text", ""))
    if not selector:
        raise ValueError("Selector is required for type_into.")

    page = session.get_page()

    if check_is_password_field(page, selector):
        raise PermissionError(
            "BLOCKED: Typing into password fields is strictly prohibited by safety policy."
        )

    if check_is_payment_or_purchase(page, selector):
        raise PermissionError(
            "BLOCKED: Entering payment or credit card details is strictly prohibited by safety policy."
        )

    locator = page.locator(selector).first
    locator.fill(text)
    return f"Successfully typed into element '{selector}'."


def handle_screenshot_page(session: BrowserSession, args: dict[str, Any]) -> str:
    """Take a screenshot of the current page and save under logs/screens/."""
    page = session.get_page()
    name = str(args.get("name", "")).strip()

    screenshot_dir = Path(session.config.screenshot_dir).resolve()
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    if name:
        safe_name = re.sub(r"[^\w\-.]", "_", name)
        if not safe_name.endswith(".png"):
            safe_name += ".png"
        filepath = screenshot_dir / safe_name
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = screenshot_dir / f"screenshot_{timestamp}.png"

    page.screenshot(path=str(filepath))
    return f"Screenshot saved to '{filepath}'."


# =============================================================================
# Tool Factory
# =============================================================================

def create_browser_tools(
    config: JarvisConfig,
    session: BrowserSession | None = None,
) -> list[ToolSpec]:
    """Create all 6 browser tools wired to a shared BrowserSession instance."""
    active_session = session if session is not None else BrowserSession(config.browser)

    def open_url(url: str) -> str:
        return handle_open_url(active_session, {"url": url})

    def get_page_text() -> str:
        return handle_get_page_text(active_session, {})

    def list_links() -> list[dict[str, str]]:
        return handle_list_links(active_session, {})

    def click_element(selector: str = "", description: str = "") -> str:
        return handle_click_element(active_session, {"selector": selector, "description": description})

    def type_into(selector: str, text: str) -> str:
        return handle_type_into(active_session, {"selector": selector, "text": text})

    def screenshot_page(name: str = "") -> str:
        return handle_screenshot_page(active_session, {"name": name})

    return [
        ToolSpec(
            name="open_url",
            description=(
                "Navigate the browser to an HTTP/HTTPS URL. Rejects non-HTTP schemes and denylisted domains."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The HTTP or HTTPS URL to open.",
                    },
                },
                "required": ["url"],
            },
            risk_tier=RiskTier.CONFIRM,
            handler=open_url,
            untrusted_output=False,
        ),
        ToolSpec(
            name="get_page_text",
            description=(
                "Extract visible text content from the current browser page. "
                "Output is strictly untrusted external data and truncated to 20 KB."
            ),
            input_schema={"type": "object", "properties": {}},
            risk_tier=RiskTier.AUTO,
            handler=get_page_text,
            untrusted_output=True,
        ),
        ToolSpec(
            name="list_links",
            description=(
                "Extract all clickable links (anchor text and target URL) on the current browser page. "
                "Output is strictly untrusted external data."
            ),
            input_schema={"type": "object", "properties": {}},
            risk_tier=RiskTier.AUTO,
            handler=list_links,
            untrusted_output=True,
        ),
        ToolSpec(
            name="click_element",
            description=(
                "Click a clickable element on the current page. "
                "Purchases, payments, and file downloads are strictly blocked."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector or text descriptor of the element to click.",
                    },
                },
                "required": ["selector"],
            },
            risk_tier=RiskTier.CONFIRM,
            handler=click_element,
            untrusted_output=False,
        ),
        ToolSpec(
            name="type_into",
            description=(
                "Type text into an input field on the current page. "
                "Typing into password fields or payment inputs is strictly blocked."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector of the input field.",
                    },
                    "text": {
                        "type": "string",
                        "description": "The text to fill into the input field.",
                    },
                },
                "required": ["selector", "text"],
            },
            risk_tier=RiskTier.CONFIRM,
            handler=type_into,
            untrusted_output=False,
        ),
        ToolSpec(
            name="screenshot_page",
            description="Capture a screenshot of the current browser page and save under logs/screens/.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional custom filename for the screenshot (default: timestamped).",
                    },
                },
            },
            risk_tier=RiskTier.AUTO,
            handler=screenshot_page,
            untrusted_output=False,
        ),
    ]
