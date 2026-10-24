"""Deterministic parts of the agent (no LLM calls): routing, chart checks, refusals."""

from app.agent.graph import route_after_guard
from app.agent.charts import build_chart
from app.agent.nodes.respond import refuse, suggestions
from app.agent.nodes.sql import MAX_ATTEMPTS, route_after_check, route_after_generate
from app.agent.state import ChartChoice
from app.security.roles import get_role

COLUMNS = ["month", "spend", "supplier"]
ROWS = [["2026-06", 100, "A"], ["2026-07", 250, "B"], ["2026-08", 175, "C"]]


def test_guard_routing():
    assert route_after_guard({"decision": "data_question"}) == "retrieve_context"
    assert route_after_guard({"decision": "needs_clarification"}) == "clarify"
    assert route_after_guard({"decision": "write_request"}) == "draft_action"  # drafted, then confirmed by the user
    for decision in ("out_of_role", "not_available", "injection", "off_topic"):
        assert route_after_guard({"decision": decision}) == "refuse"


def test_retry_routing_stops_after_max_attempts():
    assert route_after_check({"sql_error": None, "attempts": 1}) == "ok"
    assert route_after_check({"sql_error": "boom", "attempts": 1}) == "retry"
    assert route_after_check({"sql_error": "boom", "attempts": MAX_ATTEMPTS}) == "give_up"
    assert route_after_generate({"sql": None}) == "unanswerable"


def test_valid_chart_is_kept():
    chart, note = build_chart(ChartChoice(kind="line", x="month", y=["spend"], title="Spend"), "line", COLUMNS, ROWS)
    assert note is None
    assert chart["data"] == [{"month": "2026-06", "spend": 100}, {"month": "2026-07", "spend": 250}, {"month": "2026-08", "spend": 175}]


def test_chart_uses_sensible_axes_when_the_model_picks_bad_ones():
    for choice in (ChartChoice(kind="bar", x="nope", y=["spend"], title=""), ChartChoice(kind="bar", x="month", y=["supplier"], title=""), None):
        chart, _ = build_chart(choice, "bar", COLUMNS, ROWS)
        assert chart["y"] == ["spend"]


def test_large_pie_is_folded_into_top_5_and_other():
    rows = [[f"S{i}", 100 - i, "x"] for i in range(34)]
    chart, note = build_chart(ChartChoice(kind="pie", x="month", y=["spend"], title=""), "pie", COLUMNS, rows)
    assert len(chart["data"]) == 6 and chart["data"][-1]["month"] == "Other (29)"
    assert sum(d["spend"] for d in chart["data"]) == sum(r[1] for r in rows)
    assert "top 5" in note


def test_long_bar_chart_keeps_top_25():
    rows = [[f"S{i}", i, "x"] for i in range(40)]
    chart, note = build_chart(None, "bar", COLUMNS, rows)
    assert len(chart["data"]) == 25 and chart["data"][0]["spend"] == 39
    assert "top 25 of 40" in note


def test_chart_blockers():
    from app.agent.charts import chart_blocker

    assert chart_blocker(COLUMNS, ROWS) is None
    assert "one value" in chart_blocker(COLUMNS, ROWS[:1])
    assert "no numbers" in chart_blocker(["a", "b"], [["x", "y"], ["z", "w"]])
    assert "different units" in chart_blocker(["item", "qty", "unit"], [["Sheet", 100, "kg"], ["Bolt", 400, "pcs"]])
    # quantities without a unit column: units unknown, so no chart (reorder levels in kg and pcs)
    assert "different units" in chart_blocker(["item_code", "min_stock_level"], [["A", 657], ["B", 4]])
    # counts and money still chart fine
    assert chart_blocker(["supplier", "late_deliveries"], [["A", 18], ["B", 16]]) is None


def test_requested_format_is_detected():
    from app.agent.charts import requested_format

    assert requested_format("Show spend by supplier as a pie chart") == "pie"
    assert requested_format("make it a donut") == "pie"
    assert requested_format("show that as a bar chart") == "bar"
    assert requested_format("plot monthly spend as a line graph") == "line"
    assert requested_format("give me items per category as a table") == "table"
    assert requested_format("can you chart this") == "chart"
    assert requested_format("which suppliers deliver late?") is None


def test_out_of_role_refusal_names_the_right_role_and_suggests_in_role_questions():
    state = {"role": "inventory_manager", "decision": "out_of_role", "guard": {"other_role": "procurement_manager"}}
    response = refuse(state)["response"]
    assert response["type"] == "refusal"
    assert "Procurement Manager" in response["answer"]
    for q in suggestions(get_role("inventory_manager")):
        assert q in response["answer"]


def test_suggestions_are_the_roles_starter_questions():
    role = get_role("procurement_manager")
    assert suggestions(role) == list(role.starter_questions[:3])


def test_llm_cache_round_trips_structured_output(tmp_path):
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration

    from app.agent.llm import SQLiteLLMCache
    from app.agent.state import GuardOutput

    cache = SQLiteLLMCache(tmp_path / "c.sqlite")
    parsed = GuardOutput(decision="off_topic", standalone_question="", reason="r")
    gen = ChatGeneration(message=AIMessage(content="{}", additional_kwargs={"parsed": parsed}))
    cache.update("p", "llm", [gen])
    assert cache.lookup("p", "llm")[0].message.additional_kwargs["parsed"] == parsed
    assert cache.lookup("other", "llm") is None


def test_summary_totals_cover_all_rows_and_refuse_to_sum_mixed_units():
    from app.agent.formatting import summarize

    rows = [[f"S{i}", "placed", 2] for i in range(60)]
    assert "sum 120" in summarize(["supplier", "status", "po_count"], rows)
    assert "(₹1.09 crore)" in summarize(["status", "total_spend"], [["a", 5981234.5], ["b", 4952000]])
    mixed = [["A", 5, "pcs"], ["B", 7, "kg"]]
    s = summarize(["item", "quantity", "unit"], mixed)
    assert "no total" in s and "sum 12" not in s




def test_indian_number_formatting():
    from app.agent.formatting import indian, rupees

    assert indian(1234567) == "12,34,567"
    assert indian(999) == "999"
    assert indian(100000.5) == "1,00,000.50"
    assert indian(-2500) == "-2,500"
    assert rupees(5981234) == "₹59,81,234 (₹59.81 lakh)"
    assert rupees(22997890.2) == "₹2,29,97,890.20 (₹2.30 crore)"


def test_money_columns_exclude_percentages_and_quantities():
    from app.agent.formatting import is_money

    for col in ("total_amount", "spend", "spend_inr", "unit_price", "line_total", "stock_value_inr"):
        assert is_money(col), col
    for col in ("pct_of_total_spend", "share_of_spend", "po_count", "total_issued", "quantity_received", "avg_days_late"):
        assert not is_money(col), col


def test_unit_clarifications_offer_value_to_roles_that_see_prices():
    from app.agent.nodes.respond import clarify_options

    units = ["In pieces", "In kg", "In meters", "In liters"]
    assert clarify_options(units, get_role("owner")) == ["In pieces", "In kg", "In meters", "By amount spent"]
    assert clarify_options(units, get_role("inventory_manager")) == units  # Inventory can't see prices
    assert clarify_options(["Stock levels", "Below reorder level"], get_role("owner")) == ["Stock levels", "Below reorder level"]
    # the model listed the money option last among 7: it must survive the cut to 4
    many = ["In pieces", "In kg", "In meters", "In liters", "In sets", "By number of orders", "By amount spent"]
    assert clarify_options(many, get_role("owner")) == ["In pieces", "In kg", "In meters", "By amount spent"]


def test_quantities_are_not_totalled_across_unknown_or_mixed_units():
    from app.agent.formatting import summarize

    # min levels for items in kg and pcs, without a unit column: no total
    s = summarize(["item_code", "min_stock_level"], [["A", 3001], ["B", 650]])
    assert "no total" in s and "3,651" not in s
    # derived quantities too: a shortfall in kg and pcs has no total
    s = summarize(["item", "shortfall", "unit"], [["A", 100, "kg"], ["B", 5, "pcs"]])
    assert "no total" in s and "sum 105" not in s
    # one known unit: total is fine
    assert "sum 3,651" in summarize(["item_code", "min_stock_level", "unit"], [["A", 3001, "kg"], ["B", 650, "kg"]])
    # counts and money still get totals
    assert "sum 5" in summarize(["dept", "issues"], [["a", 2], ["b", 3]])


def test_no_chart_when_labels_repeat():
    rows = [["2026-07", 40, "H103"], ["2026-07", 30, "G133"], ["2026-08", 50, "H103"]]
    chart, _ = build_chart(ChartChoice(kind="line", x="month", y=["spend"], title=""), "line", COLUMNS, rows)
    assert chart is None


def test_suggestions_only_on_first_refusal():
    from langchain_core.messages import AIMessage

    state = {"role": "inventory_manager", "decision": "off_topic", "guard": {}, "messages": []}
    first = refuse(state)["response"]["answer"]
    second = refuse({**state, "messages": [AIMessage(first)]})["response"]["answer"]
    assert "You could ask" in first and "You could ask" not in second
