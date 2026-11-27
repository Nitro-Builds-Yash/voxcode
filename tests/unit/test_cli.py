"""The `voxcode` command line (was 0% covered)."""

import json

import pytest

from tests.conftest import ScriptedProvider
from voxcode import cli
from voxcode.config.settings import settings


@pytest.fixture
def scripted(isolated_storage, monkeypatch):
    from voxcode.api import services

    monkeypatch.setattr(settings, "RERANK_ENABLED", False)
    monkeypatch.setattr(settings, "DEFAULT_PROVIDER", "scripted")
    monkeypatch.setattr(services.registry, "get_embedder", lambda: None)
    provider = ScriptedProvider(["Logout lives in app/auth.py."])
    services.registry.set_provider("scripted", provider)
    return provider


def test_index_search_and_ask(scripted, sample_repo, capsys):
    cli.main(["index", str(sample_repo)])
    assert "Indexed" in capsys.readouterr().out
    cli.main(["search", "logout", "--repo", str(sample_repo)])
    assert "app/auth.py" in capsys.readouterr().out
    cli.main(["ask", "where is logout", "--repo", str(sample_repo)])
    assert "Logout lives in app/auth.py." in capsys.readouterr().out


def test_cli_builds_the_call_graph_before_exiting(scripted, sample_repo, monkeypatch):
    """Regression: a background graph thread died with the CLI process, leaving the graph pending."""
    from voxcode.api import services

    monkeypatch.setattr(settings, "BACKGROUND_CALL_GRAPH", True)
    cli.main(["index", str(sample_repo)])
    index = services.indexer.store.load(services.repository_key(sample_repo.resolve()))
    assert index.graph_pending == []


def test_ask_reports_model_errors_without_a_traceback(scripted, sample_repo, capsys):
    from voxcode.generation.providers.base_provider import ProviderError

    def broken(prompt):
        raise ProviderError("Can't reach ollama at http://localhost:11434.")

    scripted.replies = [broken]
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["ask", "where is logout", "--repo", str(sample_repo)])
    assert exit_info.value.code == 1
    assert "voxcode: Can't reach ollama" in capsys.readouterr().err


def test_bad_repository_path_exits_2(isolated_storage, tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["search", "x", "--repo", str(tmp_path / "missing")])
    assert exit_info.value.code == 2 and "Not a directory" in capsys.readouterr().err


def test_hook_install_and_reviews(isolated_storage, tmp_path, monkeypatch, capsys):
    from voxcode.review import hooks
    from voxcode.review.store import review_store

    monkeypatch.setattr(hooks.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(review_store, "_root", tmp_path / "reviews")
    monkeypatch.setattr(review_store, "_reviews", {})
    cli.main(["hook", "install", "claude"])
    assert "Added the plan-review hook" in capsys.readouterr().out
    assert json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]["PermissionRequest"]
    cli.main(["reviews"])
    assert "No reviews yet." in capsys.readouterr().out
    review_store.create("plan", "# Plan", "Plan", "cli")
    cli.main(["reviews"])
    assert "pending" in capsys.readouterr().out
    cli.main(["hook", "uninstall", "claude"])
    assert "Removed" in capsys.readouterr().out
