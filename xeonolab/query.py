"""Bounded DSL parser and AST interpreter. Never executes SQL or user code.

Supported subset (plan.md §26): FROM, WHERE with comparisons, IN / NOT IN,
CONTAINS / CONTAINS ANY / CONTAINS ALL, STARTS_WITH / ENDS_WITH / MATCHES
(LIKE-style wildcards, deliberately not regex), IS [NOT] NULL, AND/OR/NOT,
ORDER BY, LIMIT and OFFSET, plus scalar functions LOWER, UPPER, LEN,
COALESCE, DATE, YEAR and MONTH. EXISTS(...), SEARCH TEXT and row-context
functions stay unimplemented until attachments/path/search context is wired
through the execution plan.
"""

import fnmatch
import json
import re
from dataclasses import dataclass

from lark import Lark, Transformer, UnexpectedInput

from .common import APIError

NULL_LITERAL = object()
FUNCTION_ARITY = {
    "lower": (1, 1),
    "upper": (1, 1),
    "len": (1, 1),
    "coalesce": (1, None),
    "date": (1, 1),
    "year": (1, 1),
    "month": (1, 1),
}

GRAMMAR = r"""
start: "FROM"i STRING ["WHERE"i expr] ["ORDER"i "BY"i operand [direction]] ["LIMIT"i INT] ["OFFSET"i INT]
direction: ASC | DESC
ASC: "ASC"i
DESC: "DESC"i
?expr: expr "OR"i term -> or_expr
     | term
?term: term "AND"i factor -> and_expr
     | factor
?factor: "NOT"i factor -> not_expr
       | "(" expr ")"
       | operand "IS"i "NOT"i "NULL"i -> not_null
       | operand "IS"i "NULL"i -> is_null
       | operand "NOT"i "IN"i "(" operand ("," operand)* ")" -> not_in
       | operand "CONTAINS"i "ANY"i "(" operand ("," operand)* ")" -> contains_any
       | operand "CONTAINS"i "ALL"i "(" operand ("," operand)* ")" -> contains_all
       | operand "CONTAINS"i operand -> contains
       | operand "IN"i "(" operand ("," operand)* ")" -> in_expr
       | operand "STARTS_WITH"i operand -> starts_with
       | operand "ENDS_WITH"i operand -> ends_with
       | operand "MATCHES"i operand -> matches
       | operand OP operand -> compare
?operand: FIELD -> field_ref
        | STRING -> string
        | SIGNED_NUMBER -> number
        | "true"i -> true
        | "false"i -> false
        | "null"i -> null
        | func
func: FUNC "(" [operand ("," operand)*] ")"
FUNC.2: /(LOWER|UPPER|LEN|COALESCE|DATE|YEAR|MONTH)(?=\s*\()/i
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
        return NULL_LITERAL

    def field_ref(self, x):
        return ("field", str(x[0]))

    def func(self, x):
        arguments = x[1:]
        # Lark preserves an empty optional production as a None child. Keep an
        # explicit NULL_LITERAL argument distinct so arity checks remain sound.
        if arguments == [None]:
            arguments = []
        return ("func", str(x[0]).lower(), list(arguments))

    def compare(self, x):
        return ("compare", x[0], str(x[1]), x[2])

    def contains(self, x):
        return ("contains", x[0], x[1])

    def contains_any(self, x):
        return ("contains_any", x[0], list(x[1:]))

    def contains_all(self, x):
        return ("contains_all", x[0], list(x[1:]))

    def in_expr(self, x):
        return ("in", x[0], list(x[1:]))

    def not_in(self, x):
        return ("not", ("in", x[0], list(x[1:])))

    def starts_with(self, x):
        return ("starts_with", x[0], x[1])

    def ends_with(self, x):
        return ("ends_with", x[0], x[1])

    def matches(self, x):
        return ("matches", x[0], x[1])

    def is_null(self, x):
        return ("null", x[0])

    def not_null(self, x):
        return ("not", ("null", x[0]))

    def or_expr(self, x):
        return ("or", *x)

    def and_expr(self, x):
        return ("and", *x)

    def not_expr(self, x):
        return ("not", x[0])

    def direction(self, x):
        return str(x[0]).upper()

    def start(self, x):
        return Plan(
            json.loads(x[0]),
            x[1],
            int(x[4]) if x[4] is not None else 100,
            offset=int(x[5]) if x[5] is not None else 0,
            order=x[2],
            descending=(x[3] or "ASC") == "DESC",
        )


@dataclass
class Plan:
    path: str
    predicate: tuple | None
    limit: int
    offset: int = 0
    order: tuple | None = None
    descending: bool = False


parser = Lark(GRAMMAR, parser="lalr", transformer=AST())


def parse(expression):
    if len(expression) > 8192:
        raise APIError(422, "QUERY_TOO_LARGE", "expression exceeds 8192 characters")
    try:
        plan = parser.parse(expression)
        validate_function_arities(plan.predicate)
        validate_function_arities(plan.order)
        if (
            not 1 <= plan.limit <= 1000
            or not 0 <= plan.offset <= 100000
            or not plan.path.startswith("/")
        ):
            raise ValueError()
        return plan
    except (UnexpectedInput, ValueError, RecursionError):
        raise APIError(422, "INVALID_QUERY", "invalid or unsupported Query DSL") from None


def validate_function_arities(node):
    if isinstance(node, tuple):
        if node and node[0] == "func":
            minimum, maximum = FUNCTION_ARITY[node[1]]
            count = len(node[2])
            if count < minimum or (maximum is not None and count > maximum):
                raise ValueError("invalid function argument count")
        for child in node:
            validate_function_arities(child)
    elif isinstance(node, list):
        for child in node:
            validate_function_arities(child)


def json_ast(node):
    """Return the internal AST with NULL_LITERAL restored to JSON null."""
    if node is NULL_LITERAL:
        return None
    if isinstance(node, tuple):
        return tuple(json_ast(child) for child in node)
    if isinstance(node, list):
        return [json_ast(child) for child in node]
    return node


def field(values, name):
    value = values
    for part in name.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _lower(args):
    return args[0].lower() if isinstance(args[0], str) else None


def _upper(args):
    return args[0].upper() if isinstance(args[0], str) else None


def _len(args):
    return len(args[0]) if isinstance(args[0], (str, list)) else None


def _coalesce(args):
    return next((item for item in args if item is not None), None)


_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _date_match(args):
    return _DATE.match(args[0]) if isinstance(args[0], str) else None


def _date(args):
    match = _date_match(args)
    return match.group(0) if match else None


def _year(args):
    match = _date_match(args)
    return int(match.group(1)) if match else None


def _month(args):
    match = _date_match(args)
    return int(match.group(2)) if match else None


FUNCTIONS = {
    "lower": _lower,
    "upper": _upper,
    "len": _len,
    "coalesce": _coalesce,
    "date": _date,
    "year": _year,
    "month": _month,
}


def like_match(value, pattern):
    parts = []
    for character in pattern:
        if character == "%":
            parts.append(".*")
        elif character == "_":
            parts.append(".")
        else:
            parts.append(re.escape(character))
    return re.fullmatch("".join(parts), value, flags=re.DOTALL) is not None


def resolve(node, values):
    if node is NULL_LITERAL:
        return None
    if isinstance(node, tuple) and node:
        if node[0] == "field":
            return field(values, node[1])
        if node[0] == "func":
            if node[1] not in FUNCTIONS:
                raise APIError(422, "QUERY_UNSUPPORTED", "unsupported function")
            return FUNCTIONS[node[1]]([resolve(argument, values) for argument in node[2]])
    return node


def _contains_any(value, candidates):
    if isinstance(value, str):
        return any(isinstance(item, str) and item in value for item in candidates)
    if isinstance(value, list):
        return any(item in value for item in candidates)
    return False


def _contains_all(value, candidates):
    if isinstance(value, str):
        return all(isinstance(item, str) and item in value for item in candidates)
    if isinstance(value, list):
        return all(item in value for item in candidates)
    return False


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
    left = resolve(args[0], values)
    if op == "null":
        return left is None
    if op == "in":
        return left in [resolve(item, values) for item in args[1]]
    if op == "contains":
        right = resolve(args[1], values)
        return isinstance(left, (str, list)) and right in left
    if op == "contains_any":
        return _contains_any(left, [resolve(item, values) for item in args[1]])
    if op == "contains_all":
        return _contains_all(left, [resolve(item, values) for item in args[1]])
    if op == "starts_with":
        right = resolve(args[1], values)
        return isinstance(left, str) and isinstance(right, str) and left.startswith(right)
    if op == "ends_with":
        right = resolve(args[1], values)
        return isinstance(left, str) and isinstance(right, str) and left.endswith(right)
    if op == "matches":
        right = resolve(args[1], values)
        return isinstance(left, str) and isinstance(right, str) and like_match(left, right)
    right = resolve(args[2], values)
    if left is None or right is None:
        return False
    if type(left) is not type(right) and not (
        type(left) in (int, float) and type(right) in (int, float)
    ):
        raise APIError(422, "QUERY_TYPE_ERROR", "comparison has incompatible field types")
    comparator = args[1]
    return {
        "=": lambda: left == right,
        "!=": lambda: left != right,
        ">": lambda: left > right,
        "<": lambda: left < right,
        ">=": lambda: left >= right,
        "<=": lambda: left <= right,
    }[comparator]()


def sortable(value):
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, int(value))
    if isinstance(value, (int, float)):
        return (2, value)
    if isinstance(value, str):
        return (3, value)
    return (4, json.dumps(value, sort_keys=True, ensure_ascii=False))


def sort_key(node, values):
    return sortable(resolve(node, values))


def path_matches(path, pattern):
    # ** crosses path components; * stays within one component.
    if "**" in pattern:
        prefix, _, suffix = pattern.partition("**")
        return path.startswith(prefix) and (not suffix or path.endswith(suffix))
    parts, patterns = path.split("/"), pattern.split("/")
    return len(parts) == len(patterns) and all(fnmatch.fnmatchcase(p, q) for p, q in zip(parts, patterns))
