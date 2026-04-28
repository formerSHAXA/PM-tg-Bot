import asyncio
from groq_service import analyze_intent
from database import SessionLocal
from models import UserMessage, Folder
import re

async def main():
    db = SessionLocal()
    message_from_user_id = 5696695538
    text = "move task #1 from jira to Asaxiy Invest folder please"
    
    all_user_messages = db.query(UserMessage).filter(
        UserMessage.user_id == message_from_user_id,
        UserMessage.deleted == False
    ).order_by(UserMessage.timestamp.desc()).all()
    
    active_context = [m for m in all_user_messages if not m.is_completed][:15]
    
    id_mentions = re.findall(r"(?:#|task\s+|id:\s*)(\d+)", text.lower())
    for tid_str in id_mentions:
        tid = int(tid_str)
        if not any(m.id == tid for m in active_context):
            t = db.query(UserMessage).filter(UserMessage.id == tid, UserMessage.user_id == message_from_user_id).first()
            if t: active_context.append(t)
            
    folders = db.query(Folder).filter(Folder.user_id == message_from_user_id).all()
    
    res = await analyze_intent(text, active_context, folders)
    print("GROQ Response:")
    print(res)

asyncio.run(main())
