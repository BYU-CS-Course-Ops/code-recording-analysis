"""Optional Chromium regressions: install with `poetry install --with browser`."""

from pathlib import Path

import pytest

from recan.session import analyze_events
from recan.viewer import build_player_html

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as runner:
        if not Path(runner.chromium.executable_path).is_file():
            pytest.skip("Install Chromium with: poetry run playwright install chromium")
        with runner.chromium.launch(headless=True) as instance:
            yield instance


def session(document, start):
    events = [
        {"type": "edit", "offset": 0, "oldFragment": "", "newFragment": "print(42)\n"},
        {"type": "focusStatus", "focused": False},
        {"type": "focusStatus", "focused": True},
    ]
    for i, event in enumerate(events):
        event.update(document=document, timestamp=f"2026-01-01T00:00:{start + i:02d}Z")
    result = analyze_events(events)
    # The player also accepts bundles directly; retain the full path to exercise
    # both basename text and full-path attributes, including literal entities.
    result["document"] = document
    return result


@pytest.mark.parametrize("name", [
    '<img src=x onerror="window.__injected=true" data-injected=1>.py',
    'file" onmouseover="window.__injected=true" data-injected="1',
    "literal &quot; &amp; & < > ' \" é.py",
])
def test_document_names_are_literal_text_in_every_html_sink(browser, name):
    source = "folder/" + name
    html = build_player_html([session(source, 0), session("other.py", 10)])
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.set_content(html)
        assert page.title() == "Recording Playback — " + source
        assert page.locator(".session-row-name").first.text_content() == name
        assert page.locator(".session-row").first.get_attribute("title") == source
        assert name in page.locator(".flag-group.unapproved-paste .flag-meta").first.text_content()
        assert name in page.locator(".flag-group.unfocused .flag-meta").first.text_content()

        marker = page.locator(".tick-session").first
        assert marker.get_attribute("title") == name + " → other.py"
        # Exercise attribute payloads as well as markup that fires on page load.
        marker.dispatch_event("mouseover")
        page.locator(".session-row").first.dispatch_event("mouseover")

        track = page.locator("#timeline-track")
        bounds = track.bounding_box()
        track.dispatch_event("pointermove", {"clientX": bounds["x"] + 1})
        assert page.locator("#timeline-tooltip .tt-doc").text_content() == " · " + name

        assert page.locator("[data-injected]").count() == 0
        assert page.evaluate("window.__injected === undefined")
        assert errors == []
    finally:
        page.close()
