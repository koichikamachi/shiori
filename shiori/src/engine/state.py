"""
ユーザーの状態と条件 (UserCondition / NodeStatus / UserJourneyState)

ここに個人情報は入らない。氏名・金額・口座番号・住所・生年月日は持たない。
死亡日そのものも持たず、経過日数の整数（days_since_death）だけを持つ。
"""
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class NodeStatus(str, Enum):
    """S03 ルール1。二値では「待っている」が表現できないので五つ持つ。"""
    NOT_READY = "NOT_READY"        # 前提が終わっていない
    READY = "READY"                # 手をつけられる
    IN_PROGRESS = "IN_PROGRESS"    # 着手したが終わっていない
    WAITING = "WAITING"            # 出したが、相手の返事を待っている
    DONE = "DONE"                  # 済んだ


class UserCondition(BaseModel):
    # 基本情報
    has_spouse: bool = Field(default=False, description="配偶者がいる")
    children_count: int = Field(default=0, description="子の人数")
    has_own_home: bool = Field(default=False, description="持ち家がある")
    has_other_real_estate: bool = Field(default=False, description="自宅以外の不動産がある")
    has_leased_land: bool = Field(default=False, description="貸している土地・借りている土地がある")
    is_business_owner: bool = Field(default=False, description="会社（法人）を営んでいる")
    is_sole_proprietor: bool = Field(default=False, description="個人で事業を営んでいる")
    has_foreign_assets: bool = Field(default=False, description="海外に資産がある")
    has_debt_or_guarantee: bool = Field(default=False, description="借入または保証がある")
    wishes_donation: bool = Field(default=False, description="家族以外・団体に渡したい")
    has_unmarried_partner: bool = Field(default=False, description="籍を入れていない相手がいる")
    has_will: bool = Field(default=False, description="遺言がある")
    has_pets: bool = Field(default=False, description="ペットがいる")
    # 金額は聞かない。あるかないかだけ。無ければ贈与も保険の枠も検討する意味がない。
    has_surplus_savings: bool = Field(default=False, description="暮らしに使わない預貯金がある")

    # 税の境界（None は「まだ分からない」）
    estate_over_basic_deduction: Optional[bool] = Field(default=None, description="財産が基礎控除を超えそう")

    # 遺族側の探索状態
    entry_found: bool = Field(default=False, description="本人が残した入口が見つかった")
    entry_locked: bool = Field(default=False, description="入口が見つかったが開けられない")

    # 遺族側の事情
    has_insurance: bool = Field(default=False, description="生命保険があると分かっている")
    had_income_to_file: bool = Field(default=False, description="故人に所得税の確定申告の義務があった")
    has_real_estate_in_estate: bool = Field(default=False, description="遺産に不動産がある")
    viewer_is_legal_heir: bool = Field(default=True, description="見ている人が相続人である")
    days_since_death: Optional[int] = Field(default=None, description="死亡から何日目か（日付そのものは持たない）")
    # 順位が繰り上がって相続人になった人は、死亡日ではなく「知った時」から数える。
    # 先生の実話：子が土壇場で放棄し、兄弟姉妹が相続人になった。その人たちの3か月は、
    # そこから始まる。省略すると死亡からの日数を使う（これまでと同じ動き）。
    days_since_known: Optional[int] = Field(default=None, description="自己のために相続の開始があったことを知った日から何日目か")
    # 相続登記の3年は、遺産分割が成立した日から数える。分割が済むまで、この時計は始まらない。
    days_since_division: Optional[int] = Field(default=None, description="遺産分割が成立した日から何日目か")
    events: List[str] = Field(default_factory=list, description="起きた出来事")

    # S03 ルール14。「いいえ」と「まだ答えていない」を分けるためだけの箱。
    # 判定には使わない。exists_if の意味は変えない。地図に霧を描くのに使う。
    unanswered: List[str] = Field(default_factory=list, description="まだ答えていない条件の名前")

    @property
    def days_for_known_clock(self) -> Optional[int]:
        """「知った時」から数える期限に使う日数。指定がなければ死亡からの日数。"""
        return self.days_since_known if self.days_since_known is not None else self.days_since_death

    def days_for_clock(self, clock: Optional[str]) -> Optional[int]:
        """
        期限をどの時計で数えるか（S03 ルール4）。
        DIVISION は肩代わりしない。分割が済むまで、その時計は始まっていない。
        始まっていない時計を死亡日で代用すると、まだ義務のない人を急かすことになる。
        """
        if clock == "KNOWN":
            return self.days_for_known_clock
        if clock == "DIVISION":
            return self.days_since_division
        return self.days_since_death

    @property
    def has_self_memo(self) -> bool:
        """導出。本人が残した一覧の中身が手元にある＝入口が見つかり、かつ開けられた。"""
        return self.entry_found and not self.entry_locked

    def to_eval_dict(self) -> Dict[str, Any]:
        d = self.model_dump()
        d["has_self_memo"] = self.has_self_memo
        # 判定に混ぜない。unanswered は表示のための箱である。
        d.pop("unanswered", None)
        return d


class UserJourneyState(BaseModel):
    """端末のなかだけに置く状態。ノードIDと状態の対応、それだけ。"""
    actor_scope: str = Field(default="SURVIVOR", description="SELF / FAMILY_CHILD / SURVIVOR")
    condition: UserCondition = Field(default_factory=UserCondition)
    statuses: Dict[str, NodeStatus] = Field(default_factory=dict)

    def status_of(self, node_id: str) -> Optional[NodeStatus]:
        return self.statuses.get(node_id)

    def mark(self, node_id: str, status: NodeStatus) -> None:
        self.statuses[node_id] = status

    @property
    def completed_node_ids(self) -> set:
        return {k for k, v in self.statuses.items() if v == NodeStatus.DONE}
