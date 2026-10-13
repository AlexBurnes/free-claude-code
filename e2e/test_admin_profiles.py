"""Named-profile workflows against the isolated Admin fixture server."""

from playwright.sync_api import Page, expect


def open_profiles(page: Page, base: str) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{base}/admin/profile_config")
    expect(page.locator("#profileSelect")).to_have_value("default")
    expect(page.locator("#profileDefaultNotice")).to_be_visible()


def name_profile(page: Page, name: str, *, duplicate: bool = False) -> None:
    page.locator("#duplicateProfile" if duplicate else "#createProfile").click()
    page.locator("#profileName").fill(name)
    page.locator("#submitProfileName").click()
    expect(page.locator("#profileNameDialog")).not_to_be_visible()
    expect(page.locator("#profileSelect")).to_have_value(name)


def save_model(page: Page, model: str) -> None:
    page.locator("#profile-model").fill(model)
    page.locator("#profile-model").press("Tab")
    expect(page.locator("#saveProfile")).to_be_enabled()
    page.locator("#saveProfile").click()
    expect(page.locator("#profileMessage")).to_contain_text("saved")
    expect(page.locator("#profileDirtyState")).to_have_text("No changes")


def test_profiles_crud_preserves_active_binding(page: Page, admin_base_url: str):
    open_profiles(page, admin_base_url)
    name_profile(page, "deepseek")
    expect(page.locator("#profileDirtyState")).to_have_text("No changes")
    save_model(page, "open_router/vendor/model-a")
    headers = {"Authorization": "Bearer e2e-proxy-token"}
    response = page.request.post(
        f"{admin_base_url}/api/launch-sessions",
        data={"profile": "deepseek"},
        headers=headers,
    )
    assert response.status == 201
    binding = response.json()
    name_profile(page, "copy", duplicate=True)
    copied = page.request.get(f"{admin_base_url}/admin/api/profiles/copy").json()
    assert copied["configuration"]["model"] == "open_router/vendor/model-a"
    assert copied["configuration"]["model_sonnet"] is None
    assert copied["configuration"]["model_fallbacks"] is None
    page.locator("#profileSelect").select_option("deepseek")
    save_model(page, "open_router/vendor/model-b")
    active_url = admin_base_url + binding["base_path"] + "/"
    assert page.request.get(active_url, headers=headers).json()["model"] == (
        "open_router/vendor/model-a"
    )
    page.locator("#deleteProfile").click()
    page.locator("#confirmProfileDelete").click()
    expect(page.locator("#profileSelect")).to_have_value("default")
    expect(page.locator("#profileMessage")).to_contain_text("Existing sessions")
    assert page.request.get(active_url, headers=headers).json()["model"] == (
        "open_router/vendor/model-a"
    )
    assert (
        page.request.post(
            f"{admin_base_url}/api/launch-sessions",
            data={"profile": "deepseek"},
            headers=headers,
        ).status
        == 404
    )
    assert (
        page.request.delete(
            f"{admin_base_url}/api/launch-sessions/{binding['session_id']}",
            headers=headers,
        ).status
        == 204
    )
    page.locator("#editDefaultProfile").click()
    expect(page.locator("#view-model_config")).to_be_visible()
    page.get_by_role("button", name="Providers", exact=True).click()
    expect(page.locator('[data-provider="open_router"]')).to_be_visible()
    page.get_by_role("button", name="Integrations", exact=True).click()
    expect(page.locator("#view-integrations")).to_be_visible()


def test_profile_validation_and_unsaved_cancel(page: Page, admin_base_url: str):
    open_profiles(page, admin_base_url)
    page.locator("#createProfile").click()
    page.locator("#profileName").fill("default")
    page.locator("#submitProfileName").click()
    expect(page.locator("#profileNameDialog")).to_be_visible()
    assert "reserved" in page.locator("#profileName").evaluate(
        "input => input.validationMessage"
    )
    page.locator("#profileName").fill("Uppercase")
    assert not page.locator("#profileName").evaluate("input => input.checkValidity()")
    page.locator("#profileName").fill("valid-name_1")
    assert page.locator("#profileName").evaluate("input => input.checkValidity()")
    page.locator("#cancelProfileName").click()
    name_profile(page, "work")
    page.locator("#profile-model").fill("groq/unsaved")
    page.locator("#profile-model").press("Tab")
    expect(page.locator("#profileDirtyState")).to_have_text("Unsaved profile changes")
    expect(page.locator("#dirtyState")).to_have_text("No changes")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("#profileSelect").select_option("default")
    expect(page.locator("#profileSelect")).to_have_value("work")
    expect(page.locator("#profile-model")).to_have_value("groq/unsaved")
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#discardProfileChanges").click()
    expect(page.locator("#profileDirtyState")).to_have_text("No changes")
    page.locator("#duplicateProfile").click()
    page.locator("#profileName").fill("work")
    page.locator("#submitProfileName").click()
    assert "already exists" in page.locator("#profileName").evaluate(
        "input => input.validationMessage"
    )


def test_profile_fallbacks_and_reasoning_round_trip(
    page: Page, admin_base_url: str, tmp_path
):
    open_profiles(page, admin_base_url)
    name_profile(page, "routing")
    editor = page.locator('#profileFields .field[data-key="model_fallbacks"]')
    add_input = editor.locator("#profile-model_fallbacks-add")
    for model in ("groq/first", "groq/second"):
        add_input.fill(model)
        editor.get_by_label("Fallback reasoning effort", exact=True).select_option(
            "high"
        )
        editor.get_by_role("button", name="Add fallback", exact=True).click()
    handle = editor.get_by_role("button", name="Move groq/second", exact=True)
    handle.focus()
    handle.press("ArrowUp")
    page.locator("#profile-reasoning_policy").select_option("off")
    page.locator("#saveProfile").click()
    expect(page.locator("#profileMessage")).to_contain_text("saved")
    saved = page.request.get(f"{admin_base_url}/admin/api/profiles/routing").json()
    assert saved["configuration"]["model_fallbacks"] == [
        {"model": "groq/second", "reasoning": "high", "api_key_number": 1},
        {"model": "groq/first", "reasoning": "high", "api_key_number": 1},
    ]
    assert saved["configuration"]["reasoning_policy"] == "off"
    page.locator("#reloadProfiles").click()
    expect(
        editor.get_by_label("Reasoning effort for groq/second", exact=True)
    ).to_have_value("high")
    for model in ("groq/first", "groq/second"):
        editor.get_by_role("button", name=f"Remove {model}", exact=True).click()
    page.locator("#saveProfile").click()
    expect(page.locator("#profileMessage")).to_contain_text("saved")
    saved = page.request.get(f"{admin_base_url}/admin/api/profiles/routing").json()
    assert saved["configuration"]["model_fallbacks"] is None
    page.locator("#reloadProfiles").click()
    expect(page.locator("#profile-reasoning_policy")).to_have_value("off")
    expect(page.locator("#view-profile_config")).to_be_visible()
    page.screenshot(path=tmp_path / "profile-config.png", full_page=True)
