"""AppTest smoke tests for the strip states, the picker and the typed tokens — AC-33, 37, 38, 39,
40, 41, 42, 44, 48, 49, 50, 52.

Every test seeds ``scope_capabilities`` so the page never builds the agent (no serve
subprocess, no LLM client); ``page()`` adds the fake serve opener and the connection
seams every page test shares. Strip widgets live in ``st.bottom``, an anonymous block
outside ``at.main`` / ``at.sidebar`` — assert them through the FLAT accessors only.
Seed strip state BEFORE the first run: a widget that already holds a session value
ignores a changed default. Runs where the [harness-ask-your-docs] extra is installed
(the main checkout's venv, PYTHONPATH at the worktree), skipped elsewhere.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("streamlit")

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
from pydocs_mcp.harness.ask_your_docs.answer_footer import FollowUpChip, FollowUpKind
from pydocs_mcp.harness.ask_your_docs.attachments import AttachedSymbol
from pydocs_mcp.harness.ask_your_docs.catalog import workspace_branch_listing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    ScopeCell,
    ScopeCode,
    ScopeDefaultsOverride,
    ScopeKind,
    ScopeSlice,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.harness.ask_your_docs.scope_picker import (
    ONLY_THESE_KEY,
    PICKER_KEY,
    STRIP_STATE_KEY,
)
from pydocs_mcp.harness.ask_your_docs.scope_tokens import BRANCHES_NOT_CHOOSABLE
from pydocs_mcp.harness.ask_your_docs.scope_strip import (
    FORCED_HINT,
    NO_TARGET_SENTENCE,
    OVER_CAP_HINT,
    WORKSPACE_MARK_KEY,
)
from pydocs_mcp.harness.ask_your_docs.strip_state import (
    StripState,
    StripTarget,
    compile_strip_scope,
)
from pydocs_mcp.harness.ask_your_docs.transcript import (
    assistant_transcript_entry,
    user_transcript_entry,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

from ._connection_fakes import FakeBearer
from ._fixture import make_bundle

# page_env is autouse: importing it into this module is what arms it.
from ._page_fixtures import page, page_env, write_config
from .test_app_serve_session import _AgentStackSpy

U1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)
BACKEND_MAIN, BACKEND_RETRY = "a" * 40, "b" * 40
TOOLING_MAIN, TOOLING_DEVELOP = "c" * 40, "d" * 40
SOLO_MAIN, SOLO_FEATURE = "e" * 40, "f" * 40
TOOLING = ("tooling", "main")
BACKEND = ("backend", "feature/retry")
SOLO = ("solo", "feature/solo")
SHIPPED_MAX_CELLS = ScopeDefaultsConfig().max_cells
# One more project than a cap of four allows: the two-project workspace cannot reach the
# cap through tokens, since a repeated ``in:`` collapses to one cell (§6.10a).
FIVE_PROJECTS = ("api", "auth", "billing", "docs", "web")
# The 2026-09-04 keys (AC-49): an absence test over the NEW keys would pass on an empty page.
OLD_KEYS = frozenset(
    {
        "scope_project",
        "scope_code",
        "scope_package",
        "scope_defaults_project",
        "scope_defaults_branch",
        "scope_defaults_slice",
        "scope_defaults_code",
        "scope_defaults_package",
        "scope_defaults_open",
        "scope_defaults_button",
        "scope_defaults_reset",
        "scope_pin_popover",
        "scope_pin_project",
        "scope_pin_branches",
        "scope_pin_slice",
        "scope_pin_keep",
        "scope_pin_apply",
        "scope_pin_clear",
        "scope_chip_slice",
        "scope_chip_project",
    }
)


@pytest.fixture
def workspace(tmp_path, page_env):
    """Two projects that DISAGREE on default vs base, listed backend then tooling.

    backend's stamped (default) row is ``feature/retry`` with base ``main``; tooling's
    stamped row is ``main``. A single seeded target is always ``tooling`` — never the
    listing's first row — and the two stamped rows differ from the two bases, so a
    strip that showed the base, or the first row, fails (the PR #267 fixture lesson).
    The bundles are CREATED in the other order, which proves nothing on its own: this
    listing is alphabetical, and the picker's rows follow it by construction. That a
    target list follows the LISTING and not insertion order is pinned purely, against a
    reversed listing, by ``test_two_targets_pin_in_listing_order_whatever_the_checkbox_holds``
    in ``test_strip_state.py``.
    """
    make_bundle(
        tmp_path / "ws" / "tooling_0123456789.db",
        project="tooling",
        members=[("mod_t", "Bar", "class")],
        packages=["fastapi"],  # a real dependency: the Package selectbox needs a non-empty pool
        branches=[
            ("main", TOOLING_MAIN, None, 1, "active", None),
            ("develop", TOOLING_DEVELOP, "main", 0, "active", None),
        ],
    )
    make_bundle(
        tmp_path / "ws" / "backend_0123456789.db",
        project="backend",
        members=[("mod_a", "Foo", "class")],
        branches=[
            ("main", BACKEND_MAIN, None, 0, "active", None),
            ("feature/retry", BACKEND_RETRY, "main", 1, "active", None),
        ],
    )
    return tmp_path / "ws"


@pytest.fixture
def single_workspace(tmp_path, page_env):
    """ONE bundle, one project — the listing the picker cannot make plural.

    Not the two-project ``workspace`` with one bundle deleted, and never a second
    target: on a one-project listing "all projects" and "only solo" cover the SAME
    corpus, so the wording and the compiled kind are the only things that tell them
    apart — the assertions are on those, never on a result set. Its stamped row is
    ``feature/solo``, not ``main`` and not the YAML default project, so a strip that
    printed the base name or the YAML default fails.
    """
    make_bundle(
        tmp_path / "ws" / "solo_0123456789.db",
        project="solo",
        members=[("mod_s", "Baz", "class")],
        branches=[
            ("main", SOLO_MAIN, None, 0, "active", None),
            ("feature/solo", SOLO_FEATURE, "main", 1, "active", None),
        ],
    )
    return tmp_path / "ws"


@pytest.fixture
def five_project_workspace(tmp_path, page_env):
    """Five one-branch bundles, so five distinct ``in:`` tokens exceed a cap of four."""
    for position, name in enumerate(FIVE_PROJECTS):
        make_bundle(
            tmp_path / "ws" / f"{name}_0123456789.db",
            project=name,
            branches=[("main", str(position) * 40, None, 1, "active", None)],
        )
    return tmp_path / "ws"


def _app(capabilities=NO_SCOPE_CAPABILITIES, **seeds):
    return page(scope_capabilities=capabilities, **seeds)


def _strip(*targets: tuple[str, str], only_these: bool = False) -> StripState:
    return StripState(
        targets=tuple(StripTarget(p, (b,)) for p, b in targets), only_these=only_these
    )


def _widget_keys(at) -> set[str]:
    widgets = [
        *at.button,
        *at.checkbox,
        *at.selectbox,
        *at.radio,
        *at.multiselect,
        *at.pills,
        *at.toggle,
    ]
    return {w.key for w in widgets if w.key}


def _chip_keys(at) -> set[str]:
    return {b.key for b in at.button if b.key and b.key.startswith("scope_chip_")}


def _captions(at) -> list[str]:
    return [c.value for c in at.caption]


def _toasts(at) -> list[str]:
    return [t.value for t in at.toast]


def _run(at):
    at.run()
    assert not at.exception, at.exception
    return at


class TestNoTarget:  # AC-49
    def test_default_view_has_no_sidebar_scope_widget_and_no_old_key(self, workspace):
        at = _run(_app())
        assert not any(b.label in {"Scope defaults", "Where to search"} for b in at.sidebar.button)
        assert not (_widget_keys(at) & OLD_KEYS)
        assert any(m.value == NO_TARGET_SENTENCE for m in at.markdown)
        assert not any(c.key == ONLY_THESE_KEY for c in at.checkbox)  # no row 2
        assert not any(b.key == "scope_strip_clear" for b in at.button)
        assert len(at.chat_input) == 1  # a bare composer: no popover column beside it
        # The keyed popover registered its open flag and its body rendered (V2 / V3);
        # the graph page's popover key is not on the chat page.
        assert PICKER_KEY in at.session_state
        assert "graph_where_to_search" not in at.session_state
        assert any(b.key == "scope_picker_use" for b in at.button)

    def test_old_keys_are_asserted_by_name(self):
        """The absence test lists the 2026-09-04 keys by name; a rename in this set
        silently weakens AC-49, so the set is pinned here."""
        assert {"scope_pin_keep", "scope_defaults_open", "scope_chip_project"} <= OLD_KEYS


class TestPicker:  # AC-48, AC-44, AC-33
    def test_one_row_per_project_in_listing_order_and_no_branch_control_on_u0(self, workspace):
        at = _run(_app())
        rows = [c.key for c in at.checkbox if c.key and c.key.startswith("scope_picker_project_")]
        assert rows == ["scope_picker_project_backend", "scope_picker_project_tooling"]
        assert not any(p.key and p.key.startswith("scope_picker_branches_") for p in at.pills)
        assert any(r.key == "scope_picker_code" for r in at.radio)
        assert not any(r.key == "scope_picker_files" for r in at.radio)
        assert any(m.value == "**Where to search**" for m in at.markdown)
        # Tick tooling (not the first row): its STAMPED row is a read-only caption.
        at.checkbox(key="scope_picker_project_tooling").check()
        _run(at)
        assert f"indexed on main @{TOOLING_MAIN[:7]}" in _captions(at)
        assert f"indexed on feature/retry @{BACKEND_RETRY[:7]}" not in _captions(at)  # unticked

    def test_use_these_keeps_every_ticked_row_and_closes_the_picker(self, workspace):
        at = _run(_app())
        # tooling FIRST, backend second: a picker that keeps only the last tick fails here.
        # Listing order itself cannot fail here (the rows are drawn by iterating the
        # alphabetical listing) — it is pinned against a REVERSED listing by
        # test_two_targets_pin_in_listing_order_whatever_the_checkbox_holds in test_strip_state.py.
        at.checkbox(key="scope_picker_project_tooling").check()
        _run(at)
        at.checkbox(key="scope_picker_project_backend").check()
        _run(at)
        at.button(key="scope_picker_use").click()
        _run(at)
        assert at.session_state[STRIP_STATE_KEY].targets == (
            StripTarget("backend", ("feature/retry",)),
            StripTarget("tooling", ("main",)),
        )
        assert at.session_state[PICKER_KEY] is False  # closed by the callback
        assert _chip_keys(at) == {"scope_chip_backend_feature/retry", "scope_chip_tooling_main"}

    def test_use_these_keeps_the_only_these_tick(self, workspace):
        """A picker edit changes WHERE, never the person's "Only these" (§6.7)."""
        at = _run(_app(scope_strip=_strip(TOOLING, only_these=True)))
        at.checkbox(key="scope_picker_project_backend").check()
        _run(at)
        at.button(key="scope_picker_use").click()
        _run(at)
        assert at.session_state[STRIP_STATE_KEY].only_these is True

    def test_package_hides_when_code_is_project_only(self, workspace):
        """The pool is the catalog's dependency packages — `SqliteBundleReader.packages()`
        filters `__project__` OUT, so the fixture's `packages=["fastapi"]` row on tooling is
        what makes the positive half true; without it no selectbox renders at all and the
        negative half would prove nothing."""
        at = _run(_app())
        package = at.selectbox(key="scope_picker_package")
        assert list(package.options) == ["All packages", "fastapi"]
        at.radio(key="scope_picker_code").set_value(ScopeCode.OWN)
        _run(at)
        assert not any(s.key == "scope_picker_package" for s in at.selectbox)

    def test_a_hidden_package_control_keeps_the_chosen_package(self, workspace):
        """Project-only code hides the Package selectbox; a hidden control must report the
        state's package, not "All packages", or switching back silently drops the choice."""
        at = _run(_app())
        at.selectbox(key="scope_picker_package").set_value("fastapi")
        _run(at)
        at.button(key="scope_picker_use").click()
        _run(at)
        at.radio(key="scope_picker_code").set_value(ScopeCode.OWN)
        _run(at)
        at.button(key="scope_picker_use").click()  # "Use these" with the control hidden
        _run(at)
        assert at.session_state[STRIP_STATE_KEY].more.package == "fastapi"
        at.radio(key="scope_picker_code").set_value(ScopeCode.ALL)
        _run(at)
        assert at.selectbox(key="scope_picker_package").value == "fastapi"

    def test_more_values_reach_the_strip_state(self, workspace):
        """ "Use these" carries the "More" values; a picker that only wrote targets fails."""
        at = _run(_app())
        at.radio(key="scope_picker_code").set_value(ScopeCode.DEPS)
        _run(at)
        at.selectbox(key="scope_picker_package").set_value("fastapi")
        _run(at)
        at.button(key="scope_picker_use").click()
        _run(at)
        more = at.session_state[STRIP_STATE_KEY].more
        assert more.code is ScopeCode.DEPS and more.package == "fastapi"

    def test_branch_pills_render_only_when_advertised(self, workspace):
        """The U1 inactive pin: the same page, U1 fake capabilities, pills over the
        PICKABLE names defaulting to the target's branch."""
        at = _run(_app(U1, scope_strip=_strip(TOOLING)))
        pills = at.pills(key="scope_picker_branches_tooling")
        assert list(pills.options) == ["main", "develop"]
        assert list(pills.value) == ["main"]
        assert not any(p.key == "scope_picker_branches_backend" for p in at.pills)  # unticked
        assert f"indexed on main @{TOOLING_MAIN[:7]}" not in _captions(at)  # pills, not the caption

    def test_preview_counts_cells_and_disables_use_these_past_the_cap(self, workspace, monkeypatch):
        """AC-44 with the cap lowered to 1 by the env layer (AppConfig reads it): two
        targets exceed it, the caption names the YAML key, and nothing else changes."""
        monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__SCOPE__MAX_CELLS", "1")
        at = _run(_app(scope_strip=_strip(TOOLING, BACKEND)))
        assert (
            "Next question runs 2 searches: backend · feature/retry, tooling · main · 2 of 1"
            in _captions(at)
        )
        assert at.button(key="scope_picker_use").disabled is True
        assert (
            "2 searches is over the limit of 1 (ask_your_docs.scope.max_cells) — "
            "untick a project or a branch"
        ) in _captions(at)
        assert "2 searches per question · limit 1" in _captions(at)

    def test_preview_with_one_target_is_enabled(self, workspace):
        at = _run(_app(scope_strip=_strip(TOOLING)))
        assert f"Next question runs 1 search: tooling · main · 1 of {SHIPPED_MAX_CELLS}" in (
            _captions(at)
        )
        assert at.button(key="scope_picker_use").disabled is False

    def test_preview_with_no_target_names_the_union(self, workspace):
        at = _run(_app())
        assert f"Next question runs 1 search: all projects · 1 of {SHIPPED_MAX_CELLS}" in (
            _captions(at)
        )

    def test_reset_restores_the_yaml_values(self, workspace):
        """AC-33: starts AWAY from the shipped values (a target + code OWN)."""
        at = _run(_app(scope_strip=_strip(TOOLING)))
        at.radio(key="scope_picker_code").set_value(ScopeCode.OWN)
        _run(at)
        assert at.radio(key="scope_picker_code").value is ScopeCode.OWN
        at.button(key="scope_picker_reset").click()
        _run(at)
        assert at.radio(key="scope_picker_code").value is ScopeCode.ALL
        assert at.session_state[STRIP_STATE_KEY] == StripState()  # YAML project: any
        assert at.checkbox(key="scope_picker_project_tooling").value is False
        assert any(m.value == NO_TARGET_SENTENCE for m in at.markdown)


class TestStrip:  # AC-42, AC-50
    def test_one_target_row_2_is_editable(self, workspace):
        at = _run(_app(scope_strip=_strip(TOOLING)))
        box = at.checkbox(key=ONLY_THESE_KEY)
        assert box.value is False and box.disabled is False
        assert box.help != FORCED_HINT
        assert _chip_keys(at) == {"scope_chip_tooling_main"}
        assert any(b.key == "scope_strip_clear" for b in at.button)
        assert not any("searches per question" in c for c in _captions(at))

    def test_ticking_only_these_lives_in_the_strip_state(self, workspace):
        """The tick is STATE, not a widget key: it survives a page that never renders the
        strip (the graph page), where Streamlit drops the key. Seeded off, so a page that
        forgot the on_change write-back fails here."""
        at = _run(_app(scope_strip=_strip(TOOLING)))
        at.checkbox(key=ONLY_THESE_KEY).check()
        _run(at)
        assert at.session_state[STRIP_STATE_KEY].only_these is True
        _run(at)  # a plain rerun re-seeds the box from the state
        assert at.checkbox(key=ONLY_THESE_KEY).value is True
        at.button(key="scope_strip_clear").click()
        _run(at)
        assert at.session_state[STRIP_STATE_KEY].only_these is False  # Clear resets the tick too

    def test_two_targets_force_only_these_on_and_disabled(self, workspace):
        """AC-42: the box is seeded OFF (state AND widget key) — a mutant that drops the
        forcing keeps it off. The forced value must NOT reach the state; the hint is the
        checkbox's help text (§6.7)."""
        at = _run(_app(scope_strip=_strip(TOOLING, BACKEND), scope_strip_only_these=False))
        box = at.checkbox(key=ONLY_THESE_KEY)
        assert box.value is True and box.disabled is True
        assert box.help == FORCED_HINT
        assert at.session_state[STRIP_STATE_KEY].only_these is False  # a rule, never persisted
        assert f"2 searches per question · limit {SHIPPED_MAX_CELLS}" in _captions(at)
        assert _chip_keys(at) == {"scope_chip_backend_feature/retry", "scope_chip_tooling_main"}

    def test_removing_one_of_two_chips_keeps_the_other(self, workspace):
        """The chip removes ITS cell — a mutant clearing the whole strip passes the
        last-chip test below, not this one. Back at one cell the box shows the user's
        OWN choice (off), not the forced value the two-cell strip displayed, and the
        compiled scope is DEFAULT."""
        at = _run(_app(scope_strip=_strip(TOOLING, BACKEND)))
        at.button(key="scope_chip_backend_feature/retry").click()
        _run(at)
        state = at.session_state[STRIP_STATE_KEY]
        assert state.targets == (StripTarget("tooling", ("main",)),)
        assert _chip_keys(at) == {"scope_chip_tooling_main"}
        box = at.checkbox(key=ONLY_THESE_KEY)
        assert box.value is False and box.disabled is False
        listing = workspace_branch_listing(str(workspace))
        compiled = compile_strip_scope(
            state.targets, state.only_these, ScopeDefaultsConfig(), listing
        )
        assert compiled.kind is ScopeKind.DEFAULT
        # The picker's rows follow the strip: backend is unticked again.
        assert at.checkbox(key="scope_picker_project_backend").value is False

    def test_a_tick_made_before_the_second_cell_survives_the_2_to_1_transition(self, workspace):
        """AC-42's last clause: ticked at one cell, forced at two, back ON at one."""
        at = _run(_app(scope_strip=_strip(TOOLING, BACKEND, only_these=True)))
        assert at.checkbox(key=ONLY_THESE_KEY).disabled is True
        at.button(key="scope_chip_backend_feature/retry").click()
        _run(at)
        box = at.checkbox(key=ONLY_THESE_KEY)
        assert box.value is True and box.disabled is False

    def test_an_over_cap_strip_says_so_on_row_2(self, workspace, monkeypatch):
        """A YAML cap lowered under an existing strip (the picker and the "Keep searching"
        chip both refuse to build one): every send would hit E4, so row 2 names the
        overflow instead of printing a count silently past the limit."""
        monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__SCOPE__MAX_CELLS", "1")
        at = _run(_app(scope_strip=_strip(TOOLING, BACKEND)))
        assert (
            f"2 searches is over the limit of 1 (ask_your_docs.scope.max_cells) — {OVER_CAP_HINT}"
            in _captions(at)
        )

    def test_removing_the_last_chip_returns_to_no_target(self, workspace):
        at = _run(_app(scope_strip=_strip(TOOLING)))
        at.button(key="scope_chip_tooling_main").click()
        _run(at)
        assert at.session_state[STRIP_STATE_KEY].targets == ()
        assert any(m.value == NO_TARGET_SENTENCE for m in at.markdown)

    def test_clear_empties_the_strip_and_keeps_the_more_values(self, workspace):
        at = _run(_app(scope_strip=_strip(TOOLING, BACKEND)))
        at.radio(key="scope_picker_code").set_value(ScopeCode.OWN)
        _run(at)
        at.button(key="scope_picker_use").click()
        _run(at)
        at.button(key="scope_strip_clear").click()
        _run(at)
        state = at.session_state[STRIP_STATE_KEY]
        assert state.targets == () and _chip_keys(at) == set()
        assert state.more.code is ScopeCode.OWN  # "More" survives a Clear (§6.7)

    def test_transcript_caption_survives_a_rerun(self, workspace):
        """AC-50 (caption half): a stored caption re-renders above its question."""
        caption = "searched in: backend · feature/retry | tooling · main"
        at = _run(
            _app(
                scope_strip=_strip(TOOLING, BACKEND),
                messages=[{"role": "user", "text": "what is Foo?", "scope_caption": caption}],
                history=[],
            )
        )
        assert caption in _captions(at)


class TestFollowUpChips:  # AC-31
    """The page writes back the ``StripState`` ``apply_follow_up_chip`` returned (§6.9).

    Only "Keep searching" is exercised here: it is the one kind that grows the strip and
    the one kind that sends nothing, so the assertion needs no model and no agent stack.
    """

    def _transcript(self, chip):
        return [
            user_transcript_entry("why does retry drop?"),
            assistant_transcript_entry("it stops after three tries", "Searched …", (chip,)),
        ]

    def _keep_chip(self, project: str, branch: str) -> FollowUpChip:
        return FollowUpChip(
            kind=FollowUpKind.PIN_BRANCH,
            label=f"Keep searching {branch}",
            project=project,
            branches=(branch,),
            slice=ScopeSlice.WHOLE_BRANCH,
            question="",
        )

    def test_keep_searching_grows_the_strip_through_the_returned_state(self, workspace):
        """The chip's cell joins a strip that already holds another project: the new target
        is appended, its chip renders, and nothing was sent."""
        at = _run(
            _app(
                U1,
                scope_strip=_strip(TOOLING),
                messages=self._transcript(self._keep_chip(*BACKEND)),
                history=[],
            )
        )
        at.button(key="follow_up_1_pin_branch").click()
        _run(at)
        assert at.session_state[STRIP_STATE_KEY].targets == (
            StripTarget("tooling", ("main",)),
            StripTarget("backend", ("feature/retry",)),
        )
        assert _chip_keys(at) == {"scope_chip_tooling_main", "scope_chip_backend_feature/retry"}
        assert len(at.session_state.messages) == 2  # no question went out
        # The screen followed the grown state (§6.9, AC-31): the picker's rows are re-seeded
        # from it and row 2 is the forced box two cells imply. What re-seeds them here is the
        # chip's own st.rerun() (it drops the widget keys the interrupted run never rendered),
        # so the re-seed CONTRACT is pinned directly in test_scope_state_writes.py.
        assert at.checkbox(key="scope_picker_project_backend").value is True
        box = at.checkbox(key=ONLY_THESE_KEY)
        assert box.value is True and box.disabled is True

    def test_keep_searching_on_a_held_project_adds_one_branch(self, workspace):
        at = _run(
            _app(
                U1,
                scope_strip=_strip(("tooling", "main")),
                messages=self._transcript(self._keep_chip("tooling", "develop")),
                history=[],
            )
        )
        at.button(key="follow_up_1_pin_branch").click()
        _run(at)
        assert at.session_state[STRIP_STATE_KEY].targets == (
            StripTarget("tooling", ("main", "develop")),
        )


@pytest.fixture
def answer_spy(tmp_path, monkeypatch) -> _AgentStackSpy:
    """A page that can SEND: a configured model and a stand-in agent stack."""
    monkeypatch.setenv("PYDOCS_CONFIG", write_config(tmp_path, model="main-a"))
    spy = _AgentStackSpy()
    spy.install(monkeypatch)
    return spy


def _send(at, question: str):
    at.chat_input[0].set_value(question)
    return _run(at)


def _connected_page(**seeds):
    """A run page whose sends are refused by nothing but the question itself."""
    return _run(_app(connection_bearer=FakeBearer(), **seeds))


def _errors(at) -> list[str]:
    return [e.value for e in at.error]


class TestSticky:  # AC-41, AC-50 (the send half)
    def test_the_strip_survives_a_send_and_the_question_carries_its_caption(
        self, workspace, answer_spy
    ):
        at = _run(_app(scope_strip=_strip(TOOLING, BACKEND), connection_bearer=FakeBearer()))
        _send(at, "what is Foo?")
        caption = "searched in: backend · feature/retry | tooling · main"
        assert at.session_state.messages[0]["scope_caption"] == caption
        assert caption in _captions(at)
        assert at.session_state[STRIP_STATE_KEY] == _strip(TOOLING, BACKEND)
        _run(at)  # and a plain rerun
        assert at.session_state[STRIP_STATE_KEY] == _strip(TOOLING, BACKEND)
        assert _chip_keys(at) == {"scope_chip_backend_feature/retry", "scope_chip_tooling_main"}

    def test_a_one_target_soft_strip_sends_without_a_caption(self, workspace, answer_spy):
        """One cell with "Only these" off compiles to DEFAULT: no caption (§6.7)."""
        at = _run(_app(scope_strip=_strip(TOOLING), connection_bearer=FakeBearer()))
        _send(at, "what is Bar?")
        assert at.session_state.messages[0]["scope_caption"] == ""


TOOLING_ONE_SHOT_CAPTION = "searched in: tooling · main (from your question)"
UNKNOWN_PROJECT_REFUSAL = "No project named 'backnd'. Indexed: backend, tooling. Nothing was sent."


class TestTokens:  # AC-37, AC-38, AC-39, AC-40, AC-41 (the token half), E4, E13, E14
    """A typed ``in:`` / ``on:`` question (§6.10a). The token project is always ``tooling``:
    not the listing's first row (backend) and never the seeded strip's target, so "the
    strip was already that" and "the first project won" cannot explain a passing
    assertion. Sends go through ``answer_spy``, which records what ``ask`` received."""

    def test_an_unknown_project_refuses_the_send_and_records_nothing(self, workspace, answer_spy):
        at = _connected_page(scope_strip=_strip(BACKEND))
        _send(at, "what is Foo? in:backnd")
        assert _errors(at) == [UNKNOWN_PROJECT_REFUSAL]
        assert [i.value for i in at.info] == ["Your question (not sent): what is Foo? in:backnd"]
        assert at.session_state.messages == [] and at.session_state.history == []
        assert answer_spy.asked == [] and answer_spy.reformulated == []
        assert at.session_state[STRIP_STATE_KEY] == _strip(BACKEND)  # a refusal edits nothing

    def test_a_token_refusal_comes_before_the_connection_refusal(self, workspace, answer_spy):
        """The parse runs FIRST: on a page whose bearer cannot be fetched (its own refusal,
        design E19), a mistyped name is still answered by the token sentence and by nothing
        else — the connection refusal never renders."""
        at = _run(_app(connection_bearer=FakeBearer(fail=True, fail_message="token service down")))
        _send(at, "what is Foo? in:backnd")
        assert _errors(at) == [UNKNOWN_PROJECT_REFUSAL]
        assert answer_spy.asked == []

    def test_on_is_refused_on_u0_even_for_the_stamped_branch(self, workspace, answer_spy):
        at = _connected_page()
        _send(at, "what is Bar? in:tooling on:main")
        assert _errors(at) == [BRANCHES_NOT_CHOOSABLE]
        assert at.session_state.messages == [] and answer_spy.asked == []

    def test_five_projects_over_a_cap_of_four_are_refused_by_the_parsers_sentence(
        self, five_project_workspace, answer_spy, monkeypatch
    ):
        """E4 through typed tokens: the user-facing sentence, never the tool-result text."""
        monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__SCOPE__MAX_CELLS", "4")
        at = _connected_page()
        _send(at, "why? " + " ".join(f"in:{name}" for name in FIVE_PROJECTS))
        assert _errors(at) == [
            "That would be 5 searches; the limit is 4 (ask_your_docs.scope.max_cells). "
            "Nothing was sent."
        ]
        assert at.session_state.messages == [] and answer_spy.asked == []

    def test_a_token_question_is_stripped_sent_one_shot_and_leaves_the_strip_alone(
        self, workspace, answer_spy
    ):
        """AC-39 + AC-40 over a NON-EMPTY strip (backend, code OWN from "More"): the sent
        scope is a one-shot PIN over tooling alone carrying the strip's More value; the
        transcript shows the typed text; the strip is what it was. The token sits
        MID-sentence, so a strip that left the token's spacing behind would send a
        doubled space (the parser's whitespace contract, pinned in test_scope_tokens)."""
        strip = replace(_strip(BACKEND), more=ScopeDefaultsOverride(code=ScopeCode.OWN))
        at = _connected_page(scope_strip=strip)
        _send(at, "what is in:tooling Bar?")
        ((sent_text, sent_scope),) = answer_spy.asked
        assert sent_text == "what is Bar?" and answer_spy.reformulated == ["what is Bar?"]
        assert sent_scope == QuestionScope(
            kind=ScopeKind.PIN, cells=(ScopeCell("tooling", "main"),), code=ScopeCode.OWN
        )
        user = at.session_state.messages[0]
        assert user["text"] == "what is in:tooling Bar?"  # the ORIGINAL, token and all
        assert user["scope_caption"] == TOOLING_ONE_SHOT_CAPTION
        assert TOOLING_ONE_SHOT_CAPTION in _captions(at)
        assert at.session_state[STRIP_STATE_KEY] == strip  # sticky: untouched by the one-shot

    def test_a_pinned_strip_survives_a_token_send_and_a_rerun(self, workspace, answer_spy):
        """AC-41 across the token path: "Only these" on backend compiles to a PIN, yet the
        token question goes out over tooling ALONE (the strip's targets are not added,
        §6.10a) and the strip is the same PIN after the send and after a rerun."""
        at = _connected_page(scope_strip=_strip(BACKEND, only_these=True))
        _send(at, "what is Bar? in:tooling")
        ((_, sent_scope),) = answer_spy.asked
        assert sent_scope.kind is ScopeKind.PIN
        assert sent_scope.cells == (ScopeCell("tooling", "main"),)
        assert at.session_state[STRIP_STATE_KEY] == _strip(BACKEND, only_these=True)
        _run(at)  # and a plain rerun
        assert at.session_state[STRIP_STATE_KEY] == _strip(BACKEND, only_these=True)
        assert _chip_keys(at) == {"scope_chip_backend_feature/retry"}
        assert at.checkbox(key=ONLY_THESE_KEY).value is True

    def test_an_attached_symbol_rides_a_token_question_too(self, workspace, answer_spy):
        """AC-30 on the token path: the woven text names `mod_a.Foo` (backend), so the
        one-shot pin over tooling is grown by the attached cell — the tools can reach what
        the prompt asks about. Token cells first, as the parser built them."""
        attached = [AttachedSymbol("mod_a.Foo", "backend", "feature/retry")]
        at = _connected_page(attached=attached)
        _send(at, "what is Foo? in:tooling")
        ((sent_text, sent_scope),) = answer_spy.asked
        assert sent_text == "Regarding `mod_a.Foo`: what is Foo?"  # woven from the STRIPPED text
        assert sent_scope.kind is ScopeKind.PIN
        assert sent_scope.cells == (
            ScopeCell("tooling", "main"),
            ScopeCell("backend", "feature/retry"),
        )

    def test_a_turn_that_fails_after_the_send_quotes_the_typed_text(
        self, workspace, answer_spy, monkeypatch
    ):
        """The failure texts quote the original, tokens and all (§6.10a): ``ask`` raises
        after a token question went out, and the not-answered line shows what was typed."""

        async def failing_ask(*_args, **_kwargs):
            raise RuntimeError("upstream fell over")

        monkeypatch.setattr(agent_module, "ask", failing_ask)
        at = _connected_page()
        _send(at, "what is Foo? in:tooling")
        assert _errors(at) == ["RuntimeError: upstream fell over"]
        assert 'Your question was not answered: "what is Foo? in:tooling"' in _captions(at)

    def test_a_question_with_no_token_reaches_ask_byte_identical(self, workspace, answer_spy):
        at = _connected_page()
        _send(at, "what  is   Foo?")
        ((sent_text, sent_scope),) = answer_spy.asked
        assert sent_text == "what  is   Foo?" and answer_spy.reformulated == ["what  is   Foo?"]
        assert sent_scope.kind is ScopeKind.DEFAULT
        assert at.session_state.messages[0]["scope_caption"] == ""  # DEFAULT: no caption

    def test_tokens_disabled_sends_the_text_verbatim_and_refuses_nothing(
        self, workspace, answer_spy, monkeypatch
    ):
        monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__SCOPE__TOKENS_ENABLED", "false")
        at = _connected_page()
        _send(at, "what is Foo? in:backnd")
        ((sent_text, sent_scope),) = answer_spy.asked
        assert sent_text == "what is Foo? in:backnd"
        assert sent_scope.kind is ScopeKind.DEFAULT and sent_scope.projects() == ()  # no cell
        assert _errors(at) == []
        assert at.session_state.messages[0]["scope_caption"] == ""


class TestWorkspaceChange:  # AC-41 (the invalidation half), E12
    def test_a_project_the_new_listing_lacks_is_dropped_with_a_toast(self, workspace):
        at = _run(
            _app(
                scope_strip=_strip(("legacy", "main"), TOOLING),
                **{WORKSPACE_MARK_KEY: "/somewhere/else"},
            )
        )
        assert at.session_state[STRIP_STATE_KEY].targets == (StripTarget("tooling", ("main",)),)
        assert _toasts(at) == ["legacy · main is no longer indexed — removed from where to search"]
        assert at.session_state[WORKSPACE_MARK_KEY] == str(workspace)

    def test_on_u0_a_project_restamped_elsewhere_keeps_its_target(self, workspace):
        """Matched by project only while nothing branch-shaped is sent: the chip
        follows the new listing's stamped row, no toast."""
        at = _run(
            _app(
                scope_strip=_strip(("tooling", "develop")),
                **{WORKSPACE_MARK_KEY: "/somewhere/else"},
            )
        )
        assert at.session_state[STRIP_STATE_KEY].targets == (StripTarget("tooling", ("main",)),)
        assert _chip_keys(at) == {"scope_chip_tooling_main"}
        assert _toasts(at) == []

    def test_on_u1_a_target_is_matched_by_project_and_branch(self, workspace):
        at = _run(
            _app(
                U1,
                scope_strip=_strip(("tooling", "develop"), ("backend", "gone")),
                **{WORKSPACE_MARK_KEY: "/somewhere/else"},
            )
        )
        assert at.session_state[STRIP_STATE_KEY].targets == (StripTarget("tooling", ("develop",)),)
        assert _toasts(at) == ["backend · gone is no longer indexed — removed from where to search"]

    def test_a_first_sight_listing_refreshes_a_state_seeded_without_one(self, workspace):
        """No workspace mark at all: the graph page (or a failed scan) already seeded the
        strip against an EMPTY listing, so the chat page's FIRST sight is the first listing
        that can narrow. A first sight that skipped narrowing would leave the branchless
        target on screen as `tooling ✕` while the picker's caption named the stamped row."""
        at = _run(_app(scope_strip=StripState(targets=(StripTarget("tooling", ()),))))
        assert at.session_state[STRIP_STATE_KEY].targets == (StripTarget("tooling", ("main",)),)
        assert _chip_keys(at) == {"scope_chip_tooling_main"}
        assert _toasts(at) == []
        assert at.session_state[WORKSPACE_MARK_KEY] == str(workspace)

    def test_the_same_workspace_drops_nothing(self, workspace):
        at = _run(
            _app(
                scope_strip=_strip(("legacy", "main"), TOOLING),
                **{WORKSPACE_MARK_KEY: str(workspace)},
            )
        )
        assert at.session_state[STRIP_STATE_KEY] == _strip(("legacy", "main"), TOOLING)
        assert _toasts(at) == []


class TestSingleProjectListing:  # AC-52
    def test_no_target_then_one_target_keeps_every_strip_control(self, single_workspace):
        """Nothing collapses on a one-bundle workspace: the sentence with no target, then a
        chip with its ✕, an EDITABLE row 2 and "Clear" — and NO count line, because a
        "searches per question" caption is AC-50's two-target shape."""
        at = _run(_app())
        assert any(m.value == NO_TARGET_SENTENCE for m in at.markdown)
        assert not any(c.key == ONLY_THESE_KEY for c in at.checkbox)  # no row 2

        at = _run(_app(scope_strip=_strip(SOLO), scope_strip_only_these=False))
        assert _chip_keys(at) == {"scope_chip_solo_feature/solo"}
        assert at.button(key="scope_chip_solo_feature/solo").label == "solo · feature/solo ✕"
        box = at.checkbox(key=ONLY_THESE_KEY)
        assert box.value is False and box.disabled is False  # never forced at one cell
        assert box.help != FORCED_HINT
        assert any(b.key == "scope_strip_clear" for b in at.button)
        assert not any("searches per question" in c for c in _captions(at))

    def test_ticking_only_these_compiles_to_a_one_cell_pin(self, single_workspace):
        """The tick is the ONLY way to reach PIN here (§6.7's single-project paragraph),
        so the assertion is on the compiled kind and cells — a compiler that answered
        DEFAULT for a lone project would search the same corpus and hide the bug."""
        at = _run(_app(scope_strip=_strip(SOLO), scope_strip_only_these=False))
        at.checkbox(key=ONLY_THESE_KEY).check()
        _run(at)
        state = at.session_state[STRIP_STATE_KEY]
        assert state.only_these is True
        listing = workspace_branch_listing(str(single_workspace))
        scope = compile_strip_scope(state.targets, state.only_these, ScopeDefaultsConfig(), listing)
        assert scope.kind is ScopeKind.PIN
        assert scope.cells == (ScopeCell("solo", "feature/solo"),)
