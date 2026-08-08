from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from vlog_editor.cli import dispatch
from vlog_editor.project import create_episode


def test_run_stops_after_dashboard_for_review(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    episode = create_episode(tmp_path / "trip")
    calls: list[str] = []
    monkeypatch.setattr(
        "vlog_editor.cli.analyze_episode",
        lambda selected, force=False: calls.append(f"analyze:{force}"),
    )
    monkeypatch.setattr(
        "vlog_editor.cli.create_plan",
        lambda selected: calls.append("plan"),
    )
    monkeypatch.setattr(
        "vlog_editor.cli.generate_dashboard",
        lambda selected, open_browser=True: calls.append(f"preview:{open_browser}"),
    )
    result = dispatch(
        Namespace(command="run", episode=str(episode.root), force=False, no_open=True)
    )
    assert result == 0
    assert calls == ["analyze:False", "plan", "preview:False"]
    assert "Stopped for review" in capsys.readouterr().out
