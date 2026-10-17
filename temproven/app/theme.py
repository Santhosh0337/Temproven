"""
temproven/app/theme.py
──────────────────────
Design tokens, theme configuration, and CSS styling for the Temproven dashboard.
Minimalist, editorial, technical laboratory integrity dashboard aesthetic.
"""

from __future__ import annotations

# Color Palette Tokens
BG_COLOR = "#0E0F10"
CARD_BG = "#151719"
BORDER_COLOR = "#26292D"
TEXT_COLOR = "#E6E8EA"
MUTED_TEXT = "#8A9099"

# Status Semantic Colors
COLOR_ALERT = "#E8553D"     # Orange-red: QUARANTINED, BLOCK, NOT_COVERED, CONFLICTING, FAILED
COLOR_SUCCESS = "#5E9E7A"   # Muted green: VERIFIED, ALLOW, COVERED, PASSED
COLOR_WARNING = "#E09F3E"   # Amber/yellow: REVIEW_REQUIRED, UNKNOWN, DIFFERED
COLOR_NEUTRAL = "#4A5059"   # Muted grey: SKIPPED, DRAFT, PENDING

# Font Stack
MONO_FONT = '"IBM Plex Mono", "Cascadia Mono", Consolas, "Liberation Mono", ui-monospace, monospace'
SANS_FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'

CUSTOM_CSS = f"""
<style>
/* Global resets & typography */
html, body, [class*="css"] {{
    font-family: {SANS_FONT};
    color: {TEXT_COLOR};
}}

code, pre, .mono-text {{
    font-family: {MONO_FONT} !important;
}}

/* Top App Header Banner */
.temproven-header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    border-bottom: 1px solid {BORDER_COLOR};
    padding-bottom: 0.75rem;
    margin-bottom: 1.5rem;
}}

.temproven-title {{
    font-family: {SANS_FONT};
    font-size: 1.35rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: {TEXT_COLOR};
    margin: 0;
}}

.temproven-subtitle {{
    font-family: {MONO_FONT};
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: {MUTED_TEXT};
    margin-top: 0.2rem;
}}

.db-status-badge {{
    font-family: {MONO_FONT};
    font-size: 0.75rem;
    letter-spacing: 0.06em;
    padding: 0.25rem 0.6rem;
    border: 1px solid {BORDER_COLOR};
    background-color: {CARD_BG};
    border-radius: 2px;
}}

.status-indicator {{
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    margin-right: 6px;
    vertical-align: middle;
}}

.status-indicator.connected {{
    background-color: {COLOR_ALERT};
}}

.status-indicator.unavailable {{
    background-color: {COLOR_ALERT};
}}

/* Section container styling */
.panel-card {{
    background-color: {CARD_BG};
    border: 1px solid {BORDER_COLOR};
    border-radius: 2px;
    padding: 1.25rem;
    margin-bottom: 1.25rem;
}}

.panel-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid {BORDER_COLOR};
    padding-bottom: 0.6rem;
    margin-bottom: 1rem;
}}

.panel-num {{
    font-family: {MONO_FONT};
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: {MUTED_TEXT};
}}

.panel-title {{
    font-family: {SANS_FONT};
    font-size: 1.15rem;
    font-weight: 600;
    color: {TEXT_COLOR};
    margin: 0 0 0.25rem 0;
}}

.panel-desc {{
    font-size: 0.825rem;
    color: {MUTED_TEXT};
    margin: 0 0 1rem 0;
    line-height: 1.4;
}}

/* Micro labels */
.micro-label {{
    font-family: {MONO_FONT};
    font-size: 0.68rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: {MUTED_TEXT};
    margin-bottom: 0.2rem;
}}

.micro-value {{
    font-family: {MONO_FONT};
    font-size: 0.85rem;
    color: {TEXT_COLOR};
}}

/* Status Badge Styles */
.status-pill {{
    display: inline-block;
    font-family: {MONO_FONT};
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.05em;
    padding: 0.15rem 0.45rem;
    border-radius: 2px;
    text-transform: uppercase;
}}

.status-pill.success {{
    background-color: rgba(94, 158, 122, 0.15);
    color: {COLOR_SUCCESS};
    border: 1px solid {COLOR_SUCCESS};
}}

.status-pill.danger {{
    background-color: rgba(232, 85, 61, 0.15);
    color: {COLOR_ALERT};
    border: 1px solid {COLOR_ALERT};
}}

.status-pill.warning {{
    background-color: rgba(224, 159, 62, 0.15);
    color: {COLOR_WARNING};
    border: 1px solid {COLOR_WARNING};
}}

.status-pill.neutral {{
    background-color: rgba(74, 80, 89, 0.2);
    color: {MUTED_TEXT};
    border: 1px solid {BORDER_COLOR};
}}

/* Timeline stepper items */
.timeline-item {{
    display: flex;
    gap: 0.75rem;
    padding: 0.45rem 0;
    border-left: 1px solid {BORDER_COLOR};
    margin-left: 0.5rem;
    padding-left: 1rem;
    position: relative;
}}

.timeline-dot {{
    position: absolute;
    left: -4px;
    top: 0.7rem;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background-color: {COLOR_ALERT};
}}

.timeline-time {{
    font-family: {MONO_FONT};
    font-size: 0.72rem;
    color: {MUTED_TEXT};
    min-width: 120px;
}}

.timeline-type {{
    font-family: {MONO_FONT};
    font-size: 0.72rem;
    font-weight: 600;
    color: {TEXT_COLOR};
    min-width: 140px;
}}

.timeline-summary {{
    font-size: 0.8rem;
    color: {TEXT_COLOR};
}}

/* Publication Gate Hero Block */
.gate-hero-block {{
    border: 1px solid {BORDER_COLOR};
    border-radius: 2px;
    padding: 1.25rem;
    margin: 1rem 0;
    background-color: rgba(21, 23, 25, 0.7);
}}

.gate-hero-block.BLOCK {{
    border-color: {COLOR_ALERT};
    background-color: rgba(232, 85, 61, 0.08);
}}

.gate-hero-block.ALLOW {{
    border-color: {COLOR_SUCCESS};
    background-color: rgba(94, 158, 122, 0.08);
}}

.gate-hero-block.REVIEW_REQUIRED {{
    border-color: {COLOR_WARNING};
    background-color: rgba(224, 159, 62, 0.08);
}}

/* Dataframe clean borders */
div[data-testid="stTable"], div[data-testid="stDataFrame"] {{
    border: 1px solid {BORDER_COLOR};
    border-radius: 2px;
}}
</style>
"""
