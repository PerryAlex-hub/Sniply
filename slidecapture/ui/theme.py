"""A single, cohesive stylesheet applied globally (see main.py), so
every window -- the main window, dialogs, menus, message boxes --
shares one consistent dark theme instead of each picking up whatever
the OS default happens to look like.
"""

STYLESHEET = """
QMainWindow, QDialog, QMessageBox {
    background-color: #1c1c20;
}

QWidget {
    color: #e4e4e7;
    font-family: "Segoe UI", sans-serif;
    font-size: 13px;
}

QGroupBox {
    border: 1px solid #323238;
    border-radius: 10px;
    margin-top: 16px;
    padding: 14px 12px 12px 12px;
    background-color: #242428;
    font-weight: 600;
    font-size: 13px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #a1a1aa;
}

QPushButton {
    background-color: #313138;
    color: #e4e4e7;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    padding: 7px 14px;
}

QPushButton:hover {
    background-color: #3c3c44;
}

QPushButton:pressed {
    background-color: #28282d;
}

QPushButton:disabled {
    color: #68686d;
    background-color: #28282c;
    border-color: #2c2c30;
}

QPushButton#primaryButton {
    background-color: #3b82f6;
    border-color: #3b82f6;
    color: white;
    font-weight: 600;
}
QPushButton#primaryButton:hover { background-color: #2f6fe0; }
QPushButton#primaryButton:pressed { background-color: #2a60c4; }
QPushButton#primaryButton:disabled { background-color: #24436e; border-color: #24436e; color: #96b3db; }

QPushButton#dangerButton {
    background-color: #7f1d1d;
    border-color: #7f1d1d;
    color: #fee2e2;
}
QPushButton#dangerButton:hover { background-color: #991b1b; }
QPushButton#dangerButton:disabled { background-color: #3a2323; border-color: #3a2323; color: #9b7474; }

QLabel { color: #d4d4d8; }
QLabel#heading { font-size: 15px; font-weight: 700; color: #ffffff; }
QLabel#muted { color: #9ca3af; font-size: 11px; }
QLabel#banner {
    background-color: #1d2b45;
    border: 1px solid #3b82f6;
    border-radius: 8px;
    color: #d6e4fb;
    padding: 12px 16px;
    font-size: 13px;
}

QLineEdit, QSpinBox, QDoubleSpinBox {
    background-color: #18181b;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    padding: 5px 8px;
    color: #e4e4e7;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #3b82f6;
}

QListWidget {
    background-color: #18181b;
    border: 1px solid #2c2c30;
    border-radius: 8px;
    padding: 4px;
}
QListWidget::item { padding: 4px; border-radius: 6px; }
QListWidget::item:selected { background-color: #3b82f6; }

QMenu {
    background-color: #242428;
    color: #e4e4e7;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item { padding: 6px 20px; border-radius: 4px; }
QMenu::item:selected { background-color: #3b82f6; }

QScrollBar:vertical {
    background: transparent;
    width: 10px;
}
QScrollBar::handle:vertical {
    background: #3f3f46;
    border-radius: 5px;
    min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
"""
