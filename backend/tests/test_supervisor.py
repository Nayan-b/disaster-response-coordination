"""The supervisor and the draft → fact-check repair loop.

Adapted from the supervisor component of NVIDIA's AVO architecture (arXiv:2603.24517). The tests
that matter most here are the ones proving the loop *stops*: an agent that redrafts forever, or that
sneaks a blocked draft past the gate, would be worse than the dead end it replaced.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agents import graph as graph_module
from app.agents import nodes
from app.agents.graph import _after_factcheck, _after_supervisor, build_graph
from app.models import Claim, FactCheckResult, ToolCall

# `graph.py` imports the node callables by name, so `build_graph` closes over *its* module globals.
# Patching `nodes.allocation_draft` would leave the graph pointing at the original — these tests
# patch the graph module, which is what the compiled graph actually calls.
PATCH_TARGET = graph_module


def _blocked(rejected_ids: list[str], verified_ids: list[str] | None = None) -> FactCheckResult:
    return FactCheckResult(
        verified_claims=[
            Claim(claim_id=cid, text=f"verified {cid}", verified=True)
            for cid in (verified_ids or [])
        ],
        rejected_claims=[
            Claim(
                claim_id=cid,
                text=f"rejected {cid}",
                value=4_500_000.0,
                unit="people",
                verified=False,
                rejection_reason="value does not appear in tc_5 — unsupported by its result",
            )
            for cid in rejected_ids
        ],
        passed=False,
        blocking_reason="1 load-bearing claim(s) failed verification",
    )


# --- routing -------------------------------------------------------------------------------------


def test_blocked_draft_routes_to_supervisor_not_the_human():
    assert _after_factcheck({"fact_check": _blocked(["c_population"])}) == "supervisor"


def test_passing_draft_still_goes_straight_to_the_human():
    passing = FactCheckResult(verified_claims=[], rejected_claims=[], passed=True)
    assert _after_factcheck({"fact_check": passing}) == "human_gate"


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("retry", "allocation_draft"),
        ("stagnant", "action_log"),
        ("exhausted", "action_log"),
        (None, "action_log"),
    ],
)
def test_supervisor_routing(outcome, expected):
    assert _after_supervisor({"supervisor_outcome": outcome}) == expected


# --- the supervisor's decision -------------------------------------------------------------------


def test_first_block_is_retried_with_a_directive_naming_the_failure():
    out = nodes.supervisor(
        {
            "fact_check": _blocked(["c_population"], verified_ids=["c_alert", "c_extent"]),
            "draft_attempts": 1,
        }
    )
    assert out["supervisor_outcome"] == "retry"
    assert out["last_rejected_ids"] == ["c_population"]

    directive = out["repair_directive"]
    # Concrete enough to act on: the failed id, the verbatim reason, and the allowed ids.
    assert "c_population" in directive
    assert "does not appear in tc_5" in directive
    assert "c_alert, c_extent" in directive


def test_stagnation_stops_before_the_budget_is_spent():
    """Same rejections twice means the redraft changed nothing that mattered.

    This is the branch that protects the rule-based drafter, which is deterministic and would
    otherwise produce a byte-identical draft for every remaining attempt.
    """
    out = nodes.supervisor(
        {
            "fact_check": _blocked(["c_population"]),
            "draft_attempts": 2,  # budget is 3, so a naive loop would retry here
            "last_rejected_ids": ["c_population"],
        }
    )
    assert out["supervisor_outcome"] == "stagnant"
    assert out["status"] == "blocked_by_factcheck"
    assert "repair_directive" not in out
    assert "not converging" in out["trace"][0]["message"]


def test_different_rejections_are_not_stagnation():
    """Progress means the loop is worth continuing, even though it is still failing."""
    out = nodes.supervisor(
        {
            "fact_check": _blocked(["c_extent"], verified_ids=["c_alert"]),
            "draft_attempts": 2,
            "last_rejected_ids": ["c_population"],
        }
    )
    assert out["supervisor_outcome"] == "retry"


def test_budget_exhaustion_stops_the_loop():
    out = nodes.supervisor(
        {
            "fact_check": _blocked(["c_population"]),
            "draft_attempts": nodes.MAX_DRAFT_ATTEMPTS,
            "last_rejected_ids": ["c_something_else"],
        }
    )
    assert out["supervisor_outcome"] == "exhausted"
    assert out["status"] == "blocked_by_factcheck"


def test_directive_forbids_all_numbers_when_nothing_survived():
    out = nodes.supervisor(
        {"fact_check": _blocked(["c_population"], verified_ids=[]), "draft_attempts": 1}
    )
    assert "NO numbers" in out["repair_directive"]


def test_directive_names_unavailable_sources():
    out = nodes.supervisor(
        {
            "fact_check": _blocked(["c_population"], verified_ids=["c_alert"]),
            "draft_attempts": 1,
            "unavailable": {"population": "WorldPop raster missing for NPL"},
        }
    )
    assert "WorldPop raster missing for NPL" in out["repair_directive"]


# --- the loop, through the real compiled graph ---------------------------------------------------


@pytest.fixture
def fabricating_pipeline(monkeypatch):
    """Stub the data stages, and make the drafter fabricate a population figure on demand.

    Everything upstream of `allocation_draft` is replaced with fixed state so the test exercises the
    loop rather than the network. The tool-call log is real enough for the fact-check to verify
    against: `tc_1` succeeded and contains 1000, so a claim of 4,500,000 is unsupported.
    """
    call = ToolCall(
        call_id="tc_1",
        tool="estimate_population",
        arguments={},
        result={"ok": True, "population": 1000},
        ok=True,
        called_at=datetime(2026, 8, 22, tzinfo=UTC),
    )

    monkeypatch.setattr(
        PATCH_TARGET,
        "alert_monitor",
        lambda state: {
            "event": {"event_id": 1, "country": "Testland", "alert_level": "Red"},
            "event_id": 1,
            "tool_calls": [call],
            "trace": [],
            "status": "event_selected",
        },
    )
    for name in ("imagery_retrieval", "population_overlay"):
        monkeypatch.setattr(PATCH_TARGET, name, lambda state: {"trace": []})

    attempts: list[str | None] = []

    def fake_draft(state):
        """Fabricate on the first pass; comply once the supervisor has said what failed."""
        directive = state.get("repair_directive")
        attempts.append(directive)
        n = len(attempts)
        fabricated = Claim(
            claim_id="c_population",
            text="Approximately 4,500,000 people are inside the flood extent",
            value=4_500_000.0,
            unit="people",
            source_call_id="tc_1",
        )
        honest = Claim(claim_id="c_alert", text="GDACS rates this event Red", source_call_id="tc_1")
        from app.models import AllocationAction, Recommendation

        claims = [honest] if directive else [honest, fabricated]
        return {
            "claims": claims,
            "recommendation": Recommendation(
                headline="Flood in Testland",
                situation="Situation.",
                actions=[
                    AllocationAction(
                        priority=1,
                        action="Stage relief",
                        rationale="Because.",
                        supporting_claim_ids=[c.claim_id for c in claims],
                    )
                ],
                caveats=[],
                drafted_by="test-engine",
                drafted_at=datetime(2026, 8, 22, tzinfo=UTC),
            ),
            "engine": "test-engine",
            "draft_attempts": n,
            "repair_directive": None,
            "trace": [],
            "status": "drafted",
        }

    monkeypatch.setattr(PATCH_TARGET, "allocation_draft", fake_draft)
    return attempts


def test_graph_repairs_a_fabricated_figure_and_reaches_the_human(fabricating_pipeline):
    """End to end: fabricate → blocked → supervised redraft → verified → gate.

    Before the supervisor existed this run ended at the action log and the responder saw nothing.
    """
    graph = build_graph()
    result = graph.invoke({"event_id": None}, {"configurable": {"thread_id": "t-repair"}})

    assert len(fabricating_pipeline) == 2, "should have drafted exactly twice"
    assert fabricating_pipeline[0] is None, "first attempt gets no directive"
    assert "c_population" in fabricating_pipeline[1], "second attempt is told what failed"

    # The second draft passed, so the run reached the gate and stopped there for a human.
    assert result["fact_check"].passed is True
    assert result["draft_attempts"] == 2
    assert result.get("approval") is None

    messages = [t["message"] for t in result["trace"]]
    assert any("redrafting with those figures withdrawn" in m for m in messages)


def test_graph_gives_up_when_the_drafter_keeps_fabricating(monkeypatch):
    """A drafter that never complies must be stopped, and must not reach the human."""
    from app.models import AllocationAction, Recommendation

    call = ToolCall(
        call_id="tc_1", tool="estimate_population", arguments={},
        result={"ok": True, "population": 1000}, ok=True,
        called_at=datetime(2026, 8, 22, tzinfo=UTC),
    )
    monkeypatch.setattr(
        PATCH_TARGET, "alert_monitor",
        lambda state: {"event": {"event_id": 1}, "event_id": 1, "tool_calls": [call],
                       "trace": [], "status": "event_selected"},
    )
    for name in ("imagery_retrieval", "population_overlay"):
        monkeypatch.setattr(PATCH_TARGET, name, lambda state: {"trace": []})

    drafts = []

    def stubborn(state):
        drafts.append(1)
        # Rejected for a *different* claim id each time, so stagnation never trips and the budget
        # is what has to stop this.
        claim = Claim(
            claim_id=f"c_bogus_{len(drafts)}",
            text="invented",
            value=9_999_999.0,
            unit="people",
            source_call_id="tc_1",
        )
        return {
            "claims": [claim],
            "recommendation": Recommendation(
                headline="h", situation="s",
                actions=[AllocationAction(priority=1, action="a", rationale="r",
                                          supporting_claim_ids=[claim.claim_id])],
                caveats=[], drafted_by="test", drafted_at=datetime(2026, 8, 22, tzinfo=UTC),
            ),
            "engine": "test",
            "draft_attempts": len(drafts),
            "repair_directive": None,
            "trace": [],
            "status": "drafted",
        }

    monkeypatch.setattr(PATCH_TARGET, "allocation_draft", stubborn)

    graph = build_graph()
    result = graph.invoke({"event_id": None}, {"configurable": {"thread_id": "t-stubborn"}})

    assert len(drafts) == nodes.MAX_DRAFT_ATTEMPTS
    assert result["status"] == "logged_blocked"
    assert result["supervisor_outcome"] == "exhausted"
    assert result.get("approval") is None, "a blocked draft must never reach a human decision"
