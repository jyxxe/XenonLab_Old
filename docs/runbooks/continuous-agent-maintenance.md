# Continuous supervised maintenance

This is the user's requested next phase. Start it only after the DevAgent V1
handoff Goal has passed its actual acceptance requirements. Do not mark that
Goal complete merely to start this loop. Once complete, create a separate,
ongoing maintenance Goal with no inferred token budget, and keep it active until
the user requests a pause or stop.

Use exactly two GPT 6.1 Sol child agents: a bug-finding agent and an Issue
implementation agent. Give them independent working directories and concrete
bounded rounds; do not let them edit the same checkout or contend for the same
browser tab. One round's finish should lead to the next useful work item or a
bounded idle wait, never a false declaration that ongoing maintenance is done.

The bug-finding agent exercises actual deployed WebUI/API behavior using the
existing test identities and tools, searches existing Issues for duplicates,
and returns reproducible findings with expected/actual behavior, impact and
evidence. It must distinguish a confirmed bug from a proposed improvement.
The parent publishes or updates the Issue with those findings.

The implementation agent selects an existing open Issue, refreshes its latest
comments/state, and obtains the project's atomic work claim before edits. A
claim conflict means select another item; a stale or expired epoch requires
reclaiming. Refresh leases during sustained work. Use an isolated feature
branch, inspect existing code/docs/tests before questioning the user, and
deliver the exact commit, meaningful validation and any remaining blocker.
Blocking questions belong in the Issue after available tools were exhausted.

The parent observes both agents' actual behavior, including their tool calls,
claim ownership/epoch, information discovery, secret handling, duplicate
findings, honest tests, scope, review freshness and completion evidence. Correct
behavior problems through bounded follow-up tasks and document the finding.
Observe rejected actions too; process exit or a promise does not prove success.

The parent performs external writes and publication for these subordinate
harnesses under the user's existing authorization. DevAgent's separately
accepted protected deployment capability remains distinct. Review actual diffs
and current HEAD before merging, enforce a single deployment lease, deploy an
explicit SHA, verify production and preserve rollback. Do not fabricate CI or
close an Issue before its required behavior is verified.

Limit simultaneous heavy tests/builds. When the queue and deployed behavior are
unchanged, back off rather than repeatedly running models for identical work.
Recheck for meaningful changes and keep durable nonsecret progress so Goal
continuations can resume without recreating accounts, republishing duplicate
Issues or losing ownership of in-flight work.
