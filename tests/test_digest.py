"""The perception layer against the real frameset. If this is wrong, nothing above works."""

from __future__ import annotations

import pytest

from cua.surface.base import Anchor, LocatorDescriptor, LocatorUnresolved, Surface
from cua.surface.desktop import DesktopSurface
from cua.surface.web import WebPerception


@pytest.fixture
def signed_on(session, base_url, credentials):
    """Sign on by hand so the digest under test is the post-login frameset."""
    user, password = credentials
    page = session.page
    page.goto(base_url)
    surface = WebPerception(page)
    page.frame(name="main").fill('input[title="User ID"]', user)
    page.frame(name="main").fill('input[title="Password"]', password)
    page.frame(name="main").click('input[value="Sign On"]')
    page.wait_for_timeout(400)
    return surface


def test_signon_digest_numbers_the_controls_a_person_could_use(session, base_url):
    session.page.goto(base_url)
    digest = WebPerception(session.page).snapshot()
    rendered = digest.render()
    assert '[0]  textbox   "User ID"' in rendered
    assert any(e.role == "button" and e.accessible_name == "Sign On" for e in digest.entries)
    assert [e.index for e in digest.entries] == list(range(len(digest.entries)))


def test_every_entry_carries_role_name_nearby_text_and_frame_path(session, base_url):
    session.page.goto(base_url)
    entry = next(
        e for e in WebPerception(session.page).snapshot().entries if e.accessible_name == "User ID"
    )
    assert entry.role == "textbox"
    assert entry.near == "User ID:"
    assert entry.frame_path == ["main"]


def test_the_digest_walks_nested_frames_and_reports_the_path(signed_on):
    digest = signed_on.snapshot()
    member_id = next(e for e in digest.entries if e.accessible_name == "Member ID")
    assert member_id.frame_path == ["main", "content"]
    assert ["main", "nav"] in [e.frame_path for e in digest.entries]


def test_table_layout_controls_still_carry_a_usable_anchor(signed_on):
    digest = signed_on.snapshot()
    assert next(e for e in digest.entries if e.accessible_name == "Member ID").near == "Member ID:"


def test_stripping_accessible_names_leaves_the_anchor_signal_intact(
    signed_on, session, base_url
):
    session.page.goto(f"{base_url}/?fault=no_names")
    session.page.wait_for_timeout(300)
    digest = signed_on.snapshot()
    member_id = next(
        e for e in digest.entries if e.frame_path == ["main", "content"] and e.role == "textbox"
    )
    assert member_id.accessible_name == ""
    assert member_id.near == "Member ID:"


def test_locate_reports_the_tier_that_resolved(signed_on):
    located = signed_on.locate(
        LocatorDescriptor(role="textbox", accessible_name="Member ID", frame_path=["main", "content"])
    )
    assert located.tier == 1


def test_locate_falls_back_to_the_anchor_when_the_name_is_gone(signed_on, session, base_url):
    session.page.goto(f"{base_url}/?fault=no_names")
    session.page.wait_for_timeout(300)
    located = signed_on.locate(
        LocatorDescriptor(
            role="textbox",
            accessible_name="Member ID",
            anchor=Anchor(stable_text="Member ID:", relation="label_for"),
            frame_path=["main", "content"],
        )
    )
    assert located.tier == 4


def test_a_target_matching_more_than_one_control_is_an_error(signed_on):
    with pytest.raises(LocatorUnresolved) as caught:
        signed_on.locate(LocatorDescriptor(structural="input", frame_path=["main", "content"]))
    assert "tier 5: 3 matches" in str(caught.value)


def test_an_unresolvable_target_reports_every_tier_it_tried(signed_on):
    with pytest.raises(LocatorUnresolved) as caught:
        signed_on.locate(LocatorDescriptor(role="button", accessible_name="Wire Transfer"))
    assert "tier 1: 0 matches" in str(caught.value)


def test_extract_reads_a_value_by_the_label_next_to_it(signed_on, session, base_url):
    session.page.frame(name="content").fill('input[title="Member ID"]', "12345")
    session.page.frame(name="content").click('input[value="Search"]')
    session.page.wait_for_timeout(400)
    value = signed_on.extract(
        LocatorDescriptor(role="cell", anchor=Anchor(stable_text="Savings", relation="same_row"))
    )
    assert value == "$4,182.55"


def test_the_desktop_stub_satisfies_the_surface_interface():
    assert isinstance(DesktopSurface("acme-teller.exe"), Surface)


def test_the_desktop_stub_raises_rather_than_pretending():
    with pytest.raises(NotImplementedError):
        DesktopSurface("acme-teller.exe").snapshot()
