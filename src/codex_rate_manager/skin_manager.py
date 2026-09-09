"""Loading, validating, and applying built-in and user supplied skins."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from .resources import resource_path
from .theme_tokens import ThemeTokens


DEFAULT_SKIN_ID = "neon_future"


def default_tokens() -> ThemeTokens:
    """Return independent default tokens for widgets created before a manager."""
    return ThemeTokens.from_dict(None)


def default_stylesheet() -> str:
    """Return the bundled default stylesheet (also useful for compatibility)."""
    folder = resource_path("assets/skins/neon_future/style.qss")
    try:
        return _render_qss_template(folder.read_text(encoding="utf-8"), ThemeTokens(), True, True)
    except OSError:
        return ""


class SkinManager(QObject):
    skin_changed = Signal(str)

    def __init__(self, data_dir: str | Path | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._skins: dict[str, dict[str, Any]] = {}
        self._tokens = ThemeTokens.from_dict(None)
        self._skin_id = DEFAULT_SKIN_ID
        self.glow_enabled = True
        self.animation_enabled = True
        self._load_directory(resource_path("assets/skins"), bundled=True)
        if data_dir is not None:
            user_dir = Path(data_dir)
            if user_dir.name.lower() != "skins":
                user_dir /= "skins"
            self._load_directory(user_dir, bundled=False)
        self.apply(DEFAULT_SKIN_ID)

    @property
    def tokens(self) -> ThemeTokens:
        return self._tokens

    @property
    def current_skin_id(self) -> str:
        return self._skin_id

    @property
    def current_skin(self) -> dict[str, Any]:
        return dict(self._skins.get(self._skin_id, {}))

    def available_skins(self) -> list[dict[str, str]]:
        """Return stable, UI-friendly ``[{id, name}, ...]`` entries."""
        ids = sorted(self._skins, key=lambda sid: (sid != DEFAULT_SKIN_ID, str(self._skins[sid]["name"]).casefold()))
        return [{"id": sid, "name": str(self._skins[sid]["name"])} for sid in ids]

    def rate_color(self, value: float | int | None) -> str:
        return self._tokens.rate_color(value)

    def state_color(self, state: str | None) -> str:
        return self._tokens.state_color(state)

    def apply(self, skin_id: str, glow_enabled: bool = True, animation_enabled: bool = True) -> ThemeTokens:
        """Apply a skin immediately and safely fall back to Neon Future."""
        if skin_id not in self._skins:
            skin_id = DEFAULT_SKIN_ID
        effective_id = skin_id if skin_id in self._skins else DEFAULT_SKIN_ID
        info = self._skins.get(effective_id)
        if info is None:
            return self._tokens
        self._skin_id = effective_id
        self._tokens = ThemeTokens.from_dict(info)
        self.glow_enabled = bool(glow_enabled)
        self.animation_enabled = bool(animation_enabled)
        qss = self._read_qss(info.get("style_path"))
        qss = self._render_qss(qss)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(qss)
        self.skin_changed.emit(effective_id)
        return self._tokens

    def _load_directory(self, directory: Path, bundled: bool) -> None:
        try:
            if not directory.is_dir():
                return
            folders = sorted(directory.iterdir())
        except OSError:
            return
        for folder in folders:
            if not folder.is_dir():
                continue
            try:
                with (folder / "skin.json").open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if not self._valid_skin(data):
                    continue
                style = folder / "style.qss"
                if not style.is_file():
                    continue
                data["style_path"] = style
                sid = str(data["id"])
                # Built-in IDs are reserved so an invalid or spoofed user skin
                # cannot replace the known-safe bundled theme.
                if bundled or sid not in self._skins:
                    self._skins[sid] = data
            except (OSError, ValueError, TypeError):
                continue

    @staticmethod
    def _valid_skin(data: Any) -> bool:
        if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", data["id"]):
            return False
        if not isinstance(data.get("name"), str) or not data["name"].strip():
            return False
        colors = data.get("colors", {})
        if not isinstance(colors, dict):
            return False
        if any(not isinstance(value, str) or not QColor(value).isValid() for value in colors.values()):
            return False
        layout = data.get("layout", {})
        if not isinstance(layout, dict):
            return False
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 80 for value in layout.values()):
            return False
        effects = data.get("effects", {})
        if not isinstance(effects, dict):
            return False
        if "glow" in effects and not isinstance(effects["glow"], bool):
            return False
        strength = effects.get("glow_strength", 0.45)
        return isinstance(strength, (int, float)) and not isinstance(strength, bool) and math.isfinite(strength) and 0 <= strength <= 1

    def _render_qss(self, qss: str) -> str:
        return _render_qss_template(qss, self._tokens, self.glow_enabled, self.animation_enabled)

    @staticmethod
    def _read_qss(path: Any) -> str:
        try:
            return Path(path).read_text(encoding="utf-8")
        except (OSError, TypeError):
            return ""


def _render_qss_template(qss: str, tokens: ThemeTokens, glow_enabled: bool, animation_enabled: bool) -> str:
    values = {f"@COLOR_{name.upper()}@": value for name, value in tokens.colors.items()}
    values.update({f"{{{{colors.{name}}}}}": value for name, value in tokens.colors.items()})
    values.update({f"{{{{layout.{name}}}}}": str(value) for name, value in tokens.layout_values.items()})
    values.update({"@GLOW_ENABLED@": "1" if glow_enabled else "0", "@ANIMATION_ENABLED@": "1" if animation_enabled else "0"})
    for marker, value in values.items():
        qss = qss.replace(marker, str(value))
    return qss
