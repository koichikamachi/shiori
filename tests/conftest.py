"""
テスト共通の下ごしらえ。

検証済アーカイブ（server.ARCHIVE_DIR）は、本線の照合が成功するたびに書かれる。
テストが本物の data/archive を汚したり、逆に開発機に残った記録を読んで
結果が変わったりしないよう、テストごとに一時フォルダへ向け直す。
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_archive(tmp_path, monkeypatch):
    try:
        import server
    except Exception:
        yield
        return
    monkeypatch.setattr(server, "ARCHIVE_DIR", tmp_path / "archive")
    yield
