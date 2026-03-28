"""
Gmail MCP Server  - FastMCP server exposing 5 tools for Gmail operations.
Uses google-api-python-client to interact with live Gmail data.
"""

import os
import json
import time
import base64
import logging
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCOPES = ["https://mail.google.com/"]
CREDENTIALS_DIR = Path(__file__).parent / "credentials"
CREDENTIALS_FILE = CREDENTIALS_DIR / "credentials.json"
TOKEN_FILE = CREDENTIALS_DIR / "token.json"

# Send all logging to stderr so stdout stays clean for MCP JSON-RPC
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    stream=__import__("sys").stderr,
)
log = logging.getLogger("gmail_server")

mcp = FastMCP("GmailOrganizer")

# ---------------------------------------------------------------------------
# Label cache (populated lazily, avoids repeated API calls)
# ---------------------------------------------------------------------------
_label_cache: dict[str, str] = {}  # label_name -> label_id

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_gmail_service():
    """Authenticate and return a Gmail API service object.

    If the stored token lacks the required gmail scope, the user is prompted
    to re-authorize via the browser.
    """
    creds: Optional[Credentials] = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    # Check if scopes actually match  - force re-auth if they don't
    if creds and not set(SCOPES).issubset(set(creds.scopes or [])):
        log.warning("Token scopes %s do not include required %s  - re-authorizing.", creds.scopes, SCOPES)
        creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                log.warning("Token refresh failed  - re-authorizing.")
                creds = None

        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json())
        log.info("Token saved to %s", TOKEN_FILE)

    return build("gmail", "v1", credentials=creds)


def _retry_with_backoff(fn, *args, max_retries: int = 5, **kwargs):
    """Call *fn* with exponential backoff on transient HTTP errors."""
    delay = 1
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except HttpError as exc:
            code = exc.resp.status if exc.resp else 0
            if code in (429, 500, 503) and attempt < max_retries - 1:
                log.warning("HTTP %s on attempt %d  - retrying in %ds", code, attempt + 1, delay)
                time.sleep(delay)
                delay = min(delay * 2, 16)
            else:
                raise


SYSTEM_LABEL_IDS = {
    "INBOX", "SENT", "TRASH", "SPAM", "UNREAD", "STARRED", "IMPORTANT",
    "DRAFT", "CHAT", "CATEGORY_PERSONAL", "CATEGORY_SOCIAL",
    "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_FORUMS",
}


def _extract_email_details(msg: dict) -> dict:
    """Pull useful header fields from a Gmail API message resource."""
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    label_ids = msg.get("labelIds", [])
    has_user_label = any(lid not in SYSTEM_LABEL_IDS for lid in label_ids)
    return {
        "email_id": msg["id"],
        "From": headers.get("from", "Unknown"),
        "Subject": headers.get("subject", "(no subject)"),
        "Snippet": msg.get("snippet", ""),
        "has_user_label": has_user_label,
    }


def _format_email_list(details_list: list[dict]) -> str:
    """Return a human-readable block of email summaries."""
    lines: list[str] = []
    for i, d in enumerate(details_list, 1):
        lines.append(
            f"{i}. ID: {d['email_id']}\n"
            f"   From: {d['From']}\n"
            f"   Subject: {d['Subject']}\n"
            f"   Snippet: {d['Snippet']}\n"
        )
    return "\n".join(lines) if lines else "No emails found."


def _get_or_create_label(service, label_name: str) -> str:
    """Return the label ID for *label_name*, creating the label if necessary."""
    global _label_cache

    # Check cache first
    if label_name in _label_cache:
        return _label_cache[label_name]

    # Populate cache from Gmail
    if not _label_cache:
        results = _retry_with_backoff(
            service.users().labels().list(userId="me").execute
        )
        for lbl in results.get("labels", []):
            _label_cache[lbl["name"]] = lbl["id"]

    if label_name in _label_cache:
        return _label_cache[label_name]

    # Create the label
    body = {
        "name": label_name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    created = _retry_with_backoff(
        service.users().labels().create(userId="me", body=body).execute
    )
    _label_cache[created["name"]] = created["id"]
    log.info("Created label '%s' (id=%s)", created["name"], created["id"])
    return created["id"]


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def fetch_unread_emails(max_results: int = 15) -> str:
    """Fetch the latest unread emails from the inbox.

    Returns a formatted string with email_id, Sender, Subject, and Snippet.
    """
    try:
        service = get_gmail_service()
        results = _retry_with_backoff(
            service.users().messages().list(
                userId="me", q="is:unread in:inbox", maxResults=max_results
            ).execute
        )
        messages = results.get("messages", [])
        if not messages:
            return "No unread emails found."

        details: list[dict] = []
        for m in messages:
            msg = _retry_with_backoff(
                service.users().messages().get(
                    userId="me", id=m["id"], format="metadata",
                    metadataHeaders=["From", "Subject"]
                ).execute
            )
            details.append(_extract_email_details(msg))

        return _format_email_list(details)
    except Exception as exc:
        return f"Error fetching unread emails: {exc}"


@mcp.tool()
def archive_emails(email_ids: list[str]) -> str:
    """Remove the INBOX label from the specified emails (archive them)."""
    if not email_ids:
        return "No email IDs provided."

    try:
        service = get_gmail_service()
        archived = 0
        errors: list[str] = []

        for eid in email_ids:
            try:
                _retry_with_backoff(
                    service.users().messages().modify(
                        userId="me", id=eid,
                        body={"removeLabelIds": ["INBOX"]}
                    ).execute
                )
                archived += 1
            except HttpError as exc:
                errors.append(f"  {eid}: {exc}")

        result = f"Archived {archived}/{len(email_ids)} emails."
        if errors:
            result += "\nErrors:\n" + "\n".join(errors)
        return result
    except Exception as exc:
        return f"Error archiving emails: {exc}"


@mcp.tool()
def label_emails(email_label_mapping: list[dict]) -> str:
    """Apply labels to emails.

    Expects a list of dicts: [{"email_id": "...", "label_name": "..."}, ...]
    Creates labels that don't exist yet.
    """
    if not email_label_mapping:
        return "No email-label mappings provided."

    # Validate input
    for item in email_label_mapping:
        if not isinstance(item, dict) or "email_id" not in item or "label_name" not in item:
            return (
                "Invalid mapping format. Each item must be a dict with "
                "'email_id' and 'label_name' keys."
            )

    try:
        service = get_gmail_service()
        labelled = 0
        errors: list[str] = []

        for item in email_label_mapping:
            eid = item["email_id"]
            label_name = item["label_name"]
            try:
                label_id = _get_or_create_label(service, label_name)
                _retry_with_backoff(
                    service.users().messages().modify(
                        userId="me", id=eid,
                        body={"addLabelIds": [label_id]}
                    ).execute
                )
                labelled += 1
            except HttpError as exc:
                errors.append(f"  {eid} -> {label_name}: {exc}")

        result = f"Labelled {labelled}/{len(email_label_mapping)} emails."
        if errors:
            result += "\nErrors:\n" + "\n".join(errors)
        return result
    except Exception as exc:
        return f"Error labelling emails: {exc}"


@mcp.tool()
def archive_legacy_emails(years_older_than: int = 5) -> str:
    """Archive all inbox emails older than the specified number of years.

    Removes the INBOX label in batches of 500 to respect API limits.
    Returns the total count of archived emails.
    """
    try:
        service = get_gmail_service()
        query = f"older_than:{years_older_than}y in:inbox"
        total_archived = 0
        page_token: Optional[str] = None

        while True:
            kwargs = {"userId": "me", "q": query, "maxResults": 500}
            if page_token:
                kwargs["pageToken"] = page_token

            results = _retry_with_backoff(
                service.users().messages().list(**kwargs).execute
            )
            messages = results.get("messages", [])
            if not messages:
                break

            msg_ids = [m["id"] for m in messages]

            # Batch modify  - remove INBOX label
            _retry_with_backoff(
                service.users().messages().batchModify(
                    userId="me",
                    body={"ids": msg_ids, "removeLabelIds": ["INBOX"]}
                ).execute
            )
            total_archived += len(msg_ids)
            log.info("Archived batch of %d legacy emails (total: %d)", len(msg_ids), total_archived)

            page_token = results.get("nextPageToken")
            if not page_token:
                break

        return f"Archived {total_archived} emails older than {years_older_than} years."
    except Exception as exc:
        return f"Error archiving legacy emails: {exc}"


@mcp.tool()
def fetch_historical_batch(
    query: str = "newer_than:5y in:inbox",
    max_results: int = 100,
    page_token: Optional[str] = None,
) -> dict:
    """Fetch a batch of historical emails for categorization.

    Automatically skips emails that already have a user-applied label.

    Args:
        query: Gmail search query.
        max_results: Max emails to return per page.
        page_token: Token for the next page. Omit or pass null for the first page.

    Returns a dict with:
      - "emails": formatted string of email summaries (only unlabeled emails)
      - "nextPageToken": token for the next page (null if done)
      - "count": number of unlabeled emails in this batch
      - "skipped": number of already-labeled emails skipped
    """
    try:
        service = get_gmail_service()

        kwargs = {"userId": "me", "q": query, "maxResults": max_results}
        if page_token:
            kwargs["pageToken"] = page_token

        results = _retry_with_backoff(
            service.users().messages().list(**kwargs).execute
        )
        messages = results.get("messages", [])
        if not messages:
            return {"emails": "No emails found.", "nextPageToken": None, "count": 0, "skipped": 0}

        details: list[dict] = []
        skipped = 0
        for m in messages:
            msg = _retry_with_backoff(
                service.users().messages().get(
                    userId="me", id=m["id"], format="metadata",
                    metadataHeaders=["From", "Subject"]
                ).execute
            )
            info = _extract_email_details(msg)
            if info["has_user_label"]:
                skipped += 1
                continue
            details.append(info)

        return {
            "emails": _format_email_list(details),
            "email_details": [
                {"email_id": d["email_id"], "from": d["From"], "subject": d["Subject"], "snippet": d["Snippet"]}
                for d in details
            ],
            "nextPageToken": results.get("nextPageToken"),
            "count": len(details),
            "skipped": skipped,
        }
    except Exception as exc:
        return {"emails": f"Error fetching historical batch: {exc}", "nextPageToken": None, "count": 0, "skipped": 0}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys as _sys

    if "--test" in _sys.argv:
        # Standalone auth test  - prints to stderr to keep stdout clean
        _err = _sys.stderr.write
        _err("Verifying Gmail authentication...\n")
        svc = get_gmail_service()
        profile = svc.users().getProfile(userId="me").execute()
        _err(f"Authenticated as: {profile['emailAddress']}\n")
        _err(f"Total messages: {profile.get('messagesTotal', 'N/A')}\n")
        _err("\nQuick test - fetching up to 3 unread emails:\n")
        _err(fetch_unread_emails(max_results=3) + "\n")
        _err("\nAuth test passed. Run without --test to start the MCP server.\n")
    else:
        mcp.run(transport="stdio")
