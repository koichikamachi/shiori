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
    for s in ("個別の事情に応じた法的な結論は出さない",
              "税額や財産の計算は行わない",
              "公的書類の自動作成や申請代理は行わない",
              "弁護士法・税理士法に抵触する個別相談には答えない",
              "実証プロトタイプ（PoC）",
              "法規範の遵守"):
        assert s in r.text, s


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_every_map_is_served(key):
    r = client.get(f"/map/{key}")
    assert r.status_code == 200
    assert "<svg" in r.text
    # どの地図にも、断っていない一文が付く（ルール15）
    assert "個別の法律相談や税務相談を提供するものではありません" in r.text


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


# ---- 監査ドロワー（差し込みUI） --------------------------------------------
# ドロワーは画面の外（right:-450px）に置いてあり、駅をクリックしたときだけ出る。
# だから、差し込んだスクリプトが死んでいても画面は壊れて見えない。
# 「ボタンが出ない」という形でしか気づけないので、ここで固定する。

def _drawer_script(html: str) -> str:
    """配信された HTML から、差し込んだドロワーのスクリプトだけを取り出す。"""
    import re
    m = re.search(r"<!-- Shiori Agent Inspector Drawer -->.*?<script>(.*?)</script>",
                  html, re.S)
    assert m, "ドロワーのスクリプトが配信されていない"
    return m.group(1)


def test_the_audit_drawer_is_served_with_its_button():
    """ヘッダー・根拠URLの枠・監査ボタンが、地図のページに必ず付いていること。"""
    html = client.get("/map/surv-day1").text
    for needle in ('id="inspector-drawer"',
                   "制度追従 照合エージェント",
                   'id="drawer-node-title"',
                   'id="drawer-body"',
                   'id="btn-run-check"',
                   "詳しく調べてみます",
                   'id="agent-result-area"'):
        assert needle in html, f"ドロワーの {needle} が欠けている"


def test_the_drawer_script_does_not_collide_with_the_map_script():
    """
    地図側のスクリプトとドロワーのスクリプトは別の <script> だが、
    トップレベルの const / let / function は同じグローバル字句環境を共有する。
    名前が一つでもぶつかると、**ドロワーのブロックが丸ごと構文エラーで死ぬ**。
    （実際に `const esc`（render_map_svg.py）と `function esc` がぶつかり、
    　駅をクリックしてもドロワーが出なくなった。）

    だからドロワー側は全部を無名関数に閉じ込め、
    HTML の onclick から呼ぶ二つだけを window に出す。
    """
    import re

    html = client.get("/map/surv-day1").text
    script = _drawer_script(html)

    # 1. 閉じ込められていること
    body = "\n".join(l for l in script.strip().splitlines()
                     if l.strip() and not l.strip().startswith("//"))
    assert body.startswith("(function ()"), "ドロワーのスクリプトが閉じ込められていない"
    assert body.rstrip().endswith("})();"), "無名関数が閉じられていない"

    # 2. 外に出しているのは、onclick から呼ぶ二つだけ
    exported = set(re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", script))
    assert exported == {"closeInspector", "runAgentCheck"}, \
        f"window に余計なものを出している: {sorted(exported)}"

    # 3. その二つが、地図側の宣言とぶつかっていないこと
    map_src = (ROOT / "tools" / "render_map_svg.py").read_text(encoding="utf-8")
    map_names = set(re.findall(r"\b(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)",
                               map_src))
    assert exported.isdisjoint(map_names), \
        f"地図側と名前がぶつかっている: {sorted(exported & map_names)}"

    # 4. onclick から呼んでいる名前が、ちゃんと出ていること
    called = set(re.findall(r'onclick="([A-Za-z_$][\w$]*)\(', html))
    assert called <= exported, f"onclick から呼ぶのに window に無い: {sorted(called - exported)}"


def test_the_drawer_carries_the_audit_labels_and_the_process_bar():
    """
    判定ラベルと「一次資料の照合プロセス」の段は、画面に出る言葉そのものである。
    ここが勝手に変わると、照合の見え方が崩れる。文言を固定する。
    """
    script = _drawer_script(client.get("/map/surv-day1").text)

    assert "【公的文献資料を見た結果はこうです】" in script
    for badge, note in (
        ("【要改訂】制度変更を検知", "公的発表において制度の変更が確認されたため、案内の見直しが必要です。"),
        ("【個別の確認が必要な項目です】", "各家庭の事情によって対応が分かれることがあります。"),
        ("【このことにずばり回答するような記述は見出せませんでした】", "該当する制度の明確な記述を確認できませんでした。"),
    ):
        assert badge in script, badge
        assert note in script, note

    # 整合（UNCHANGED）は、資料に当たれたことだけを言う（説明文は出さない。2026-09-24 仕様）
    assert "【資料に当たることができました】" in script

    # 確度は機械の値で持ち、画面には日本語で出す
    assert "照合確度" in script
    for ja in ("'高'", "'中'", "'低'"):
        assert ja in script, ja

    # 照合プロセスの五段
    for step in ("一次資料取得", "差分検知", "リスク評価", "パッチ起草",
                 "内部統制（七つの関門）検証通過"):
        assert step in script, step
    assert "一次資料の照合プロセス完了" in script


def test_the_process_bar_is_derived_from_the_result_not_hardcoded():
    """
    段は、実際に踏んだものだけを濃く出す。
    通っていない段まで「完了」と書いたら、固定の PASS を並べていた頃に戻る。
    """
    script = _drawer_script(client.get("/map/surv-day1").text)
    import re

    body = re.search(r"function renderProcess\(d, action\) \{(.*?)\n\}", script, re.S)
    assert body, "renderProcess が無い"
    body = body.group(1)

    # 各段が、結果のどこかを見て決まっていること
    for source in ("ev.length", "action ===", "d.risk_if_wrong",
                   "d.proposed_patch", "rej.length", "d.human_review_required"):
        assert source in body, f"{source} を見ていない"
    # 全段そろったときだけ「完了」と名乗る
    assert "done === steps.length" in body


def test_the_internal_control_panel_reads_as_a_quality_report():
    """
    関門の欄は、開発者の独白ではなく、利用者が読んで納得できる保証の言葉で書く。
    内部変数の名前や「要約は通しません」のような言い回しを出さない。
    """
    script = _drawer_script(client.get("/map/surv-day1").text)

    assert "【AIの安全性と品質の検証は内部的に確認しています】" in script
    for title in ("公式発表への参照限定", "公式原文との完全一致", "更新範囲の厳格な制限",
                  "専門家承認の必須化", "【人の確認が前提】", "手引きの更新要否"):
        assert title in script, title
    for note in ("不確かな民間情報を排除しています",
                 "AIによる勝手な要約や歪曲を防ぐため",
                 "あらかじめ定められた項目以外をAIが勝手に改ざんできないよう制限しています。",
                 "AIが自らを「検証済み」へ格上げすることを禁止しています。",
                 "AIの提案だけで手引きが書き換わることはありません。人が目視で確認してから反映します。",
                 "修正案の作成は不要と判断しました。"):
        assert note in script, note

    # まとめの一文
    assert "すべての安全基準（内部統制）に適合しています。" in script
    assert "安全基準に抵触したため、AIの提案を取り下げました。" in script

    # 開発者の独白は残っていないこと
    for gone in ("要約は通しません", "人が見るまで、手引きは書き換わりません",
                 "チェックで取り下げたものはありません", "内部統制・七つの関門チェック",
                 "ここへ上げられるのは人だけです", "あらかじめ許した範囲の中にあります"):
        assert gone not in script, gone
