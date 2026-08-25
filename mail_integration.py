"""
Outlook Email Integration for Amigo Voice Assistant.
Reads emails from Microsoft Outlook via win32com COM API.
All data stays local — read-only access + draft creation (never auto-sends).
"""

import datetime
import logging
import re
import threading

logger = logging.getLogger("amigo.mail_integration")

_outlook = None
_outlook_lock = threading.Lock()


def _get_outlook():
    """Lazy-connect to Outlook COM object."""
    global _outlook
    if _outlook is not None:
        return _outlook

    with _outlook_lock:
        if _outlook is not None:
            return _outlook
        try:
            import pythoncom
            pythoncom.CoInitialize()
            import win32com.client
            _outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            logger.info("[Mail] Connected to Microsoft Outlook.")
            return _outlook
        except Exception as e:
            logger.warning("[Mail] Outlook not available: %s", e)
            return None


def _safe_body(msg, max_len: int = 500) -> str:
    """Extract plain-text body preview, stripping signatures and HTML."""
    try:
        body = msg.Body or ""
        # Strip common signature markers
        for marker in ["--", "Sent from", "Best regards", "Kind regards"]:
            idx = body.find(marker)
            if idx > 50:
                body = body[:idx]
        body = re.sub(r"<[^>]+>", "", body)  # Strip any HTML tags
        body = re.sub(r"\s+", " ", body).strip()
        return body[:max_len]
    except Exception:
        return ""


def _format_date(dt) -> str:
    """Format Outlook datetime to ISO string."""
    try:
        if hasattr(dt, "isoformat"):
            return dt.isoformat()
        return str(dt)
    except Exception:
        return ""


def is_outlook_available() -> bool:
    """Check if Outlook is installed and accessible."""
    return _get_outlook() is not None


def get_recent_emails(count: int = 5, folder_name: str = "Inbox") -> list[dict]:
    """Fetch the most recent N emails from a folder."""
    ns = _get_outlook()
    if not ns:
        return []

    try:
        # 6 = olFolderInbox
        folder_map = {"inbox": 6, "sent": 5, "drafts": 16, "deleted": 3}
        folder_id = folder_map.get(folder_name.lower(), 6)
        folder = ns.GetDefaultFolder(folder_id)
        messages = folder.Items
        messages.Sort("[ReceivedTime]", True)  # Newest first

        emails = []
        for i, msg in enumerate(messages):
            if i >= count:
                break
            try:
                emails.append({
                    "subject": msg.Subject or "(No Subject)",
                    "sender": str(msg.SenderName or msg.SenderEmailAddress or "Unknown"),
                    "date": _format_date(msg.ReceivedTime),
                    "body_preview": _safe_body(msg, 300),
                    "is_read": bool(msg.UnRead == False),
                    "folder": folder_name,
                })
            except Exception:
                continue

        return emails
    except Exception as e:
        logger.error("[Mail] Error fetching emails: %s", e)
        return []


def get_unread_count() -> int:
    """Return count of unread emails in Inbox."""
    ns = _get_outlook()
    if not ns:
        return -1

    try:
        inbox = ns.GetDefaultFolder(6)  # olFolderInbox
        return inbox.UnReadItemCount
    except Exception as e:
        logger.error("[Mail] Error getting unread count: %s", e)
        return -1


def get_unread_emails(count: int = 5) -> list[dict]:
    """Fetch the most recent unread emails."""
    ns = _get_outlook()
    if not ns:
        return []

    try:
        inbox = ns.GetDefaultFolder(6)
        messages = inbox.Items
        messages.Sort("[ReceivedTime]", True)
        filtered = messages.Restrict("[Unread] = true")

        emails = []
        for i, msg in enumerate(filtered):
            if i >= count:
                break
            try:
                emails.append({
                    "subject": msg.Subject or "(No Subject)",
                    "sender": str(msg.SenderName or msg.SenderEmailAddress or "Unknown"),
                    "date": _format_date(msg.ReceivedTime),
                    "body_preview": _safe_body(msg, 300),
                    "is_read": False,
                    "folder": "Inbox",
                })
            except Exception:
                continue

        return emails
    except Exception as e:
        logger.error("[Mail] Error fetching unread: %s", e)
        return []


def search_emails(query: str, max_results: int = 5) -> list[dict]:
    """Search emails by subject or sender keyword."""
    ns = _get_outlook()
    if not ns or not query:
        return []

    try:
        inbox = ns.GetDefaultFolder(6)
        messages = inbox.Items
        messages.Sort("[ReceivedTime]", True)

        # Outlook DASL filter for subject/sender
        safe_query = query.replace("'", "''")
        restriction = (
            f"@SQL=("
            f"\"urn:schemas:httpmail:subject\" LIKE '%{safe_query}%' OR "
            f"\"urn:schemas:httpmail:fromemail\" LIKE '%{safe_query}%' OR "
            f"\"urn:schemas:httpmail:fromname\" LIKE '%{safe_query}%'"
            f")"
        )

        try:
            filtered = messages.Restrict(restriction)
        except Exception:
            # Fallback to manual search
            filtered = messages

        emails = []
        for i, msg in enumerate(filtered):
            if i >= max_results * 3:  # Safety limit
                break
            try:
                subject = msg.Subject or ""
                sender = str(msg.SenderName or msg.SenderEmailAddress or "")
                body = _safe_body(msg, 200)

                q_lower = query.lower()
                if q_lower in subject.lower() or q_lower in sender.lower() or q_lower in body.lower():
                    emails.append({
                        "subject": subject or "(No Subject)",
                        "sender": sender or "Unknown",
                        "date": _format_date(msg.ReceivedTime),
                        "body_preview": body,
                        "is_read": bool(msg.UnRead == False),
                        "folder": "Inbox",
                    })
                    if len(emails) >= max_results:
                        break
            except Exception:
                continue

        return emails
    except Exception as e:
        logger.error("[Mail] Search error: %s", e)
        return []


def get_email_summary_text(count: int = 5) -> str:
    """Return a text summary of recent emails for the LLM to process."""
    emails = get_recent_emails(count)
    if not emails:
        return "No emails found in your inbox."

    lines = []
    for i, e in enumerate(emails, 1):
        status = "📩" if not e["is_read"] else "✉️"
        lines.append(f"{i}. {status} From: {e['sender']} | Subject: {e['subject']} | {e['date'][:10]}")
        if e["body_preview"]:
            lines.append(f"   Preview: {e['body_preview'][:150]}")

    return "\n".join(lines)


def draft_email(to: str = "", subject: str = "", body: str = "") -> str:
    """Create an email draft in Outlook (does NOT send). Returns status message."""
    try:
        import win32com.client
        app = win32com.client.Dispatch("Outlook.Application")
        mail = app.CreateItem(0)  # olMailItem
        if to:
            mail.To = to
        if subject:
            mail.Subject = subject
        if body:
            mail.Body = body
        mail.Save()  # Save as draft
        mail.Display()  # Show the compose window
        return f"Email draft created{' to ' + to if to else ''}. Review and send when ready."
    except Exception as e:
        logger.error("[Mail] Draft error: %s", e)
        return "Could not create email draft. Make sure Outlook is running."


def index_emails_to_rag(rag_engine, count: int = 50) -> int:
    """Bulk-index recent emails into the RAG vector store."""
    emails = get_recent_emails(count)
    indexed = 0
    for e in emails:
        try:
            rag_engine.index_email(
                subject=e["subject"],
                sender=e["sender"],
                body_preview=e["body_preview"],
                date=e["date"],
                is_read=e["is_read"],
                folder=e["folder"],
            )
            indexed += 1
        except Exception:
            continue
    logger.info("[Mail] Indexed %d emails into RAG.", indexed)
    return indexed
