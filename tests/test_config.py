import json

import pytest
from pydantic import ValidationError

from claude_assistant import paths
from claude_assistant.config import AppConfig, BrainConfig, SystemConfig, load_config, save_config


def test_missing_file_creates_defaults():
    result = load_config()
    assert result.warnings == []
    assert paths.config_path().exists()
    assert result.config.voice.wake_words == ["клод"]
    assert result.config.brain.autonomy == "balanced"
    assert result.config.voice.clap_enabled is False  # clap is opt-in


def test_roundtrip_persists_changes():
    config = AppConfig()
    config.user_name = "Азиз"
    config.brain.autonomy = "assisted"
    save_config(config)
    assert load_config().config.user_name == "Азиз"
    assert load_config().config.brain.autonomy == "assisted"


def test_corrupt_json_is_backed_up_and_replaced():
    paths.config_path().write_text("{not json", encoding="utf-8")
    result = load_config()
    assert result.warnings and "повреждён" in result.warnings[0]
    assert list(paths.data_dir().glob("config.invalid-*.json"))
    assert result.config == AppConfig()


def test_invalid_field_keeps_rest_of_section():
    data = AppConfig().model_dump()
    data["user_name"] = "Аня"
    data["voice"]["tts_rate"] = 99  # out of range
    data["voice"]["wake_words"] = ["джарвис"]
    paths.config_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    result = load_config()
    assert result.config.user_name == "Аня"
    assert result.config.voice.wake_words == ["джарвис"]  # valid sibling kept
    assert result.config.voice.tts_rate == 1  # default restored
    assert any("tts_rate" in w for w in result.warnings)


@pytest.mark.parametrize("hotkey", ["ctrl+alt+space", "ctrl+shift+f5", "alt+x", "win+alt+k"])
def test_valid_hotkeys(hotkey):
    assert SystemConfig(hotkey_talk=hotkey).hotkey_talk == hotkey


@pytest.mark.parametrize("hotkey", ["space", "ctrl+", "ctrl+alt+ü", "hyper+x", "ctrl+f13"])
def test_invalid_hotkeys(hotkey):
    with pytest.raises(ValidationError):
        SystemConfig(hotkey_talk=hotkey)


@pytest.mark.parametrize("rule", ['Bash(npm run dev)', 'Read', 'PowerShell(Get-Service *)'])
def test_extra_tool_rules_accepted(rule):
    assert BrainConfig(extra_allowed_tools=[rule]).extra_allowed_tools == [rule]


@pytest.mark.parametrize("rule", ['Bash(x)" --dangerously-skip-permissions', "Bash(a)\nEdit", "--foo", "Bash(')"])
def test_extra_tool_rules_reject_injection(rule):
    with pytest.raises(ValidationError):
        BrainConfig(extra_allowed_tools=[rule])


def test_sites_must_be_http():
    with pytest.raises(ValidationError):
        AppConfig(sites={"evil": "javascript:alert(1)"})
    with pytest.raises(ValidationError):
        AppConfig(sites={"local": "file:///C:/Windows"})


def test_workspaces_must_be_absolute():
    with pytest.raises(ValidationError):
        BrainConfig(workspaces=["relative/path"])


def test_model_name_validated():
    assert BrainConfig(model="sonnet").model == "sonnet"
    with pytest.raises(ValidationError):
        BrainConfig(model="opus; rm -rf /")


def test_empty_wake_words_rejected():
    from claude_assistant.config import VoiceConfig

    with pytest.raises(ValidationError):
        VoiceConfig(wake_words=["  "])
