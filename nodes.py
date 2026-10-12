import json
import os
from datetime import datetime

from dotenv import load_dotenv
from langchain_community.document_loaders import WebBaseLoader
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from config import MAX_CONTENT_LENGTH, POSTS_JSON_PATH
from state import OverallState, ScrapeState

load_dotenv()

# ── Model ──────────────────────────────────────────────────────────────────────
model = ChatOpenAI(
    model=os.getenv("MODEL_NAME", "claude-sonnet-4-6"),
    base_url=os.getenv("LITELLM_API_BASE"),
    api_key=os.getenv("LITELLM_API_KEY"),
)


# ── Structured output schema for the decision node ────────────────────────────
class PostDecision(BaseModel):
    is_worth_posting: bool = Field(
        description="True if this finding is new, relevant, and worth a LinkedIn post."
    )
    rejection_reason: str = Field(
        description="Why this is not worth posting. Empty string if is_worth_posting is True."
    )
    draft_post: str = Field(
        description="The drafted LinkedIn post (max 200 words). Empty string if is_worth_posting is False."
    )


# ── Node 1: Load past posts ────────────────────────────────────────────────────
def load_past_posts(state: OverallState) -> dict:
    """Read the post history JSON so the decision node can check for duplicates."""
    try:
        with open(POSTS_JSON_PATH, "r") as f:
            past_posts = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        past_posts = []

    return {"past_posts": past_posts}


# ── Node 2: Scrape one source ──────────────────────────────────────────────────
def scrape_source(state: ScrapeState) -> dict:
    """
    Scrape a single URL and return its content.
    Receives {"url": "..."} from the Send API.
    Returns raw_findings (appended via operator.add) and increments pages_visited by 1.
    """
    url = state["url"]
    try:
        loader = WebBaseLoader(url)
        docs = loader.load()
        content = docs[0].page_content[:MAX_CONTENT_LENGTH] if docs else ""
    except Exception as e:
        content = f"Could not scrape {url}: {e}"

    return {
        "raw_findings": [{"url": url, "content": content}],
        "pages_visited": 1,
    }


# ── Node 3: Evaluate findings and draft a post ────────────────────────────────
def evaluate_and_draft(state: OverallState) -> dict:
    """
    Agent B — reads raw findings and post history, decides whether to post,
    and drafts the LinkedIn post if the decision is yes.
    """
    past_posts_text = (
        "\n".join(
            f"- {p.get('content', '')[:120]}" for p in state["past_posts"]
        )
        or "No past posts yet."
    )

    findings_text = "\n\n".join(
        f"Source: {f['url']}\n{f['content'][:500]}"
        for f in state["raw_findings"]
    )

    system_prompt = f"""You are a strict editor for a LinkedIn account focused on Data Engineering and AI.

RECENT FINDINGS (last 24 hours):
{findings_text}

ALREADY POSTED (do not repeat these topics):
{past_posts_text}

RULES:
1. Pick the single most interesting and genuinely new development.
2. Reject anything that is substantially similar to a past post.
3. Reject anything that is not directly relevant to Data Engineering or AI.
4. Reject if nothing meaningful happened — it is better to skip than to post noise.
5. If posting, write a concise, professional LinkedIn post (max 200 words).
   Lead with the insight, not "I wanted to share..."."""

    decision = model.with_structured_output(PostDecision).invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content="Evaluate the findings and make your decision."),
        ]
    )

    return {
        "is_worth_posting": decision.is_worth_posting,
        "rejection_reason": decision.rejection_reason,
        "draft_post": decision.draft_post,
    }


# ── Node 4: Human review (HITL) ───────────────────────────────────────────────
def human_review(state: OverallState) -> dict:
    """
    Pause execution and present the draft post to the human.
    Resume by calling graph.invoke(Command(resume="<approved text>"), config).
    The human can approve as-is or provide an edited version.
    """
    approved_text = interrupt(
        {
            "draft_post": state["draft_post"],
            "instructions": (
                "Review the draft above. Resume with the approved text "
                "(edit freely or paste as-is). Pass empty string to use draft as-is."
            ),
        }
    )
    return {"final_post": approved_text or state["draft_post"]}


# ── Node 5: Save approved post ────────────────────────────────────────────────
def save_post(state: OverallState) -> dict:
    """Append the approved post to posts.json for future deduplication."""
    try:
        with open(POSTS_JSON_PATH, "r") as f:
            posts = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        posts = []

    posts.append(
        {
            "content": state.get("final_post") or state.get("draft_post", ""),
            "posted_at": datetime.now().isoformat(),
        }
    )

    with open(POSTS_JSON_PATH, "w") as f:
        json.dump(posts, f, indent=2)

    return {}


# ── Node 6: Publish to LinkedIn ───────────────────────────────────────────────
def publish_post(state: OverallState) -> dict:
    """Publish the approved post to LinkedIn via the UGC Posts API."""
    from linkedin_client import post_to_linkedin

    text = state.get("final_post") or state.get("draft_post", "")
    result = post_to_linkedin(text)
    post_id = result.get("id", result.get("status", "unknown"))
    print(f"\n── Published to LinkedIn (id: {post_id}) ──────────────")
    print(text)
    print("─────────────────────────────────────────────\n")
    return {}
