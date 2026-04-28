from aiogram import Bot, Dispatcher, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.orm import Session
from database import SessionLocal
from models import UserMessage, Folder, UserSettings
from groq_service import analyze_intent
from jira_service import create_jira_issue, update_jira_issue, complete_jira_issue, delete_jira_issue, fetch_jira_backlog
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
import os
import re

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

JIRA_FOLDER_NAME = "Jira"
OWNER_ID = 5696695538

def sync_jira_to_local(db: Session, user_id: int):
    """Pull Jira backlog issues and create local tasks for any missing ones."""
    try:
        jira_issues = fetch_jira_backlog()
        if not jira_issues:
            return

        # Get all local jira_keys for this user
        existing_keys = set(
            row.jira_key for row in db.query(UserMessage.jira_key).filter(
                UserMessage.user_id == user_id,
                UserMessage.jira_key != None,
                UserMessage.deleted == False
            ).all()
        )

        # Ensure Jira folder exists
        jira_folder = db.query(Folder).filter(
            Folder.user_id == user_id, Folder.name == JIRA_FOLDER_NAME
        ).first()
        if not jira_folder:
            jira_folder = Folder(user_id=user_id, name=JIRA_FOLDER_NAME)
            db.add(jira_folder)
            db.commit()
            db.refresh(jira_folder)

        imported = 0
        for issue in jira_issues:
            if issue["key"] not in existing_keys:
                msg = UserMessage(
                    user_id=user_id,
                    text=issue["summary"],
                    jira_key=issue["key"],
                    folder_id=jira_folder.id,
                )
                db.add(msg)
                imported += 1

        if imported:
            db.commit()
            print(f"Synced {imported} Jira issues for user {user_id}")
    except Exception as e:
        print(f"Jira sync error: {e}")

def get_task_keyboard(tasks, is_folder_view=False):
    builder = InlineKeyboardBuilder()
    for t in tasks:
        builder.button(text=f"✅ #{t.id}", callback_data=f"done_{t.id}")
        builder.button(text=f"📂 Move", callback_data=f"move_{t.id}")
        builder.button(text=f"🗑 #{t.id}", callback_data=f"del_{t.id}")
    builder.adjust(3)
    
    if is_folder_view:
        builder.row(types.InlineKeyboardButton(text="🔙 Back to Folders", callback_data="list_folders"))
        
    return builder.as_markup()

def get_folder_keyboard(folders, tasks):
    builder = InlineKeyboardBuilder()
    folder_counts = {}
    uncategorized_count = 0
    for t in tasks:
        if t.folder_id:
            folder_counts[t.folder_id] = folder_counts.get(t.folder_id, 0) + 1
        else:
            uncategorized_count += 1
            
    for f in folders:
        count = folder_counts.get(f.id, 0)
        builder.button(text=f"📁 {f.name} ({count})", callback_data=f"list_folder_{f.id}")
    
    if uncategorized_count > 0:
        builder.button(text=f"📋 Uncategorized ({uncategorized_count})", callback_data="list_folder_none")
        
    builder.adjust(1)
    return builder.as_markup()

def format_task_line(m):
    time_info = f" (Next: {m.reminder_at.strftime('%H:%M')})" if m.reminder_at else ""
    jira_tag = f" 🔗`{m.jira_key}`" if m.jira_key else ""
    return f"  `#{m.id}` {m.text}{time_info}{jira_tag}\n"

def build_task_list_chunks(tasks, max_len=3800):
    """Split tasks into message chunks that fit Telegram's 4096 char limit."""
    chunks = []  # list of (text, tasks_in_chunk)
    header = "📌 *Active Tasks*\n\n"
    current_text = header
    current_tasks = []

    for m in tasks:
        line = format_task_line(m)
        if len(current_text) + len(line) > max_len and current_tasks:
            chunks.append((current_text, current_tasks))
            current_text = "📌 *Active Tasks (cont.)*\n\n" + line
            current_tasks = [m]
        else:
            current_text += line
            current_tasks.append(m)

    if current_tasks:
        chunks.append((current_text, current_tasks))

    return chunks

def build_task_list_text(tasks):
    """Single-string version for edit_text callbacks (truncates to fit)."""
    chunks = build_task_list_chunks(tasks)
    if not chunks:
        return "📌 *Active Tasks*\n\nNo tasks."
    return chunks[0][0]

def build_folder_view_chunks(tasks, folders, max_len=3000):
    """Build task list grouped by folders, split by lines to stay under limit."""
    from collections import OrderedDict

    # Group tasks by folder
    grouped = OrderedDict()
    no_folder = []
    for m in tasks:
        if m.folder:
            fname = m.folder.name
            if fname not in grouped: grouped[fname] = []
            grouped[fname].append(m)
        else:
            no_folder.append(m)

    # Header for the very first chunk
    total = len(tasks)
    folder_count = len(grouped) + (1 if no_folder else 0)
    header = f"🗂 *Your Folders* — {folder_count} folders, {total} active tasks\n{'━' * 30}\n\n"

    chunks = []
    current_text = header
    current_tasks = []

    def add_to_chunk(text, task):
        nonlocal current_text, current_tasks, chunks
        if len(current_text) + len(text) > max_len and current_tasks:
            chunks.append((current_text, current_tasks))
            current_text = "🗂 *Continued...*\n\n"
            current_tasks = []
        current_text += text
        if task: current_tasks.append(task)

    # Process grouped folders
    for fname, folder_tasks in grouped.items():
        add_to_chunk(f"📂 *{fname}* ({len(folder_tasks)} tasks)\n", None)
        for m in folder_tasks:
            add_to_chunk(format_task_line(m), m)
        add_to_chunk("\n", None)

    # Process uncategorized
    if no_folder:
        add_to_chunk(f"📋 *Uncategorized* ({len(no_folder)} tasks)\n", None)
        for m in no_folder:
            add_to_chunk(format_task_line(m), m)
        add_to_chunk("\n", None)

    if current_tasks or current_text != header:
        chunks.append((current_text, current_tasks))

    return chunks


async def update_message_tasks(callback: types.CallbackQuery, db: Session, completed_or_deleted_id: int):
    current_task_ids = []
    has_back_btn = False
    if callback.message.reply_markup:
        for row in callback.message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == "list_folders":
                    has_back_btn = True
                elif btn.callback_data and (btn.callback_data.startswith("done_") or btn.callback_data.startswith("del_")):
                    try:
                        tid = int(btn.callback_data.split("_")[1])
                        if tid != completed_or_deleted_id and tid not in current_task_ids:
                            current_task_ids.append(tid)
                    except:
                        pass
                        
    if not current_task_ids:
        if has_back_btn:
            sync_jira_to_local(db, callback.from_user.id)
            all_user_messages = db.query(UserMessage).filter(
                UserMessage.user_id == callback.from_user.id,
                UserMessage.deleted == False
            ).order_by(UserMessage.timestamp.desc()).all()
            active = [m for m in all_user_messages if not m.is_completed]
            folders = db.query(Folder).filter(Folder.user_id == callback.from_user.id).all()
            await callback.message.edit_text(
                "🗂 *Your Folders*\nSelect a folder to view its tasks:",
                parse_mode="Markdown",
                reply_markup=get_folder_keyboard(folders, active)
            )
        else:
            await callback.message.edit_text("Your list is now empty! 🎉")
        return
        
    remaining_in_view = db.query(UserMessage).filter(UserMessage.id.in_(current_task_ids)).all()
    ordered_remaining = []
    for tid in current_task_ids:
        for t in remaining_in_view:
            if t.id == tid:
                ordered_remaining.append(t)
                break
    
    first_line = callback.message.text.split('\n')[0] if callback.message.text else "📌 Active Tasks"
    header = f"*{first_line}*\n\n"
    
    if first_line.startswith("📂"):
        header = f"📂 *{first_line[2:].strip()}*\n\n"
    elif first_line.startswith("📌"):
        header = f"📌 *{first_line[2:].strip()}*\n\n"
    elif first_line.startswith("📋"):
        header = f"📋 *{first_line[2:].strip()}*\n\n"
        
    text = header
    for m in ordered_remaining:
        text += format_task_line(m)
        
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_task_keyboard(ordered_remaining, is_folder_view=has_back_btn)
    )

@dp.callback_query(F.data.startswith("done_"))
async def cb_done(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔ You are not the owner.", show_alert=True)
    db: Session = SessionLocal()
    try:
        task_id = int(callback.data.split("_")[1])
        task = db.query(UserMessage).filter(UserMessage.id == task_id).first()
        if not task: return await callback.answer("Task not found")
        
        task.is_completed = True
        if task.jira_key:
            complete_jira_issue(task.jira_key)
        db.commit()
        await callback.answer("Task completed! ✅")
        
        await update_message_tasks(callback, db, task_id)
    finally:
        db.close()

@dp.callback_query(F.data.startswith("del_"))
async def cb_del(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔ You are not the owner.", show_alert=True)
    db: Session = SessionLocal()
    try:
        task_id = int(callback.data.split("_")[1])
        task = db.query(UserMessage).filter(UserMessage.id == task_id).first()
        if not task: return await callback.answer("Task not found")
        
        task.deleted = True
        if task.jira_key:
            delete_jira_issue(task.jira_key)
        db.commit()
        await callback.answer("Task deleted! 🗑")
        
        await update_message_tasks(callback, db, task_id)
    finally:
        db.close()

@dp.callback_query(F.data.startswith("move_"))
async def cb_move(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔ You are not the owner.", show_alert=True)
    task_id = int(callback.data.split("_")[1])
    db: Session = SessionLocal()
    try:
        task = db.query(UserMessage).filter(UserMessage.id == task_id).first()
        if not task: return await callback.answer("Task not found")
        
        folders = db.query(Folder).filter(Folder.user_id == callback.from_user.id).all()
        builder = InlineKeyboardBuilder()
        for f in folders:
             builder.button(text=f"📁 {f.name}", callback_data=f"setf_{task_id}_{f.id}")
        builder.button(text=f"📋 Uncategorized", callback_data=f"setf_{task_id}_none")
        builder.button(text=f"❌ Cancel", callback_data=f"cancel_move")
        builder.adjust(1)
        
        short_text = (task.text[:30] + '...') if len(task.text) > 30 else task.text
        await callback.message.answer(
            f"Where do you want to move Task `#{task_id}`: *{short_text}*?", 
            reply_markup=builder.as_markup(), 
            parse_mode="Markdown"
        )
        await callback.answer()
    finally:
        db.close()

@dp.callback_query(F.data.startswith("setf_"))
async def cb_setf(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔ You are not the owner.", show_alert=True)
    parts = callback.data.split("_")
    task_id = int(parts[1])
    folder_id_str = parts[2]
    
    db: Session = SessionLocal()
    try:
        task = db.query(UserMessage).filter(UserMessage.id == task_id).first()
        if not task:
            return await callback.message.edit_text("Task not found.")
            
        if folder_id_str == "none":
            task.folder_id = None
            fname = "Uncategorized"
        else:
            fid = int(folder_id_str)
            task.folder_id = fid
            f = db.query(Folder).filter(Folder.id == fid).first()
            fname = f.name if f else "Unknown"

        jira_folder = db.query(Folder).filter(Folder.user_id == callback.from_user.id, Folder.name == JIRA_FOLDER_NAME).first()
        is_jira = False
        if jira_folder and task.folder_id == jira_folder.id:
            is_jira = True
            
        if is_jira and not task.jira_key:
            task.jira_key = create_jira_issue(task.text)
        elif not is_jira and task.jira_key:
            delete_jira_issue(task.jira_key)
            task.jira_key = None
            
        db.commit()
        await callback.message.edit_text(f"✅ Task `#{task_id}` has been moved to *{fname}*.", parse_mode="Markdown")
    finally:
        db.close()

@dp.callback_query(F.data == "cancel_move")
async def cb_cancel_move(callback: types.CallbackQuery):
    await callback.message.delete()

@dp.callback_query(F.data == "list_folders")
async def cb_list_folders_handler(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔ You are not the owner.", show_alert=True)
    db: Session = SessionLocal()
    try:
        sync_jira_to_local(db, callback.from_user.id)
        all_user_messages = db.query(UserMessage).filter(
            UserMessage.user_id == callback.from_user.id,
            UserMessage.deleted == False
        ).order_by(UserMessage.timestamp.desc()).all()
        active = [m for m in all_user_messages if not m.is_completed]
        folders = db.query(Folder).filter(Folder.user_id == callback.from_user.id).all()
        
        await callback.message.edit_text(
            "🗂 *Your Folders*\nSelect a folder to view its tasks:",
            parse_mode="Markdown",
            reply_markup=get_folder_keyboard(folders, active)
        )
    finally:
        db.close()

@dp.callback_query(F.data.startswith("list_folder_"))
async def cb_view_folder(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔ You are not the owner.", show_alert=True)
    db: Session = SessionLocal()
    try:
        folder_id_str = callback.data.split("_")[2]
        if folder_id_str == "none":
            folder_id = None
            folder_name = "Uncategorized"
        else:
            folder_id = int(folder_id_str)
            folder = db.query(Folder).filter(Folder.id == folder_id).first()
            folder_name = folder.name if folder else "Unknown"

        tasks = db.query(UserMessage).filter(
            UserMessage.user_id == callback.from_user.id,
            UserMessage.folder_id == folder_id,
            UserMessage.deleted == False,
            UserMessage.is_completed == False
        ).order_by(UserMessage.timestamp.desc()).all()

        if not tasks:
            await callback.answer(f"No active tasks in {folder_name}")
            return

        chunks = build_task_list_chunks(tasks)
        if len(chunks) == 1:
            chunk_text, chunk_tasks = chunks[0]
            header = f"📂 *{folder_name}*\n\n" if folder_id is not None else f"📋 *{folder_name}*\n\n"
            text_to_send = header + chunk_text.replace("📌 *Active Tasks*\n\n", "")
            await callback.message.edit_text(
                text_to_send, 
                parse_mode="Markdown", 
                reply_markup=get_task_keyboard(chunk_tasks, is_folder_view=True)
            )
        else:
            await callback.message.delete()
            for i, (chunk_text, chunk_tasks) in enumerate(chunks):
                header = f"📂 *{folder_name}* (Part {i+1})\n\n" if folder_id is not None else f"📋 *{folder_name}* (Part {i+1})\n\n"
                await callback.message.answer(
                    header + chunk_text.replace("📌 *Active Tasks*\n\n", ""),
                    parse_mode="Markdown",
                    reply_markup=get_task_keyboard(chunk_tasks, is_folder_view=True)
                )
        await callback.answer()
    finally:
        db.close()

@dp.message(F.text)
async def handle_message(message: types.Message):
    # Owner check
    if message.from_user.id != OWNER_ID:
        return await message.reply("⛔ You are not the owner of this bot.")

    if message.text.lower() in ["/help", "help", "/start", "start", "/menu", "menu"]:
        help_text = (
            "🤖 *PM Bot - Management Guide*\n\n"
            "You can just talk to me naturally! For example:\n"
            "• _'Add a task to call John at 5pm'_ \n"
            "• _'Move task 12 to Jira'_ \n"
            "• _'Show my folders'_ \n"
            "• _'Delete the Jira folder'_\n\n"
            "⚡ *Quick Shortcuts* (Start with Task ID):\n"
            "• `ID ok` - Mark task as done (e.g. `12 ok`)\n"
            "• `ID del` - Delete task (e.g. `12 del`)\n"
            "• `ID <new text>` - Edit task text (e.g. `12 call John tomorrow`)\n\n"
            "💡 *UI Buttons*:\n"
            "Every task has inline buttons to instantly check off ✅, move 📂, or delete 🗑 them directly from the list."
        )
        return await message.reply(help_text, parse_mode="Markdown")

    db: Session = SessionLocal()
    try:
        # Ensure UserSettings
        settings = db.query(UserSettings).filter(UserSettings.user_id == message.from_user.id).first()
        if not settings:
            settings = UserSettings(user_id=message.from_user.id)
            db.add(settings); db.commit()

        # Ensure Jira folder exists
        jira_folder = db.query(Folder).filter(
            Folder.user_id == message.from_user.id, Folder.name == JIRA_FOLDER_NAME
        ).first()
        if not jira_folder:
            jira_folder = Folder(user_id=message.from_user.id, name=JIRA_FOLDER_NAME)
            db.add(jira_folder); db.commit(); db.refresh(jira_folder)

        all_user_messages = db.query(UserMessage).filter(
            UserMessage.user_id == message.from_user.id,
            UserMessage.deleted == False
        ).order_by(UserMessage.timestamp.desc()).all()
        
        # Include recent non-completed tasks
        active_context = [m for m in all_user_messages if not m.is_completed][:15]
        
        # If user mentions a specific ID (e.g. #8 or "task 8"), inject it into context if not already there
        search_text = message.text.lower()
        if message.reply_to_message and message.reply_to_message.text:
            search_text += " " + message.reply_to_message.text.lower()
            
        id_mentions = re.findall(r"(?:#|task\s+|id:\s*)(\d+)", search_text)
        added_count = 0
        for tid_str in id_mentions:
            if added_count >= 10: break
            tid = int(tid_str)
            if not any(m.id == tid for m in active_context):
                t = db.query(UserMessage).filter(UserMessage.id == tid, UserMessage.user_id == message.from_user.id).first()
                if t: 
                    active_context.append(t)
                    added_count += 1
        
        # --- QUICK COMMANDS ---
        shortcut_match = re.match(r"^(\d+)\s+(.+)$", message.text.strip())
        if shortcut_match:
            target_id = int(shortcut_match.group(1))
            cmd_or_text = shortcut_match.group(2).strip()
            task = db.query(UserMessage).filter(
                UserMessage.id == target_id, UserMessage.user_id == message.from_user.id
            ).first()
            
            if task:
                low_cmd = cmd_or_text.lower()
                if low_cmd == "del":
                    task.deleted = True
                    if task.jira_key: delete_jira_issue(task.jira_key)
                    db.commit()
                    return await message.reply(f"🗑 Task `#{target_id}` deleted.", parse_mode="Markdown")
                elif low_cmd in ["ok", "done", "complete"]:
                    task.is_completed = True
                    if task.jira_key: complete_jira_issue(task.jira_key)
                    db.commit()
                    return await message.reply(f"✅ Task `#{target_id}` completed.", parse_mode="Markdown")
                else:
                    old_text = task.text
                    task.text = cmd_or_text
                    if task.jira_key: update_jira_issue(task.jira_key, cmd_or_text)
                    db.commit()
                    return await message.reply(
                        f"📝 Task `#{target_id}` updated!\n*Old:* {old_text}\n*New:* {task.text}",
                        parse_mode="Markdown")

        # --- AI ANALYSIS ---
        folders = db.query(Folder).filter(Folder.user_id == message.from_user.id).all()
        
        text_for_ai = message.text
        if message.reply_to_message and message.reply_to_message.text:
            reply_text = message.reply_to_message.text
            if len(reply_text) > 500:
                reply_text = reply_text[:500] + "..."
            text_for_ai = f"[In reply to previous message: '{reply_text}']\nUser says: {message.text}"
            
        analysis = await analyze_intent(text_for_ai, active_context, folders)
        action = analysis.get("action")
        response_text = analysis.get("response", "Ok.")
        reminder_iso = analysis.get("reminder_at_iso")
        repeat_hours = analysis.get("repeat_hours")
        folder_name = analysis.get("folder_name")
        
        reminder_dt = None
        if reminder_iso:
            try: reminder_dt = datetime.fromisoformat(reminder_iso.replace('Z', '+00:00'))
            except: pass

        sync_jira = False
        target_folder_id = None
        if folder_name or analysis.get("folder_id"):
            fid = analysis.get("folder_id")
            # Try lookup by ID first if provided
            if fid:
                folder = db.query(Folder).filter(Folder.id == fid, Folder.user_id == message.from_user.id).first()
            else:
                folder = db.query(Folder).filter(
                    Folder.user_id == message.from_user.id,
                    Folder.name.ilike(folder_name)
                ).first()
            
            if (folder_name and folder_name.lower() == "jira") or (folder and folder.name.lower() == "jira"):
                sync_jira = True
                target_folder_id = jira_folder.id
            elif folder:
                target_folder_id = folder.id
            elif folder_name:
                # Create if it doesn't exist and we have a name
                folder = Folder(user_id=message.from_user.id, name=folder_name)
                db.add(folder); db.commit(); db.refresh(folder)
                target_folder_id = folder.id

        if action in ["ADD", "ASK_REMINDER"]:
            tasks = analysis.get("tasks_to_add", [])
            if not tasks: tasks = [message.text]
            for t in tasks:
                jira_key = None
                if sync_jira:
                    jira_key = create_jira_issue(t)
                msg = UserMessage(
                    user_id=message.from_user.id, text=t,
                    reminder_at=reminder_dt, repeat_hours=repeat_hours,
                    folder_id=target_folder_id, jira_key=jira_key
                )
                db.add(msg)
            db.commit()
            jira_note = " (synced to Jira ✅)" if sync_jira else ""
            await message.reply(response_text + jira_note)
            
        elif action == "DELETE":
            target_id = analysis.get("target_id")
            if target_id:
                msg = db.query(UserMessage).filter(UserMessage.id == target_id).first()
                if msg:
                    msg.deleted = True
                    if msg.jira_key: delete_jira_issue(msg.jira_key)
                    db.commit()
            await message.reply(response_text)

        elif action == "COMPLETE":
            target_id = analysis.get("target_id")
            if target_id:
                msg = db.query(UserMessage).filter(UserMessage.id == target_id).first()
                if msg:
                    msg.is_completed = True
                    if msg.jira_key: complete_jira_issue(msg.jira_key)
                    db.commit()
            await message.reply(response_text)
                
        elif action == "LIST":
            # Sync Jira backlog before listing
            sync_jira_to_local(db, message.from_user.id)
            # Re-query after sync to include newly imported tasks
            all_user_messages = db.query(UserMessage).filter(
                UserMessage.user_id == message.from_user.id,
                UserMessage.deleted == False
            ).order_by(UserMessage.timestamp.desc()).all()
            active = [m for m in all_user_messages if not m.is_completed]
            if not active:
                await message.reply("Your list is empty. Try adding some tasks!")
            else:
                chunks = build_task_list_chunks(active)
                for chunk_text, chunk_tasks in chunks:
                    await message.reply(
                        chunk_text,
                        parse_mode="Markdown",
                        reply_markup=get_task_keyboard(chunk_tasks))

        elif action == "LIST_FOLDERS":
            # Sync Jira backlog before listing
            sync_jira_to_local(db, message.from_user.id)
            # Re-query after sync
            all_user_messages = db.query(UserMessage).filter(
                UserMessage.user_id == message.from_user.id,
                UserMessage.deleted == False
            ).order_by(UserMessage.timestamp.desc()).all()
            active = [m for m in all_user_messages if not m.is_completed]
            folders = db.query(Folder).filter(Folder.user_id == message.from_user.id).all()
            
            if not active and not folders:
                await message.reply("Your list and folders are empty. Try adding some tasks!")
            else:
                await message.reply(
                    "🗂 *Your Folders*\nSelect a folder to view its tasks:",
                    parse_mode="Markdown",
                    reply_markup=get_folder_keyboard(folders, active)
                )
        
        elif action == "CREATE_FOLDER":
            fn = analysis.get("folder_name")
            if fn:
                existing = db.query(Folder).filter(
                    Folder.user_id == message.from_user.id,
                    Folder.name.ilike(fn)
                ).first()
                if not existing:
                    db.add(Folder(user_id=message.from_user.id, name=fn))
                    db.commit()
                else:
                    response_text = f"Folder '{fn}' already exists."
            await message.reply(response_text)

        elif action == "DELETE_FOLDER":
            fn = analysis.get("folder_name")
            fid = analysis.get("folder_id")
            folder = None
            
            if fid:
                folder = db.query(Folder).filter(Folder.id == fid, Folder.user_id == message.from_user.id).first()
            elif fn:
                # Try exact match first
                folder = db.query(Folder).filter(Folder.user_id == message.from_user.id, Folder.name == fn).first()
                if not folder:
                    # Fallback to ilike
                    folder = db.query(Folder).filter(Folder.user_id == message.from_user.id, Folder.name.ilike(fn)).first()
            
            if folder:
                if folder.name.lower() == "jira":
                    await message.reply("🔒 Error: The main 'Jira' folder cannot be deleted.")
                else:
                    # Move tasks to uncategorized
                    db.query(UserMessage).filter(UserMessage.folder_id == folder.id).update({UserMessage.folder_id: None})
                    db.delete(folder)
                    db.commit()
                    await message.reply(f"🗑 Folder '{folder.name}' deleted. Any tasks in it were moved to Uncategorized.")
            else:
                await message.reply(f"Folder not found.")

        elif action in ["EDIT", "MOVE_TASK"]:
            target_id = analysis.get("target_id")
            # Convert target_id to int if possible, or handle "None"/invalid
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
                    is_moving = "move" in message.text.lower() or action == "MOVE_TASK" or folder_name or analysis.get("folder_id")
                    if is_moving and not target_folder_id and not new_text:
                        await message.reply(f"I found task `#{target_id}`, but I couldn't determine the destination folder.")
                        return

                    if not any([new_text, target_folder_id, reminder_dt, repeat_hours]):
                        await message.reply(f"I found task `#{target_id}`, but I couldn't understand what to modify. Please be more specific.")
                        return

                    if new_text:
                        msg.text = new_text
                        if msg.jira_key: update_jira_issue(msg.jira_key, new_text)
                    if reminder_dt: msg.reminder_at = reminder_dt
                    if repeat_hours: msg.repeat_hours = repeat_hours
                    if target_folder_id is not None: msg.folder_id = target_folder_id
                    
                    is_currently_jira = (msg.folder_id == jira_folder.id)
                    if is_currently_jira and not msg.jira_key:
                        msg.jira_key = create_jira_issue(msg.text)
                    elif not is_currently_jira and msg.jira_key:
                        delete_jira_issue(msg.jira_key)
                        msg.jira_key = None
                        
                    db.commit()
                    await message.reply(response_text)
                else:
                    await message.reply(f"Task `#{target_id}` not found.")
            else:
                await message.reply("Which task should I edit? Please specify an ID.")
            
        elif action == "OTHER":
            await message.reply(response_text)
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback; traceback.print_exc()
        await message.reply("Oops, something went wrong.")
    finally:
        db.close()

async def send_hourly_summary(chat_id, summary_text):
    await bot.send_message(chat_id, f"📊 *Hour Summary*\n\n{summary_text}", parse_mode="Markdown")

async def send_specific_reminder(chat_id, task_text):
    await bot.send_message(chat_id, f"🔔 *REMINDER*\n{task_text}")
