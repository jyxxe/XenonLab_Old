"""Deterministic rule-based triage for registered DevAgent work items.

Issue titles are treated as data only (plan.md §44.2, §84, §98). The service
never executes Issue text, never writes to Forgejo and never calls an LLM:
output is an advisory classification (labels, priority, duplicate/related
candidates, recommended harness) that is reproducible for a given
work.sqlite snapshot.
"""

import re

from fastapi import Depends, Request
from pydantic import BaseModel, Field

from .common import APIError, connect, response, scope
from .work import repository_allowed

RULE_VERSION = "triage-rules-v1"

DUPLICATE_THRESHOLD = 0.6
RELATED_THRESHOLD = 0.25
MAX_CANDIDATES = 5

_BRACKETS = re.compile(r"\[[^\]]*\]")
_SPLIT = re.compile(r"[^\w]+")
_STOPWORDS = frozenset(
    {"a", "add", "an", "and", "for", "implement", "in", "of", "on", "perform", "the", "to", "with"}
)

CATEGORY_RULES = (
    ("deploy", re.compile(r"deploy|release|lease|rollback|state machine|executor")),
    ("devagent", re.compile(r"triage|webhook|work queue|forgejo|issue")),
    ("xelibrary", re.compile(r"xelibrary|\.xl|\btable\b|\brow\b|attachment|query dsl|metadata|catalog|path")),
    ("embedding", re.compile(r"embedding|d100m|chunker|tokenizer|chunk")),
    ("search", re.compile(r"search|qdrant|semantic|image|quality|rerank|qwen|vector")),
    ("library-agent", re.compile(r"library agent|lean|theorem|verifier|proposal|derive")),
    ("hf-cache", re.compile(r"hf-cache|hf cache|cache")),
    ("desktop", re.compile(r"desktop|registry|web shell|webui|\bui\b")),
    (
        "infra",
        re.compile(r"metrics|dashboard|monitor|network|backup|restore|pve|failure-injection|firewall|skeleton|audit"),
    ),
    ("docs", re.compile(r"runbook|convention|\badr\b|readme|docs")),
)

PRIORITY_RULES = (
    (
        "high",
        re.compile(r"security|secret|credential|production|deploy|release|lease|rollback|failure|injection|firewall|pve"),
    ),
    ("low", re.compile(r"runbook|convention|skeleton|\badr\b|readme|docs")),
)
DEFAULT_PRIORITY = "normal"

# Only Codex can hold a production deploy lease (plan.md §75, §76), so work on
# the production boundary is recommended to codex; verification/review/docs
# style work suits the independent OpenCode harness, and everything else
# defaults to the primary implementer.
PRODUCTION_BOUNDARY = re.compile(r"deploy|release|lease|rollback|production|pve|secret|credential|firewall|executor")
VERIFICATION_WORK = re.compile(r"\btest|verify|verification|review|audit|experiment|runbook|docs|convention")
DEFAULT_AGENT = "codex"


class TriageRequest(BaseModel):
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def title_tokens(title):
    lowered = _BRACKETS.sub(" ", (title or "").lower())
    return {token for token in _SPLIT.split(lowered) if len(token) > 1 and token not in _STOPWORDS}


def jaccard(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def categories(title):
    lowered = (title or "").lower()
    return [name for name, pattern in CATEGORY_RULES if pattern.search(lowered)][:4]


def priority(title):
    lowered = (title or "").lower()
    for name, pattern in PRIORITY_RULES:
        if pattern.search(lowered):
            return name
    return DEFAULT_PRIORITY


def recommended_agent(title):
    lowered = (title or "").lower()
    if PRODUCTION_BOUNDARY.search(lowered):
        return "codex"
    if VERIFICATION_WORK.search(lowered):
        return "opencode"
    return DEFAULT_AGENT


def candidate_numbers(title, issue_id, candidates, links):
    tokens = title_tokens(title)
    duplicates, related = [], []
    for link in links:
        if link["source"] == issue_id:
            other, kind = link["target"], link["kind"]
        elif link["target"] == issue_id:
            other, kind = link["source"], link["kind"]
        else:
            continue
        bucket = duplicates if kind == "duplicate_of" else related
        if other != issue_id and other not in bucket:
            bucket.append(other)
    scored = sorted(
        ((jaccard(tokens, title_tokens(item["title"])), item["issue_id"]) for item in candidates),
        key=lambda pair: (-pair[0], pair[1]),
    )
    for score, number in scored:
        if number in duplicates or number in related:
            continue
        if score >= DUPLICATE_THRESHOLD and len(duplicates) < MAX_CANDIDATES:
            duplicates.append(number)
        elif score >= RELATED_THRESHOLD and len(related) < MAX_CANDIDATES:
            related.append(number)
    related = [number for number in related if number not in duplicates]
    return duplicates[:MAX_CANDIDATES], related[:MAX_CANDIDATES]


def install(app):
    database = app.state.settings.data_dir / "work.sqlite"

    @app.post("/api/v1/issues/{issue_id}/triage")
    def triage(issue_id: int, body: TriageRequest, request: Request, actor=Depends(scope("dev:read"))):
        repository_allowed(app.state.settings, body.repository)
        if issue_id < 1:
            raise APIError(422, "INVALID_ISSUE", "positive issue number required")
        with connect(database) as db:
            item = db.execute(
                "SELECT title FROM work WHERE repository=? AND issue_id=?",
                (body.repository, issue_id),
            ).fetchone()
            if not item:
                raise APIError(404, "ISSUE_NOT_REGISTERED", "issue is not registered in the work queue")
            candidates = [
                dict(row)
                for row in db.execute(
                    "SELECT issue_id,title FROM work WHERE repository=? AND issue_id<>?",
                    (body.repository, issue_id),
                )
            ]
            links = [
                dict(row)
                for row in db.execute(
                    "SELECT source,target,kind FROM issue_links WHERE repository=? AND (source=? OR target=?)",
                    (body.repository, issue_id, issue_id),
                )
            ]
        duplicates, related = candidate_numbers(item["title"], issue_id, candidates, links)
        return response(
            request,
            {
                "repository": body.repository,
                "issue_id": issue_id,
                "title": item["title"],
                "labels": categories(item["title"]),
                "priority": priority(item["title"]),
                "duplicates": duplicates,
                "related": related,
                "recommended_agent": recommended_agent(item["title"]),
                "rule_version": RULE_VERSION,
            },
        )
