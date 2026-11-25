"""Small reusable widgets the main window is composed from.

These exist so main_window.py reads as a description of the layout
rather than three hundred lines of QVBoxLayout bookkeeping, and so a
card or a stat tile looks identical everywhere without the same
dozen setup calls being retyped (and diverging) at each use site.

Styling lives in theme.py, reached through object names -- these
classes set structure and behaviour only, with the single exception
of StatusPill, whose colour is its entire point and changes at
runtime, which a static stylesheet cannot express.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sniply.ui.theme import PILL_COLOURS, TOKENS


class Card(QFrame):
    """A titled surface. Replaces QGroupBox, whose title is drawn
    inside its own border by the style and so can't be laid out with
    anything beside it -- which every card here needs (a count, a
    pill, a button). `header_row` is where those go.
    """

    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(10)

        self.header_row = QHBoxLayout()
        self.header_row.setContentsMargins(0, 0, 0, 0)
        self.header_row.setSpacing(8)
        if title:
            label = QLabel(title.upper())
            label.setObjectName("sectionTitle")
            self.header_row.addWidget(label)
        self.header_row.addStretch()
        outer.addLayout(self.header_row)

        #: Where callers put the card's actual content.
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(10)
        outer.addLayout(self.body, stretch=1)


class StatusPill(QWidget):
    """A coloured dot plus a short label: the app's single answer to
    "is this working right now?".

    Painted rather than stylesheet-driven because it changes state at
    runtime and a Qt stylesheet can't vary a colour by widget state
    beyond the fixed pseudo-states (:hover, :disabled...). Doing it in
    paintEvent also gets a real circle, which a border-radius on a
    tiny QLabel renders inconsistently across DPI scales.
    """

    _DOT = 8
    _PAD_X = 11
    _PAD_Y = 5
    _GAP = 7

    def __init__(self, text: str = "Idle", state: str = "idle", parent: QWidget | None = None):
        super().__init__(parent)
        self._text = text
        self._state = state
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def set_status(self, text: str, state: str = "idle") -> None:
        """`state` is one of theme.PILL_COLOURS' keys."""
        if (text, state) == (self._text, self._state):
            return
        self._text = text
        self._state = state
        self.setToolTip(text)
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._text

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(
            self._PAD_X * 2 + self._DOT + self._GAP + metrics.horizontalAdvance(self._text),
            self._PAD_Y * 2 + max(metrics.height(), self._DOT),
        )

    def paintEvent(self, event) -> None:
        foreground, background = PILL_COLOURS.get(self._state, PILL_COLOURS["idle"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        radius = self.height() / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(self.rect(), radius, radius)

        centre_y = self.height() / 2
        painter.setBrush(QColor(foreground))
        painter.drawEllipse(
            int(self._PAD_X), int(centre_y - self._DOT / 2), self._DOT, self._DOT
        )

        painter.setPen(QColor(foreground))
        painter.drawText(
            self.rect().adjusted(self._PAD_X + self._DOT + self._GAP, 0, -self._PAD_X, 0),
            Qt.AlignVCenter | Qt.AlignLeft,
            self._text,
        )


class StatTile(QFrame):
    """One number with a caption. A row of these replaces the old
    column of "Saved: 0" / "Duplicates ignored: 0" labels: the numbers
    are what the user glances at mid-lecture, so they get the visual
    weight, and the words shrink to a caption.
    """

    def __init__(self, label: str, value: str = "0", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("statTile")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(1)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("statValue")
        caption = QLabel(label.upper())
        caption.setObjectName("statLabel")

        layout.addWidget(self.value_label)
        layout.addWidget(caption)

    def set_value(self, value) -> None:
        self.value_label.setText(str(value))


class EmptyState(QWidget):
    """Centred title + explanation, shown where content will appear
    once there is any. Saying what to do next in the space where the
    result will be beats an empty box that looks broken.
    """

    def __init__(self, title: str, body: str, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(6)
        layout.addStretch()

        self.title_label = QLabel(title)
        self.title_label.setObjectName("emptyTitle")
        self.title_label.setAlignment(Qt.AlignCenter)

        self.body_label = QLabel(body)
        self.body_label.setObjectName("emptyBody")
        self.body_label.setAlignment(Qt.AlignCenter)
        self.body_label.setWordWrap(True)

        layout.addWidget(self.title_label)
        layout.addWidget(self.body_label)
        layout.addStretch()

    def set_text(self, title: str, body: str) -> None:
        self.title_label.setText(title)
        self.body_label.setText(body)


def horizontal_divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Plain)
    line.setFixedHeight(1)
    return line


def wordmark() -> QWidget:
    """The app name with an accent dot -- a logo's worth of identity
    without an asset to load or scale."""
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)

    dot = QLabel()
    dot.setFixedSize(9, 9)
    dot.setStyleSheet(
        f"background-color: {TOKENS['ACCENT']}; border-radius: 4px;"
    )
    name = QLabel("Sniply")
    name.setObjectName("wordmark")

    layout.addWidget(dot)
    layout.addWidget(name)
    return holder
