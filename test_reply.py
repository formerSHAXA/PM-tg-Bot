import asyncio
from groq_service import analyze_intent
from database import SessionLocal
from models import Folder

async def main():
    text = "User is replying to this previous message: 'Task added to Asaxiy Invest folder.'.\nUser's new message: where did you added this task?"
    db = SessionLocal()
    folders = db.query(Folder).filter(Folder.user_id == 5696695538).all()
    res = await analyze_intent(text, [], folders)
    print(res)

asyncio.run(main())
