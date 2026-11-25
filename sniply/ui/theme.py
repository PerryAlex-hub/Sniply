"""The app's visual design system: one set of tokens, one stylesheet
built from them, applied globally (see main.py) so every window --
main window, dialogs, menus, message boxes -- shares it rather than
each picking up whatever the OS default looks like.

Why tokens rather than colours written inline
---------------------------------------------
Qt stylesheets have no equivalent of CSS custom properties, so the
usual result is the same hex value copied into thirty rules and
drifting apart over time -- which is how this file previously ended
up with four different greys all meaning "border". Here the palette
is a Python dict and the stylesheet is a string.Template filled from
it, so a colour exists in exactly one place and the semantic names
(SURFACE vs SURFACE_RAISED, ACCENT vs DANGER) say what a value is
*for* rather than what it looks like.

string.Template (`$name`) rather than str.format or an f-string:
CSS is almost entirely braces, and `{}`-based formatting would mean
doubling every single one.

Layering
--------
Three surface levels, from the window background up through cards to
raised controls, carry the depth of the interface -- not shadows,
which Qt stylesheets can't draw. Borders are what separate regions,
so they need to be visible against their own surface; each surface
has a matching border token rather than one global border colour.
"""

from string import Template

# ---- tokens ------------------------------------------------------

TOKENS = {
    # Surfaces, darkest (window) to lightest (raised control).
    "BG": "#0e0e11",
    "SURFACE": "#17171c",
    "SURFACE_RAISED": "#1f1f26",
    "SURFACE_SUNKEN": "#0a0a0c",  # for insets: the preview, inputs, lists
    "BORDER": "#272730",
    "BORDER_STRONG": "#34343f",

    # Text, by emphasis.
    "TEXT": "#f4f4f5",
    "TEXT_SECONDARY": "#a9a9b4",
    "TEXT_MUTED": "#6f6f7b",
    "TEXT_DISABLED": "#4e4e58",

    # Accent and semantics. These are the only saturated colours in
    # the interface, which is what lets a single pill or a primary
    # button carry meaning without competing with anything.
    "ACCENT": "#4f8cff",
    "ACCENT_HOVER": "#6b9eff",
    "ACCENT_PRESSED": "#3c76e0",
    "ACCENT_DIM": "#1b2a47",
    "ACCENT_TEXT": "#ffffff",

    "SUCCESS": "#34d399",
    "SUCCESS_DIM": "#102a21",
    "WARN": "#fbbf24",
    "WARN_DIM": "#2e2411",
    "DANGER": "#f87171",
    "DANGER_DIM": "#2f1515",
    "DANGER_SOLID": "#b91c1c",
    "DANGER_SOLID_HOVER": "#dc2626",

    # Shape.
    "RADIUS_CARD": "12px",
    "RADIUS_CONTROL": "8px",
    "RADIUS_SMALL": "6px",

    "FONT": '"Segoe UI Variable Text", "Segoe UI", sans-serif',
}

#: Semantic state -> (foreground, background) for StatusPill. Kept
#: here with the rest of the palette rather than in the widget, so
#: every colour in the app is defined in one file.
PILL_COLOURS = {
    "idle": (TOKENS["TEXT_SECONDARY"], TOKENS["SURFACE_RAISED"]),
    "live": (TOKENS["SUCCESS"], TOKENS["SUCCESS_DIM"]),
    "warn": (TOKENS["WARN"], TOKENS["WARN_DIM"]),
    "error": (TOKENS["DANGER"], TOKENS["DANGER_DIM"]),
    "info": (TOKENS["ACCENT"], TOKENS["ACCENT_DIM"]),
}


_TEMPLATE = Template("""
QMainWindow, QDialog, QMessageBox { background-color: $BG; }

QWidget {
    color: $TEXT;
    font-family: $FONT;
    font-size: 13px;
}

/* ---- typography ------------------------------------------------ */

QLabel { color: $TEXT; background: transparent; }
QLabel#wordmark { font-size: 16px; font-weight: 700; letter-spacing: 0.3px; }
QLabel#sectionTitle {
    font-size: 11px;
    font-weight: 700;
    color: $TEXT_MUTED;
    letter-spacing: 0.8px;
}
QLabel#secondary { color: $TEXT_SECONDARY; }
QLabel#muted { color: $TEXT_MUTED; font-size: 11px; }
QLabel#statValue { font-size: 19px; font-weight: 700; color: $TEXT; }
QLabel#statLabel {
    font-size: 10px;
    font-weight: 600;
    color: $TEXT_MUTED;
    letter-spacing: 0.6px;
}
QLabel#emptyTitle { font-size: 14px; font-weight: 600; color: $TEXT_SECONDARY; }
QLabel#emptyBody { color: $TEXT_MUTED; }

/* ---- containers ------------------------------------------------ */

QFrame#card {
    background-color: $SURFACE;
    border: 1px solid $BORDER;
    border-radius: $RADIUS_CARD;
}

QFrame#topBar {
    background-color: $SURFACE;
    border-bottom: 1px solid $BORDER;
}

/* The thin rule between a card's header and its body. */
QFrame#divider { background-color: $BORDER; border: none; max-height: 1px; }

QFrame#banner {
    background-color: $ACCENT_DIM;
    border: 1px solid $ACCENT;
    border-radius: $RADIUS_CONTROL;
}
QFrame#banner QLabel { color: #dbe7fe; }

QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 12px; }
QSplitter::handle:vertical { height: 12px; }

/* ---- buttons --------------------------------------------------- */

QPushButton {
    background-color: $SURFACE_RAISED;
    color: $TEXT;
    border: 1px solid $BORDER_STRONG;
    border-radius: $RADIUS_CONTROL;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 500;
}
QPushButton:hover { background-color: #2a2a33; border-color: #41414e; }
QPushButton:pressed { background-color: #202028; }
QPushButton:disabled {
    background-color: #15151a;
    border-color: #1f1f26;
    color: $TEXT_DISABLED;
}
QPushButton:focus { border-color: $ACCENT; }

QPushButton#primaryButton {
    background-color: $ACCENT;
    border-color: $ACCENT;
    color: $ACCENT_TEXT;
    font-weight: 600;
}
QPushButton#primaryButton:hover { background-color: $ACCENT_HOVER; border-color: $ACCENT_HOVER; }
QPushButton#primaryButton:pressed { background-color: $ACCENT_PRESSED; }
QPushButton#primaryButton:disabled {
    background-color: #23314a;
    border-color: #23314a;
    color: #7d93b8;
}

QPushButton#dangerButton {
    background-color: transparent;
    border-color: #4d2626;
    color: $DANGER;
}
QPushButton#dangerButton:hover { background-color: $DANGER_DIM; border-color: $DANGER; }
QPushButton#dangerButton:disabled {
    background-color: transparent;
    border-color: #2a1d1d;
    color: #6b4040;
}

/* Square, borderless buttons that carry only an icon -- the top
   bar's settings button, the floating bar's expand button. */
QPushButton#iconButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: $RADIUS_SMALL;
    padding: 6px;
}
QPushButton#iconButton:hover { background-color: $SURFACE_RAISED; border-color: $BORDER; }
QPushButton#iconButton:disabled { background-color: transparent; border-color: transparent; }

/* A quiet, text-weight button for secondary actions sitting next to
   a primary one, so a row of equals doesn't read as a row of equals. */
QPushButton#subtleButton {
    background-color: transparent;
    border-color: transparent;
    color: $TEXT_SECONDARY;
}
QPushButton#subtleButton:hover { background-color: $SURFACE_RAISED; color: $TEXT; }
QPushButton#subtleButton:disabled { background-color: transparent; color: $TEXT_DISABLED; }

/* ---- inset regions --------------------------------------------- */

QLabel#previewSurface {
    background-color: $SURFACE_SUNKEN;
    border: 1px solid $BORDER;
    border-radius: $RADIUS_CONTROL;
    color: $TEXT_MUTED;
}

QFrame#statTile {
    background-color: $SURFACE_RAISED;
    border: 1px solid $BORDER;
    border-radius: $RADIUS_CONTROL;
}

QLineEdit, QSpinBox, QDoubleSpinBox {
    background-color: $SURFACE_SUNKEN;
    border: 1px solid $BORDER_STRONG;
    border-radius: $RADIUS_SMALL;
    padding: 7px 9px;
    color: $TEXT;
    selection-background-color: $ACCENT;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: $ACCENT; }

QListWidget {
    background-color: $SURFACE_SUNKEN;
    border: 1px solid $BORDER;
    border-radius: $RADIUS_CONTROL;
    padding: 6px;
    outline: none;
}
QListWidget::item { padding: 4px; border-radius: $RADIUS_SMALL; }
QListWidget::item:hover { background-color: $SURFACE_RAISED; }
QListWidget::item:selected { background-color: $ACCENT_DIM; border: 1px solid $ACCENT; }

QGroupBox {
    border: 1px solid $BORDER;
    border-radius: $RADIUS_CARD;
    margin-top: 16px;
    padding: 16px 14px 14px 14px;
    background-color: $SURFACE;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: $TEXT_SECONDARY;
}

QMenu {
    background-color: $SURFACE_RAISED;
    color: $TEXT;
    border: 1px solid $BORDER_STRONG;
    border-radius: $RADIUS_CONTROL;
    padding: 6px;
}
QMenu::item { padding: 7px 22px; border-radius: $RADIUS_SMALL; }
QMenu::item:selected { background-color: $ACCENT; color: $ACCENT_TEXT; }

QToolTip {
    background-color: $SURFACE_RAISED;
    color: $TEXT;
    border: 1px solid $BORDER_STRONG;
    border-radius: $RADIUS_SMALL;
    padding: 6px 8px;
}

/* ---- scrollbars ------------------------------------------------- */

QScrollArea { background: transparent; border: none; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical {
    background: $BORDER_STRONG;
    border-radius: 5px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: #4a4a57; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: $BORDER_STRONG;
    border-radius: 5px;
    min-width: 28px;
}
QScrollBar::handle:horizontal:hover { background: #4a4a57; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
""")

STYLESHEET = _TEMPLATE.substitute(TOKENS)
