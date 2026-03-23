"""
CLI Orchestrator - LangChain agent that uses a local LLM to triage Gmail via the
GmailOrganizer MCP server.
"""

import sys
import os
import json
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
import warnings
warnings.filterwarnings("ignore", message="create_react_agent has been moved")
from langgraph.prebuilt import create_react_agent

# ---------------------------------------------------------------------------
# Windows async compatibility
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3-4b")

GMAIL_SERVER_PATH = str(Path(__file__).parent / "gmail_server.py")
PROJECT_DIR = str(Path(__file__).parent)

# ---------------------------------------------------------------------------
# Load categories from .env
# ---------------------------------------------------------------------------
_raw_categories = os.getenv("EMAIL_CATEGORIES", "{}")
try:
    EMAIL_CATEGORIES: dict[str, list[str]] = json.loads(_raw_categories)
except json.JSONDecodeError:
    print("WARNING: EMAIL_CATEGORIES in .env is not valid JSON. Using defaults.")
    EMAIL_CATEGORIES = {}

if not EMAIL_CATEGORIES:
    EMAIL_CATEGORIES = {
        "Jobs": ["linkedin", "recruiter", "job", "career", "hiring"],
        "Academic": ["haas", "ewmba", ".edu", "university", "berkeley"],
        "Online Shopping": ["amazon", "ebay", "target", "walmart", "etsy"],
        "Grocery": ["whole foods", "meat corner", "instacart", "grocery"],
        "Restaurant": ["doordash", "ubereats", "grubhub", "restaurant"],
        "Bills": ["pg&e", "utility", "bill", "payment due", "invoice"],
        "Travel": ["airline", "flight", "hotel", "booking", "boarding pass"],
        "Banks/Investment": ["bank", "chase", "wells fargo", "credit card", "venmo", "zelle"],
        "Social Media": ["facebook", "instagram", "twitter", "tiktok", "reddit"],
        "Newsletters": ["substack", "medium", "newsletter", "digest"],
        "Promotions": ["sale", "coupon", "promo", "% off", "deal", "loyalty"],
        "Family": ["rashna9@gmail.com", "harun.rashid68@yahoo.com", "harunur.rashid68@gmail.com", "laila.rashid1980@gmail.com"],
    }

# Build the category list for the system prompt
_category_lines = "\n".join(
    f"- **{name}**: Match keywords: {', '.join(kws)}" for name, kws in EMAIL_CATEGORIES.items()
)

# ---------------------------------------------------------------------------
# System prompt with categorization rules
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = f"""\
You are an intelligent email triage assistant. You analyze emails and categorize
them according to the rules below. You also help archive and label emails in
the user's Gmail inbox.

## Categorization Rules

Assign each email to exactly ONE category from the list below. Use the keywords
as hints but also use your judgement based on sender name and subject content.
If an email does not clearly fit any category, assign it to "Misc".

{_category_lines}
- **Misc**: Anything that does not clearly fit the categories above.

## Behaviour Rules

1. When asked to triage emails, fetch them, analyze each one, and present a
   **triage plan** - a table grouping emails by category with proposed actions
   (label + archive).
2. Wait for the user to say "Approve" before executing any changes.
3. If the user provides feedback, revise the plan accordingly and present it
   again.
4. When executing an approved plan, first call `label_emails` with the full
   mapping, then call `archive_emails` for any emails the plan marks for
   archiving.
5. For legacy purge, simply run `archive_legacy_emails` with the number of
   years the user specifies and report the final count.
6. For historical categorization, process emails batch by batch. For each batch,
   read the sender and subject of every email and assign it to one of the
   allowed categories above (or "Misc"). Present a triage plan for that batch.
   After the user approves and the batch is applied, ask if they want to
   continue with the next batch.
"""

# ---------------------------------------------------------------------------
# MCP client configuration
# ---------------------------------------------------------------------------
MCP_SERVERS = {
    "gmail": {
        "command": sys.executable,
        "args": [GMAIL_SERVER_PATH],
        "cwd": PROJECT_DIR,
        "transport": "stdio",
    }
}

# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

async def run_agent(user_message: str, agent, history: list) -> str:
    """Invoke the agent and return its final text response."""
    history.append({"role": "user", "content": user_message})

    response = await agent.ainvoke({"messages": history})

    # Extract the last AI message's text content
    messages = response.get("messages", [])
    assistant_reply = ""
    for msg in reversed(messages):
        if not (hasattr(msg, "type") and msg.type == "ai"):
            continue
        content = msg.content
        if isinstance(content, str):
            assistant_reply = content
        elif isinstance(content, list):
            text_parts = [
                block if isinstance(block, str) else block.get("text", "")
                for block in content
                if isinstance(block, str) or (isinstance(block, dict) and block.get("type") == "text")
            ]
            assistant_reply = "\n".join(text_parts)
        else:
            assistant_reply = str(content)
        if assistant_reply.strip():
            break

    history.append({"role": "assistant", "content": assistant_reply})
    return assistant_reply


# ---------------------------------------------------------------------------
# Menu handlers
# ---------------------------------------------------------------------------

async def triage_unread(agent, history: list):
    """Option 1: Triage unread emails with human-in-the-loop approval."""
    print("\n--- Fetching unread emails and generating triage plan... ---\n")

    reply = await run_agent(
        "Fetch my unread emails and present a triage plan. "
        "Group them by category and show which label to apply to each. "
        "Do NOT execute any changes yet - just show the plan.",
        agent, history
    )
    print(reply)

    # Approval loop
    while True:
        feedback = input("\nType 'Approve' to execute the plan, or provide feedback to revise: ").strip()
        if not feedback:
            continue

        if feedback.lower() == "approve":
            print("\n--- Executing approved triage plan... ---\n")
            reply = await run_agent(
                "The user has approved the plan. Execute it now: "
                "apply the labels using label_emails and archive the emails using archive_emails.",
                agent, history
            )
            print(reply)
            break
        else:
            print("\n--- Revising plan based on your feedback... ---\n")
            reply = await run_agent(
                f"The user wants changes to the plan. Their feedback: {feedback}\n"
                "Please revise the triage plan and present it again. Do NOT execute yet.",
                agent, history
            )
            print(reply)


async def purge_legacy(agent, history: list):
    """Option 2: Archive emails older than N years (user chooses)."""
    while True:
        years_input = input("\nHow many years old should emails be to archive? (e.g. 3, 5, 10): ").strip()
        try:
            years = int(years_input)
            if years < 1:
                print("Please enter a positive number.")
                continue
            break
        except ValueError:
            print("Invalid number. Please enter a whole number like 3, 5, or 10.")

    print(f"\n--- Purging emails older than {years} year(s)... ---\n")
    reply = await run_agent(
        f"Archive all emails older than {years} years using the archive_legacy_emails tool "
        f"with years_older_than={years}. Report the total count when done.",
        agent, history
    )
    print(reply)


async def categorize_historical(agent, tools: list, history: list):
    """Option 3: Categorize historical emails batch-by-batch.

    Python drives the pagination loop and calls fetch/label/archive tools
    directly. The LLM is only used for categorization decisions per batch.
    """

    # Grab direct tool references for calling without the LLM
    fetch_tool = next(t for t in tools if t.name == "fetch_historical_batch")
    label_tool = next(t for t in tools if t.name == "label_emails")
    archive_tool = next(t for t in tools if t.name == "archive_emails")

    # Show configured categories
    print("\n--- Categories loaded from .env ---")
    for name, keywords in EMAIL_CATEGORIES.items():
        print(f"  {name}: {', '.join(keywords[:5])}{'...' if len(keywords) > 5 else ''}")
    print(f"  Misc: (anything that doesn't match above)")

    batch_size = 10

    # Ask if user wants approval per batch or auto-process all
    approval_choice = input("\nRequire approval before applying labels? (yes/no, default yes): ").strip().lower()
    require_approval = approval_choice not in ("no", "n")

    if require_approval:
        print("Mode: Review each batch before applying.")
    else:
        print("Mode: Auto-apply labels to all batches without approval.")

    allowed_categories = ", ".join(list(EMAIL_CATEGORIES.keys()) + ["Misc"])
    page_token = None
    batch_num = 0
    total_categorized = 0
    total_skipped = 0

    while True:
        batch_num += 1
        print(f"\n--- Batch {batch_num}: fetching up to {batch_size} emails... ---\n")

        # Step 1: Fetch emails directly (Python controls pagination)
        fetch_args = {
            "query": "newer_than:5y in:inbox",
            "max_results": batch_size,
        }
        if page_token:
            fetch_args["page_token"] = page_token

        try:
            raw_result = await fetch_tool.ainvoke(fetch_args)
        except Exception as exc:
            print(f"Error fetching emails: {exc}")
            break

        # Parse the tool result. MCP adapter returns a list of content blocks:
        # [{"type": "text", "text": "{...json...}"}]
        result_text = ""
        if isinstance(raw_result, list):
            # Extract text from content blocks
            for block in raw_result:
                if isinstance(block, dict) and block.get("type") == "text":
                    result_text = block.get("text", "")
                    break
                elif isinstance(block, str):
                    result_text = block
                    break
        elif isinstance(raw_result, str):
            result_text = raw_result
        elif isinstance(raw_result, dict):
            result = raw_result
            result_text = None  # already parsed

        if result_text is not None:
            try:
                result = json.loads(result_text)
            except (json.JSONDecodeError, TypeError):
                result = {"emails": str(raw_result), "nextPageToken": None, "count": 0, "skipped": 0}

        emails_text = result.get("emails", "No emails found.")
        next_page_token = result.get("nextPageToken")
        count = result.get("count", 0)
        skipped = result.get("skipped", 0)
        total_skipped += skipped

        print(f"Fetched: {count} uncategorized, {skipped} skipped (already labeled)")

        if count == 0:
            if next_page_token:
                # All emails in this page were already labeled, skip to next
                print("All emails in this page already labeled. Moving to next page...")
                page_token = next_page_token
                continue
            else:
                print("\n--- No more uncategorized emails. Done! ---")
                break

        # Step 2: Send emails to LLM for categorization only
        categorize_prompt = (
            f"Here are {count} emails to categorize. For each email, assign exactly ONE "
            f"category from: {allowed_categories}.\n\n"
            f"{emails_text}\n\n"
            f"Respond with ONLY a JSON array of objects, no other text:\n"
            f'[{{"email_id": "...", "label_name": "..."}}, ...]\n'
            f"Use the sender and subject to decide the category. If unsure, use \"Misc\"."
        )

        reply = await run_agent(categorize_prompt, agent, history)

        # Parse the JSON mapping from the LLM reply
        mapping = _parse_label_mapping(reply)

        if not mapping:
            print("Could not parse categorization from LLM. Raw response:")
            print(reply)
            if require_approval:
                skip = input("Skip this batch? (yes/no): ").strip().lower()
                if skip in ("yes", "y"):
                    page_token = next_page_token
                    if not page_token:
                        break
                    continue
            page_token = next_page_token
            if not page_token:
                break
            continue

        # Show summary
        category_counts: dict[str, int] = {}
        for item in mapping:
            cat = item["label_name"]
            category_counts[cat] = category_counts.get(cat, 0) + 1

        print(f"\nCategorization plan ({len(mapping)} emails):")
        for cat, cnt in sorted(category_counts.items()):
            print(f"  {cat}: {cnt}")

        if require_approval:
            # Approval loop
            while True:
                feedback = input("\nType 'Approve' to apply, 'Skip' to skip, or provide feedback: ").strip()
                if not feedback:
                    continue
                if feedback.lower() == "approve":
                    break
                elif feedback.lower() == "skip":
                    mapping = None
                    break
                else:
                    # Let LLM revise
                    reply = await run_agent(
                        f"Revise the categorization based on this feedback: {feedback}\n"
                        f"Only use these categories: {allowed_categories}.\n"
                        f"Respond with ONLY the JSON array.",
                        agent, history
                    )
                    mapping = _parse_label_mapping(reply)
                    if mapping:
                        category_counts = {}
                        for item in mapping:
                            cat = item["label_name"]
                            category_counts[cat] = category_counts.get(cat, 0) + 1
                        print(f"\nRevised plan ({len(mapping)} emails):")
                        for cat, cnt in sorted(category_counts.items()):
                            print(f"  {cat}: {cnt}")
                    else:
                        print("Could not parse revised plan:")
                        print(reply)

        # Step 3: Apply labels and archive
        if mapping:
            print("\nApplying labels...", end=" ", flush=True)
            try:
                label_result = await label_tool.ainvoke({"email_label_mapping": mapping})
                print(_extract_tool_text(label_result))
            except Exception as exc:
                print(f"Error: {exc}")

            email_ids = [item["email_id"] for item in mapping]
            print("Archiving...", end=" ", flush=True)
            try:
                archive_result = await archive_tool.ainvoke({"email_ids": email_ids})
                print(_extract_tool_text(archive_result))
            except Exception as exc:
                print(f"Error: {exc}")

            total_categorized += len(mapping)

        print(f"\n--- Batch {batch_num} done. Running total: {total_categorized} categorized, {total_skipped} skipped ---")

        # Step 4: Continue to next page
        if not next_page_token:
            print("\n--- All pages processed. Done! ---")
            break

        page_token = next_page_token

        if require_approval:
            cont = input("\nMore emails available. Continue? (yes/no, default yes): ").strip().lower()
            if cont in ("no", "n"):
                print("Done categorizing.")
                break

    print(f"\nFinal totals: {total_categorized} categorized, {total_skipped} skipped (already labeled).")


def _extract_tool_text(result) -> str:
    """Extract the text string from an MCP tool result (list of content blocks)."""
    if isinstance(result, list):
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
            if isinstance(block, str):
                return block
    if isinstance(result, str):
        return result
    return str(result)


def _parse_label_mapping(text: str) -> list[dict] | None:
    """Extract a JSON array of {email_id, label_name} from LLM text."""
    # Try to find JSON array in the response
    # Strip markdown code fences if present
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1]
        cleaned = cleaned.split("```", 1)[0]
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1]
        cleaned = cleaned.split("```", 1)[0]

    # Try to find the JSON array
    cleaned = cleaned.strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1:
        return None

    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None

    if not isinstance(data, list):
        return None

    # Validate each item
    valid = []
    for item in data:
        if isinstance(item, dict) and "email_id" in item and "label_name" in item:
            valid.append({"email_id": str(item["email_id"]), "label_name": str(item["label_name"])})

    return valid if valid else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("  Gmail Organizer - Powered by Local LLM + MCP")
    print("=" * 60)

    mcp_client = MultiServerMCPClient(MCP_SERVERS)
    tools = await mcp_client.get_tools()
    print(f"\nConnected to MCP server. {len(tools)} tool(s) available.")

    # Show loaded categories
    cat_names = list(EMAIL_CATEGORIES.keys()) + ["Misc"]
    print(f"Categories: {', '.join(cat_names)}")

    # Verify llama-server is reachable before proceeding
    import urllib.request
    import urllib.error
    health_url = LLM_BASE_URL.rsplit("/v1", 1)[0] + "/health"
    try:
        urllib.request.urlopen(health_url, timeout=5)
    except (urllib.error.URLError, OSError):
        print(f"\nERROR: Cannot connect to LLM server at {LLM_BASE_URL}")
        print("Make sure llama-server is running. Use start.bat to launch it automatically.")
        return

    llm = ChatOpenAI(
        base_url=LLM_BASE_URL,
        api_key="not-needed",
        model=LLM_MODEL,
        temperature=0.2,
    )

    agent = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
    history: list = []

    while True:
        print("\n" + "-" * 40)
        print("  1. Triage Unread Emails")
        print("  2. Purge Legacy Emails")
        print("  3. Categorize Historical Archive")
        print("  4. Exit")
        print("-" * 40)

        choice = input("Select an option (1-4): ").strip()

        if choice == "1":
            await triage_unread(agent, history)
        elif choice == "2":
            await purge_legacy(agent, history)
        elif choice == "3":
            await categorize_historical(agent, tools, history)
        elif choice == "4":
            print("Goodbye!")
            break
        else:
            print("Invalid option. Please enter 1, 2, 3, or 4.")


if __name__ == "__main__":
    asyncio.run(main())
