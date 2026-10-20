from pathlib import Path

from asktrace.cli import main

FIX = Path(__file__).parent / "fixtures"


def test_end_to_end(tmp_path, capsys):
    db = tmp_path / "a.db"
    assert (
        main(["--db", str(db), "ingest", "claude-code", str(FIX / "claude_code_session.jsonl")])
        == 0
    )
    assert (
        main(["--db", str(db), "ingest", "chatgpt-export", str(FIX / "chatgpt_export.json")]) == 0
    )
    assert main(["--db", str(db), "tag"]) == 0
    assert main(["--db", str(db), "report"]) == 0
    assert main(["--db", str(db), "stats"]) == 0
    out = capsys.readouterr().out
    assert "ingested 3 new questions" in out
    assert "tagged 5/5 with heuristic" in out
    assert "# asktrace report" in out
    assert "questions: 5  tagged: 5" in out
