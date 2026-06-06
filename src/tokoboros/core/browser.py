"""Headless browser automation backend (Playwright).

Tokoboros tools are synchronous functions, but the gateway invokes them from
inside an asyncio event loop. Playwright's *sync* API refuses to run inside a
running event loop, so this module owns the browser on a dedicated worker
thread and dispatches commands to it over a queue. That thread has no event
loop, so the sync API is happy, and — crucially — the same thread keeps the
``Browser``/``Page`` alive across calls, so the agent can navigate, then click,
then read on the *same* page (Playwright objects are not portable across
threads).

Playwright is an OPTIONAL dependency (``pip install 'tokoboros[browser]'`` plus
``playwright install chromium``). If it is missing — or the browser binary is
not installed — every command fails with a clear :class:`BrowserUnavailable`
carrying the install hint, instead of crashing the agent. This means the entire
control path (thread, queue, error propagation, friendly messaging) is
exercisable in tests without a real browser present.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any

INSTALL_HINT = (
    "Browser automation needs Playwright. Install it once with:\n"
    "  pip install 'tokoboros[browser]'\n"
    "  playwright install chromium"
)

# Sane caps so a single page read can't flood the agent's context window. The cap
# is deliberately tight (the model is told when text is truncated and can re-read a
# specific selector or page further), keeping per-call tokens down on big pages.
DEFAULT_NAV_TIMEOUT_MS = 30_000
DEFAULT_MAX_CHARS = 5_000
# Headroom over the navigation timeout so a slow page load doesn't look like a hang.
_DISPATCH_TIMEOUT_BUFFER_S = 15.0


class BrowserUnavailable(RuntimeError):
    """Raised when Playwright or its browser binary is not installed/launchable."""


@dataclass
class _Command:
    op: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    reply: queue.Queue[tuple[bool, Any]] = field(default_factory=lambda: queue.Queue(maxsize=1))


class BrowserController:
    """A persistent headless-browser session driven from a dedicated thread.

    Thread-safe: public methods may be called from any thread (including an
    asyncio worker). Each call blocks until the worker thread replies.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        nav_timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    ) -> None:
        self._headless = headless
        self._nav_timeout = nav_timeout_ms
        self._cmd: queue.Queue[_Command | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- public API ---------------------------------------------------------

    def navigate(self, url: str) -> dict[str, Any]:
        """Load ``url`` and return ``{url, title}`` of the resulting page."""
        return self._dispatch("navigate", url=url)

    def read(
        self, selector: str | None = None, max_chars: int = DEFAULT_MAX_CHARS
    ) -> dict[str, Any]:
        """Return the visible text of the page (or a CSS ``selector`` subtree)."""
        return self._dispatch("read", selector=selector, max_chars=max_chars)

    def act(self, action: str, selector: str, value: str = "") -> dict[str, Any]:
        """Perform ``click``/``fill``/``press``/``select`` on ``selector``."""
        return self._dispatch("act", action=action, selector=selector, value=value)

    def screenshot(self, path: str, full_page: bool = False) -> dict[str, Any]:
        """Save a PNG screenshot of the current page to ``path``."""
        return self._dispatch("screenshot", path=path, full_page=full_page)

    def close(self) -> None:
        """Shut down the worker thread and browser (idempotent)."""
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._thread = None
                return
            self._cmd.put(None)  # sentinel
        thread.join(timeout=10.0)
        with self._lock:
            self._thread = None

    # -- internals ----------------------------------------------------------

    def _ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            thread = threading.Thread(target=self._run, name="tokoboros-browser", daemon=True)
            thread.start()
            self._thread = thread

    def _dispatch(self, op: str, **kwargs: Any) -> dict[str, Any]:
        self._ensure_started()
        cmd = _Command(op=op, kwargs=kwargs)
        self._cmd.put(cmd)
        timeout = self._nav_timeout / 1000.0 + _DISPATCH_TIMEOUT_BUFFER_S
        try:
            ok, payload = cmd.reply.get(timeout=timeout)
        except queue.Empty as exc:
            raise BrowserUnavailable(
                f"Browser command '{op}' timed out after {timeout:.0f}s."
            ) from exc
        if not ok:
            raise payload  # the worker forwards the original exception instance
        return payload  # type: ignore[no-any-return]

    def _run(self) -> None:
        """Worker-thread main loop. Owns the Playwright lifecycle."""
        launch_error: BrowserUnavailable | None = None
        pw = None
        browser = None
        page = None
        try:
            from playwright.sync_api import sync_playwright

            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=self._headless)
            page = browser.new_page()
            page.set_default_timeout(self._nav_timeout)
        except ImportError:
            launch_error = BrowserUnavailable(INSTALL_HINT)
        except Exception as exc:  # browser binary missing, sandbox denied launch, etc.
            launch_error = BrowserUnavailable(
                f"Could not launch a browser: {exc}\nIf this is the first run: playwright install "
                "chromium"
            )

        while True:
            cmd = self._cmd.get()
            if cmd is None:  # shutdown sentinel
                break
            if launch_error is not None:
                cmd.reply.put((False, launch_error))
                continue
            try:
                cmd.reply.put((True, self._handle(page, cmd.op, cmd.kwargs)))
            except Exception as exc:  # surface per-command failures, keep the loop alive
                cmd.reply.put((False, exc))

        try:
            if browser is not None:
                browser.close()
            if pw is not None:
                pw.stop()
        except Exception:
            pass

    def _handle(self, page: Any, op: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Execute one command against the live page (worker thread only)."""
        if op == "navigate":
            page.goto(kwargs["url"], wait_until="domcontentloaded")
            return {"url": page.url, "title": page.title()}

        if op == "read":
            selector = kwargs.get("selector")
            max_chars = int(kwargs.get("max_chars") or DEFAULT_MAX_CHARS)
            target = page.locator(selector) if selector else page.locator("body")
            text = target.inner_text() if selector else page.inner_text("body")
            truncated = len(text) > max_chars
            return {
                "url": page.url,
                "title": page.title(),
                "text": text[:max_chars],
                "truncated": truncated,
            }

        if op == "act":
            action = kwargs["action"]
            selector = kwargs["selector"]
            value = kwargs.get("value", "")
            if action == "click":
                page.click(selector)
            elif action == "fill":
                page.fill(selector, value)
            elif action == "press":
                page.press(selector, value or "Enter")
            elif action == "select":
                page.select_option(selector, value)
            else:
                raise ValueError(f"Unknown action '{action}' (use click/fill/press/select).")
            return {"url": page.url, "title": page.title(), "action": action}

        if op == "screenshot":
            path = kwargs["path"]
            page.screenshot(path=path, full_page=bool(kwargs.get("full_page")))
            return {"path": path}

        raise ValueError(f"Unknown browser op '{op}'.")
