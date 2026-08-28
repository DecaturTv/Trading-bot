import json

import pytest

from tournament import promote as promote_mod
from tournament.promote import (
    _apply_env,
    _full_weights,
    _render_weight_block,
    _replace_weight_block,
    promote,
    winner_from_latest,
)
from tournament.strategies import TREND_FOLLOWER

_SCORING_SAMPLE = '''\
# a comment
DEFAULT_WEIGHTS = {
    "momentum": 1.0,
    "trend": 0.0,
    "macd": 0.0,
    "unusual_volume": 0.0,
    "gap": 0.0,
    "candlestick": 0.0,
    "congress": 0.0,
}

FOREX_WEIGHTS = {
    "momentum": 0.16,
    "trend": 0.20,
    "macd": 0.12,
    "unusual_volume": 0.0,
    "gap": 0.08,
    "candlestick": 0.12,
    "congress": 0.20,
}
'''

_ENV_SAMPLE = "CONFIDENCE_THRESHOLD=75\nKELLY_FRACTION=0.25\nSTOP_LOSS_PCT=0.50\nUNRELATED=keep\n"


def test_full_weights_expands_to_all_seven_factors_in_canonical_order():
    weights = _full_weights(TREND_FOLLOWER)
    assert list(weights) == ["momentum", "trend", "macd", "unusual_volume", "gap", "candlestick", "congress"]
    assert weights["trend"] == 0.45
    assert weights["congress"] == 0.0


def test_replace_weight_block_swaps_only_the_named_dict():
    block = _render_weight_block("DEFAULT_WEIGHTS", _full_weights(TREND_FOLLOWER), "Trend Follower")
    out = _replace_weight_block(_SCORING_SAMPLE, "DEFAULT_WEIGHTS", block)
    assert '"trend": 0.45,' in out
    assert "promoted from tournament winner 'Trend Follower'" in out
    # FOREX_WEIGHTS left untouched
    assert '"momentum": 0.16,' in out


def test_replace_weight_block_is_idempotent_across_repeated_promotes():
    block1 = _render_weight_block("DEFAULT_WEIGHTS", _full_weights(TREND_FOLLOWER), "Trend Follower")
    once = _replace_weight_block(_SCORING_SAMPLE, "DEFAULT_WEIGHTS", block1)
    twice = _replace_weight_block(once, "DEFAULT_WEIGHTS", block1)
    assert once == twice
    assert twice.count("promoted from tournament winner") == 1


def test_replace_weight_block_raises_when_var_missing():
    with pytest.raises(ValueError):
        _replace_weight_block("x = 1\n", "DEFAULT_WEIGHTS", "DEFAULT_WEIGHTS = {}")


def test_apply_env_updates_existing_keys_in_place_and_appends_new_ones():
    out = _apply_env(_ENV_SAMPLE, {"CONFIDENCE_THRESHOLD": 68, "KELLY_FRACTION": 0.3, "OPTION_TARGET_DTE": 40})
    assert "CONFIDENCE_THRESHOLD=68" in out
    assert "KELLY_FRACTION=0.3" in out
    assert "STOP_LOSS_PCT=0.50" in out  # untouched
    assert "UNRELATED=keep" in out
    assert "OPTION_TARGET_DTE=40" in out  # appended
    assert out.count("CONFIDENCE_THRESHOLD=") == 1


def test_promote_dry_run_writes_nothing(tmp_path, monkeypatch):
    scoring = tmp_path / "scoring.py"
    env = tmp_path / ".env"
    scoring.write_text(_SCORING_SAMPLE)
    env.write_text(_ENV_SAMPLE)
    monkeypatch.setattr(promote_mod, "SCORING_PATH", scoring)
    monkeypatch.setattr(promote_mod, "ENV_PATH", env)

    report = promote("equities", "Trend Follower", apply=False)

    assert "Dry run" in report
    assert scoring.read_text() == _SCORING_SAMPLE
    assert env.read_text() == _ENV_SAMPLE


def test_promote_apply_writes_both_files(tmp_path, monkeypatch):
    scoring = tmp_path / "scoring.py"
    env = tmp_path / ".env"
    scoring.write_text(_SCORING_SAMPLE)
    env.write_text(_ENV_SAMPLE)
    monkeypatch.setattr(promote_mod, "SCORING_PATH", scoring)
    monkeypatch.setattr(promote_mod, "ENV_PATH", env)

    promote("forex", "Trend Follower", apply=True)

    assert '"trend": 0.45,' in scoring.read_text()
    assert "FOREX_CONFIDENCE_THRESHOLD=86" in env.read_text()
    # equities dict untouched when promoting forex
    assert scoring.read_text().count("promoted from tournament winner") == 1


def test_winner_from_latest_reads_the_saved_board(tmp_path):
    path = tmp_path / "latest.json"
    path.write_text(json.dumps({"boards": {"equities": {"winner": "Balanced Blend"}}}))
    assert winner_from_latest("equities", path) == "Balanced Blend"


def test_winner_from_latest_raises_for_missing_market(tmp_path):
    path = tmp_path / "latest.json"
    path.write_text(json.dumps({"boards": {}}))
    with pytest.raises(KeyError):
        winner_from_latest("forex", path)
