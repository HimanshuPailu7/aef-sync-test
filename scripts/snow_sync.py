"""
Sync a GitHub issue event to a ServiceNow case.

- On "opened": create a SNOW case, tag it with the GitHub issue number
  (correlation_id) so we never create duplicates, and post the case
  number back onto the GitHub issue as a comment.
- On "closed": find the linked case via correlation_id, pull the last
  comment from the GitHub issue, and close the case with that comment
  as the resolution note.
"""

import json
import os
import sys

import requests

# ---------- config ----------
SNOW_INSTANCE = os.environ["SNOW_INSTANCE"]
SNOW_USER = os.environ["SNOW_USER"]
SNOW_PASSWORD = os.environ["SNOW_PASSWORD"]
SNOW_TABLE = os.environ.get("SNOW_TABLE", "sn_customerservice_case")

GH_TOKEN = os.environ["GITHUB_TOKEN"]
GH_REPO = os.environ["GH_REPO"]
ISSUE_NUMBER = os.environ["ISSUE_NUMBER"]
ISSUE_TITLE = os.environ.get("ISSUE_TITLE", "")
ISSUE_BODY = os.environ.get("ISSUE_BODY", "") or ""
ISSUE_URL = os.environ.get("ISSUE_URL", "")
EVENT_ACTION = os.environ["EVENT_ACTION"]
ISSUE_ASSIGNEE = os.environ.get("ISSUE_ASSIGNEE", "") or ""
ISSUE_LABELS = json.loads(os.environ.get("ISSUE_LABELS", "[]") or "[]")

SNOW_BASE = f"https://{SNOW_INSTANCE}/api/now/table/{SNOW_TABLE}"
SNOW_AUTH = (SNOW_USER, SNOW_PASSWORD)
SNOW_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}

GH_API_BASE = f"https://api.github.com/repos/{GH_REPO}"
GH_HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
}

CORRELATION_TAG = f"[GH-ISSUE-{ISSUE_NUMBER}]"


# ---------- ServiceNow helpers ----------
def find_existing_case():
    params = {
        "sysparm_query": f"correlation_id={ISSUE_NUMBER}^correlation_display={GH_REPO}",
        "sysparm_limit": 1,
    }
    resp = requests.get(SNOW_BASE, auth=SNOW_AUTH, headers=SNOW_HEADERS, params=params)
    resp.raise_for_status()
    results = resp.json().get("result", [])
    return results[0] if results else None


def create_case():
    existing = find_existing_case()
    if existing:
        print(f"Case already exists: {existing.get('number')} — skipping create.")
        return existing

    payload = {
        "short_description": ISSUE_TITLE[:160],
        "description": (
            f"{ISSUE_BODY}\n\n"
            f"---\n"
            f"Source: GitHub issue {ISSUE_URL}\n"
            f"Repo: {GH_REPO}\n"
            f"Labels: {', '.join(ISSUE_LABELS) if ISSUE_LABELS else 'none'}\n"
            f"Assignee: {ISSUE_ASSIGNEE or 'unassigned'}"
        ),
        "correlation_id": ISSUE_NUMBER,
        "correlation_display": GH_REPO,
    }

    resp = requests.post(SNOW_BASE, auth=SNOW_AUTH, headers=SNOW_HEADERS, json=payload)
    resp.raise_for_status()
    case = resp.json()["result"]
    print(f"Created SNOW case {case.get('number')} for issue #{ISSUE_NUMBER}")
    return case


def close_case(case_sys_id, resolution_note):
    payload = {
        "state": "Closed",
        "close_notes": resolution_note,
        "work_notes": f"Closed via GitHub issue #{ISSUE_NUMBER}. Last comment:\n\n{resolution_note}",
    }
    url = f"{SNOW_BASE}/{case_sys_id}"
    params = {"sysparm_input_display": "true"}
    resp = requests.patch(
        url, auth=SNOW_AUTH, headers=SNOW_HEADERS, json=payload, params=params
    )
    resp.raise_for_status()
    print(f"Closed SNOW case {case_sys_id}")


# ---------- GitHub helpers ----------
def get_last_comment():
    url = f"{GH_API_BASE}/issues/{ISSUE_NUMBER}/comments"
    resp = requests.get(url, headers=GH_HEADERS, params={"per_page": 100})
    resp.raise_for_status()
    comments = resp.json()
    if not comments:
        return "(No comments on the GitHub issue — closed without a final note.)"
    return comments[-1]["body"]


def post_comment(body):
    url = f"{GH_API_BASE}/issues/{ISSUE_NUMBER}/comments"
    resp = requests.post(url, headers=GH_HEADERS, json={"body": body})
    resp.raise_for_status()


# ---------- main ----------
def main():
    if EVENT_ACTION == "opened":
        case = create_case()
        post_comment(f"🔗 Logged as ServiceNow case **{case.get('number')}**.")

    elif EVENT_ACTION == "closed":
        case = find_existing_case()
        if not case:
            case = create_case()
        last_comment = get_last_comment()
        close_case(case["sys_id"], last_comment)
        post_comment(f"✅ ServiceNow case **{case.get('number')}** closed.")

    else:
        print(f"No handling for action '{EVENT_ACTION}', skipping.")
        sys.exit(0)


if __name__ == "__main__":
    main()
