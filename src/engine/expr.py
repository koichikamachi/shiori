"""
exists_if の式を読むための、小さな専用の読み取り機。

Python の eval は使わない。理由は三つある。

  1. JSONに書いてあるのは `condition.has_own_home == true && ...` という書き方であり、
     これは Python の文法ではない（true は True、&& は and）。eval に渡すと必ず失敗する。
  2. eval が失敗したときに False を返す実装にすると、書き間違いのあるノードが
     「条件を満たさなかった」ものとして黙って消える。原因を追えない事故になる。
  3. データファイルとはいえ、任意の式を実行できる仕組みを残さない。

ここで扱えるのは次だけである。

    式      := 論理和
    論理和  := 論理積 ( '||' 論理積 )*
    論理積  := 比較   ( '&&' 比較 )*
    比較    := 値 ( '=='|'!=' ) 値  |  '(' 式 ')'  |  値
    値      := condition.<キー>  |  true  |  false  |  null  |  整数

未知のキーも、文法の誤りも、その場で例外を出す。黙って False を返さない。
"""

from typing import Any, Dict, List, Optional
import re

TOKEN_RE = re.compile(r"""
    \s*(?:
        (?P<lparen>\() |
        (?P<rparen>\)) |
        (?P<or>\|\|) |
        (?P<and>&&) |
        (?P<eq>==) |
        (?P<ne>!=) |
        (?P<cond>condition\.[a-z_][a-z0-9_]*) |
        (?P<true>\btrue\b) |
        (?P<false>\bfalse\b) |
        (?P<null>\bnull\b) |
        (?P<int>-?\d+)
    )
""", re.VERBOSE)


class ExprError(ValueError):
    """exists_if の式が読めない、または未知のキーを含む。"""


def tokenize(src: str) -> List[tuple]:
    tokens, pos = [], 0
    while pos < len(src):
        if src[pos].isspace():
            pos += 1
            continue
        m = TOKEN_RE.match(src, pos)
        if not m or m.end() == m.start():
            raise ExprError(f"読めない文字がある（{pos}文字目付近）: {src[pos:pos+20]!r} / 式全体: {src!r}")
        kind = m.lastgroup
        tokens.append((kind, m.group(kind)))
        pos = m.end()
    if not tokens:
        raise ExprError(f"式が空である: {src!r}")
    return tokens


class _Parser:
    def __init__(self, tokens: List[tuple], values: Dict[str, Any], src: str):
        self.t, self.i, self.values, self.src = tokens, 0, values, src

    def peek(self) -> Optional[str]:
        return self.t[self.i][0] if self.i < len(self.t) else None

    def take(self) -> tuple:
        if self.i >= len(self.t):
            raise ExprError(f"式が途中で終わっている: {self.src!r}")
        tok = self.t[self.i]
        self.i += 1
        return tok

    def parse(self) -> Any:
        v = self.or_expr()
        if self.i != len(self.t):
            raise ExprError(f"式の後ろに余りがある（{self.t[self.i]}）: {self.src!r}")
        return v

    def or_expr(self) -> Any:
        v = self.and_expr()
        while self.peek() == "or":
            self.take()
            r = self.and_expr()
            v = bool(v) or bool(r)
        return v

    def and_expr(self) -> Any:
        v = self.cmp_expr()
        while self.peek() == "and":
            self.take()
            r = self.cmp_expr()
            v = bool(v) and bool(r)
        return v

    def cmp_expr(self) -> Any:
        left = self.operand()
        kind = self.peek()
        if kind in ("eq", "ne"):
            self.take()
            right = self.operand()
            return (left == right) if kind == "eq" else (left != right)
        return left

    def operand(self) -> Any:
        kind, text = self.take()
        if kind == "lparen":
            v = self.or_expr()
            if self.peek() != "rparen":
                raise ExprError(f"閉じ括弧がない: {self.src!r}")
            self.take()
            return v
        if kind == "cond":
            key = text.split(".", 1)[1]
            if key not in self.values:
                raise ExprError(
                    f"未定義の condition がある: {key}"
                    f"（data/schema/conditions.json に追加するか、綴りを直すこと）/ 式: {self.src!r}"
                )
            return self.values[key]
        if kind == "true":
            return True
        if kind == "false":
            return False
        if kind == "null":
            return None
        if kind == "int":
            return int(text)
        raise ExprError(f"値が来るべきところに {text!r} がある: {self.src!r}")


def evaluate(expr: Optional[str], values: Dict[str, Any]) -> bool:
    """exists_if を評価する。None は「無条件で存在する」の意味。"""
    if expr is None:
        return True
    return bool(_Parser(tokenize(expr), values, expr).parse())
