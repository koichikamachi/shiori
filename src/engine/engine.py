"""
しおり 判定エンジン (ShioriEngine)

data/nodes/*.json（地図）と、ユーザーの状態（現在地）を突き合わせて、
「今日やること」「今は待ってよい理由」「通り過ぎる前の注意」「生き残っている範囲」を出す。

守るべき決めごとは docs/S03_エンジンの決めごと.md にある。番号はそれに対応する。
"""
import calendar
import json
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

        if cond.days_since_death is None:
            return None
        base = today - timedelta(days=cond.days_since_death)

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
            if existing[nid].get("irreversibility") and resolved[nid] != NodeStatus.DONE
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
            "reset_by_events": reset_ids,
            "surviving_after_event": surviving,
            "mitigation_preview": mitigation_preview,
            "all_active_nodes": [existing[nid] for nid in sorted(existing)],
            "statuses": {nid: st.value for nid, st in resolved.items()},
            "urgency": urgency,
            "days_left": days_left,
            "verification_notice": (
                "この案内はまだ専門家の確認を受けていません。"
                "制度の詳細は、記載の相談先で確かめてください。"
            ),
            "executable_count": len(actionable),
        }
