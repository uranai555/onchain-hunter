from __future__ import annotations

import re
import shutil
import time
from typing import Any

try:
    from playwright.sync_api import Browser, Error, Locator, Page, TimeoutError, sync_playwright
except ImportError:  # playwright is optional; scraper functions raise at call time
    Browser = Error = Locator = Page = TimeoutError = sync_playwright = None  # type: ignore[assignment,misc]


LEADERBOARD_URL = "https://app.hyperliquid.xyz/leaderboard"
FULL_ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
TRUNCATED_ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{4,10}\.\.\.[a-fA-F0-9]{4,10}")
RANK_RE = re.compile(r"^\s*#?\s*(\d{1,4})\b")
METRIC_RE = re.compile(r"^[+$-]?(?:\$)?[\d,.]+(?:\.\d+)?[KMBTkmbt]?(?:%|x)?$")
WINDOW_LABELS = {
    "7d": "7D",
    "30d": "30D",
    "90d": "90D",
}
BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
]
ROW_MARKER = "data-hl-leaderboard-row"


def _normalise_window(window: str) -> str:
    key = window.strip().lower()
    if key not in WINDOW_LABELS:
        raise ValueError(f"Unsupported leaderboard window: {window!r}")
    return key


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _visible_lines(text: str) -> list[str]:
    # Split by any whitespace (newlines, tabs, spaces) to get individual cells/values
    parts = re.split(r"\s+", text)
    return [_clean_text(p) for p in parts if _clean_text(p)]


def _extract_rank(text: str) -> int | None:
    match = RANK_RE.search(text)
    return int(match.group(1)) if match else None


def _extract_first_address(text: str) -> str | None:
    match = FULL_ADDRESS_RE.search(text)
    return match.group(0).lower() if match else None


def _has_truncated_address(text: str) -> bool:
    return bool(TRUNCATED_ADDRESS_RE.search(text))


def _parse_metric_columns(lines: list[str]) -> tuple[str, str, str, str]:
    metrics = [line for line in lines if METRIC_RE.match(line.replace(" ", ""))]
    tail = metrics[-4:]
    padded = [""] * (4 - len(tail)) + tail
    return tuple(padded)  # type: ignore[return-value]


def _parse_row_text(text: str, window: str) -> dict[str, Any] | None:
    rank = _extract_rank(text)
    if rank is None:
        return None

    lines = _visible_lines(text)
    if not lines:
        return None

    account_value, pnl, roi, volume = _parse_metric_columns(lines)
    visible_address = _extract_first_address(text)
    name = ""
    for line in lines:
        if _extract_rank(line) is not None:
            continue
        if METRIC_RE.match(line.replace(" ", "")):
            continue
        if FULL_ADDRESS_RE.search(line) or TRUNCATED_ADDRESS_RE.search(line):
            continue
        name = line
        break

    return {
        "rank": rank,
        "address": visible_address or "",
        "name": name,
        "account_value": account_value,
        "pnl": pnl,
        "roi": roi,
        "volume": volume,
        "window": window,
        "_needs_address_lookup": not visible_address and _has_truncated_address(text),
        "_raw_text": text,
    }


def _open_browser() -> tuple[Any, Browser]:  # type: ignore[type-arg]
    if sync_playwright is None:
        raise ImportError(
            "playwright is required for leaderboard scraping. "
            "Install it with: pip install playwright && playwright install chromium"
        )
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
        return playwright, browser
    except Exception as bundled_error:
        for binary in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
            executable_path = shutil.which(binary)
            if not executable_path:
                continue
            try:
                browser = playwright.chromium.launch(headless=True, executable_path=executable_path, args=BROWSER_ARGS)
                return playwright, browser
            except Exception:
                continue
        playwright.stop()
        raise bundled_error


def _wait_for_leaderboard(page: Page) -> None:
    page.wait_for_load_state("domcontentloaded", timeout=30_000)
    # Wait for actual leaderboard table data to load via WebSocket
    page.wait_for_function(
        "() => document.querySelectorAll('table tbody tr').length >= 5",
        timeout=30_000,
    )
    page.wait_for_timeout(2_000)

def _select_window(page: Page, window: str) -> None:
    from playwright.sync_api import Error as PwError
    from playwright.sync_api import TimeoutError as PwTimeoutError
    label = WINDOW_LABELS[window]

    # If already on the default window (30D), no action needed
    if window == "30d":
        page.wait_for_timeout(2_000)
        return

    # For non-default windows (7D, 90D): click the dropdown to open it,
    # then click the desired option.
    try:
        # Step 1: Open the dropdown by clicking the current label
        dds = page.get_by_text("30D", exact=True)
        count = dds.count()
        clicked = False
        for i in range(count):
            try:
                el = dds.nth(i)
                text = el.inner_text().strip()
                box = el.bounding_box()
                if text == "30D" and box and box["width"] < 60 and box["height"] < 50:
                    el.click(timeout=5_000)
                    clicked = True
                    break
            except (PwError, PwTimeoutError):
                continue
        if not clicked:
            dds.first.click(timeout=5_000)
        page.wait_for_timeout(1_000)

        # Step 2: Click the target option
        opt = page.get_by_text(label, exact=True).last
        opt.scroll_into_view_if_needed(timeout=3_000)
        opt.click(timeout=5_000)
        page.wait_for_load_state("networkidle", timeout=10_000)
        page.wait_for_timeout(3_000)
        return
    except (PwError, PwTimeoutError):
        pass

    raise RuntimeError(f"Could not switch Hyperliquid leaderboard to {label}")


def _row_locators(page: Page) -> list[Locator]:
    """Find all data rows in the leaderboard table.

    Uses the simple `table tbody tr` selector which reliably returns all rows.
    """
    page.wait_for_timeout(1_000)
    locators: list[Locator] = []
    for selector in ("table tbody tr", "[role='row']", f"[{ROW_MARKER}]"):
        try:
            rows = page.locator(selector)
            count = rows.count()
        except Error:
            continue
        for index in range(count):
            row = rows.nth(index)
            try:
                text = row.inner_text(timeout=1_000)
            except (Error, TimeoutError):
                continue
            if _parse_row_text(text, "30d") is not None:
                locators.append(row)
        if locators:
            break
    return locators


def _row_hrefs(row: Locator) -> list[str]:
    try:
        return row.evaluate(
            """(row) => {
            const values = [];
            const push = (value) => {
                if (value && typeof value === "string") values.push(value);
            };
            for (const el of [row, ...row.querySelectorAll("*")]) {
                push(el.href);
                for (const attr of el.getAttributeNames()) {
                    push(el.getAttribute(attr));
                }
            }
            return values;
        }"""
        )
    except Error:
        return []


def _address_from_row_navigation(page: Page, row: Locator) -> str | None:
    for href in _row_hrefs(row):
        address = _extract_first_address(href)
        if address:
            return address

    before_url = page.url
    try:
        link = row.locator("a").first
        if link.count():
            link.click(timeout=5_000)
        else:
            row.click(timeout=5_000)
        page.wait_for_timeout(1_200)
        address = _extract_first_address(page.url)
        if address:
            return address
    except (Error, TimeoutError):
        return None
    finally:
        if page.url != before_url:
            try:
                page.go_back(wait_until="domcontentloaded", timeout=15_000)
                # Wait for the leaderboard table to reload after going back
                page.wait_for_function(
                    "() => document.querySelectorAll('table tbody tr').length >= 5",
                    timeout=15_000,
                )
                page.wait_for_timeout(1_500)
            except (Error, TimeoutError):
                page.goto(LEADERBOARD_URL, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(5_000)  # give time for WebSocket data to load
    return None


def _is_disabled(locator: Locator) -> bool:
    try:
        disabled = locator.get_attribute("disabled")
        aria_disabled = locator.get_attribute("aria-disabled")
        class_name = locator.get_attribute("class") or ""
    except Error:
        return True
    return disabled is not None or aria_disabled == "true" or "disabled" in class_name.lower()


def _click_next_page(page: Page) -> bool:
    candidates = [
        page.get_by_role("button", name=re.compile(r"^next$", re.I)),
        page.locator("button, [role='button'], [onclick], a, div").filter(has_text=re.compile(r"^next$", re.I)),
        page.locator(
            "button[aria-label*='Next' i], [role='button'][aria-label*='Next' i], "
            "[onclick][aria-label*='Next' i], a[aria-label*='Next' i], div[aria-label*='Next' i]"
        ),
        page.locator("button, [role='button'], [onclick], a, div").filter(has_text=re.compile(r"^(›|>)$")),
    ]
    for candidate in candidates:
        try:
            if candidate.count() == 0:
                continue
            next_button = candidate.last
            if _is_disabled(next_button):
                continue
            next_button.scroll_into_view_if_needed(timeout=3_000)
            next_button.click(timeout=5_000)
            page.wait_for_load_state("networkidle", timeout=10_000)
            page.wait_for_timeout(1_500)
            return True
        except (Error, TimeoutError):
            continue
    try:
        clicked = page.evaluate(
            """() => {
            const isVisible = (el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
            };
            const disabled = (el) => {
                const className = el.getAttribute("class") || "";
                return el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true" || /disabled/i.test(className);
            };
            const candidates = Array.from(document.querySelectorAll("[onclick], [role='button'], button, a, div, span"))
                .filter((el) => {
                    if (!isVisible(el) || disabled(el)) return false;
                    const text = (el.innerText || el.textContent || "").trim();
                    const aria = el.getAttribute("aria-label") || "";
                    const title = el.getAttribute("title") || "";
                    return /^(next|›|>)$/i.test(text) || /next/i.test(aria) || /next/i.test(title);
                })
                .sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left);
            for (const el of candidates) {
                el.click();
                return true;
            }
            return false;
        }"""
        )
        if clicked:
            page.wait_for_load_state("networkidle", timeout=10_000)
            page.wait_for_timeout(1_500)
            return True
    except (Error, TimeoutError):
        pass
    return False


def _collect_from_page(page: Page, window: str, max_rank: int, seen_ranks: set[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    locators = _row_locators(page)
    for index in range(len(locators)):
        locators = _row_locators(page)
        if index >= len(locators):
            break
        row = locators[index]
        try:
            text = row.inner_text(timeout=2_000)
        except (Error, TimeoutError):
            continue

        parsed = _parse_row_text(text, window)
        if not parsed:
            continue

        rank = int(parsed["rank"])
        if rank in seen_ranks or rank > max_rank:
            continue

        if parsed["_needs_address_lookup"] or not parsed["address"]:
            parsed["address"] = _address_from_row_navigation(page, row) or ""

        if not parsed["address"]:
            print(f"[leaderboard] Skipping rank {rank}: no public wallet address found")
            seen_ranks.add(rank)
            continue

        seen_ranks.add(rank)
        parsed.pop("_needs_address_lookup", None)
        parsed.pop("_raw_text", None)
        rows.append(parsed)
        print(f"[leaderboard] {window} rank {rank}: {parsed['address']}")
        time.sleep(0.2)
    return rows


def fetch_leaderboard_wallets(window: str = "30d", max_rank: int = 200) -> list[dict]:
    """Scrape Hyperliquid leaderboard wallet rows with Playwright.

    The leaderboard is rendered by the frontend and some wallet addresses are
    truncated. For those rows, the collector opens the row detail page and reads
    the full wallet address from the SPA URL.
    """
    normalised_window = _normalise_window(window)
    last_error: Exception | None = None

    for attempt in range(1, 4):
        playwright = None
        browser = None
        try:
            playwright, browser = _open_browser()
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.goto(LEADERBOARD_URL, wait_until="domcontentloaded", timeout=45_000)
            _wait_for_leaderboard(page)
            _select_window(page, normalised_window)

            collected: list[dict[str, Any]] = []
            seen_ranks: set[int] = set()
            while len(seen_ranks) < max_rank:
                previous_seen_count = len(seen_ranks)
                page_rows = _collect_from_page(page, normalised_window, max_rank, seen_ranks)
                collected.extend(page_rows)
                if any(row["rank"] >= max_rank for row in page_rows):
                    break
                if len(seen_ranks) == previous_seen_count:
                    break
                if not _click_next_page(page):
                    break

            collected.sort(key=lambda row: row["rank"])
            return collected
        except Exception as exc:
            last_error = exc
            print(f"[leaderboard] Attempt {attempt}/3 failed for {normalised_window}: {exc}")
            time.sleep(2 * attempt)
        finally:
            if browser is not None:
                browser.close()
            if playwright is not None:
                playwright.stop()

    raise RuntimeError(f"Failed to collect Hyperliquid leaderboard for {normalised_window}") from last_error
