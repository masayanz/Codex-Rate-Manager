"""Shared, DPI-independent rate meters for the panel and notification area."""
from PySide6.QtCore import Qt, QRectF, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QFont, QRadialGradient, QLinearGradient
from PySide6.QtWidgets import QWidget
from .skin_manager import default_tokens
from .resources import resource_path


def rate_color(value, tokens=None):
    return (tokens or default_tokens()).rate_color(value)


def tray_icon(five=None, weekly=None, state="CONNECTING", mode="rings", tokens=None):
    tokens = tokens or default_tokens()
    if state == "CONNECTING":
        return QIcon(str(resource_path("assets/app.ico")))
    if state in {"CONNECTING", "DISCONNECTED", "ERROR", "VERIFYING", "WAITING_RESET"}:
        five = weekly = None
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64):
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(size / 64, size / 64)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tokens.color("window_bg")))
        p.drawRoundedRect(QRectF(0, 0, 64, 64), 12, 12)
        for index, value in enumerate((five, weekly)):
            color = QColor(rate_color(value, tokens))
            if mode == "bars":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(tokens.color("disabled")))
                p.drawRoundedRect(QRectF(8, 12 + index * 25, 48, 15), 3, 3)
                p.setBrush(color)
                p.drawRoundedRect(QRectF(8, 12 + index * 25, max(4, 48 * (value or 0) / 100), 15), 3, 3)
            else:
                inset = 6 + index * 13
                rect = QRectF(inset, inset, 64 - inset * 2, 64 - inset * 2)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(tokens.color("disabled")), 8))
                p.drawEllipse(rect)
                p.setPen(QPen(color, 8))
                p.drawArc(rect, 90 * 16, -int(360 * 16 * (value / 100 if value else 1 if value is None else .035)))
        p.end()
        icon.addPixmap(pix)
    return icon


class RingMeter(QWidget):
    def __init__(self, skins=None):
        super().__init__()
        self.skins = skins
        self.value = None
        self.display_value = None
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(300)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._animate)
        if skins:
            skins.skin_changed.connect(self.apply_skin)
        self.setFixedSize(134, 134)

    def _animate(self, value):
        self.display_value = float(value)
        self.update()

    def apply_skin(self, *args):
        if self.skins and not self.skins.animation_enabled:
            self.animation.stop()
            self.display_value = self.value
        self.update()

    def set_value(self, value):
        if value == self.value:
            return
        previous = self.display_value
        self.animation.stop()
        self.value = value
        if value is not None and previous is not None and self.skins and self.skins.animation_enabled and self.isVisible():
            self.animation.setStartValue(previous)
            self.animation.setEndValue(value)
            self.animation.start()
        else:
            self.display_value = value
        self.setAccessibleName("残量未取得" if value is None else f"残り {value:g}%")
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(self.width() / 144, self.height() / 144)
        tokens = self.skins.tokens if self.skins else default_tokens()
        color = QColor(rate_color(self.display_value, tokens))
        if self.skins and self.skins.glow_enabled and tokens.effect("glow", False):
            for width in (12, 8, 4):
                glow = QColor(color)
                glow.setAlphaF(float(tokens.effect("glow_strength", .45)) / 5)
                p.setPen(QPen(glow, width))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QRectF(8, 8, 128, 128))
        gradient = QRadialGradient(65, 56, 70)
        gradient.setColorAt(0, QColor(tokens.color("panel_bg_alt")))
        gradient.setColorAt(1, QColor(tokens.color("panel_bg")))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gradient)
        p.drawEllipse(QRectF(22, 22, 100, 100))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(tokens.color("border")), 1))
        p.drawEllipse(QRectF(2, 2, 140, 140))
        for segment in range(28):
            active = self.display_value is not None and segment < round(self.display_value * 28 / 100)
            p.setPen(QPen(color if active else QColor(tokens.color("disabled")), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawArc(QRectF(11, 11, 122, 122), int((90 - segment * 360 / 28) * 16), -int(9 * 16))
        p.setPen(QColor(tokens.color("text_secondary")))
        p.setFont(QFont("Yu Gothic UI", 10))
        p.drawText(QRectF(20, 39, 104, 22), Qt.AlignmentFlag.AlignCenter, "残り")
        p.setPen(color)
        font = QFont("Segoe UI", 25)
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRectF(15, 59, 114, 47), Qt.AlignmentFlag.AlignCenter, "—" if self.display_value is None else f"{self.display_value:.0f}%")
        p.end()


class SegmentBar(QWidget):
    def __init__(self, skins=None):
        super().__init__()
        self.value = None
        self.skins = skins
        if skins:
            skins.skin_changed.connect(self.update)
        self.setFixedHeight(12)

    def set_value(self, value):
        self.value = value
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(Qt.PenStyle.NoPen)
        tokens = self.skins.tokens if self.skins else default_tokens()
        width = self.width() / 20
        for i in range(20):
            active = self.value is not None and i < round(self.value / 5)
            p.setBrush(QColor(rate_color(self.value, tokens) if active else tokens.color("disabled")))
            p.drawRoundedRect(QRectF(i * width, 1, max(1, width - 3), 10), 2, 2)
        p.end()


class CompactRateBar(QWidget):
    """Compact horizontal remaining-rate bar shared by BAR and MINI modes."""
    def __init__(self, title, skins=None, compact_kind="mini"):
        super().__init__()
        self.title = title
        self.compact_kind = compact_kind
        self.skins = skins
        self.value = None
        self.stale = False
        self.setMinimumWidth(90)
        self.setMinimumHeight(16)
        if skins:
            skins.skin_changed.connect(self.update)

    def set_value(self, value, stale=False):
        self.value = value
        self.stale = stale
        self.update()

    def paintEvent(self, event):
        tokens = self.skins.tokens if self.skins else default_tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        label_w = 24 if self.compact_kind == "bar" else 42
        percent_w = 34 if self.compact_kind == "bar" else 42
        bar = QRectF(label_w, 4, max(1, self.width() - label_w - percent_w - 6), max(8, self.height() - 8))
        p.setPen(QColor(tokens.color("text_primary")))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        p.drawText(QRectF(0, 0, label_w - 4, self.height()), Qt.AlignmentFlag.AlignVCenter, self.title)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tokens.color("disabled")))
        p.drawRoundedRect(bar, 4, 4)
        if self.value is not None:
            fill = max(2, bar.width() * max(0, min(100, float(self.value))) / 100)
            color = QColor(rate_color(self.value, tokens))
            if self.stale:
                color.setAlpha(110)
            gradient = QLinearGradient(bar.topLeft(), bar.topRight())
            lighter = QColor(color).lighter(115)
            if self.stale:
                lighter.setAlpha(110)
            gradient.setColorAt(0, lighter)
            gradient.setColorAt(1, color)
            p.setBrush(gradient)
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), fill, bar.height()), 4, 4)
        p.setPen(QColor(tokens.color("text_secondary") if self.value is None else tokens.color("text_primary")))
        p.setFont(QFont("Segoe UI", 9))
        text = "—%" if self.value is None else f"{self.value:.0f}%"
        p.drawText(QRectF(self.width() - percent_w, 0, percent_w, self.height()), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
        p.end()
