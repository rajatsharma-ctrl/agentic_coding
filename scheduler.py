"""
Local scheduler for the LinkedIn posting agent.
Runs fully automatically at 09:00 every day — no human review.

Usage:
    python scheduler.py              # runs daily at 09:00
    python scheduler.py --now        # run immediately once (for testing)
"""

import sys
from datetime import datetime

import schedule
import time

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from config import MAX_PAGES, SOURCES
from nodes import evaluate_and_draft, load_past_posts, publish_post, save_post, scrape_source
from state import OverallState

RUN_AT = "09:00"


# ── Auto graph (no human_review node) ─────────────────────────────────────────

def _initiate_research(state: OverallState) -> list:
    return [Send("scrape_source", {"url": url}) for url in SOURCES[:MAX_PAGES]]


def _route_after_evaluation(state: OverallState) -> str:
    if state["is_worth_posting"]:
        return "save_post"
    return END


builder = StateGraph(OverallState)
builder.add_node("load_past_posts", load_past_posts)
builder.add_node("scrape_source", scrape_source)
builder.add_node("evaluate_and_draft", evaluate_and_draft)
builder.add_node("save_post", save_post)
builder.add_node("publish_post", publish_post)

builder.add_edge(START, "load_past_posts")
builder.add_conditional_edges("load_past_posts", _initiate_research)
builder.add_edge("scrape_source", "evaluate_and_draft")
builder.add_conditional_edges(
    "evaluate_and_draft",
    _route_after_evaluation,
    {"save_post": "save_post", END: END},
)
builder.add_edge("save_post", "publish_post")
builder.add_edge("publish_post", END)

auto_graph = builder.compile()


# ── Run function ───────────────────────────────────────────────────────────────

def run():
    print(f"\n[{datetime.now():%Y-%m-%d %H:%M}] Starting scheduled run...")
    try:
        for chunk in auto_graph.stream({}, stream_mode="updates"):
            node = next(iter(chunk))
            data = chunk[node]
            if node == "evaluate_and_draft" and not data.get("is_worth_posting"):
                reason = data.get("rejection_reason", "")
                print(f"  ── {node}: nothing worth posting — {reason}")
            else:
                print(f"  ── {node} complete")
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] Run complete.\n")
    except Exception as e:
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] Run failed: {e}\n")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--now" in sys.argv:
        run()
    else:
        schedule.every().day.at(RUN_AT).do(run)
        print(f"Scheduler started. Agent runs daily at {RUN_AT}.")
        print("Keep this terminal open. Ctrl+C to stop.\n")
        while True:
            schedule.run_pending()
            time.sleep(30)
