import pytest

from xeonolab.common import APIError
from xeonolab.query import evaluate, like_match, parse

from .conftest import auth


def where(clause, values):
    return evaluate(parse(f'FROM "/**" WHERE {clause}').predicate, values)


def test_plan_defaults_and_clauses():
    plan = parse('FROM "/**" WHERE a = 1 ORDER BY b DESC LIMIT 5 OFFSET 2')
    assert (plan.path, plan.limit, plan.offset, plan.descending) == ("/**", 5, 2, True)
    assert plan.order == ("field", "b")
    plan = parse('FROM "/数学/**"')
    assert (plan.path, plan.limit, plan.offset, plan.order, plan.descending) == ("/数学/**", 100, 0, None, False)
    assert plan.predicate is None
    plan = parse('FROM "/**" ORDER BY LEN(title) ASC LIMIT 3')
    assert plan.order == ("func", "len", [("field", "title")]) and plan.descending is False


def test_invalid_unsupported_and_injected_expressions():
    for expression in (
        'FROM "/**" WHERE FOO(title) = 1',
        'FROM "/**" WHERE LOWER() IS NULL',
        'FROM "/**" WHERE LOWER(title, title) = "x"',
        'FROM "/**" WHERE LEN(title, 1) = 1',
        'FROM "/**" WHERE COALESCE() IS NULL',
        'FROM "/**" WHERE EXISTS(attachments WHERE media_type = "application/pdf")',
        'FROM "/**" SEARCH TEXT "一致收敛" USING fast',
        'FROM "数学"',
        'FROM "/**" LIMIT 0',
        'FROM "/**" LIMIT 1001',
        'FROM "/**" OFFSET -1',
        'FROM "/**" WHERE title ~ "regex"',
        'FROM "/**" WHERE title = "x" OR 1 = 1; DROP TABLE data;--',
        "FROM \"//**\" ORDER BY title SIDEWAYS",
    ):
        with pytest.raises(APIError) as error:
            parse(expression)
        assert error.value.status == 422 and error.value.code == "INVALID_QUERY"
    with pytest.raises(APIError) as error:
        parse('FROM "/**" WHERE ' + "a = 1 AND " * 1000 + "a = 1")
    assert error.value.code == "QUERY_TOO_LARGE"


def test_set_and_text_operators():
    values = {"title": "一致收敛 证明", "tags": ["math", "analysis"], "confidence": 0.8}
    assert where('title IN ("x", "一致收敛 证明")', values)
    assert not where('title NOT IN ("x", "一致收敛 证明")', values)
    assert where('title NOT IN ("x")', values)
    assert where('tags CONTAINS "math"', values)
    assert where('tags CONTAINS ANY ("physics", "math")', values)
    assert not where('tags CONTAINS ALL ("physics", "math")', values)
    assert where('tags CONTAINS ALL ("math", "analysis")', values)
    assert where('title STARTS_WITH "一致"', values)
    assert where('title ENDS_WITH "证明"', values)
    assert where('title MATCHES "%收敛 证明"', values)
    assert not where('title MATCHES "一致_证明"', values)
    assert where('confidence >= 0.8 AND NOT (title = "x")', values)


def test_matches_uses_like_wildcards_not_regex():
    assert like_match("a.b", "a.b")
    assert not like_match("axb", "a.b")
    assert like_match("abc", "a%")
    assert like_match("abc", "a_c")
    assert not like_match("abc", "a_")
    assert like_match("(a+)+x", "(a+)+x")
    assert not like_match("aaaa", "(a+)+$")


def test_scalar_functions():
    values = {"title": "Alpha", "number": 5, "created": "2026-09-30T12:00:00Z"}
    assert where('LOWER(title) = "alpha"', values)
    assert where('UPPER(title) = "ALPHA"', values)
    assert where('LEN(title) = 5', values)
    assert where("LEN(number) IS NULL", values)
    assert where("COALESCE(missing, 3) = 3", values)
    assert where('COALESCE(missing, title) = "Alpha"', values)
    assert where('COALESCE(null, title) = "Alpha"', values)
    assert where('DATE(created) = "2026-09-30"', values)
    assert where("YEAR(created) = 2026 AND MONTH(created) = 9", values)
    assert where("YEAR(missing) IS NULL", values)
    assert where('DATE("not-a-date") IS NULL', values)
    assert where('title = UPPER("alpha")', {"title": "ALPHA"})


def test_null_and_type_semantics():
    assert not where('number > 3', {"number": None})
    assert not where('missing = "x"', {})
    assert where("missing IS NULL", {})
    assert where("missing IS NOT NULL", {"missing": 1})
    assert not where("missing IS NOT NULL", {})
    with pytest.raises(APIError) as error:
        where('number > "x"', {"number": 1})
    assert error.value.code == "QUERY_TYPE_ERROR"


def table_with_rows(client, rows):
    result = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/测试/排序",
            "name": "排序表",
            "columns": [
                {"name": "title", "type": "string", "nullable": False},
                {"name": "score", "type": "float64"},
                {"name": "tags", "type": "json"},
            ],
        },
    )
    assert result.status_code == 201, result.text
    for title, score, tags in rows:
        created = client.post(
            f"/api/v1/tables/{result.json()['data']['table_id']}/rows",
            headers=auth(),
            json={"values": {"title": title, "score": score, "tags": tags}},
        )
        assert created.status_code == 201, created.text
    return result.json()["data"]["table_id"]


def test_query_endpoint_orders_pages_and_explains(library):
    table_with_rows(
        library,
        [("b", 0.2, ["math"]), ("d", 0.4, ["math"]), ("a", 0.1, ["physics"]), ("c", 0.3, [])],
    )
    descending = library.post(
        "/api/v1/query",
        headers=auth(),
        json={"expression": 'FROM "/测试/**" WHERE score >= 0.1 ORDER BY score DESC LIMIT 2 OFFSET 1'},
    ).json()["data"]
    assert [row["values"]["title"] for row in descending] == ["c", "b"]
    ascending = library.post(
        "/api/v1/query",
        headers=auth(),
        json={"expression": 'FROM "/测试/**" ORDER BY score ASC LIMIT 2'},
    ).json()["data"]
    assert [row["values"]["title"] for row in ascending] == ["a", "b"]
    everything = library.post(
        "/api/v1/query", headers=auth(), json={"expression": 'FROM "/测试/**" LIMIT 100'}
    ).json()["data"]
    paged = library.post(
        "/api/v1/query", headers=auth(), json={"expression": 'FROM "/测试/**" LIMIT 2 OFFSET 1'}
    ).json()["data"]
    assert [row["_row_id"] for row in paged] == [row["_row_id"] for row in everything[1:3]]
    explain = library.post(
        "/api/v1/query/explain",
        headers=auth(),
        json={"expression": 'FROM "/测试/**" ORDER BY score DESC LIMIT 2 OFFSET 1'},
    ).json()["data"]
    assert explain["offset"] == 1 and explain["descending"] is True
    assert explain["order_by"] == ["field", "score"] and explain["limit"] == 2
    null_explain = library.post(
        "/api/v1/query/explain",
        headers=auth(),
        json={"expression": 'FROM "/测试/**" WHERE title = null'},
    )
    assert null_explain.status_code == 200
    assert null_explain.json()["data"]["ast"] == ["compare", ["field", "title"], "=", None]


def test_query_endpoint_operators_and_rejected_injection(library):
    table_with_rows(library, [("alpha", 0.9, ["math", "analysis"]), ("beta", 0.2, ["physics"])])
    data = library.post(
        "/api/v1/query",
        headers=auth(),
        json={"expression": 'FROM "/测试/**" WHERE tags CONTAINS ANY ("math", "physics") ORDER BY score DESC LIMIT 1'},
    ).json()["data"]
    assert [row["values"]["title"] for row in data] == ["alpha"]
    data = library.post(
        "/api/v1/query",
        headers=auth(),
        json={"expression": 'FROM "/测试/**" WHERE LOWER(title) MATCHES "a%" AND score NOT IN (0.0)'},
    ).json()["data"]
    assert [row["values"]["title"] for row in data] == ["alpha"]
    rejected = library.post(
        "/api/v1/query",
        headers=auth(),
        json={"expression": 'FROM "/**" WHERE title = "x" OR 1 = 1; DROP TABLE data;--'},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "INVALID_QUERY"
    remaining = library.post(
        "/api/v1/query", headers=auth(), json={"expression": 'FROM "/**" LIMIT 100'}
    ).json()["data"]
    assert len(remaining) == 2
