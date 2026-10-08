import re
import pytest
from playwright.sync_api import sync_playwright, expect

BASE_URL = "http://127.0.0.1:8000"

def parse_currency(text: str) -> float:
    """Parse '₹ 10,24,560.50' or '₹1024560.50' into float."""
    cleaned = re.sub(r"[^\d.]", "", text.strip())
    return float(cleaned) if cleaned else 0.0

def parse_qty_from_text(text: str) -> int:
    """Extract integer quantity from '💼 49 Owned' or 'SELL (49)'."""
    m = re.search(r"\b(\d+)\b", text)
    return int(m.group(1)) if m else 0

def test_playwright_e2e_sell_and_buy_and_insufficient_holdings():
    """
    Browser end-to-end test against the live running app with active price simulator:
    1. Log in as demo user (9000000001 / demo123)
    2. Open watchlist, click Sell on held stock, submit -> success toast, holding decreases, funds increase
    3. Click Buy on stock, submit -> success toast, holding increases, funds decrease
    4. Click Sell on unowned stock (0 holdings) -> error banner displays inside modal
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # Step 1: Login
        page.goto(f"{BASE_URL}/login")
        page.fill("input[name='mobile']", "9000000001")
        page.fill("input[name='password']", "demo123")
        page.click("button[type='submit']")
        page.wait_for_url(f"{BASE_URL}/home", timeout=10000)

        # Step 2: Open Watchlist
        page.goto(f"{BASE_URL}/watchlists")
        page.wait_for_selector(".data-table", timeout=10000)

        # Read initial funds
        nav_bal_el = page.locator("#navWalletBalance")
        expect(nav_bal_el).to_be_visible()
        initial_funds = parse_currency(nav_bal_el.inner_text())

        # Select a held stock on the watchlist, e.g. RELIANCE
        reliance_row = page.locator("tr[data-symbol='RELIANCE']")
        expect(reliance_row).to_be_visible()

        holding_badge = page.locator(".holding-badge[data-symbol='RELIANCE']")
        initial_qty = parse_qty_from_text(holding_badge.inner_text())
        assert initial_qty > 0, "Demo user should have existing holdings for RELIANCE"

        # Step 3: SELL 1 held share
        sell_btn = page.locator("button.btn-sell-action[data-symbol='RELIANCE']")
        sell_btn.click()

        modal = page.locator("#buySellModal")
        expect(modal).to_be_visible()
        expect(page.locator("#modalTitle")).to_contain_text("SELL")

        # Submit SELL order
        submit_btn = page.locator("#btnSubmitOrder")
        expect(submit_btn).to_be_enabled()
        submit_btn.click()

        # Assert success toast appears
        toast = page.locator(".toast-success")
        expect(toast).to_be_visible(timeout=8000)
        assert "executed" in toast.inner_text().lower() or "success" in toast.inner_text().lower()

        # Close modal via modal close button
        close_btn = page.locator("#buySellModal button[onclick*='closeBuySellModal']").first
        if close_btn.is_visible():
            close_btn.click()

        # Assert holdings decreased
        after_sell_qty = parse_qty_from_text(page.locator(".holding-badge[data-symbol='RELIANCE']").inner_text())
        assert after_sell_qty == initial_qty - 1, f"Expected {initial_qty - 1} but got {after_sell_qty}"

        # Assert funds increased
        funds_after_sell = parse_currency(nav_bal_el.inner_text())
        assert funds_after_sell > initial_funds, f"Funds should increase after sell: {funds_after_sell} > {initial_funds}"

        # Step 4: BUY 1 share of RELIANCE
        buy_btn = page.locator("button.btn-buy-action[data-symbol='RELIANCE']")
        buy_btn.click()

        expect(modal).to_be_visible()
        expect(page.locator("#modalTitle")).to_contain_text("BUY")
        expect(submit_btn).to_be_enabled()
        submit_btn.click()

        # Assert success toast appears for BUY
        expect(page.locator(".toast-success")).to_be_visible(timeout=8000)

        # Close modal
        close_btn = page.locator("#buySellModal button[onclick*='closeBuySellModal']").first
        if close_btn.is_visible():
            close_btn.click()

        # Assert holdings increased back
        after_buy_qty = parse_qty_from_text(page.locator(".holding-badge[data-symbol='RELIANCE']").inner_text())
        assert after_buy_qty == after_sell_qty + 1

        # Assert funds decreased after buy
        funds_after_buy = parse_currency(nav_bal_el.inner_text())
        assert funds_after_buy < funds_after_sell

        # Step 5: Test Insufficient Holdings error inside modal
        # ADANIENT is on the watchlist and owned qty is 0
        adanient_row = page.locator("tr[data-symbol='ADANIENT']")
        expect(adanient_row).to_be_visible()

        adanient_sell_btn = page.locator("button.btn-sell-action[data-symbol='ADANIENT']")
        adanient_sell_btn.click()

        expect(modal).to_be_visible()
        expect(page.locator("#modalTitle")).to_contain_text("SELL")
        expect(page.locator("#modalOwnedDisplay")).to_contain_text("0 Shares")

        # Submit order for unowned stock
        expect(submit_btn).to_be_enabled()
        submit_btn.click()

        # Assert error is displayed inside the modal
        feedback = page.locator("#orderFeedback")
        expect(feedback).to_be_visible(timeout=5000)
        feedback_text = feedback.inner_text()
        assert "INSUFFICIENT_HOLDINGS" in feedback_text or "Order Failed" in feedback_text, (
            f"Expected INSUFFICIENT_HOLDINGS in feedback, got: {feedback_text}"
        )

        browser.close()
