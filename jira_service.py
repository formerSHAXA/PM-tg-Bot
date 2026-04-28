import os
from jira import JIRA
from dotenv import load_dotenv

load_dotenv()

JIRA_URL = os.getenv("JIRA_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT = os.getenv("JIRA_PROJECT", "SCRUM")

_jira = None

def get_jira():
    global _jira
    if _jira is None:
        _jira = JIRA(server=JIRA_URL, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))
    return _jira

def create_jira_issue(summary: str, description: str = "") -> str:
    """Create a Jira issue and return its key (e.g. SCRUM-42)."""
    try:
        j = get_jira()
        issue = j.create_issue(
            project=JIRA_PROJECT,
            summary=summary,
            description=description or summary,
            issuetype={"name": "Task"},
        )
        return issue.key
    except Exception as e:
        print(f"Jira create error: {e}")
        return None

def update_jira_issue(key: str, summary: str) -> bool:
    """Update the summary of an existing Jira issue."""
    try:
        j = get_jira()
        issue = j.issue(key)
        issue.update(summary=summary)
        return True
    except Exception as e:
        print(f"Jira update error: {e}")
        return False

def complete_jira_issue(key: str) -> bool:
    """Transition a Jira issue to Done."""
    try:
        j = get_jira()
        transitions = j.transitions(key)
        done_id = None
        for t in transitions:
            if t["name"].lower() in ["done", "complete", "resolved"]:
                done_id = t["id"]
                break
        if done_id:
            j.transition_issue(key, done_id)
            return True
        return False
    except Exception as e:
        print(f"Jira complete error: {e}")
        return False

def fetch_jira_backlog() -> list:
    """Fetch all open/in-progress issues from Jira project."""
    try:
        j = get_jira()
        jql = f'project = {JIRA_PROJECT} AND status != Done ORDER BY created DESC'
        issues = j.search_issues(jql, maxResults=100)
        return [
            {
                "key": issue.key,
                "summary": issue.fields.summary,
                "status": str(issue.fields.status),
            }
            for issue in issues
        ]
    except Exception as e:
        print(f"Jira fetch error: {e}")
        return []

def delete_jira_issue(key: str) -> bool:
    """Delete a Jira issue."""
    try:
        j = get_jira()
        issue = j.issue(key)
        issue.delete()
        return True
    except Exception as e:
        print(f"Jira delete error: {e}")
        return False
