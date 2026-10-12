from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from config import MAX_PAGES, SOURCES
from nodes import (
    evaluate_and_draft,
    human_review,
    load_past_posts,
    publish_post,
    save_post,
    scrape_source,
)
from state import OverallState


# ── Conditional edge: fan out to sources ──────────────────────────────────────
def initiate_research(state: OverallState) -> list:
    """
    Called after load_past_posts completes.
    Fans out to each source in parallel via Send, capped at MAX_PAGES.
    Each Send dispatch runs scrape_source independently with its own URL.
    """
    sources_to_check = SOURCES[:MAX_PAGES]
    return [Send("scrape_source", {"url": url}) for url in sources_to_check]


# ── Conditional edge: route after evaluation ──────────────────────────────────
def route_after_evaluation(state: OverallState) -> str:
    """
    If the LLM decided the content is worth posting, go to human review.
    Otherwise end immediately — no point in waking the human up for nothing.
    """
    if state["is_worth_posting"]:
        return "human_review"
    print(f"Nothing worth posting today. Reason: {state['rejection_reason']}")
    return END


# ── Build graph ───────────────────────────────────────────────────────────────
builder = StateGraph(OverallState)

builder.add_node("load_past_posts", load_past_posts)
builder.add_node("scrape_source", scrape_source)
builder.add_node("evaluate_and_draft", evaluate_and_draft)
builder.add_node("human_review", human_review)
builder.add_node("save_post", save_post)
builder.add_node("publish_post", publish_post)

# Flow
builder.add_edge(START, "load_past_posts")
builder.add_conditional_edges("load_past_posts", initiate_research)  # fan-out
builder.add_edge("scrape_source", "evaluate_and_draft")              # fan-in
builder.add_conditional_edges(
    "evaluate_and_draft",
    route_after_evaluation,
    {"human_review": "human_review", END: END},
)
builder.add_edge("human_review", "save_post")
builder.add_edge("save_post", "publish_post")
builder.add_edge("publish_post", END)

graph = builder.compile()


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from langchain_core.messages import HumanMessage
    from langgraph.types import Command

    config = {"configurable": {"thread_id": "linkedin-agent-1"}}

    print("Starting research...\n")

    # Run until interrupt
    for chunk in graph.stream({}, config, stream_mode="updates"):
        node = next(iter(chunk))
        print(f"── {node} complete")
        if node == "evaluate_and_draft":
            state = graph.get_state(config)
            if not state.values.get("is_worth_posting"):
                break
        if "__interrupt__" in chunk:
            draft = chunk["__interrupt__"][0].value["draft_post"]
            print(f"\nDraft post:\n{draft}\n")
            approved = input("Approve / edit (paste final text, or press Enter to use as-is):\n> ")
            # Resume with the approved text
            for chunk in graph.stream(Command(resume=approved), config, stream_mode="updates"):
                node = next(iter(chunk))
                print(f"── {node} complete")
            break
