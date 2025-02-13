import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler, # Import CallbackQueryHandler
    MessageHandler,
    filters,
)
import subprocess
import os
import zipfile
import json
import shutil
import re

# --- Constants ---
TOKEN = "8098433835:AAHY_9dJsoRZp9ydVtdawSneIyMEFPdgLbI"  # Replace with your bot token
DATA_FILE = "user_bots.json"
BASE_DIR = "deployed_bots"

# --- States for the ConversationHandler ---
GET_TOKEN, GET_ZIP, GET_MAIN_FILE, GET_REMOVE_BOT = range(4)

# --- Helper Functions ---

def load_user_data():
    """Loads user data from the JSON file."""
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_user_data(user_data):
    """Saves user data to the JSON file."""
    with open(DATA_FILE, "w") as f:
        json.dump(user_data, f, indent=4)

def sanitize_filename(filename):
    """Sanitizes a filename by removing potentially dangerous characters."""
    return re.sub(r'[\\/*?:"<>|]', "", filename)

def is_valid_filename(filename):
    """Simple check of the provided file name"""
    if ".." in filename or "/" in filename or "\\" in filename:
      return False
    return True

async def start_bot_process(user_id, bot_token, main_file, extraction_dir, application): # Pass application
    """Starts the user's bot script in a new process."""
    user_data = load_user_data()
    try:
        # Attempt to install requirements.
        requirements_file = os.path.join(extraction_dir, "requirements.txt")
        if os.path.exists(requirements_file):
            command = [
                "python",
                "-m",
                "pip",
                "install",
                "-r",
                requirements_file,
                "--target",
                extraction_dir,
            ]
            process_install = subprocess.run(
                command, capture_output=True, text=True, check=True, cwd=extraction_dir, timeout=120
            )
            print(f"Requirements installation output: {process_install.stdout}")
        else:
            print("requirements.txt not found. Skipping installation.")

        # Start the bot process.
        command = ["python", os.path.join(extraction_dir, main_file)]
        process = subprocess.Popen(command, cwd=extraction_dir, start_new_session=True)

        # Generate a unique bot ID
        bot_id = 1
        while str(bot_id) in user_data.get(str(user_id), {}):
          bot_id += 1
        bot_id = str(bot_id)

        # Store bot information.
        if str(user_id) not in user_data:
            user_data[str(user_id)] = {}

        user_data[str(user_id)][bot_id] = {
            "token": bot_token,
            "main_file": main_file,
            "pid": process.pid,
            "extraction_dir": extraction_dir,
        }
        save_user_data(user_data)
        return bot_id

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"Error in start_bot_process: {e}")
        error_message = f"Error installing requirements or starting bot:\n{e}"
        if isinstance(e, subprocess.CalledProcessError):
          error_message += f"\nStdout:\n{e.stdout}\nStderr:\n{e.stderr}"

        await application.bot.send_message(chat_id=user_id, text=error_message)
        # Clean the user data
        if str(user_id) in user_data:
            del user_data[str(user_id)]
            save_user_data(user_data)
        return None
    except Exception as e:
        print(f"Other error: {e}")
        await application.bot.send_message(chat_id=user_id, text=f"An unexpected error occurred: {e}")
        # Clean the user data
        if str(user_id) in user_data:
            del user_data[str(user_id)]
            save_user_data(user_data)
        return None

# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Starts the bot."""
    await update.message.reply_text(
        "👋 Hello! I'm your personal bot deployment manager. Use the commands below to manage your bots:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Deploy a new bot", callback_data='help_new')],
            [InlineKeyboardButton("❓ Help", callback_data='help_commands')],
            [InlineKeyboardButton("🤖 List my bots", callback_data='help_all')],
            [InlineKeyboardButton("⛔ Remove a bot", callback_data='help_remove')],
        ])
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays help information."""
    await update.message.reply_text(
        "Here are the available commands:\n\n"
        "**/new** - Deploy a new bot.\n"
        "**/help** - Show this help message.\n"
        "**/all** - List your running bots.\n"
        "**/remove** - Remove a running bot.",
        parse_mode=telegram.constants.ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back to Start", callback_data='back_to_start')],
        ])
    )

async def new_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the bot deployment conversation."""
    await update.message.reply_text("Okay, let's deploy a new bot! First, please send me your bot's API token:")
    return GET_TOKEN

async def get_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets the bot token from the user."""
    context.user_data["token"] = update.message.text
    await update.message.reply_text("Great! Now, please upload the zip file containing your bot's code:")
    return GET_ZIP

async def get_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets the zip file from the user."""
    user_id = update.message.from_user.id
    document = update.message.document

    if not document.file_name.endswith(".zip"):
        await update.message.reply_text("⚠️ Please send a valid zip file. The file must have a `.zip` extension.")
        return GET_ZIP  # Stay in the same state

    # Create a directory for the user if it doesn't exist.
    user_dir = os.path.join(BASE_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)

    # Download the zip file.
    zip_file_path = os.path.join(user_dir, sanitize_filename(document.file_name))  # Use sanitize_filename
    file = await document.get_file()
    await file.download_to_drive(zip_file_path)
    context.user_data["zip_path"] = zip_file_path

    await update.message.reply_text("Almost there! Lastly, tell me the name of your main Python file (e.g., `main.py`):")
    return GET_MAIN_FILE

async def get_main_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets the main file name, unzips, installs dependencies, and starts the bot."""
    user_id = update.message.from_user.id
    main_file = update.message.text
    if not is_valid_filename(main_file):
        await update.message.reply_text("❌ Invalid file name. Please provide a valid file name (e.g., `main.py`). Avoid using paths or special characters.")
        return GET_MAIN_FILE

    context.user_data["main_file"] = main_file
    zip_file_path = context.user_data["zip_path"]
    token = context.user_data["token"]

    # Create a unique directory for the extracted files.
    extraction_dir = os.path.join(BASE_DIR, str(user_id), os.path.splitext(os.path.basename(zip_file_path))[0])
    # Add an increment if folder exists
    i = 1
    while os.path.isdir(extraction_dir):
       extraction_dir = os.path.join(BASE_DIR, str(user_id), os.path.splitext(os.path.basename(zip_file_path))[0] + "_" + str(i))
       i += 1
    os.makedirs(extraction_dir, exist_ok=True)


    # Unzip the file.
    try:
        with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
            zip_ref.extractall(extraction_dir)
    except zipfile.BadZipFile:
        await update.message.reply_text("🚫 The provided zip file is invalid or corrupted. Please check your zip file and try again.")
        shutil.rmtree(extraction_dir)  # Clean up the extraction directory
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"⚠️ An error occurred while unzipping the file: `{e}`. Please try again or check your zip file.", parse_mode=telegram.constants.ParseMode.MARKDOWN)
        shutil.rmtree(extraction_dir)  # Clean up the extraction directory
        return ConversationHandler.END

    # Start the bot and store its information.
    bot_id = await start_bot_process(user_id, token, main_file, extraction_dir, application) # Pass application

    if bot_id is not None:
      await update.message.reply_text(f"✅ Your bot (ID: `{bot_id}`) has been successfully deployed and started!", parse_mode=telegram.constants.ParseMode.MARKDOWN)
    else:
       await update.message.reply_text(f"❌ Failed to start your bot. Please check the error messages and try again.")

    return ConversationHandler.END

async def all_bots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lists all running bots for the current user."""
    user_id = str(update.message.from_user.id)
    user_data = load_user_data()

    if user_id in user_data and user_data[user_id]:
        bot_list_text = "Here are your currently running bots:\n\n"
        keyboard_buttons = []
        for bot_id, bot_data in user_data[user_id].items():
            bot_list_text += f"🤖 **ID:** `{bot_id}` - **Main File:** `{bot_data['main_file']}`\n"
            keyboard_buttons.append([InlineKeyboardButton(f"⛔ Remove Bot ID {bot_id}", callback_data=f'remove_bot_{bot_id}')]) # Callback data includes bot_id

        reply_markup = InlineKeyboardMarkup(keyboard_buttons) if keyboard_buttons else None # Create markup only if buttons exist

        await update.message.reply_text(bot_list_text, parse_mode=telegram.constants.ParseMode.MARKDOWN, reply_markup=reply_markup)

    else:
        await update.message.reply_text("You don't have any bots running at the moment.",
                                      reply_markup=InlineKeyboardMarkup([
                                          [InlineKeyboardButton("🚀 Deploy a new bot", callback_data='help_new')],
                                          [InlineKeyboardButton("⬅️ Back to Start", callback_data='back_to_start')],
                                      ]))


async def remove_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: # Changed to None, handles initial command
    """Lists bots with inline buttons for removal."""
    user_id = str(update.message.from_user.id)
    user_data = load_user_data()

    if user_id not in user_data or not user_data[user_id]:
        await update.message.reply_text("You don't have any bots running to remove.",
                                      reply_markup=InlineKeyboardMarkup([
                                          [InlineKeyboardButton("🚀 Deploy a new bot", callback_data='help_new')],
                                          [InlineKeyboardButton("⬅️ Back to Start", callback_data='back_to_start')],
                                      ]))
        return

    keyboard_buttons = []
    for bot_id, bot_data in user_data[user_id].items():
        keyboard_buttons.append([InlineKeyboardButton(f"⛔ Remove Bot ID {bot_id} ({bot_data['main_file']})", callback_data=f'remove_bot_{bot_id}')]) # Callback data includes bot_id

    reply_markup = InlineKeyboardMarkup(keyboard_buttons)
    await update.message.reply_text("Which bot would you like to remove? Please select a bot from the list below:", reply_markup=reply_markup)


async def remove_bot_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Handle button press
    """Handles the callback query from inline keyboard button press for removal."""
    query = update.callback_query
    await query.answer() # Acknowledge the button press

    bot_id_to_remove = query.data.split('_')[-1] # Extract bot_id from callback_data
    user_id = str(query.from_user.id)
    user_data = load_user_data()

    if bot_id_to_remove in user_data.get(user_id, {}):
      try:
          bot_data = user_data[user_id][bot_id_to_remove]
          pid = bot_data["pid"]
          extraction_dir = bot_data["extraction_dir"]

          # Terminate the process.
          os.kill(pid, 9)  # Send SIGKILL signal
          # Clean the folder
          shutil.rmtree(extraction_dir)

          # Remove bot information from user_data.
          del user_data[user_id][bot_id_to_remove]

          # If the user has no more bots, remove the user entry
          if not user_data[user_id]:
              del user_data[user_id]

          save_user_data(user_data)
          await query.edit_message_text(text=f"✅ Bot ID `{bot_id_to_remove}` has been stopped and removed.", parse_mode=telegram.constants.ParseMode.MARKDOWN,
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Start", callback_data='back_to_start')]])) # Edit message instead of new message
      except ProcessLookupError:
          await query.edit_message_text(text=f"⚠️ Bot ID `{bot_id_to_remove}` process not found. It might have already stopped. Removing its data.", parse_mode=telegram.constants.ParseMode.MARKDOWN,
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Start", callback_data='back_to_start')]]))
          del user_data[user_id][bot_id_to_remove] # Remove anyway
          save_user_data(user_data)
      except Exception as e:
          await query.edit_message_text(text=f"❌ An error occurred while removing Bot ID `{bot_id_to_remove}`: `{e}`", parse_mode=telegram.constants.ParseMode.MARKDOWN,
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Start", callback_data='back_to_start')]]))
    else:
      await query.edit_message_text(text="❌ Invalid Bot ID selected. Please try again.",
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Start", callback_data='back_to_start')]]))

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles all callback queries."""
    query = update.callback_query
    await query.answer() # Acknowledge button press

    if query.data == 'help_new':
        await query.message.reply_text("To deploy a new bot, use the /new command.") # Or directly start new_bot conversation if desired
    elif query.data == 'help_commands':
        await help_command(update, context)
    elif query.data == 'help_all':
        await all_bots(update, context)
    elif query.data == 'help_remove':
        await remove_bot(update, context)
    elif query.data == 'back_to_start':
        await start(update, context)
    elif query.data.startswith('remove_bot_'): # Handle remove bot callback
        await remove_bot_confirm(update, context)
    else:
        await query.message.reply_text(f"Unknown callback data: {query.data}")

def main() -> None:
    """Runs the bot."""
    application = Application.builder().token(TOKEN).build()

    # Create the base directory if it doesn't exist.
    os.makedirs(BASE_DIR, exist_ok=True)

    # Conversation handler for /new command.
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("new", new_bot)],
        states={
            GET_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_token)],
            GET_ZIP: [MessageHandler(filters.ATTACHMENT, get_zip)],
            GET_MAIN_FILE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_main_file)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)

    # Command handlers.
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("all", all_bots))
    application.add_handler(CommandHandler("remove", remove_bot)) # remove_bot now handles initial command

    # Callback query handler - handles button presses
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    # Start the bot.
    application.run_polling()

if __name__ == "__main__":
    main()