import os
import json
from groq import Groq
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

async def summarize_tasks(messages):
    combined_text = "\n".join([f"- {m.text}" for m in messages])
    prompt = f"Summarize these tasks concisely:\n{combined_text}"
    chat_completion = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.1-8b-instant",
    )
    return chat_completion.choices[0].message.content

async def analyze_intent(text, active_messages, folders_list=None):
    user_tz = timezone(timedelta(hours=5))
    current_time_user = datetime.now(user_tz).strftime("%Y-%m-%d %H:%M:%S")
    
    messages_context = "\n".join([
        f"ID: {m.id} | Task: {m.text} | Folder: {m.folder.name if m.folder else 'None'}" 
        for m in active_messages
    ])
    
    folders_context = ", ".join([f"{f.name} [ID:{f.id}]" for f in folders_list]) if folders_list else "None"
    
    system_prompt = f"""
    You are a Smart Personal Manager AI. Your job is to organize the user's life using tasks and folders.
    
    Current Time: {current_time_user} (UTC+5).
    Existing Folders: {folders_context}
    
    INTELLIGENCE RULES:
    1. ACTIONS:
       - 'ADD': Create a new task.
       - 'DELETE': Remove a task.
       - 'COMPLETE': Mark task as done.
       - 'EDIT': Change task text.
       - 'MOVE_TASK': Move a task to a different folder.
       - 'LIST': Show all tasks in a flat list.
       - 'LIST_FOLDERS': Show tasks grouped by folders. Use this when user says "folders", "show folders", "by folder", or wants to see tasks organized by category.
       - 'CREATE_FOLDER': Create a new organizational folder.
       - 'DELETE_FOLDER': Remove a folder.
       - 'OTHER': Conversation.
    2. FOLDERS: If a user mentions a category (e.g. "Work", "Personal"), map it to an existing folder ID or provide a folder_name for a new one.
    3. TARGETS: For EDIT, DELETE, COMPLETE, or MOVE_TASK, you MUST provide 'target_id' (the integer ID of the task). For MOVE_TASK, you MUST provide 'folder_id'.
    3. TIME: Be smart with relative time ("in 2 hours", "at 5pm tomorrow"). Convert to UTC ISO format.
    4. PRESERVE original language.
    
    Return JSON:
    {{
        "action": "ADD" | "DELETE" | "COMPLETE" | "EDIT" | "MOVE_TASK" | "LIST" | "LIST_FOLDERS" | "CREATE_FOLDER" | "DELETE_FOLDER" | "OTHER",
        "tasks_to_add": ["Text"],
        "target_id": <int>,
        "new_text": "Updated text",
        "folder_name": "Name for new folder or matching",
        "folder_id": <int>,
        "response": "Smart, clean response",
        "reminder_at_iso": "UTC ISO timestamp",
        "repeat_hours": <int>
    }}
    """
    
    chat_completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        model="llama-3.1-8b-instant",
        response_format={"type": "json_object"}
    )
    
    return json.loads(chat_completion.choices[0].message.content)
