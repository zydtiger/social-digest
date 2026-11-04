import datetime as dt
import json

import pytest

from social_digest import cli


@pytest.mark.parametrize(
    "moment,expected",
    [
        ("2026-11-09T04:00:00-05:00", ("2026-11-02", "2026-11-09")),
        ("2027-01-03T23:30:00-05:00", ("2026-12-28", "2027-01-04")),
        ("2026-03-09T04:00:00-04:00", ("2026-03-02", "2026-03-09")),
    ],
)
def test_weekly_covers_seven_complete_utc_days(moment, expected):
    bounds = cli.date_window(weekly=True, now=dt.datetime.fromisoformat(moment))
    assert tuple(day.isoformat() for day in bounds) == expected
    assert bounds[1] - bounds[0] == dt.timedelta(days=7)


def test_daily_default_and_explicit_windows_are_unchanged():
    now = dt.datetime.fromisoformat("2027-01-03T23:30:00-05:00")
    assert cli.date_window(now=now) == (dt.date(2027, 1, 3), dt.date(2027, 1, 4))
    assert cli.date_window(until=dt.date(2026, 12, 31), now=now) == (
        dt.date(2026, 12, 30),
        dt.date(2026, 12, 31),
    )
    assert cli.date_window(dt.date(2026, 12, 1), dt.date(2026, 12, 8)) == (
        dt.date(2026, 12, 1),
        dt.date(2026, 12, 8),
    )


@pytest.mark.parametrize("option", ["--since", "--until"])
def test_weekly_rejects_explicit_dates_before_loading_config(option, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "load_config", lambda *_: pytest.fail("config loaded for invalid flags")
    )
    with pytest.raises(SystemExit) as raised:
        cli.main(["run", "--weekly", option, "2026-11-01"])
    assert raised.value.code == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_notification_finds_nearest_vault_and_retains_unicode(tmp_path):
    outer = tmp_path / "Outer"
    (outer / ".obsidian").mkdir(parents=True)
    vault = outer / "My 笔记"
    (vault / ".obsidian").mkdir(parents=True)
    note = vault / "Calendar/Social Digests/2026-W45 Social Digest.md"
    note.parent.mkdir(parents=True)
    note.write_text("## Developments\n")
    assert cli.note_result(note) == {
        "vault": "My 笔记",
        "note": "Calendar/Social Digests/2026-W45 Social Digest.md",
        "summary": "Open 2026-W45 Social Digest",
    }
    alias = tmp_path / "Alias.md"
    alias.symlink_to(note)
    assert cli.note_result(alias) == cli.note_result(note)


def test_notification_is_null_without_existing_note_and_vault(tmp_path):
    note = tmp_path / "note.md"
    assert cli.note_result(note) is None
    note.write_text("A standalone Markdown note")
    assert cli.note_result(note) is None


def test_cli_passes_weekly_bounds_and_keeps_absolute_note(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "My Vault"
    (vault / ".obsidian").mkdir(parents=True)
    note = vault / "Calendar/2026-W45 Social Digest.md"
    note.parent.mkdir(parents=True)
    note.write_text("## Developments\n")
    real_window = cli.date_window
    monkeypatch.setattr(
        cli,
        "date_window",
        lambda start, end, weekly: real_window(
            start, end, weekly, now=dt.datetime(2026, 11, 9, tzinfo=dt.UTC)
        ),
    )
    config = object()
    monkeypatch.setattr(cli, "load_config", lambda *_: config)
    monkeypatch.setattr(cli, "load_env", lambda *_: None)
    calls = []

    def execute(cfg, since, until, env, **kwargs):
        calls.append((cfg, since, until, kwargs))
        return {"note": str(note), "digest": str(tmp_path / "digest.md"), "stage": "write"}

    monkeypatch.setattr(cli, "execute", execute)
    assert cli.main(["run", "--weekly"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert calls[0][:3] == (config, "2026-11-02", "2026-11-09")
    assert report["note"] == str(note)
    assert report["notification"]["vault"] == "My Vault"
    assert report["notification"]["note"] == "Calendar/2026-W45 Social Digest.md"


def test_cli_weekly_dry_run_has_no_notification_or_note_write(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda *_: object())
    monkeypatch.setattr(cli, "load_env", lambda *_: None)

    def execute(_cfg, since, until, _env, **kwargs):
        assert dt.date.fromisoformat(until) - dt.date.fromisoformat(since) == dt.timedelta(days=7)
        assert kwargs["dry_run"] is True
        return {"note": None, "stage": "cards", "digest": None}

    monkeypatch.setattr(cli, "execute", execute)
    assert cli.main(["run", "--weekly", "--dry-run"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["notification"] is None
    assert report["note"] is None
    assert not list(tmp_path.iterdir())
