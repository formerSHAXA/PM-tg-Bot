from database import SessionLocal
from models import UserMessage
import re

db = SessionLocal()
all_user_messages = db.query(UserMessage).filter(
    UserMessage.user_id == 5696695538,
    UserMessage.deleted == False
).order_by(UserMessage.timestamp.desc()).all()

active_context = [m for m in all_user_messages if not m.is_completed][:15]

id_mentions = re.findall(r"(?:#|task\s+|id:\s*)(\d+)", "move task #1 from jira to Asaxiy Invest folder please")
for tid_str in id_mentions:
    tid = int(tid_str)
    if not any(m.id == tid for m in active_context):
        t = db.query(UserMessage).filter(UserMessage.id == tid, UserMessage.user_id == 5696695538).first()
        if t: active_context.append(t)

messages_context = "\n".join([
    f"ID: {m.id} | Task: {m.text} | Folder: {m.folder.name if m.folder else 'None'}" 
    for m in active_context
])
print(messages_context)
