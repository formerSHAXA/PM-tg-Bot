from database import SessionLocal
from models import UserMessage, Folder
import re

db = SessionLocal()
message_from_user_id = 5696695538

analysis = {'action': 'EDIT', 'response': 'Task #1: Task Text has been moved to Asaxiy Invest folder.', 'target_id': 1, 'folder_id': 6}

action = analysis.get("action")
response_text = analysis.get("response", "Ok.")
folder_name = analysis.get("folder_name")

sync_jira = False
target_folder_id = None
if folder_name or analysis.get("folder_id"):
    fid = analysis.get("folder_id")
    if fid:
        folder = db.query(Folder).filter(Folder.id == fid, Folder.user_id == message_from_user_id).first()
        print(f"Folder matched by id {fid}: {folder.name if folder else None}")
    else:
        folder = db.query(Folder).filter(
            Folder.user_id == message_from_user_id,
            Folder.name.ilike(folder_name)
        ).first()
    
    if (folder_name and folder_name.lower() == "jira") or (folder and folder.name.lower() == "jira"):
        sync_jira = True
        jira_folder = dict(id=5) # mock
        target_folder_id = jira_folder["id"]
    elif folder:
        target_folder_id = folder.id
    elif folder_name:
        print("Create new folder")
        target_folder_id = 999 

print(f"Target folder ID: {target_folder_id}")

target_id = analysis.get("target_id")
try:
    if target_id and str(target_id).isdigit():
        target_id = int(target_id)
    else:
        target_id = None
except:
    target_id = None

new_text = analysis.get("new_text")
if target_id:
    msg = db.query(UserMessage).filter(UserMessage.id == target_id).first()
    if msg:
        print(f"Modifying task {msg.id}")
        if (action == "MOVE_TASK" or folder_name) and not target_folder_id and not new_text:
            print(f"I found task `#{target_id}`, but I couldn't find the folder '{folder_name or 'specified'}'.")
        
        if target_folder_id: 
            msg.folder_id = target_folder_id
            print(f"Set msg.folder_id to {target_folder_id}")
        
    else:
        print(f"Task `#{target_id}` not found.")

