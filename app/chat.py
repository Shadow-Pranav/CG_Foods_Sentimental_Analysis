"""
Text-to-query chatbot — app/chat.py

Answers natural-language questions about the dataset by having an Anthropic
model call the *existing* /api/* endpoints as tools, rather than generating
raw SQL or doing any embedding/vector-retrieval over comment text. This
means any number the model cites came from the same aggregation code the
dashboard charts themselves call -- there's no second, divergent query path
that could quietly drift from what's on screen.

Deliberately NOT included: a free-text semantic-search tool over comment
content, and no embedding/RAG step anywhere. get_comments exists for
pulling a few illustrative example comments (its `q` param is the same
plain substring match /api/comments already used, not a new retrieval
mechanism) -- the system prompt tells the model to keep page_size small
there and to compute statistics from the aggregation tools instead.

Tool execution goes through a FastAPI TestClient bound to the running app,
not raw SQL -- so a tool call is exactly the HTTP request the dashboard
itself would make, in-process (no real network hop).
"""

import json

MAX_TOOL_ITERATIONS = 4

FILTER_PROPERTIES = {
    "platform": {
        "type": "string",
        "description": "Comma-separated platform filter, e.g. 'youtube,twitter'. Valid values: youtube, twitter, facebook. Omit for all platforms.",
    },
    "sentiment": {
        "type": "string",
        "description": "Comma-separated sentiment filter, e.g. 'negative'. Valid values: positive, negative, neutral. Omit for all sentiments.",
    },
    "region": {
        "type": "string",
        "description": "Comma-separated region filter. Valid values: nepal, india, unknown. Omit for all regions. Region is a best-effort signal, not verified geography -- see DATA_SOURCES.md.",
    },
    "start_date": {"type": "string", "description": "Inclusive start date, YYYY-MM-DD. Omit for no lower bound."},
    "end_date": {"type": "string", "description": "Inclusive end date, YYYY-MM-DD. Omit for no upper bound."},
}


def _filtered_schema(extra_properties=None):
    props = dict(FILTER_PROPERTIES)
    if extra_properties:
        props.update(extra_properties)
    return {"type": "object", "properties": props}


TOOLS = [
    {
        "name": "get_summary",
        "description": "Overall sentiment split (counts + percentages) and the most-discussed theme, "
                        "optionally filtered. Also returns an engagement-weighted view. Use this for "
                        "'what's the overall sentiment' / 'what % is negative' style questions.",
        "input_schema": _filtered_schema(),
    },
    {
        "name": "get_timeline",
        "description": "Monthly sentiment counts (plain and engagement-weighted) across the full date range, "
                        "plus the known real-world events (price hikes, quality fine, trademark ruling, etc.) "
                        "with their dates. Use this for trend-over-time or 'what happened around event X' questions.",
        "input_schema": _filtered_schema(),
    },
    {
        "name": "get_by_platform",
        "description": "Sentiment split broken out per platform (YouTube/X/Facebook). Use this for "
                        "'which platform is most negative/positive' style questions. Note Facebook and X "
                        "volumes are structurally smaller than YouTube -- don't imply the sample sizes are comparable.",
        "input_schema": _filtered_schema(),
    },
    {
        "name": "get_by_region",
        "description": "Sentiment split broken out per region (nepal/india/unknown). Region is a best-effort "
                        "signal, not verified geography; 'unknown' can be a large share. Use this for "
                        "Nepal-vs-India comparison questions.",
        "input_schema": _filtered_schema(),
    },
    {
        "name": "get_themes",
        "description": "Theme x sentiment counts (price, quality, taste, spice, packaging, nostalgia, "
                        "availability, competitor, legal). Use this for 'what are people complaining/praising "
                        "about' questions.",
        "input_schema": _filtered_schema(),
    },
    {
        "name": "get_competitors",
        "description": "Mention count and sentiment split per named competitor (Current Noodles, 2PM Noodles, "
                        "Maggi, Sunfeast Yippee!), plus a monthly trend per competitor. Use this for "
                        "competitor-comparison questions.",
        "input_schema": _filtered_schema(),
    },
    {
        "name": "get_comments",
        "description": "Paginated raw comment table. Use this ONLY to pull a few illustrative example comments "
                        "to quote or reference -- never to compute a statistic (use the aggregation tools above "
                        "for that). Keep page_size small (5 or fewer) since results are shown to the user. "
                        "The optional q parameter does a plain case-insensitive substring match on comment text, "
                        "not semantic search.",
        "input_schema": _filtered_schema({
            "q": {"type": "string", "description": "Optional keyword substring to search within comment text."},
            "page": {"type": "integer", "description": "Page number, 1-indexed. Default 1."},
            "page_size": {"type": "integer", "description": "Rows per page. Keep this small (<=5) for chat use."},
        }),
    },
    {
        "name": "get_events",
        "description": "The list of known real-world events (price hikes, the 2024 KMC quality/health fine, "
                        "the Current Noodles trademark ruling, competitor surge, India expansion push) with "
                        "their dates, categories, and a short description of each. Use this to answer "
                        "'what events are tracked' or 'when did X happen' questions.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

_ENDPOINT_MAP = {
    "get_summary": "/api/summary",
    "get_timeline": "/api/timeline",
    "get_by_platform": "/api/by-platform",
    "get_by_region": "/api/by-region",
    "get_themes": "/api/themes",
    "get_competitors": "/api/competitors",
    "get_comments": "/api/comments",
    "get_events": "/api/events",
}


def execute_tool(test_client, name: str, tool_input: dict) -> dict:
    """Maps a tool call onto the matching GET /api/* endpoint via an
    in-process TestClient -- the exact same code path the dashboard's own
    fetch() calls hit, just without a real network round trip."""
    path = _ENDPOINT_MAP.get(name)
    if path is None:
        return {"error": f"Unknown tool '{name}'"}

    params = {k: v for k, v in (tool_input or {}).items() if v not in (None, "")}
    response = test_client.get(path, params=params)
    if response.status_code != 200:
        return {"error": f"{path} returned HTTP {response.status_code}"}
    return response.json()


def build_label_source_context(db_conn) -> str:
    """Summarizes which sentiment source (VADER vs. the HuggingFace
    transformer) the currently-published labels come from, and whether a
    real human-validated gold sample exists yet -- injected into the
    system prompt so the model can't cite a number as more validated than
    it actually is."""
    import json as _json
    from pathlib import Path

    rows = db_conn.execute(
        "SELECT label_source, COUNT(*) as c FROM comments WHERE exclusion_reason IS NULL GROUP BY label_source"
    ).fetchall()
    counts = {r["label_source"]: r["c"] for r in rows}
    total = sum(counts.values())

    report_path = Path(__file__).resolve().parent.parent / "data" / "classification_report.json"
    report = {}
    if report_path.exists():
        try:
            report = _json.loads(report_path.read_text())
        except _json.JSONDecodeError:
            report = {}

    lines = [
        "LABELING PROVENANCE (mention this if asked how sentiment was determined, or how "
        "trustworthy a number is):",
        f"- Of {total} classified comments, {counts.get('transformer', 0)} are labeled by the "
        f"HuggingFace multilingual transformer validation pass and {counts.get('vader', 0)} by "
        "the VADER rule-based baseline (METHODOLOGY.md's comparison protocol). Which source wins "
        "is decided per pipeline run by comparing both against a gold sample.",
    ]

    if report.get("transformer_available"):
        lines.append(
            f"- Transformer vs. VADER accuracy against the current gold comparison: transformer "
            f"{report.get('transformer_accuracy_vs_gold')}, VADER {report.get('vader_accuracy_vs_gold')} "
            f"(agreement rate {report.get('agreement_rate')})."
        )
    else:
        lines.append(
            "- The transformer validation pass was unavailable when this dataset was last classified "
            "(e.g. no network access to download model weights); all labels are VADER-only for this run."
        )

    if report.get("gold_sample_note") and "synthetic" in report.get("gold_sample_note", ""):
        lines.append(
            "- IMPORTANT: that gold comparison used a SYNTHETIC, template-derived gold_sentiment "
            "column (see generate_sample_data.py), not real human judgment. Do not describe accuracy "
            "figures based on it as 'human-validated'."
        )

    gold_eval = report.get("human_gold_evaluation")
    if gold_eval and gold_eval.get("n_gold_labeled"):
        published = gold_eval.get("final_label_as_published", {})
        lines.append(
            f"- A REAL human-labeled gold sample now exists: {gold_eval['n_gold_labeled']} rows "
            f"hand-labeled, published-label accuracy against it is {published.get('accuracy')}. "
            "You may describe this one as human-validated."
        )
    else:
        lines.append(
            "- No human-labeled gold sample has been completed yet (pipeline/label_gold.py + "
            "pipeline/evaluate.py). Do not claim sentiment labels have been validated against real "
            "human judgment -- they haven't, yet."
        )

    return "\n".join(lines)


SYSTEM_PROMPT_TEMPLATE = """You are a data analyst assistant embedded in the Wai Wai / CG Foods \
Sentiment Tracker dashboard. Answer questions using ONLY the tool results you retrieve in this \
conversation -- never estimate, guess, or recall a number from outside the tool output. Every \
numeric claim must be traceable to a specific tool call you made.

Use the aggregation tools (get_summary, get_timeline, get_by_platform, get_by_region, get_themes, \
get_competitors, get_events) to compute statistics. Use get_comments only to surface a few example \
comments, with a small page_size -- never to eyeball a count.

When you state a filtered number, say what filter it was computed under (e.g. "in the Nepal region" \
or "for negative comments in September 2024"). If a question can't be answered from the available \
tools, say so plainly rather than guessing.

{label_source_context}

Keep answers concise -- a few sentences, plain prose, unless the question genuinely needs a \
breakdown. This is an internal analytics tool, not a marketing chatbot: be direct, cite the numbers, \
skip preamble."""


def run_chat(test_client, anthropic_client, db_conn, question: str) -> dict:
    """Runs the tool-calling loop (capped at MAX_TOOL_ITERATIONS rounds)
    and returns {"answer": str, "tool_calls": [...]}."""
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        label_source_context=build_label_source_context(db_conn)
    )
    messages = [{"role": "user", "content": question}]
    tool_call_log = []

    for _ in range(MAX_TOOL_ITERATIONS):
        response = anthropic_client.messages.create(
            model="claude-opus-5",
            max_tokens=4096,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "refusal":
            return {
                "answer": "I'm not able to answer that one.",
                "tool_calls": tool_call_log,
                "refused": True,
            }

        if response.stop_reason != "tool_use":
            answer = next((b.text for b in response.content if b.type == "text"), "")
            return {"answer": answer, "tool_calls": tool_call_log}

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            output = execute_tool(test_client, block.name, block.input)
            tool_call_log.append({"tool": block.name, "input": block.input, "output": output})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(output),
            })
        messages.append({"role": "user", "content": tool_results})

    # Hit the tool-call cap -- ask once more for a final answer with no further tool calls.
    messages.append({
        "role": "user",
        "content": "You've reached the tool-call limit for this question. Answer now with "
                    "whatever you've already found, and say if you weren't able to fully answer.",
    })
    response = anthropic_client.messages.create(
        model="claude-opus-5",
        max_tokens=4096,
        system=system_prompt,
        tools=TOOLS,
        tool_choice={"type": "none"},
        messages=messages,
    )
    answer = next(
        (b.text for b in response.content if b.type == "text"),
        "I wasn't able to fully answer within the tool-call limit for this question.",
    )
    return {"answer": answer, "tool_calls": tool_call_log}
