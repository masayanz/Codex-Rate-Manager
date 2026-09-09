import json

from codex_rate_manager.resources import resource_path
from codex_rate_manager.skin_manager import SkinManager
from codex_rate_manager.theme_tokens import ThemeTokens


def test_bundled_skins_are_available_and_default_is_neon():
    manager = SkinManager()
    skins = manager.available_skins()
    assert [skin["id"] for skin in skins[:3]] == ["neon_future", "graphite", "minimal_dark"]
    assert manager.current_skin_id == "neon_future"
    assert resource_path("assets/skins/neon_future/style.qss").is_file()


def test_theme_tokens_have_safe_rate_and_state_colors():
    tokens = ThemeTokens()
    assert tokens.rate_color(0) == tokens.color("danger")
    assert tokens.rate_color(32) == tokens.color("warning")
    assert tokens.state_color("AVAILABLE") == tokens.color("success")


def test_malformed_user_skin_is_ignored_and_builtin_cannot_be_overridden(tmp_path):
    skins = tmp_path / "skins"
    malformed = skins / "broken"
    malformed.mkdir(parents=True)
    (malformed / "skin.json").write_text("{bad", encoding="utf-8")
    (malformed / "style.qss").write_text("QWidget {}", encoding="utf-8")
    duplicate = skins / "neon"
    duplicate.mkdir()
    (duplicate / "skin.json").write_text(json.dumps({"id": "neon_future", "name": "Spoof", "colors": {"accent": "#010101"}}), encoding="utf-8")
    (duplicate / "style.qss").write_text("QWidget {}", encoding="utf-8")
    manager = SkinManager(tmp_path)
    assert manager.current_skin_id == "neon_future"
    assert manager.tokens.color("accent") != "#010101"


def test_unsafe_skin_values_are_ignored(tmp_path):
    skins = tmp_path / "skins"
    bad = skins / "bad"
    bad.mkdir(parents=True)
    (bad / "skin.json").write_text(json.dumps({
        "id": "bad skin", "name": "Bad", "layout": {"card_radius": 999},
        "effects": {"glow_strength": "nan"},
    }), encoding="utf-8")
    (bad / "style.qss").write_text("QWidget {}", encoding="utf-8")
    assert all(item["id"] != "bad skin" for item in SkinManager(tmp_path).available_skins())
