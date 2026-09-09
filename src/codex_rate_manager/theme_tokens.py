"""Typed-ish access to the values supplied by a skin.json file."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping


DEFAULT_COLORS = {
    "window_bg": "#07111f", "panel_bg": "#0d1c2d", "panel_bg_alt": "#10243a",
    "border": "#1b4962", "border_active": "#19d9e6", "text_primary": "#e8f6ff",
    "text_secondary": "#87b6d4", "accent": "#22e6d2", "accent_secondary": "#23bde5",
    "success": "#2fd4ad", "warning": "#f4c95d", "danger": "#ff5b68", "disabled": "#425569",
}
DEFAULT_LAYOUT = {"window_radius": 16, "card_radius": 14, "card_spacing": 12, "outer_margin": 18}
DEFAULT_EFFECTS = {"glow": True, "glow_strength": 0.45}


@dataclass(frozen=True)
class ThemeTokens:
    """Skin values with safe accessors for UI code and custom skins."""

    colors: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_COLORS))
    layout_values: Mapping[str, Any] = field(default_factory=lambda: dict(DEFAULT_LAYOUT))
    effects: Mapping[str, Any] = field(default_factory=lambda: dict(DEFAULT_EFFECTS))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ThemeTokens":
        data = data or {}
        colors = deepcopy(DEFAULT_COLORS)
        colors.update({str(k): str(v) for k, v in (data.get("colors") or {}).items()})
        layout = deepcopy(DEFAULT_LAYOUT)
        layout.update(data.get("layout") or {})
        effects = deepcopy(DEFAULT_EFFECTS)
        effects.update(data.get("effects") or {})
        return cls(colors, layout, effects)

    def color(self, name: str, default: str | None = None) -> str:
        return str(self.colors.get(name, default if default is not None else ""))

    def layout(self, name: str, default: Any = None) -> Any:
        return self.layout_values.get(name, default)

    def effect(self, name: str, default: Any = None) -> Any:
        return self.effects.get(name, default)

    def rate_color(self, value: float | int | None) -> str:
        """Color a remaining-rate percentage using the product thresholds."""
        try:
            value = float(value)
        except (TypeError, ValueError):
            return self.color("disabled")
        if value <= 0:
            return self.color("danger")
        if value < 20:
            return self.color("danger")
        if value < 40:
            return self.color("warning")
        if value < 70:
            return self.color("success")
        return self.color("accent")

    def state_color(self, state: str | None) -> str:
        state = str(state or "").upper()
        if state in {"AVAILABLE", "ONLINE", "LOW"}:
            return self.color("success") if state != "LOW" else self.color("warning")
        if state in {"CONNECTING", "VERIFYING", "WAITING_RESET"}:
            return self.color("warning")
        if state in {"DISCONNECTED", "ERROR", "LIMITED_5H", "LIMITED_WEEKLY", "LIMITED_OTHER"}:
            return self.color("danger")
        return self.color("disabled")
