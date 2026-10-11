"""Optional Chromium regressions: install with `poetry install --with browser`."""

from datetime import datetime, timedelta, timezone
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


@pytest.fixture
def player_page(browser):
    page = browser.new_page()
    try:
        yield page
    finally:
        page.close()


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
        assert name in page.locator(".flag-group.high .flag-meta").first.text_content()
        assert name in page.locator(".flag-group.medium .flag-meta").first.text_content()

        marker = page.locator(".tick-session").first
        assert marker.get_attribute("title") == name + " → other.py"
        # Exercise attribute payloads as well as markup that fires on page load.
        marker.dispatch_event("mouseover")
        page.locator(".session-row").first.dispatch_event("mouseover")

        assert page.locator("[data-injected]").count() == 0
        assert page.evaluate("window.__injected === undefined")
        assert errors == []
    finally:
        page.close()


def test_sessions_and_key_moments_are_chronological(player_page):
    player_page.set_content(build_player_html([
        session("later.py", 20),
        session("earlier.py", 0),
    ]))

    assert player_page.locator(".tab").all_text_contents() == ["earlier.py", "later.py"]
    assert player_page.locator(".flag-group.high .flag-meta").all_text_contents() == [
        "earlier.py", "later.py",
    ]
    player_page.locator(".tab").nth(1).click()
    assert player_page.locator(".tab.active").text_content() == "later.py"
    assert player_page.locator(".tab").nth(1).get_attribute("aria-selected") == "true"


def test_summary_and_removed_navigation_contract(player_page):
    player_page.set_content(build_player_html([
        session("later.py", 20),
        session("earlier.py", 0),
    ]))

    assert player_page.locator("#sum-unapproved-pastes").text_content() == "2"
    assert player_page.locator("#sum-unfocused").text_content() == "2"
    assert player_page.locator("#total-time").text_content() != " / —"
    assert player_page.locator(
        "#prev-burst, #next-burst, #prev-unfocused, #next-unfocused",
    ).count() == 0
    dialogs = []
    player_page.once("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    player_page.locator("#help-btn").click()
    assert len(dialogs) == 1
    assert "boundary" not in dialogs[0].lower()


def test_duplicate_fragment_finding_highlights_its_transformed_range(player_page):
    duplicate = analyze_events([
        {
            "type": "edit", "document": "duplicate.py",
            "timestamp": "2026-01-01T00:00:30Z", "offset": 0,
            "oldFragment": "same! prefix ", "newFragment": "same! prefix ",
        },
        {
            "type": "edit", "document": "duplicate.py",
            "timestamp": "2026-01-01T00:00:31Z", "offset": 13,
            "oldFragment": "", "newFragment": "same!",
        },
        {
            "type": "edit", "document": "duplicate.py",
            "timestamp": "2026-01-01T00:00:31.100Z", "offset": 13,
            "oldFragment": "", "newFragment": ">",
        },
    ])
    duplicate["document"] = "duplicate.py"
    player_page.set_content(build_player_html([duplicate]))

    # The inserted text duplicates an earlier occurrence. Clicking the finding
    # must use the event offset and account for the trailing generated-group fixup.
    player_page.locator(".flag-group.high > summary").click()
    player_page.locator(".flag-group.high .flag").click()
    assert player_page.locator("#editor-code").text_content() == "same! prefix >same!"
    highlight = player_page.evaluate("""() => {
      const ranges = [...CSS.highlights.get("annotation")];
      return {
        count: ranges.length,
        start: ranges[0].startOffset,
        end: ranges[0].endOffset,
        text: ranges[0].toString(),
      };
    }""")
    assert highlight == {"count": 1, "start": 14, "end": 19, "text": "same!"}


def test_cluster_fixups_highlight_the_final_fragment(player_page):
    recording = analyze_events([
        {
            "type": "edit", "document": "main.py",
            "timestamp": "2026-01-01T00:00:00Z", "offset": 0,
            "oldFragment": "", "newFragment": "a\n    b",
        },
        {
            "type": "edit", "document": "main.py",
            "timestamp": "2026-01-01T00:00:00.010Z", "offset": 2,
            "oldFragment": "    ", "newFragment": "",
        },
    ])
    player_page.set_content(build_player_html([recording]))

    player_page.locator(".flag-group.high > summary").click()
    player_page.locator(".flag-group.high .flag").click()
    highlight = player_page.evaluate("""() => {
      const ranges = [...CSS.highlights.get("annotation")];
      return { count: ranges.length, text: ranges[0].toString() };
    }""")
    assert highlight == {"count": 1, "text": "a\nb"}


def test_syntax_highlighting_preserves_annotation_ranges_across_tokens(player_page):
    player_page.set_content(build_player_html([session("main.py", 0)]))

    player_page.locator(".flag-group.high > summary").click()
    player_page.locator(".flag-group.high .flag").click()
    assert player_page.locator("#editor-code").get_attribute("class") == "hljs language-python"
    assert player_page.locator("#editor-code .hljs-built_in").text_content() == "print"
    assert player_page.locator("#editor-code .hljs-number").text_content() == "42"
    highlight = player_page.evaluate("""() => {
      const ranges = [...CSS.highlights.get("annotation")];
      return { count: ranges.length, text: ranges[0].toString() };
    }""")
    assert highlight == {"count": 1, "text": "print(42)\n"}


def test_play_button_renders_pause_state(player_page):
    player_page.set_content(build_player_html([session("main.py", 0)]))

    player_page.locator("#play-btn").click()
    assert player_page.locator("#play-btn").get_attribute("aria-label") == "Pause"
    assert "M4 2.5h3" in player_page.locator("#play-icon").inner_html()


@pytest.mark.parametrize("newline", ["\r\n", "\r", "\n"])
def test_large_paste_preserves_recorded_offsets_and_exact_text(player_page, newline):
    initial = newline.join(f"value_{i} = {i}" for i in range(40)) + newline
    fragment = newline.join(["def pasted_function():", '    """documentation"""', "    return 42", ""])
    recording = analyze_events([
        {"type": "edit", "document": "main.py", "timestamp": "2026-01-01T00:00:00Z",
         "offset": 0, "oldFragment": initial, "newFragment": initial},
        {"type": "edit", "document": "main.py", "timestamp": "2026-01-01T00:00:01Z",
         "offset": len(initial), "oldFragment": "", "newFragment": fragment},
    ])
    player_page.set_content(build_player_html([recording]))
    player_page.locator(".flag-group.high > summary").click()
    player_page.locator(".flag-group.high .flag").click()
    assert player_page.locator("#editor-code").text_content() == initial + fragment
    assert player_page.evaluate('[...CSS.highlights.get("annotation")][0].toString()') == fragment


def test_scrubbing_and_playback_highlight_paste_and_click_pauses(player_page):
    player_page.clock.install()
    player_page.set_content(build_player_html([session("main.py", 0)]))
    player_page.keyboard.press("ArrowRight")
    assert player_page.evaluate('[...CSS.highlights.get("annotation")][0].toString()') == "print(42)\n"
    player_page.keyboard.press("Home")
    player_page.locator("#play-btn").click()
    player_page.clock.run_for(100)
    assert player_page.evaluate('[...CSS.highlights.get("annotation")][0].toString()') == "print(42)\n"
    player_page.locator(".flag-group.high > summary").click()
    player_page.locator(".flag-group.high .flag").click()
    player_page.clock.run_for(1500)
    assert player_page.locator("#play-btn").get_attribute("aria-label") == "Play"
    assert player_page.evaluate('[...CSS.highlights.get("annotation")][0].toString()') == "print(42)\n"


def test_recording_issue_shows_last_reliable_text_and_snapshot_restores_playback(player_page):
    events = []
    for second, (old, new, offset) in enumerate([
        ("known", "known", 0), ("missing", "unreliable paste!", 0),
        ("", "more unreliable!", 0), ("restored", "restored", 0),
        ("", "!", 8),
    ]):
        events.append({"type": "edit", "document": "main.py",
                       "timestamp": f"2026-01-01T00:00:{second:02d}Z",
                       "oldFragment": old, "newFragment": new, "offset": offset})
    player_page.set_content(build_player_html([analyze_events(events)]))
    player_page.locator(".flag-group.medium > summary").click()
    player_page.locator(".flag.recording-issue").first.click()
    assert player_page.locator("#editor-code").text_content() == "known"
    assert player_page.locator("#recording-warning").is_visible()
    assert "2 edits skipped" in player_page.locator(".flag.recording-issue").first.text_content()
    player_page.locator(".flag.recording-issue").last.click()
    assert player_page.locator("#editor-code").text_content() == "restored"
    assert not player_page.locator("#recording-warning").is_visible()
    player_page.keyboard.press("End")
    assert player_page.locator("#editor-code").text_content() == "restored!"
    assert not player_page.locator("#recording-warning").is_visible()
    player_page.keyboard.press("Home")
    assert player_page.locator("#editor-code").text_content() == "known"
    assert not player_page.locator("#recording-warning").is_visible()


def test_key_moment_scrolls_its_code_into_view(player_page):
    initial = "".join(f"line_{index} = {index}\n" for index in range(200))
    recording = analyze_events([
        {
            "type": "edit", "document": "main.py",
            "timestamp": "2026-01-01T00:00:00Z", "offset": 0,
            "oldFragment": initial, "newFragment": initial,
        },
        {
            "type": "edit", "document": "main.py",
            "timestamp": "2026-01-01T00:00:01Z", "offset": len(initial),
            "oldFragment": "", "newFragment": "print('review this code')\n",
        },
    ])
    player_page.set_content(build_player_html([recording]))

    assert player_page.locator("#editor").evaluate("editor => editor.scrollTop") == 0
    player_page.locator(".flag-group.high > summary").click()
    player_page.locator(".flag-group.high .flag").click()

    position = player_page.evaluate("""() => {
      const range = [...CSS.highlights.get("annotation")][0];
      const target = range.getClientRects()[0];
      const editor = document.getElementById("editor");
      const viewport = editor.getBoundingClientRect();
      return {
        scrollTop: editor.scrollTop,
        targetTop: target.top,
        targetBottom: target.bottom,
        viewportTop: viewport.top,
        viewportBottom: viewport.bottom,
      };
    }""")
    assert position["scrollTop"] > 0
    assert position["viewportTop"] <= position["targetTop"]
    assert position["targetBottom"] <= position["viewportBottom"]


def test_long_recording_can_scrub_to_end_without_reconstructing_on_every_frame(browser):
    event_count = 1_500
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = [{
        "type": "edit", "document": "long.py", "timestamp": start.isoformat(),
        "offset": 0, "oldFragment": "", "newFragment": "",
    }]
    events.extend({
        "type": "edit",
        "document": "long.py",
        "timestamp": (start + timedelta(milliseconds=index)).isoformat(),
        "offset": index - 1,
        "oldFragment": "",
        "newFragment": "x",
    } for index in range(1, event_count + 1))

    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.set_content(build_player_html([analyze_events(events)]))
        page.keyboard.press("End")
        assert page.locator("#editor-code").text_content() == "x" * event_count
        page.keyboard.press("ArrowLeft")
        assert page.locator("#editor-code").text_content() == "x" * (event_count - 1)
        assert errors == []
    finally:
        page.close()
