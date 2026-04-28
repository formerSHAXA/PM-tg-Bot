import asyncio
from database import SessionLocal, init_db
from models import UserMessage
from groq_service import summarize_tasks
from bot_logic import send_hourly_summary

async def test_summary():
    init_db()
    db = SessionLocal()
    
    # Add some dummy messages
    test_messages = [
        UserMessage(user_id=123456, text="Need to update the website content"),
        UserMessage(user_id=123456, text="Don't forget the meeting at 3pm"),
        UserMessage(user_id=123456, text="Call John about the invoice")
    ]
    
    for msg in test_messages:
        db.add(msg)
    db.commit()
    
    # Query back messages
    messages = db.query(UserMessage).filter(UserMessage.user_id == 123456, UserMessage.summarized == False).all()
    
    print(f"Propagating {len(messages)} messages to Groq...")
    summary = await summarize_tasks(messages)
    print("Summary from Groq:")
    print(summary)
    
    db.close()

if __name__ == "__main__":
    asyncio.run(test_summary())
