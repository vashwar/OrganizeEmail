# Gmail Organizer

A local CLI application that uses the Gemini API to organize your Gmail inbox and historical archives based on semantic content. Built with a two-process architecture using the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/).

## Architecture

```
┌─────────────────────┐   stdio/JSON-RPC   ┌─────────────────────┐
│   cli_agent.py      │◄─────────────────►│   gmail_server.py   │
│   (LangChain +      │                    │   (FastMCP server)  │
│    Gemini LLM)      │                    │                     │
│   "The Brain"       │                    │   "The Hands"       │
└─────────────────────┘                    └────────┬────────────┘
                                                    │
                                                    ▼
                                              Gmail API
```

- **`gmail_server.py`** — MCP server exposing 5 tools for Gmail operations (fetch, label, archive). Uses `google-api-python-client` for live Gmail access.
- **`cli_agent.py`** — LangChain orchestrator that connects to the MCP server, sends emails to Gemini for semantic categorization, and presents an interactive CLI with human-in-the-loop approval.

## Prerequisites

- Python 3.11+
- A Google Cloud project with the Gmail API enabled
- OAuth 2.0 client credentials (`credentials.json`) from Google Cloud Console
- A [Google AI Studio](https://aistudio.google.com/) API key for Gemini

## Setup

1. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

2. **Configure credentials:**

   Place your Google OAuth `credentials.json` in the `credentials/` directory:
   ```
   credentials/
   ├── credentials.json   # From Google Cloud Console
   └── token.json         # Auto-generated on first run
   ```

3. **Configure environment variables:**

   Create a `.env` file in the project root:
   ```env
   GOOGLE_API_KEY=your-gemini-api-key
   GEMINI_MODEL=gemini-2.0-flash
   ```

   Optionally customize email categories by adding an `EMAIL_CATEGORIES` variable as a JSON object:
   ```env
   EMAIL_CATEGORIES={"Jobs": ["linkedin", "recruiter"], "Bills": ["utility", "invoice"], ...}
   ```

4. **Test Gmail authentication:**

   ```bash
   python gmail_server.py --test
   ```

   This will open a browser for OAuth consent on first run, then print your email address and a sample of unread emails.

## Usage

```bash
python cli_agent.py
```

The CLI presents three options:

### Option 1: Triage Unread Emails
Fetches your unread emails, sends them to Gemini for analysis, and presents a categorization plan grouped by label. You review the plan and type "Approve" to apply labels and archive, or provide feedback to revise.

### Option 2: Purge Legacy Emails
Archives all inbox emails older than a specified number of years. Processes in batches of 500 to respect Gmail API limits.

### Option 3: Categorize Historical Archive
Pages through up to 5 years of inbox history in configurable batch sizes. Each batch is sent to Gemini for categorization, then labels are applied and emails are archived. Already-labeled emails are automatically skipped. Supports both manual approval per batch and auto-apply modes.

## Email Categories

Default categories (customizable via `.env`):

| Category | Example Keywords |
|---|---|
| Jobs | linkedin, recruiter, career, hiring |
| Academic | haas, ewmba, .edu, university |
| Online Shopping | amazon, ebay, target, walmart |
| Grocery | whole foods, meat corner, instacart |
| Restaurant | doordash, ubereats, grubhub |
| Bills | pg&e, utility, payment due, invoice |
| Travel | airline, flight, hotel, boarding pass |
| Banks/Investment | bank, chase, credit card, venmo |
| Social Media | facebook, instagram, twitter |
| Newsletters | substack, medium, newsletter |
| Promotions | sale, coupon, promo, deal |
| Misc | anything that doesn't match above |

## MCP Tools

The server exposes these tools over stdio:

| Tool | Description |
|---|---|
| `fetch_unread_emails` | Fetch latest unread inbox emails |
| `archive_emails` | Remove INBOX label from specified emails |
| `label_emails` | Apply labels to emails (auto-creates labels) |
| `archive_legacy_emails` | Batch-archive emails older than N years |
| `fetch_historical_batch` | Paginated fetch for historical categorization |
