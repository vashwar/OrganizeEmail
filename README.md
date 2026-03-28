# Gmail Organizer

A local CLI application that uses a local LLM (via llama-server) to organize your Gmail inbox and historical archives based on semantic content. Built with a two-process architecture using the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/).

## Architecture

```
                                  ┌─────────────────────┐
                                  │   llama-server       │
                                  │   (port 1234)        │
                                  │   Qwen3-4B GGUF      │
                                  └────────▲─────────────┘
                                           │ OpenAI API
┌─────────────────────┐   stdio/JSON-RPC   │   ┌─────────────────────┐
│   cli_agent.py      │◄─────────────────►│   │   gmail_server.py   │
│   (LangChain +      │                       │   (FastMCP server)  │
│    ChatOpenAI)      │                       │                     │
│   "The Brain"       │                       │   "The Hands"       │
└─────────────────────┘                       └────────┬────────────┘
                                                       │
                                                       ▼
                                                 Gmail API
```

- **`llama-server`** — Runs the GGUF model and exposes an OpenAI-compatible API on `http://127.0.0.1:1234`. Started automatically by `start.bat`.
- **`gmail_server.py`** — MCP server exposing 5 tools for Gmail operations (fetch, label, archive). Uses `google-api-python-client` for live Gmail access.
- **`cli_agent.py`** — LangChain orchestrator that connects to both the LLM server and the MCP server, sends emails to the local LLM for semantic categorization, and presents an interactive CLI with human-in-the-loop approval.

## Folder Structure

Everything is self-contained in one folder:

```
EmailOrganizer/
├── start.bat              # One-click launcher
├── cli_agent.py           # Orchestrator
├── gmail_server.py        # MCP server
├── .env                   # Configuration
├── requirements.txt
├── llama-server/          # llama.cpp inference server
│   ├── llama-server.exe
│   └── *.dll
├── models/                # GGUF model file
│   └── Qwen3-4B-Instruct-2507-Q4_K_M.gguf
└── credentials/           # Gmail OAuth credentials
    ├── credentials.json
    └── token.json
```

## Prerequisites

- Python 3.11+
- A Google Cloud project with the Gmail API enabled
- OAuth 2.0 client credentials (`credentials.json`) from Google Cloud Console

## Setup

### 1. Download llama-server

Download the latest CPU release for Windows from the [llama.cpp releases page](https://github.com/ggml-org/llama.cpp/releases). Look for a file named like `llama-<version>-bin-win-cpu-x64.zip`.

Extract the contents into the `llama-server/` folder. You should end up with `llama-server.exe` in there.

### 2. Download the model

Download the Qwen3-4B GGUF model from Hugging Face:

1. Go to: https://huggingface.co/Qwen/Qwen3-4B-Instruct-GGUF
2. Download `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (~2.5 GB)
3. Place it in the `models/` folder

Or download via the Hugging Face CLI:
```bash
pip install huggingface-hub
huggingface-cli download Qwen/Qwen3-4B-Instruct-GGUF Qwen3-4B-Instruct-2507-Q4_K_M.gguf --local-dir models
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Gmail credentials

Place your Google OAuth `credentials.json` in the `credentials/` directory:
```
credentials/
├── credentials.json   # From Google Cloud Console
└── token.json         # Auto-generated on first run
```

Test Gmail authentication:
```bash
python gmail_server.py --test
```

### 5. Configure environment variables (optional)

The `.env` file is pre-configured with defaults. You can customize:
```env
LLM_BASE_URL=http://127.0.0.1:1234/v1
LLM_MODEL=qwen3-4b
EMAIL_CATEGORIES={"Jobs": ["linkedin", "recruiter"], "Bills": ["utility", "invoice"], ...}
MAX_HISTORY_TOKENS=3000   # Token budget for conversation history (tune for your model's context window)
```

## Usage

Double-click `start.bat` or run:
```bash
start.bat
```

This will:
1. Start the llama-server (if not already running)
2. Wait for the model to load
3. Launch the Gmail Organizer CLI

The CLI presents three options:

### Option 1: Triage Unread Emails
Fetches your unread emails, sends them to the local LLM for analysis, and presents a categorization plan grouped by label. You review the plan and type "Approve" to apply labels and archive, or provide feedback to revise.

### Option 2: Purge Legacy Emails
Archives all inbox emails older than a specified number of years. Processes in batches of 500 to respect Gmail API limits.

### Option 3: Categorize Historical Archive
Pages through up to 5 years of inbox history in batches of 10. Emails are first **pre-categorized deterministically** by keyword/sender matching against your configured categories — only unmatched emails are sent to the local LLM. This makes sender-based rules (e.g. `onboarding@resend.dev` → `NewsSummary`) 100% reliable and reduces LLM token usage. Labels are applied and emails are archived. Already-labeled emails are automatically skipped. Supports both manual approval per batch and auto-apply modes.

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
| Family | specific email addresses |
| NewsSummary | onboarding@resend.dev |
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
