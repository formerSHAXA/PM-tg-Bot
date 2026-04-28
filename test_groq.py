import asyncio
from groq_service import analyze_intent
from models import Folder

class MockFolder:
    def __init__(self, id, name):
        self.id = id
        self.name = name

async def main():
    folders = [MockFolder(5, "Jira"), MockFolder(6, "Asaxiy Invest")]
    active_messages = []
    text = "move task #1 from jira to Asaxiy Invest folder please"
    res = await analyze_intent(text, active_messages, folders)
    print(res)

asyncio.run(main())
