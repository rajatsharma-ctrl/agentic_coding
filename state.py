from typing import Annotated, TypedDict
import operator


class OverallState(TypedDict):
    # ── Research phase ─────────────────────────────────────────────────────────
    # Annotated[int, operator.add] means each parallel scrape_source adds 1.
    # After all parallel scrapes finish, this holds the total pages visited.
    pages_visited: Annotated[int, operator.add]

    # Each scrape_source appends its findings. operator.add concatenates lists
    # so all results accumulate safely across parallel executions.
    raw_findings: Annotated[list, operator.add]

    # ── Decision phase ─────────────────────────────────────────────────────────
    # Loaded once by load_past_posts, read by evaluate_and_draft.
    past_posts: list[dict]

    # Set by evaluate_and_draft. Drives the conditional edge after evaluation.
    is_worth_posting: bool
    rejection_reason: str  # populated when is_worth_posting is False
    draft_post: str        # populated when is_worth_posting is True

    # ── Human review phase ─────────────────────────────────────────────────────
    # Set by human_review after the human approves or edits the draft.
    final_post: str


class ScrapeState(TypedDict):
    """Minimal state passed to scrape_source via the Send API."""
    url: str
