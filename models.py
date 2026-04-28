from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

Base = declarative_base()

class Folder(Base):
    __tablename__ = "folders"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    name = Column(String)
    
    tasks = relationship("UserMessage", back_populates="folder")

class UserMessage(Base):
    __tablename__ = "user_messages"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    text = Column(Text)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    summarized = Column(Boolean, default=False)
    deleted = Column(Boolean, default=False)
    reminder_at = Column(DateTime, nullable=True)
    reminder_sent = Column(Boolean, default=False)
    repeat_hours = Column(Integer, nullable=True)
    is_completed = Column(Boolean, default=False)
    
    folder_id = Column(Integer, ForeignKey("folders.id"), nullable=True)
    folder = relationship("Folder", back_populates="tasks")
    jira_key = Column(String, nullable=True)

class UserSettings(Base):
    __tablename__ = "user_settings"
    
    user_id = Column(BigInteger, primary_key=True, index=True)
    briefing_time = Column(String, default="08:00")
    timezone_offset = Column(Integer, default=5)
    last_briefing_date = Column(String, nullable=True)

class BotState(Base):
    __tablename__ = "bot_state"
    
    id = Column(Integer, primary_key=True, index=True)
    last_summary_time = Column(DateTime, default=lambda: datetime.now(timezone.utc))
