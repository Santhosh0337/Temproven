"""
temproven/app/theme.py
──────────────────────
Design tokens, theme configuration, and CSS styling for the Temproven dashboard.
Minimalist, editorial, technical laboratory integrity dashboard aesthetic.
"""

from __future__ import annotations

# Color Palette Tokens (Refined Editorial Charcoal Aesthetic)
BG_COLOR = "#0B0D10"
CARD_BG = "#111419"
BORDER_COLOR = "#1D2128"
TEXT_COLOR = "#F3F4F6"
MUTED_TEXT = "#8C929D"

# Status Semantic Colors (Restrained Accents from Reference Images)
COLOR_ALERT = "#F95738"     # Vibrant coral red: QUARANTINED, BLOCK, NOT_COVERED, CONFLICTING, FAILED
COLOR_SUCCESS = "#22C55E"   # Muted emerald green: VERIFIED, ALLOW, COVERED, PASSED, CONNECTED
COLOR_WARNING = "#F59E0B"   # Restrained amber: REVIEW_REQUIRED, UNKNOWN, DIFFERED, ESCALATED
COLOR_NEUTRAL = "#4B5563"   # Cool slate grey: SKIPPED, DRAFT, PENDING, NOT RECORDED
COLOR_CYAN = "#00D2B4"      # Teal / cyan accent for graph selection and active links

# Font Stack
MONO_FONT = '"JetBrains Mono", "SF Mono", "Fira Code", "IBM Plex Mono", Consolas, monospace'
SANS_FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", Helvetica, Arial, sans-serif'

CUSTOM_CSS = f"""
<style>
/* Global resets & typography */
html, body, [class*="css"] {{
    font-family: {SANS_FONT};
    color: {TEXT_COLOR};
    background-color: {BG_COLOR};
}}

.stApp {{
    background-color: {BG_COLOR};
}}

.block-container {{
    padding-top: 1.25rem !important;
    padding-bottom: 2.5rem !important;
    max-width: 96% !important;
}}

code, pre, .mono-text {{
    font-family: {MONO_FONT} !important;
}}

/* Sidebar refinement */
section[data-testid="stSidebar"] {{
    background-color: #0E1014 !important;
    border-right: 1px solid {BORDER_COLOR} !important;
}}

section[data-testid="stSidebar"] .stSelectbox label {{
    font-family: {MONO_FONT} !important;
    font-size: 0.72rem !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
    color: {MUTED_TEXT} !important;
}}

/* Top App Header Banner */
.temproven-breadcrumb {{
    font-family: {MONO_FONT};
    font-size: 0.7rem;
    color: #6B7280;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 0.25rem;
}}

.temproven-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid {BORDER_COLOR};
    padding-bottom: 0.85rem;
    margin-bottom: 1.25rem;
}}

.temproven-title {{
    font-family: {SANS_FONT};
    font-size: 1.5rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    color: #FFFFFF;
    margin: 0;
}}

.temproven-subtitle {{
    font-family: {MONO_FONT};
    font-size: 0.75rem;
    color: {MUTED_TEXT};
    margin-top: 0.25rem;
    letter-spacing: 0.04em;
}}

.db-status-badge {{
    font-family: {MONO_FONT};
    font-size: 0.75rem;
    letter-spacing: 0.06em;
    padding: 0.35rem 0.75rem;
    border: 1px solid {BORDER_COLOR};
    background-color: {CARD_BG};
    border-radius: 4px;
    display: inline-flex;
    align-items: center;
}}

.status-indicator {{
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 8px;
    vertical-align: middle;
}}

.status-indicator.connected {{
    background-color: {COLOR_SUCCESS};
    box-shadow: 0 0 6px rgba(34, 197, 94, 0.4);
}}

.status-indicator.unavailable {{
    background-color: {COLOR_ALERT};
    box-shadow: 0 0 6px rgba(249, 87, 56, 0.4);
}}

/* Metric Card Strip (Reference Image 1 & 4 Style) */
.metric-strip-card {{
    background-color: {CARD_BG};
    border: 1px solid {BORDER_COLOR};
    border-radius: 4px;
    padding: 0.75rem 0.9rem;
    height: 100%;
}}

.metric-strip-label {{
    font-family: {MONO_FONT};
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: {MUTED_TEXT};
    margin-bottom: 0.35rem;
}}

.metric-strip-val {{
    font-family: {SANS_FONT};
    font-size: 1.35rem;
    font-weight: 700;
    color: #FFFFFF;
    line-height: 1.1;
}}

.metric-strip-sub {{
    font-family: {MONO_FONT};
    font-size: 0.72rem;
    margin-top: 0.3rem;
}}

/* Section container styling */
.panel-card {{
    background-color: {CARD_BG};
    border: 1px solid {BORDER_COLOR};
    border-radius: 4px;
    padding: 1.25rem;
    margin-bottom: 1.25rem;
}}

.panel-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid {BORDER_COLOR};
    padding-bottom: 0.6rem;
    margin-bottom: 0.85rem;
}}

.panel-num {{
    font-family: {MONO_FONT};
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: #6B7280;
    text-transform: uppercase;
}}

.panel-title {{
    font-family: {SANS_FONT};
    font-size: 1.15rem;
    font-weight: 600;
    color: #FFFFFF;
    margin: 0.15rem 0 0.15rem 0;
}}

.panel-desc {{
    font-size: 0.8rem;
    color: {MUTED_TEXT};
    margin: 0 0 0.85rem 0;
    line-height: 1.45;
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
    border-radius: 3px;
    text-transform: uppercase;
}}

.status-pill.success {{
    background-color: rgba(34, 197, 94, 0.12);
    color: {COLOR_SUCCESS};
    border: 1px solid rgba(34, 197, 94, 0.35);
}}

.status-pill.danger {{
    background-color: rgba(249, 87, 56, 0.12);
    color: {COLOR_ALERT};
    border: 1px solid rgba(249, 87, 56, 0.35);
}}

.status-pill.warning {{
    background-color: rgba(245, 158, 11, 0.12);
    color: {COLOR_WARNING};
    border: 1px solid rgba(245, 158, 11, 0.35);
}}

.status-pill.neutral {{
    background-color: rgba(75, 85, 99, 0.18);
    color: {MUTED_TEXT};
    border: 1px solid {BORDER_COLOR};
}}

/* Compliance Checklist Rows (Reference Image 4 Style) */
.compliance-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.5rem 0.75rem;
    background-color: {CARD_BG};
    border: 1px solid {BORDER_COLOR};
    border-radius: 3px;
    margin-bottom: 0.35rem;
}}

.compliance-info {{
    display: flex;
    flex-direction: column;
}}

.compliance-code {{
    font-family: {MONO_FONT};
    font-size: 0.75rem;
    font-weight: 600;
    color: #FFFFFF;
}}

.compliance-desc {{
    font-size: 0.72rem;
    color: {MUTED_TEXT};
    margin-top: 0.15rem;
}}

/* Publication Gate Hero Block (Reference Image 4 Style) */
.gate-hero-block {{
    border: 1px solid {BORDER_COLOR};
    border-radius: 4px;
    padding: 1.15rem;
    margin: 1rem 0;
    background-color: {CARD_BG};
}}

.gate-hero-block.BLOCK {{
    border-left: 4px solid {COLOR_ALERT};
    border-color: #2F1E20 {BORDER_COLOR} {BORDER_COLOR} {COLOR_ALERT};
    background-color: #141113;
}}

.gate-hero-block.ALLOW {{
    border-left: 4px solid {COLOR_SUCCESS};
    border-color: #1A281E {BORDER_COLOR} {BORDER_COLOR} {COLOR_SUCCESS};
    background-color: #101512;
}}

.gate-hero-block.REVIEW_REQUIRED {{
    border-left: 4px solid {COLOR_WARNING};
    border-color: #2D2418 {BORDER_COLOR} {BORDER_COLOR} {COLOR_WARNING};
    background-color: #151310;
}}

/* Dataframe clean borders */
div[data-testid="stTable"], div[data-testid="stDataFrame"] {{
    border: 1px solid {BORDER_COLOR};
    border-radius: 3px;
}}
</style>
"""
