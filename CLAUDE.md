# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A local CLI application that uses a local LLM (via llama-server's OpenAI-compatible API) to organize a live Gmail inbox and historical archives based on semantic content. The architecture is split into two processes communicating via the Model Context Protocol (MCP) over stdio. The `start.bat` script launches llama-server automatically from the `llama-server/` subfolder.

## Architecture

**Two-process MCP design:**

- `gmail_server.py` — FastMCP server ("the hands"). Exposes 5 Gmail tools (`fetch_unread_emails`, `archive_emails`, `label_emails`, `archive_legacy_emails`, `fetch_historical_batch`). Communicates with Gmail API using `google-api-python-client`. Runs as a subprocess spawned by the orchestrator. All logging goes to stderr to keep stdout clean for MCP JSON-RPC.

- `cli_agent.py` — LangChain/LangGraph orchestrator ("the brain"). Connects to the MCP server via `langchain_mcp_adapters.client.MultiServerMCPClient` using stdio transport. Uses `ChatOpenAI` pointed at a local llama-server (OpenAI-compatible API on port 1234) with a `create_react_agent` from langgraph. For Options 1 & 2, the LLM agent drives tool calls. For Option 3 (historical categorization), Python directly invokes MCP tools for pagination/labeling/archiving and only sends email text to the LLM for categorization decisions.

**Key data flow for Option 3:** Python loop fetches batches → sends email text to LLM for JSON categorization → parses JSON response → calls `label_emails` and `archive_emails` tools directly (bypassing agent). This avoids the LLM making tool calls for bulk operations.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Test Gmail authentication (prints to stderr, keeps stdout clean)
python gmail_server.py --test

# Run the CLI application
python cli_agent.py
```

## Configuration

- `credentials/credentials.json` — Google OAuth client credentials (not committed)
- `credentials/token.json` — Cached OAuth token (auto-generated on first auth)
- `.env` — Contains `LLM_BASE_URL`, `LLM_MODEL`, and `EMAIL_CATEGORIES` (JSON object mapping label names to keyword arrays)

Categories in `.env` are loaded as `EMAIL_CATEGORIES` dict. If missing or invalid JSON, defaults are used (defined in `cli_agent.py` lines 49-61). The LLM uses these categories plus "Misc" as the allowed label set.

## Important Constraints

- The MCP server uses `mcp.run(transport="stdio")` — stdout must never have non-JSON-RPC output. Use `log` (which writes to stderr) instead of `print()` in `gmail_server.py`.
- Gmail API rate limits are handled via `_retry_with_backoff()` with exponential backoff on HTTP 429/500/503.
- `archive_legacy_emails` processes in batches of 500 (Gmail API `batchModify` limit).
- `fetch_historical_batch` skips emails that already have a user-applied label (non-system label), tracked via `has_user_label` in `_extract_email_details`.
- Label cache (`_label_cache`) is populated lazily on first use; new labels are created on-the-fly via `_get_or_create_label`.
- Windows requires `asyncio.WindowsProactorEventLoopPolicy()` (set in `cli_agent.py`).
