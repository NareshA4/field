"""
Playwright fixtures and configuration for FieldOps Nexus QA Acceptance Suite.
"""
import os
import pytest
from pathlib import Path
from playwright.sync_api import sync_playwright, Page, BrowserContext

BASE_URL = os.environ.get("SITE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent.parent / "qa" / "artifacts" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def playwright_instance():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance):
    browser = playwright_instance.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"]
    )
    yield browser
    browser.close()


@pytest.fixture
def context(browser: BrowserContext):
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 900},
        ignore_https_errors=True,
    )
    yield ctx
    ctx.close()


@pytest.fixture
def page(context: BrowserContext, request):
    pg = context.new_page()
    console_errors = []
    failed_requests = []

    def on_console(msg):
        if msg.type in ("error",):
            console_errors.append(f"[{msg.type}] {msg.text}")

    def on_request_failed(req):
        # Ignore intentional aborts or expected test cases
        failed_requests.append(f"{req.method} {req.url}: {req.failure}")

    pg.on("console", on_console)
    pg.on("requestfailed", on_request_failed)

    # Attach tracking lists to page
    pg.console_errors = console_errors
    pg.failed_requests = failed_requests

    yield pg

    # On test failure, capture full-page screenshot
    if hasattr(request.node, "rep_call") and request.node.rep_call.failed:
        test_name = request.node.name.replace("/", "_").replace("\\", "_")
        screenshot_path = SCREENSHOT_DIR / f"{test_name}_failed.png"
        try:
            pg.screenshot(path=str(screenshot_path), full_page=True)
        except Exception:
            pass

    pg.close()


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    # Hook to record call status on node for fixture access
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


@pytest.fixture
def login_as(base_url):
    def _login(page: Page, email: str = "admin@apexindustrial.com", password: str = "Password123!"):
        page.context.clear_cookies()
        page.goto(f"{base_url}/accounts/login/")
        page.fill("input[name='username']", email)
        page.fill("input[name='password']", password)
        page.click("button:has-text('Sign in')")
        page.wait_for_load_state("networkidle")
        # Ensure session was created and redirected away from login
        assert "/accounts/login" not in page.url, f"Login failed for {email}; still on {page.url}"
        return page
    return _login
