import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database import init_db, SessionLocal
from models import UserMessage
from groq_service import summarize_tasks
from bot_logic import dp, bot, send_hourly_summary, send_specific_reminder
from datetime import datetime, timezone, timedelta

SUMMARY_INTERVAL = int(os.getenv("SUMMARY_INTERVAL_HOURS", 1))
scheduler = AsyncIOScheduler()

async def hourly_task_processor():
    db = SessionLocal()
    try:
        unread = db.query(UserMessage).filter(
            UserMessage.summarized == False,
            UserMessage.deleted == False,
            UserMessage.is_completed == False
        ).all()
        if not unread: return
        
        user_groups = {}
        for msg in unread:
            if msg.user_id not in user_groups: user_groups[msg.user_id] = []
            user_groups[msg.user_id].append(msg)

        for user_id, messages in user_groups.items():
            summary = await summarize_tasks(messages)
            await send_hourly_summary(user_id, summary)
            for msg in messages:
                if not msg.repeat_hours: # Only mark summarized if not repeating
                    msg.summarized = True
        db.commit()
    except Exception as e: print(f"Hourly error: {e}")
    finally: db.close()

async def reminder_processor():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due = db.query(UserMessage).filter(
            UserMessage.reminder_at != None,
            UserMessage.reminder_at <= now,
            UserMessage.reminder_sent == False,
            UserMessage.deleted == False,
            UserMessage.is_completed == False
        ).all()
        
        for r in due:
            await send_specific_reminder(r.user_id, r.text)
            if r.repeat_hours:
                # Schedule next reminder
                r.reminder_at = r.reminder_at + timedelta(hours=r.repeat_hours)
                # Keep reminder_sent as False so it triggers again
            else:
                r.reminder_sent = True
        db.commit()
    except Exception as e: print(f"Reminder error: {e}")
    finally: db.close()

async def daily_briefing_processor():
    db = SessionLocal()
    try:
        from models import UserSettings
        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")
        
        settings = db.query(UserSettings).filter(
            (UserSettings.last_briefing_date == None) | (UserSettings.last_briefing_date != today_str)
        ).all()
        
        for s in settings:
            user_local = now_utc + timedelta(hours=s.timezone_offset)
            h, m = map(int, s.briefing_time.split(":"))
            
            if user_local.hour >= h and user_local.minute >= m:
                active = db.query(UserMessage).filter(
                    UserMessage.user_id == s.user_id,
                    UserMessage.deleted == False,
                    UserMessage.is_completed == False
                ).all()
                
                if active:
                    summary = await summarize_tasks(active)
                    briefing = f"☀️ *Good Morning! Explore your tasks:*\n\n{summary}\n\n🚀 Let's make today count!"
                    await bot.send_message(s.user_id, briefing, parse_mode="Markdown")
                
                s.last_briefing_date = today_str
        db.commit()
    except Exception as e: print(f"Briefing error: {e}")
    finally: db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.add_job(hourly_task_processor, 'interval', hours=SUMMARY_INTERVAL)
    scheduler.add_job(reminder_processor, 'interval', seconds=30)
    scheduler.add_job(daily_briefing_processor, 'interval', minutes=5)
    scheduler.start()
    polling_task = asyncio.create_task(dp.start_polling(bot))
    yield
    scheduler.shutdown()
    polling_task.cancel()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root(): return {"status": "PM Bot with Repeating Reminders is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
