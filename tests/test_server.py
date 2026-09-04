"""
地図を配るサーバが、地図と同じ約束を守っているかを見る。

画面のためのテストは `test_render_map.py` にある。
ここで見るのは、**Cloud Run のうえに置いたときに壊れる種類のこと**である。
    ・場面がぜんぶ入口に並んでいるか
    ・存在しない場面で落ちないか
    ・状態を保存していないか（Cookie を出していないか）
    ・断らずに済ませていないか（免責の一文が必ず付くか）
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

fastapi = pytest.importorskip("fastapi", reason="requirements-web.txt を入れると走る")
from fastapi.testclient import TestClient          # noqa: E402

from server import app                             # noqa: E402
from src.main import SCENARIOS                     # noqa: E402

client = TestClient(app)


def test_healthz():
    """Cloud Run が生きているかを見るところ。"""
    r = client.get("/healthz")
    assert r.status_code == 200 and r.text == "ok"


def test_index_lists_every_scenario():
    """入口に、用意した場面がひとつ残らず並ぶこと。"""
    r = client.get("/")
    assert r.status_code == 200
    for key, sc in SCENARIOS.items():
        assert f'href="/map/{key}' in r.text, f"{key} が入口に無い"
        assert sc["title"] in r.text


def test_index_says_what_the_app_does_not_do():
    """
    しないことを、入口で書く。
    免責は「責任を負いません」ではなく「これは何であって何でないか」である。
    """
    r = client.get("/")
    for s in ("あなたの場合の結論は出さない",
              "金額を計算しない",
              "書類を作らない",
              "法律相談・税務相談に答えない",
              "試作品",
              "研究途上"):
        assert s in r.text, s


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_every_map_is_served(key):
    r = client.get(f"/map/{key}")
    assert r.status_code == 200
    assert "<svg" in r.text
    # どの地図にも、断っていない一文が付く（ルール15）
    assert "個別の法律相談や税務相談ではありません" in r.text


def test_unknown_scenario_is_a_plain_404():
    """知らない場面を渡されても、落ちずに404を返す。"""
    r = client.get("/map/no-such-scenario")
    assert r.status_code == 404


def test_nothing_is_stored_about_the_visitor():
    """
    保存しない。Cookie を出さない。ログインを求めない。
    書き込む先が無いから、インスタンスが消えても失うものが無い。
    """
    for path in ("/", "/map/surv-day1", "/healthz"):
        r = client.get(path)
        assert "set-cookie" not in {k.lower() for k in r.headers}, path


def test_the_date_comes_from_the_url_not_from_the_server():
    """
    「今日」をURLで受ける。サーバは何も覚えない。
    同じURLを二度開けば、同じ地図が出る（決定論）。
    """
    a = client.get("/map/surv-day10-nomemo?today=2026-09-05").text
    b = client.get("/map/surv-day10-nomemo?today=2026-09-05").text
    assert a == b
    assert "2026-09-05 時点" in a
    c = client.get("/map/surv-day10-nomemo?today=2026-12-01").text
    assert a != c, "日付を変えても同じ絵が出ている"


def test_a_broken_date_does_not_break_the_page():
    """おかしな日付を渡されても、落とさずに今日で描く。"""
    r = client.get("/map/surv-day1?today=abc")
    assert r.status_code == 200


def test_the_docker_setup_is_there_and_names_the_port():
    """
    Cloud Run は $PORT で待つことを求める。
    ここを外すと、上げても起動しない。
    """
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "${PORT}" in df
    assert "server:app" in df
    req = (ROOT / "requirements-web.txt").read_text(encoding="utf-8")
    for pkg in ("fastapi", "uvicorn", "pydantic"):
        assert pkg in req
    # テストや資料はイメージに入れない
    di = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for skip in ("tests/", "docs/", ".env"):
        assert skip in di, skip
