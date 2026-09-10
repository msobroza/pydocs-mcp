"""Palettes and CSS for the ask-your-docs UI.

Single source of truth for two consumers: ``streamlit_theme_flags`` hands BOTH
palettes to Streamlit's native ``[theme.light]`` / ``[theme.dark]`` sections on
the ``streamlit run`` CLI (so there is no separate ``.streamlit/config.toml`` to
keep in sync), and ``theme_css`` adds the brand/accent touches on top.

The viewer switches between the two with Streamlit's own main menu (System /
Light / Dark). Streamlit cannot switch its theme from Python, which is why an
in-app toggle over a pinned dark base left Light mode unreadable (0.6.1).

App-UI style: calm surfaces, one accent (teal). Answers read directly on the
canvas; the user's turn is set apart by elevation, not a colored border.
"""

from __future__ import annotations

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0E141B",
        "surface": "#161E27",
        "recessed": "#0A0F14",
        "border": "#222D38",
        "text": "#DEE4EA",
        "muted": "#8A97A6",
        "accent": "#34D3B7",
        "wash": "rgba(52, 211, 183, .10)",
        # The activity panel's failed step and warning note; both clear 4.5:1 on bg and
        # surface (test_theme_contrast), and the state is always spelled out in words too.
        "danger": "#FF7B72",
        "warn": "#E3B341",
    },
    "light": {
        "bg": "#F4F6F8",
        "surface": "#FFFFFF",
        "recessed": "#E9EEF2",
        "border": "#D4DCE3",
        "text": "#17242F",
        "muted": "#5B6B79",
        # Darker teal than the dark-mode accent: #0B9E85 fails WCAG on light
        # backgrounds (~3.1:1) and #0B7A66 failed on its own wash chip (3.95:1 over
        # recessed). #096B5A clears 4.5:1 on every ground AND on the wash, for the
        # brand, links, active nav and inline code that render in the accent colour.
        "accent": "#096B5A",
        "wash": "rgba(9, 107, 90, .10)",
        "danger": "#B42318",
        "warn": "#8A5A00",
    },
}


def palette_for_theme_type(theme_type: str | None) -> dict[str, str]:
    """The ``THEMES`` palette for a Streamlit theme type; dark when it is unknown.

    >>> palette_for_theme_type("light") is THEMES["light"]
    True
    """
    return THEMES["light" if theme_type == "light" else "dark"]


def current_palette() -> dict[str, str]:
    """The palette matching the theme the viewer picked in Streamlit's main menu.

    ``st.context.theme.type`` is read-only and inferred from the background, so it can
    be ``None`` or lag behind on a first load or right after a switch (Streamlit issue
    #11920) — it falls back to dark, and the next rerun catches up. Only the accent
    and brand touches in ``theme_css`` depend on it; text readability never does."""
    import streamlit as st

    return palette_for_theme_type(st.context.theme.type)


# Muted text is the NATIVE text colour at this opacity — never a hard-coded grey, so it
# follows Streamlit's theme even when current_palette() lags (test_theme_css_scope pins
# that it still clears 4.5:1 in both palettes).
MUTED_TEXT_OPACITY = ".72"
# The user's bubble lifts off the canvas with a translucent neutral, not an opaque
# palette surface: its text stays native text on (nearly) the native ground.
_RAISED_LIFT = "rgba(128, 128, 128, .08)"


def theme_css(p: dict[str, str]) -> str:
    """The brand / accent / bubble ``<style>`` block for one palette.

    Only the accent, its wash, the border and the activity panel's danger / warn
    tokens appear: every text and ground colour belongs to Streamlit's native theme
    (``streamlit_theme_flags``), so readability never depends on this CSS — nor on
    ``current_palette`` guessing the right palette.

    >>> THEMES["light"]["text"] in theme_css(THEMES["light"])
    False
    """
    muted = MUTED_TEXT_OPACITY
    return f"""<style>
    /* ---- type + layout (colours are Streamlit's native theme) ---- */
    .stApp {{
        font-family: ui-sans-serif, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }}
    .block-container {{ padding-top: 2.4rem; max-width: 46rem; }}

    /* ---- hide Streamlit chrome for an app-clean surface ---- */
    [data-testid="stHeader"] {{ background: transparent; }}
    /* Hide the toolbar's chrome piecemeal, never the stToolbar container:
       stExpandSidebarButton lives inside it, and the collapsed-sidebar state
       persists across reloads — hiding the container makes a collapsed
       sidebar unrecoverable from the UI. The main menu stays visible too: it
       holds Streamlit's System / Light / Dark theme picker. */
    [data-testid="stToolbarActions"], [data-testid="stAppDeployButton"],
    [data-testid="stStatusWidget"], [data-testid="stDecoration"],
    footer {{ display: none; }}

    /* ---- brand (two-tone: "docs" carries the accent) ---- */
    .brand {{ font-size: 1.9rem; font-weight: 650; letter-spacing: -.01em; }}
    .brand .accent {{ color: {p["accent"]}; }}
    .brand-sub {{ opacity: {muted}; font-size: .9rem; margin: .1rem 0 1.1rem; }}

    /* ---- sidebar ---- */
    .side-label {{ opacity: {muted}; font-size: .72rem; font-weight: 600; letter-spacing: .08em;
                   text-transform: uppercase; margin: .2rem 0 .4rem; }}
    [data-testid="stSidebarNav"] a[aria-current="page"] span {{
        color: {p["accent"]} !important; font-weight: 600;
    }}

    /* ---- chat: assistant reads on the canvas, user is a compact raised bubble ---- */
    [data-testid="stChatMessage"] {{ background: transparent; border: none; padding: .1rem 0; gap: .75rem; }}
    [data-testid="stChatMessage"] p, [data-testid="stChatMessage"] li {{ line-height: 1.65; }}
    [data-testid="stChatMessage"]:has([aria-label="Chat message from user"]) {{
        background: {_RAISED_LIFT};
        border: 1px solid {p["border"]};
        border-radius: 14px;
        padding: .35rem 1rem;
    }}
    [data-testid="stChatMessageAvatarAssistant"] {{ background: {p["wash"]}; color: {p["accent"]}; }}

    /* ---- empty state ---- */
    .empty {{ border: 1px solid {p["border"]}; border-radius: 16px; padding: 1.15rem 1.35rem; }}
    .empty-title {{ font-weight: 600; font-size: 1.02rem; margin-bottom: .35rem; }}
    .empty .eg {{ color: {p["accent"]}; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                  font-size: .85rem; margin-top: .3rem; }}

    /* ---- activity panel (st.status + its step expanders) ---- */
    [data-testid="stChatMessage"] [data-testid="stExpander"] details {{
        border: 1px solid {p["border"]}; border-radius: 10px;
    }}
    [data-testid="stChatMessage"] [data-testid="stExpander"] summary:hover {{ color: {p["accent"]}; }}
    /* Reasoning: plain text, muted, set apart by a left rule (never markdown). */
    [class*="st-key-ayd-thinking"] [data-testid="stText"] {{
        opacity: {muted}; border-left: 3px solid {p["border"]}; padding-left: .6rem;
    }}
    /* A failed step: a danger rule AND the word "failed" in its outcome. */
    [class*="st-key-ayd-failed"] {{ border-left: 3px solid {p["danger"]}; padding-left: .5rem; }}
    [class*="st-key-ayd-failed"] [data-testid="stText"] {{ color: {p["danger"]}; }}
    [class*="st-key-ayd-warn"] [data-testid="stText"] {{ color: {p["warn"]}; }}
    </style>"""


# Streamlit native theme option -> THEMES token. Links and inline code carry the
# accent natively, so no CSS is needed for them to read in either mode.
_NATIVE_PAGE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("primaryColor", "accent"),
    ("backgroundColor", "bg"),
    ("secondaryBackgroundColor", "surface"),
    ("textColor", "text"),
    ("linkColor", "accent"),
    ("codeTextColor", "accent"),
    ("codeBackgroundColor", "recessed"),
    ("borderColor", "border"),
)
# The sidebar is raised (surface) and its inputs sit recessed, as on the page.
_NATIVE_SIDEBAR_OPTIONS: tuple[tuple[str, str], ...] = (
    ("backgroundColor", "surface"),
    ("secondaryBackgroundColor", "recessed"),
)


def _native_option_flags(
    section: str, options: tuple[tuple[str, str], ...], palette: dict[str, str]
) -> list[str]:
    """``["--<section>.<option>", "<colour>", ...]`` for one theme config section."""
    return [arg for option, token in options for arg in (f"--{section}.{option}", palette[token])]


def streamlit_theme_flags() -> list[str]:
    """``streamlit run`` args that register BOTH palettes as Streamlit's native themes.

    No ``--theme.base`` and no top-level ``--theme.*``: either would pin one look for
    both modes. With ``[theme.light]`` and ``[theme.dark]`` set, Streamlit's main menu
    offers System / Light / Dark itself.

    >>> "--theme.light.backgroundColor" in streamlit_theme_flags()
    True
    """
    flags: list[str] = []
    for variant, palette in THEMES.items():
        flags += _native_option_flags(f"theme.{variant}", _NATIVE_PAGE_OPTIONS, palette)
        flags += _native_option_flags(f"theme.{variant}.sidebar", _NATIVE_SIDEBAR_OPTIONS, palette)
    return flags
