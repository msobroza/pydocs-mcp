"""Palettes and CSS for the ask-your-docs UI.

Single source of truth for two consumers: ``theme_css`` themes the chat via
injected CSS, and ``streamlit_theme_flags`` themes Streamlit's own chrome
(spinner, widgets) by passing the dark base to ``streamlit run`` on the CLI —
so there is no separate ``.streamlit/config.toml`` to keep in sync.

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
    },
    "light": {
        "bg": "#F4F6F8",
        "surface": "#FFFFFF",
        "recessed": "#E9EEF2",
        "border": "#D4DCE3",
        "text": "#17242F",
        "muted": "#5B6B79",
        "accent": "#0B9E85",
        "wash": "rgba(11, 158, 133, .10)",
    },
}


def theme_css(p: dict[str, str]) -> str:
    """The full ``<style>`` block for one palette."""
    return f"""<style>
    /* ---- base ---- */
    .stApp {{
        background: {p["bg"]};
        color: {p["text"]};
        font-family: ui-sans-serif, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }}
    a {{ color: {p["accent"]}; }}
    .block-container {{ padding-top: 2.4rem; max-width: 46rem; }}

    /* ---- hide Streamlit chrome for an app-clean surface ---- */
    [data-testid="stHeader"] {{ background: transparent; }}
    [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {{ display: none; }}

    /* ---- brand (two-tone: "docs" carries the accent) ---- */
    .brand {{ font-size: 1.9rem; font-weight: 650; letter-spacing: -.01em; color: {p["text"]}; }}
    .brand .accent {{ color: {p["accent"]}; }}
    .brand-sub {{ color: {p["muted"]}; font-size: .9rem; margin: .1rem 0 1.1rem; }}

    /* ---- sidebar ---- */
    section[data-testid="stSidebar"] {{ background: {p["surface"]}; border-right: 1px solid {p["border"]}; }}
    .side-label {{ color: {p["muted"]}; font-size: .72rem; font-weight: 600; letter-spacing: .08em;
                   text-transform: uppercase; margin: .2rem 0 .4rem; }}

    /* ---- re-theme Streamlit widgets (the CLI sets only the dark base) ---- */
    [data-testid="stWidgetLabel"] p, .stRadio p, [data-testid="stToggle"] p {{ color: {p["text"]}; }}
    [data-testid="stCaptionContainer"] {{ color: {p["muted"]} !important; }}
    [data-baseweb="select"] > div {{ background: {p["recessed"]}; border-color: {p["border"]}; color: {p["text"]}; }}
    ul[data-testid="stSelectboxVirtualDropdown"] {{ background: {p["surface"]}; }}
    ul[data-testid="stSelectboxVirtualDropdown"] li {{ background: {p["surface"]}; color: {p["text"]}; }}
    [data-testid="stBottom"], [data-testid="stBottom"] > div {{ background: {p["bg"]}; }}

    /* ---- chat: assistant reads on the canvas, user is a compact raised bubble ---- */
    [data-testid="stChatMessage"] {{ background: transparent; border: none; padding: .1rem 0; gap: .75rem; }}
    [data-testid="stChatMessage"] p, [data-testid="stChatMessage"] li {{ line-height: 1.65; }}
    [data-testid="stChatMessage"]:has([aria-label="Chat message from user"]) {{
        background: {p["surface"]};
        border: 1px solid {p["border"]};
        border-radius: 14px;
        padding: .35rem 1rem;
    }}
    [data-testid="stChatMessageAvatarAssistant"] {{ background: {p["wash"]}; color: {p["accent"]}; }}
    [data-testid="stChatMessageAvatarUser"] {{ background: {p["surface"]}; color: {p["muted"]}; }}

    /* ---- code ---- */
    code {{ color: {p["accent"]}; background: {p["wash"]}; padding: .12em .38em; border-radius: 5px; }}
    pre {{ background: {p["recessed"]} !important; border: 1px solid {p["border"]}; border-radius: 10px; }}
    pre code {{ background: transparent; padding: 0; color: {p["text"]}; }}

    /* ---- inputs + composer (accent focus ring) ---- */
    .stChatInput textarea, section[data-testid="stSidebar"] input {{ background: {p["recessed"]}; color: {p["text"]}; }}
    .stChatInput > div {{ background: {p["recessed"]}; border-color: {p["border"]}; }}
    .stChatInput textarea:focus, section[data-testid="stSidebar"] input:focus {{
        border-color: {p["accent"]} !important; box-shadow: 0 0 0 2px {p["wash"]} !important;
    }}

    /* ---- empty state ---- */
    .empty {{ border: 1px solid {p["border"]}; background: {p["surface"]}; border-radius: 16px;
              padding: 1.15rem 1.35rem; color: {p["muted"]}; }}
    .empty-title {{ color: {p["text"]}; font-weight: 600; font-size: 1.02rem; margin-bottom: .35rem; }}
    .empty .eg {{ color: {p["accent"]}; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                  font-size: .85rem; margin-top: .3rem; }}
    </style>"""


def streamlit_theme_flags() -> list[str]:
    """``streamlit run`` args that set the native dark base to match ``THEMES``."""
    d = THEMES["dark"]
    return [
        "--theme.base",
        "dark",
        "--theme.primaryColor",
        d["accent"],
        "--theme.backgroundColor",
        d["bg"],
        "--theme.secondaryBackgroundColor",
        d["surface"],
        "--theme.textColor",
        d["text"],
    ]
