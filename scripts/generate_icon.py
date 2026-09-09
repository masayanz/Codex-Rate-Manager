"""Generate the application ICO without requiring an image editing package."""

from pathlib import Path
import struct

from PySide6.QtCore import Qt, QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage, QPainter, QPen


def main() -> None:
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#101923"))
    painter.drawRoundedRect(8, 8, 240, 240, 58, 58)

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("#36bf91"), 22, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap))
    painter.drawArc(34, 34, 188, 188, 35 * 16, 285 * 16)
    painter.setPen(QPen(QColor("#35c7e8"), 18, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap))
    painter.drawArc(74, 74, 108, 108, 45 * 16, 245 * 16)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#edbd5c"))
    painter.drawEllipse(115, 115, 26, 26)
    painter.end()

    output = Path(__file__).resolve().parents[1] / "assets" / "app.ico"
    output.parent.mkdir(parents=True, exist_ok=True)
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    chunks = []
    for size in sizes:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation).save(buffer, "PNG")
        chunks.append(bytes(buffer.data()))
    offset = 6 + 16 * len(sizes)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    for size, chunk in zip(sizes, chunks):
        header += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(chunk), offset)
        offset += len(chunk)
    output.write_bytes(header + b"".join(chunks))
    image.save(str(output.with_suffix(".png")), "PNG")



if __name__ == "__main__":
    main()
