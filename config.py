# ── Sources ────────────────────────────────────────────────────────────────────
# Add or remove URLs here. The agent will scrape these in parallel.
SOURCES = [
    "https://www.databricks.com/blog",
    "https://www.anthropic.com/news",
    "https://openai.com/news",
    "https://news.ycombinator.com",
    "https://www.deeplearning.ai/the-batch/",
    "https://huggingface.co/blog",
    "https://www.confluent.io/blog/",
    "https://engineering.linkedin.com/blog",
]

# ── Limits ─────────────────────────────────────────────────────────────────────
# Maximum number of sources to scrape per run. Caps the fan-out.
MAX_PAGES = 10

# Maximum characters to read from each source page before truncating.
MAX_CONTENT_LENGTH = 3000

# ── Storage ────────────────────────────────────────────────────────────────────
# Path to the JSON file that stores past posts for deduplication.
POSTS_JSON_PATH = "posts.json"
