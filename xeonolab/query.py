"""Bounded DSL parser and AST interpreter. Never executes SQL or user code."""

import fnmatch
import json
from dataclasses import dataclass

from lark import Lark, Transformer, UnexpectedInput

from .common import APIError

GRAMMAR = r"""
start: "FROM"i STRING ["WHERE"i expr] ["LIMIT"i INT]
?expr: expr "OR"i term -> or_expr
     | term
?term: term "AND"i factor -> and_expr
     | factor
?factor: "NOT"i factor -> not_expr
       | "(" expr ")"
       | FIELD OP value -> compare
       | FIELD "IS"i "NULL"i -> is_null
       | FIELD "IS"i "NOT"i "NULL"i -> not_null
       | FIELD "CONTAINS"i value -> contains
       | FIELD "IN"i "(" value ("," value)* ")" -> in_expr
?value: STRING -> string
      | SIGNED_NUMBER -> number
      | "true"i -> true
      | "false"i -> false
      | "null"i -> null
OP: "!=" | ">=" | "<=" | "=" | ">" | "<"
FIELD: /[A-Za-z_\u0080-\uffff][A-Za-z_0-9.\u0080-\uffff]*/
STRING: /"([^"\\]|\\.)*"/
%import common.SIGNED_NUMBER
%import common.INT
%import common.WS
%ignore WS
"""


class AST(Transformer):
    def string(self, x):
        return json.loads(x[0])

    def number(self, x):
        return float(x[0]) if "." in x[0] else int(x[0])

    def true(self, x):
        return True

    def false(self, x):
        return False

    def null(self, x):
        return None

    def compare(self, x):
        return ("compare", str(x[0]), str(x[1]), x[2])

    def contains(self, x):
        return ("contains", str(x[0]), x[1])

    def in_expr(self, x):
        return ("in", str(x[0]), list(x[1:]))

    def is_null(self, x):
        return ("null", str(x[0]))

    def not_null(self, x):
        return ("not", ("null", str(x[0])))

    def or_expr(self, x):
        return ("or", *x)

    def and_expr(self, x):
        return ("and", *x)

    def not_expr(self, x):
        return ("not", x[0])

    def start(self, x):
        return Plan(json.loads(x[0]), x[1] if len(x) > 1 else None, int(x[2]) if len(x) > 2 and x[2] else 100)


@dataclass
class Plan:
    path: str
    predicate: tuple | None
    limit: int


parser = Lark(GRAMMAR, parser="lalr", transformer=AST())


def parse(expression):
    if len(expression) > 8192:
        raise APIError(422, "QUERY_TOO_LARGE", "expression exceeds 8192 characters")
    try:
        plan = parser.parse(expression)
        if not 1 <= plan.limit <= 1000 or not plan.path.startswith("/"):
            raise ValueError()
        return plan
    except (UnexpectedInput, ValueError, RecursionError):
        raise APIError(422, "INVALID_QUERY", "invalid or unsupported Query DSL") from None


def field(values, name):
    value = values
    for part in name.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def evaluate(node, values):
    if node is None:
        return True
    op, *args = node
    if op == "and":
        return evaluate(args[0], values) and evaluate(args[1], values)
    if op == "or":
        return evaluate(args[0], values) or evaluate(args[1], values)
    if op == "not":
        return not evaluate(args[0], values)
    value = field(values, args[0])
    if op == "null":
        return value is None
    if op == "in":
        return value in args[1]
    if op == "contains":
        return isinstance(value, (str, list)) and args[1] in value
    rhs = args[2]
    if value is None or rhs is None:
        return False
    if type(value) is not type(rhs) and not (type(value) in (int, float) and type(rhs) in (int, float)):
        raise APIError(422, "QUERY_TYPE_ERROR", "comparison has incompatible field types")
    comparator = args[1]
    return {
        "=": lambda: value == rhs,
        "!=": lambda: value != rhs,
        ">": lambda: value > rhs,
        "<": lambda: value < rhs,
        ">=": lambda: value >= rhs,
        "<=": lambda: value <= rhs,
    }[comparator]()


def path_matches(path, pattern):
    # ** crosses path components; * stays within one component.
    if "**" in pattern:
        prefix, _, suffix = pattern.partition("**")
        return path.startswith(prefix) and (not suffix or path.endswith(suffix))
    parts, patterns = path.split("/"), pattern.split("/")
    return len(parts) == len(patterns) and all(fnmatch.fnmatchcase(p, q) for p, q in zip(parts, patterns))
