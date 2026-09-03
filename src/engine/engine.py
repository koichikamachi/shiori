"""
しおり 判定エンジン (ShioriEngine)

data/nodes/*.json（地図）と、ユーザーの状態（現在地）を突き合わせて、
「今日やること」「今は待ってよい理由」「通り過ぎる前の注意」「生き残っている範囲」を出す。

守るべき決めごとは docs/S03_エンジンの決めごと.md にある。番号はそれに対応する。
"""
import calendar
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .expr import evaluate as eval_exists_if
from .state import NodeStatus, UserCondition

FILE_MAP = {
    "SELF": "nodes_self.json",
    "FAMILY_CHILD": "nodes_family.json",
    "SURVIVOR": "nodes_survivor.json",
}

# S03 ルール4。残り日数から緊急度を決める。
URGENCY_ORDER = {"OVERDUE": 0, "MUST_IMMEDIATE": 1, "SOON": 2, "CAN_WAIT": 3}
IMMEDIATE_DAYS = 14
SOON_DAYS = 45

# S03 ルール14。exists_if に出てくる条件名を拾う（霧を描くためだけに使う）
CONDITION_KEY = re.compile(r"condition\.(\w+)")

# S03 ルール7。出来事が起きたときに DONE から戻すノード。
EVENT_RESETS = {
    "NEW_WILL_FOUND": ["SURV_ESTATE_DIVISION"],
    "UNKNOWN_DEBT_FOUND": ["SURV_DECIDE_ACCEPT_OR_RENOUNCE"],
    "NEW_HEIR_FOUND": ["SURV_DETERMINE_HEIRS", "SURV_ESTATE_DIVISION"],
    "HEIR_RENOUNCED": ["SURV_DETERMINE_HEIRS", "SURV_ESTATE_DIVISION"],
    "FOREIGN_ASSET_FOUND": [],
}


def add_months(base: date, months: int) -> date:
    """月は月で数える。日数に換算しない（S03 ルール4）。月末は繰り上げずに丸める。"""
    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


class ShioriEngine:
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or (Path(__file__).resolve().parents[2] / "data" / "nodes")
        self.nodes_by_scope: Dict[str, Dict[str, Any]] = {k: {} for k in FILE_MAP}
        self._load_nodes()
        # 条件の日本語名。霧を言葉にするためだけに読む（S03 ルール14）
        self.condition_labels: Dict[str, str] = {}
        cpath = self.data_dir.parent / "schema" / "conditions.json"
        if cpath.exists():
            conds = json.loads(cpath.read_text(encoding="utf-8")).get("conditions", {})
            self.condition_labels = {k: v.get("label", k) for k, v in conds.items()}

    # ---- 読み込み ---------------------------------------------------------

    def _load_nodes(self) -> None:
        for scope, fname in FILE_MAP.items():
            path = self.data_dir / fname
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            for node in data.get("nodes", []):
                self.nodes_by_scope[scope][node["id"]] = node

    # ---- 期限 -------------------------------------------------------------

    def _days_left(self, node: Dict[str, Any], cond: UserCondition, today: date) -> Optional[int]:
        dl = node.get("deadline")
        if not dl:
            return None

        if dl.get("absolute_date"):
            y, m, d = (int(x) for x in dl["absolute_date"].split("-"))
            return (date(y, m, d) - today).days

        # どの時計で数えるか（S03 ルール4）。既定は死亡日から。
        # 「自己のために相続の開始があったことを知った時」から数える期限は clock=KNOWN。
        # 順位が繰り上がって相続人になった人は、死亡から何か月経っていても、
        # その人の3か月はそこから始まる。ここを死亡日で数えると、まだ間に合う人に
        # 「もう過ぎています」と言うことになる。
        elapsed = (cond.days_for_known_clock if dl.get("clock") == "KNOWN"
                   else cond.days_since_death)
        if elapsed is None:
            return None
        base = today - timedelta(days=elapsed)

        if dl.get("days") is not None:
            return ((base + timedelta(days=dl["days"])) - today).days
        if dl.get("months") is not None:
            return (add_months(base, dl["months"]) - today).days
        return None

    def _urgency(self, node: Dict[str, Any], days_left: Optional[int]) -> str:
        if days_left is not None:
            if days_left < 0:
                return "OVERDUE"
            if days_left <= IMMEDIATE_DAYS:
                return "MUST_IMMEDIATE"
            if days_left <= SOON_DAYS:
                return "SOON"
        if node.get("base_urgency") == "MUST_IMMEDIATE":
            return "MUST_IMMEDIATE"
        return "CAN_WAIT"

    # ---- 地図の面（S03 ルール14） -----------------------------------------

    def _deadline_label(self, node: Dict[str, Any]) -> Optional[str]:
        dl = node.get("deadline")
        if not dl:
            return None
        trigger = dl.get("trigger") or "起算"
        if dl.get("absolute_date"):
            return f"{dl['absolute_date']} まで"
        if dl.get("months") is not None:
            return f"{trigger}から {dl['months']} か月"
        if dl.get("days") is not None:
            return f"{trigger}から {dl['days']} 日"
        return None

    def _fog_keys(self, node: Dict[str, Any], cond_values: Dict[str, Any],
                  unanswered: Set[str]) -> List[str]:
        """
        この道が消えたのは「いいえ」だからか、「まだ答えていない」からか。
        判定には使わない。霧を描くためだけに使う（S03 ルール14）。
        """
        keys = set(CONDITION_KEY.findall(node.get("exists_if") or ""))
        return sorted(k for k in keys if k in unanswered or cond_values.get(k) is None)

    # ---- 本体 -------------------------------------------------------------

    def evaluate(
        self,
        actor_scope: str,
        condition: UserCondition,
        completed_node_ids: Optional[Set[str]] = None,
        statuses: Optional[Dict[str, NodeStatus]] = None,
        today: Optional[date] = None,
    ) -> Dict[str, Any]:
        if actor_scope not in self.nodes_by_scope:
            raise ValueError(f"知らない立場である: {actor_scope}")

        today = today or date.today()
        statuses = dict(statuses or {})
        for nid in (completed_node_ids or set()):
            statuses.setdefault(nid, NodeStatus.DONE)

        # S03 ルール7。出来事で VOLATILE を DONE から戻す。
        reset_ids: List[str] = []
        for ev in condition.events:
            for nid in EVENT_RESETS.get(ev, []):
                node = self.nodes_by_scope[actor_scope].get(nid)
                if node and node.get("durability") == "VOLATILE" and statuses.get(nid) == NodeStatus.DONE:
                    statuses[nid] = NodeStatus.READY
                    reset_ids.append(nid)

        cond_values = condition.to_eval_dict()

        # 1. exists_if で存在するノードを絞る（式の誤りは例外になる。黙って消さない）
        existing: Dict[str, Any] = {}
        for nid, node in self.nodes_by_scope[actor_scope].items():
            if not eval_exists_if(node.get("exists_if"), cond_values):
                continue
            # 相続人でない人に、相続人しかできないノードを見せない（S02 第三部6）
            if actor_scope == "SURVIVOR" and node.get("actor") == "LEGAL_HEIR" \
                    and condition.viewer_is_legal_heir is False:
                continue
            existing[nid] = node

        # 2. 状態を決める。S03 ルール2 = 存在しない前提は満たされたものとみなす
        resolved: Dict[str, NodeStatus] = {}
        unmet_map: Dict[str, List[str]] = {}
        for nid, node in existing.items():
            given = statuses.get(nid)
            if given in (NodeStatus.DONE, NodeStatus.IN_PROGRESS, NodeStatus.WAITING):
                resolved[nid] = given
                continue
            unmet = [
                p for p in node.get("prerequisites", [])
                if p in existing and statuses.get(p) != NodeStatus.DONE
            ]
            unmet_map[nid] = unmet
            resolved[nid] = NodeStatus.NOT_READY if unmet else NodeStatus.READY

        actionable = [
            existing[nid] for nid, st in resolved.items()
            if st in (NodeStatus.READY, NodeStatus.IN_PROGRESS)
        ]

        # 3. 期限と緊急度
        days_left = {nid: self._days_left(existing[nid], condition, today) for nid in existing}
        urgency = {nid: self._urgency(existing[nid], days_left[nid]) for nid in existing}

        # 4. 並べ方（S03 ルール5）。同点は id で必ず割るので、結果は毎回同じになる。
        def sort_key(n: Dict[str, Any]):
            nid = n["id"]
            return (
                URGENCY_ORDER[urgency[nid]],
                0 if n.get("irreversibility") else 1,
                n.get("priority", 999),
                nid,
            )

        # 赤い帯にだけ出すノードは、今日の3件に数えない（S03 ルール5）
        actionable = [n for n in actionable if not n.get("is_warning_only")]

        candidates = sorted(actionable, key=sort_key)
        if actor_scope == "SURVIVOR":
            mine = [n for n in candidates if n.get("decided_by_you", True)]
            others = [n for n in candidates if not n.get("decided_by_you", True)]
        else:
            mine, others = candidates, []

        today_actions = mine[:3]

        # 5. 期限が近いものは、前提が終わっていなくても出す（S03 ルール4の例外）
        shown = {n["id"] for n in today_actions}
        deadline_alerts = [
            {
                "node_id": nid,
                "title": existing[nid]["title"],
                "days_left": days_left[nid],
                "urgency": urgency[nid],
                "status": resolved[nid].value,
                "blocked_by": unmet_map.get(nid, []),
            }
            for nid in sorted(existing)
            if urgency[nid] in ("OVERDUE", "MUST_IMMEDIATE", "SOON")
            and days_left[nid] is not None
            and resolved[nid] != NodeStatus.DONE
            and nid not in shown
        ]
        deadline_alerts.sort(key=lambda a: (URGENCY_ORDER[a["urgency"]], a["days_left"], a["node_id"]))

        # 6. 警告は済んだものには出さない
        warnings = [
            {
                "node_id": nid,
                "title": existing[nid]["title"],
                "lost_option": existing[nid]["irreversibility"]["lost_option"],
                "warning_fact": existing[nid]["irreversibility"]["warning_fact"],
                "warning_exception": existing[nid]["irreversibility"]["warning_exception"],
            }
            for nid in sorted(existing)
            if existing[nid].get("irreversibility")
            and resolved[nid] != NodeStatus.DONE
            # 遺族はパニックのなかにいる。赤い帯は「いま関係のあるもの」に絞る。
            # 本人は元気なうちなので、まだ手を打てる不可逆こそ先に知らせる（S02 第一部8）。
            and (
                actor_scope != "SURVIVOR"
                or resolved[nid] in (NodeStatus.READY, NodeStatus.IN_PROGRESS, NodeStatus.WAITING)
                or urgency[nid] in ("OVERDUE", "MUST_IMMEDIATE", "SOON")
            )
        ]
        warnings.sort(key=lambda x: (existing[x["node_id"]].get("priority", 999), x["node_id"]))

        waiting = [
            {
                "node_id": nid,
                "title": existing[nid]["title"],
                "reason": existing[nid].get("wait", {}).get("reason", ""),
            }
            for nid in sorted(existing)
            if resolved[nid] == NodeStatus.WAITING
        ]

        surviving = []
        if reset_ids:
            surviving = [
                {"node_id": nid, "title": existing[nid]["title"]}
                for nid in sorted(existing)
                if statuses.get(nid) == NodeStatus.DONE and nid not in reset_ids
            ]

        # ---- 地図の面（S03 ルール14）------------------------------------
        # 判定は上で終わっている。ここから下は表示だけである。計算をやり直さない。
        all_nodes = self.nodes_by_scope[actor_scope]
        unanswered = set(condition.unanswered)

        # この先に何が開くか（prerequisites の逆引き。データは足していない）
        unlocks: Dict[str, List[str]] = {nid: [] for nid in all_nodes}
        for nid, node in existing.items():
            for p in node.get("prerequisites", []):
                if p in existing:
                    unlocks[p].append(existing[nid]["title"])

        absent = []
        for nid in sorted(all_nodes):
            if nid in existing:
                continue
            node = all_nodes[nid]
            fog = self._fog_keys(node, cond_values, unanswered)
            absent.append({
                "node_id": nid,
                "title": node["title"],
                "note": node.get("absent_note") or "",
                "fog": bool(fog),
                "fog_keys": fog,
                "fog_labels": [self.condition_labels.get(k, k) for k in fog],
            })
        absent_by_id = {a["node_id"]: a for a in absent}

        # 条件で消えたノードのうち、消えたことを伝える値打ちがあるもの（S02 第三部）。
        # 霧のものは入れない。「不動産がないので名義変更は要りません」と、
        # まだ分かっていないのに言い切ってしまうからである（S03 ルール14）。
        shortcuts = [
            {"node_id": a["node_id"], "note": a["note"]}
            for a in absent if a["note"] and not a["fog"]
        ]

        def map_key(nid: str):
            n = all_nodes[nid]
            return (n.get("stream", ""), n.get("priority", 999), nid)

        node_map = []
        for nid in sorted(all_nodes, key=map_key):
            node = all_nodes[nid]
            present = nid in existing
            a = absent_by_id.get(nid)
            node_map.append({
                "node_id": nid,
                "title": node["title"],
                "stream": node.get("stream", ""),
                "present": present,
                "status": resolved[nid].value if present else None,
                "urgency": urgency[nid] if present else None,
                "days_left": days_left[nid] if present else None,
                "deadline_label": self._deadline_label(node),
                # 止めているノードは「名前」で出す。idは利用者の言葉ではない。
                "blocked_by": [existing[p]["title"] for p in unmet_map.get(nid, []) if p in existing],
                "unlocks": sorted(unlocks.get(nid, [])),
                "absent_reason": (a["note"] if a else None),
                "fog": (a["fog"] if a else False),
                "fog_keys": (a["fog_keys"] if a else []),
                "verification_status": node.get("legal_metadata", {}).get("verification_status"),
            })

        # 「◯◯が分かると、この先に◯件つながります」を作るための束ね方
        fog_groups: Dict[str, Dict[str, Any]] = {}
        for a in absent:
            for k, label in zip(a["fog_keys"], a["fog_labels"]):
                g = fog_groups.setdefault(k, {"key": k, "label": label, "count": 0, "titles": []})
                g["count"] += 1
                g["titles"].append(a["title"])
        fog_groups_list = [fog_groups[k] for k in sorted(fog_groups)]

        by_stream: Dict[str, Dict[str, int]] = {}
        for nid, node in existing.items():
            s = by_stream.setdefault(node.get("stream", ""), {"total": 0, "done": 0})
            s["total"] += 1
            if resolved[nid] == NodeStatus.DONE:
                s["done"] += 1

        progress = {
            "total": len(all_nodes),
            "present": len(existing),
            "absent": len(absent),
            "fog": sum(1 for a in absent if a["fog"]),
            "done": sum(1 for st in resolved.values() if st == NodeStatus.DONE),
            "ready": sum(1 for st in resolved.values() if st == NodeStatus.READY),
            "in_progress": sum(1 for st in resolved.values() if st == NodeStatus.IN_PROGRESS),
            "waiting": sum(1 for st in resolved.values() if st == NodeStatus.WAITING),
            "not_ready": sum(1 for st in resolved.values() if st == NodeStatus.NOT_READY),
            "by_stream": dict(sorted(by_stream.items())),
        }

        mitigation_preview = []
        if actor_scope == "SELF":
            for nid in sorted(existing):
                for m in existing[nid].get("mitigates_nodes", []):
                    if statuses.get(nid) == NodeStatus.DONE:
                        mitigation_preview.append({
                            "node_id": nid,
                            "effect": m["effect"],
                            "explanation": m["explanation"],
                        })

        return {
            "actor_scope": actor_scope,
            "today_actions": today_actions,
            "handled_by_others": [{"node_id": n["id"], "title": n["title"]} for n in others],
            "deferred_count": max(0, len(mine) - 3),
            "irreversible_warnings": warnings,
            "deadline_alerts": deadline_alerts,
            "waiting": waiting,
            "not_ready_count": sum(1 for st in resolved.values() if st == NodeStatus.NOT_READY),
            "shortcuts": shortcuts,
            "reset_by_events": reset_ids,
            "surviving_after_event": surviving,
            "mitigation_preview": mitigation_preview,
            # 地図の面（S03 ルール14）。動詞（today_action）は入れない。
            "map": node_map,
            "absent": absent,
            "fog_groups": fog_groups_list,
            "progress": progress,
            "all_active_nodes": [existing[nid] for nid in sorted(existing)],
            "statuses": {nid: st.value for nid, st in resolved.items()},
            "urgency": urgency,
            "days_left": days_left,
            "verification_notice": (
                # S03 ルール15。作っているのは弁護士でも税理士でもない。線を画面に出す。
                "これは、手続の順序と期限を示す地図です。個別の法律相談や税務相談ではありません。"
                "あなたの場合にどうなるかは、記載の相談先で確かめてください。"
                "制度は変わります。多くの項目は、まだ専門家の確認を受けていません。"
            ),
            "executable_count": len(actionable),
        }
