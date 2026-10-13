"""Claude auto-compact window controls against the isolated Admin server."""

from playwright.sync_api import Page, expect

WINDOW_KEY = "FCC_CLAUDE_AUTO_COMPACT_WINDOW"


def test_claude_auto_compact_window_round_trip_and_validation(
    page: Page, admin_base_url: str
) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{admin_base_url}/admin/model_config")
    expect(page.locator("#view-model_config")).to_be_visible()
    window = page.get_by_label("Claude Auto-compact Window")
    expect(window).to_be_visible()
    expect(window).to_be_enabled()
    expect(window).to_have_value("")
    expect(window).to_have_attribute("type", "number")
    expect(window).to_have_attribute("min", "100000")
    expect(window).to_have_attribute("max", "1000000")
    expect(window).to_have_attribute("step", "1")
    expect(window).to_have_attribute("placeholder", "Automatic (model default)")

    window.fill("500000")
    expect(page.locator("#dirtyState")).to_have_text("1 unsaved change")
    with page.expect_response("**/admin/api/config/apply") as saved:
        page.locator("#applyButton").click()
    assert saved.value.json()["applied"] is True
    expect(page.locator("#dirtyState")).to_have_text("No changes")
    page.reload()
    expect(window).to_have_value("500000")

    window.fill("19000")
    assert window.evaluate("input => input.validity.rangeUnderflow")
    assert not window.evaluate("input => input.checkValidity()")
    with page.expect_response("**/admin/api/config/apply") as rejected:
        page.locator("#applyButton").click()
    assert rejected.value.json()["applied"] is False
    expect(page.locator("#messageArea")).to_contain_text(WINDOW_KEY)
    expect(window).to_have_value("19000")
    expect(page.locator("#dirtyState")).to_have_text("1 unsaved change")
    page.reload()
    expect(window).to_have_value("500000")

    window.fill("")
    with page.expect_response("**/admin/api/config/apply") as cleared:
        page.locator("#applyButton").click()
    assert cleared.value.json()["applied"] is True
    expect(page.locator("#dirtyState")).to_have_text("No changes")
    page.reload()
    expect(window).to_have_value("")
    response = page.request.get(f"{admin_base_url}/admin/api/config")
    assert response.ok
    field = next(
        field for field in response.json()["fields"] if field["key"] == WINDOW_KEY
    )
    assert field["value"] is None
