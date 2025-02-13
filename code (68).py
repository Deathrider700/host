import os
import zipfile
import subprocess
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler, CallbackQueryHandler
import re
import tempfile
import shutil

# --- Configuration ---
BOT_TOKEN = "YOUR_BOT_TOKEN" # Replace with your actual bot token
SCRIPTS_DIR = "user_scripts" # Directory to store user scripts
REQUIREMENTS_FILE = "requirements.txt"
MAIN_FILE_REGEX = r"main\.py$" # Regex to detect main.py (adjust if needed)
BOT_TOKEN_REGEX = r"(BOT_TOKEN|TELEGRAM_BOT_TOKEN|TOKEN)\s*=\s*[\'\"]([^\'\"]+)[\'\"]" # Regex to find bot token in main file

# --- Global Data Structures ---
user_scripts = {}  # Dictionary to store user scripts: {user_id: {script_name: {path: ..., process: ...}}}
running_processes = {} # Dictionary to track running processes: {process_id: {user_id: ..., script_name: ...}}

# --- Utility Functions ---
def extract_bot_token_variable_name(main_file_path):
    """Attempts to extract a potential environment variable name for the bot token from the main file."""
    try:
        with open(main_file_path, 'r') as f:
            content = f.read()
            match = re.search(BOT_TOKEN_REGEX, content, re.IGNORECASE)
            if match:
                return match.group(1).strip()
    except Exception as e:
        print(f"Error reading main file to extract token variable: {e}")
    return "BOT_TOKEN" # Default if not found

def install_requirements(script_path):
    """Installs requirements from requirements.txt if it exists in the script directory."""
    requirements_path = os.path.join(script_path, REQUIREMENTS_FILE)
    if os.path.exists(requirements_path):
        try:
            subprocess.check_call(["pip", "install", "-r", requirements_path], cwd=script_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) # Suppress output for cleaner bot experience
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error installing requirements: {e}")
            return False
    return True # No requirements.txt, so consider it successful

def run_script(user_id, script_name, script_path, main_file, bot_token=None, bot_token_env_name="BOT_TOKEN"):
    """Runs the user's Python script as a background process."""
    main_file_path = os.path.join(script_path, main_file)

    env = os.environ.copy() # Copy current environment
    if bot_token:
        env[bot_token_env_name] = bot_token # Set bot token as environment variable

    try:
        process = subprocess.Popen(["python", main_file], cwd=script_path, env=env, start_new_session=True) # start_new_session for independent process group
        process_id = process.pid
        running_processes[process_id] = {"user_id": user_id, "script_name": script_name}

        if user_id not in user_scripts:
            user_scripts[user_id] = {}
        if script_name not in user_scripts[user_id]:
            user_scripts[user_id][script_name] = {}

        user_scripts[user_id][script_name]["path"] = script_path
        user_scripts[user_id][script_name]["process"] = process_id

        return process_id
    except Exception as e:
        print(f"Error running script: {e}")
        return None

def stop_script(user_id, script_name):
    """Stops a running script for a user."""
    if user_id in user_scripts and script_name in user_scripts[user_id] and "process" in user_scripts[user_id][script_name]:
        process_id = user_scripts[user_id][script_name]["process"]
        try:
            if process_id in running_processes:
                process = subprocess.Popen(["kill", "-9", str(process_id)]) # Forcefully kill the process (consider SIGTERM and timeout for graceful shutdown)
                process.wait() # Wait for kill command to complete
                del running_processes[process_id]
                del user_scripts[user_id][script_name]
                return True
            else:
                return False # Process not found in running list (maybe already stopped?)
        except Exception as e:
            print(f"Error stopping script: {e}")
            return False
    return False # Script not found for user

# --- Command Handlers ---

def start(update, context):
    """Sends a welcome message and help information with inline keyboard."""
    user = update.message.from_user
    keyboard = [[InlineKeyboardButton("Help", callback_data='help')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        f"Hello {user.first_name}! Welcome to the Script Host Bot.\nUse the buttons below to navigate.",
        reply_markup=reply_markup
    )

def help_command(update, context):
    """Sends help information with available commands using inline keyboard."""
    keyboard = [
        [InlineKeyboardButton("New Bot", callback_data='new')],
        [InlineKeyboardButton("List Bots", callback_data='all')],
        [InlineKeyboardButton("Remove Bot", callback_data='remove_help')], # Help for remove command
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message: # Command initiated via text command
        update.message.reply_text("Here are the available commands:", reply_markup=reply_markup)
    elif update.callback_query: # Command initiated via button
        query = update.callback_query
        query.answer() # Acknowledge button press
        query.edit_message_text("Here are the available commands:", reply_markup=reply_markup)


# --- /new command conversation ---
NEW_SCRIPT_ZIP, GET_MAIN_FILE_NAME, CHECK_BOT_TOKEN, GET_BOT_TOKEN = range(4)

def new_script_start(update, context):
    """Starts the /new command conversation by asking for a zip file."""
    if update.message: # Command initiated via text command
        update.message.reply_text(
            "Okay, let's host a *new bot*! 🚀\nPlease send me a *zip file* containing your Python script and requirements (if any).",
            parse_mode=telegram.ParseMode.MARKDOWN
        )
    elif update.callback_query: # Command initiated via button
        query = update.callback_query
        query.answer() # Acknowledge button press
        query.edit_message_text(
            "Okay, let's host a *new bot*! 🚀\nPlease send me a *zip file* containing your Python script and requirements (if any).",
            parse_mode=telegram.ParseMode.MARKDOWN
        )
    return NEW_SCRIPT_ZIP

def new_script_zip_file(update, context):
    """Handles the uploaded zip file."""
    user = update.message.from_user
    zip_file = update.message.document
    if zip_file.mime_type != 'application/zip':
        update.message.reply_text("⚠️ Please send a *valid zip file*.", parse_mode=telegram.ParseMode.MARKDOWN)
        return NEW_SCRIPT_ZIP

    try:
        temp_zip_file = tempfile.NamedTemporaryFile(delete=False) # Create a temp file to store zip
        context.user_data['temp_zip_path'] = temp_zip_file.name
        file_id = zip_file.file_id
        zip_telegram_file = context.bot.get_file(file_id)
        zip_telegram_file.download(temp_zip_file.name)
        temp_zip_file.close()

        update.message.reply_text("✅ Zip file received! Now, please tell me the name of your *main Python file* (e.g., `main.py`).", parse_mode=telegram.ParseMode.MARKDOWN)
        return GET_MAIN_FILE_NAME
    except Exception as e:
        print(f"Error downloading or saving zip file: {e}")
        update.message.reply_text("❌ Sorry, there was an error processing your zip file. Please try again.", parse_mode=telegram.ParseMode.MARKDOWN)
        return ConversationHandler.END

def new_script_main_file_name(update, context):
    """Gets the main file name from the user and processes the zip."""
    user = update.message.from_user
    main_file_name = update.message.text.strip()

    if not re.search(MAIN_FILE_REGEX, main_file_name): # Basic validation, improve as needed
        update.message.reply_text(f"⚠️ Please provide a *valid main file name* (e.g., `main.py`). Currently you provided: `{main_file_name}`", parse_mode=telegram.ParseMode.MARKDOWN)
        return GET_MAIN_FILE_NAME

    context.user_data['main_file_name'] = main_file_name
    temp_zip_path = context.user_data.get('temp_zip_path')

    if not temp_zip_path or not os.path.exists(temp_zip_path):
        update.message.reply_text("❌ Error: Zip file not found. Please start the process again with /new.", parse_mode=telegram.ParseMode.MARKDOWN)
        return ConversationHandler.END

    script_name = f"bot_{user.id}_{update.message.message_id}" # Unique script name
    script_path = os.path.join(SCRIPTS_DIR, script_name)
    context.user_data['script_name'] = script_name # Store script_name for later use

    try:
        os.makedirs(script_path, exist_ok=True) # Create script directory
        with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
            zip_ref.extractall(script_path)
        os.remove(temp_zip_path) # Delete temp zip file

        if not os.path.exists(os.path.join(script_path, main_file_name)):
            update.message.reply_text(f"❌ Error: Main file `{main_file_name}` not found in the zip file. Please check your zip contents and try again.", parse_mode=telegram.ParseMode.MARKDOWN)
            shutil.rmtree(script_path) # Cleanup created directory
            return ConversationHandler.END

        if not install_requirements(script_path):
            update.message.reply_text("⚠️ *Warning*: There was an issue installing requirements. The bot might not run correctly if it has dependencies.", parse_mode=telegram.ParseMode.MARKDOWN)

        main_file_full_path = os.path.join(script_path, main_file_name)
        bot_token_var_name = extract_bot_token_variable_name(main_file_full_path)
        context.user_data['bot_token_var_name'] = bot_token_var_name

        with open(main_file_full_path, 'r') as f:
            content = f.read()
            if re.search(BOT_TOKEN_REGEX, content, re.IGNORECASE):
                update.message.reply_text(f"👍 Great! It seems your script might already handle the bot token. I will try to run it now assuming you've set the environment variable `{bot_token_var_name}` or will provide it via environment.\nIf it doesn't work, you might need to provide the token manually.", parse_mode=telegram.ParseMode.MARKDOWN)
                process_id = run_script(user.id, script_name, script_path, main_file_name, bot_token_env_name=bot_token_var_name)
                if process_id:
                    update.message.reply_text(f"🚀 Bot `{script_name}` *started successfully*! (Process ID: `{process_id}`)", parse_mode=telegram.ParseMode.MARKDOWN)
                else:
                    update.message.reply_text(f"❌ Failed to start bot `{script_name}`. Check server logs for errors.", parse_mode=telegram.ParseMode.MARKDOWN)
                return ConversationHandler.END
            else:
                keyboard = [
                    [InlineKeyboardButton("Yes, I'll provide it", callback_data='needs_token_yes')],
                    [InlineKeyboardButton("No, it's handled differently", callback_data='needs_token_no')],
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                update.message.reply_text("❓ Does your script require a *Telegram Bot Token* as an environment variable?", reply_markup=reply_markup, parse_mode=telegram.ParseMode.MARKDOWN)
                return CHECK_BOT_TOKEN

    except Exception as e:
        print(f"Error processing zip file or running script: {e}")
        update.message.reply_text("❌ Sorry, there was an error processing your script. Please try again.", parse_mode=telegram.ParseMode.MARKDOWN)
        shutil.rmtree(script_path, ignore_errors=True) # Cleanup in case of errors
        return ConversationHandler.END


def new_script_check_bot_token(update, context):
    """Asks user if bot token is included or needed using inline buttons."""
    query = update.callback_query
    query.answer() # Acknowledge button press
    answer = query.data

    if answer == 'needs_token_yes':
        query.edit_message_text("👍 Okay, please send me your *Bot Token*. I will set it as an environment variable for your script.", parse_mode=telegram.ParseMode.MARKDOWN)
        return GET_BOT_TOKEN
    elif answer == 'needs_token_no':
        user = query.from_user
        script_name = context.user_data['script_name'] # Retrieve script_name
        main_file_name = context.user_data['main_file_name']
        script_path = os.path.join(SCRIPTS_DIR, script_name)
        bot_token_var_name = context.user_data.get('bot_token_var_name', "BOT_TOKEN") # Get the detected name or default

        query.edit_message_text(f"👍 Okay, I will try to run your script *without* a bot token provided by me. Make sure your script handles token in other ways (e.g., config file, already set environment variable).\nRunning script assuming environment variable name will be `{bot_token_var_name}` (if used in script)", parse_mode=telegram.ParseMode.MARKDOWN)
        process_id = run_script(user.id, script_name, script_path, main_file_name, bot_token_env_name=bot_token_var_name)
        if process_id:
            query.message.reply_text(f"🚀 Bot `{script_name}` *started successfully*! (Process ID: `{process_id}`)", parse_mode=telegram.ParseMode.MARKDOWN)
        else:
            query.message.reply_text(f"❌ Failed to start bot `{script_name}`. Check server logs for errors.", parse_mode=telegram.ParseMode.MARKDOWN)
        return ConversationHandler.END
    else:
        keyboard = [
            [InlineKeyboardButton("Yes, I'll provide it", callback_data='needs_token_yes')],
            [InlineKeyboardButton("No, it's handled differently", callback_data='needs_token_no')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text("❓ Please choose an option: Does your script require a *Bot Token*?", reply_markup=reply_markup, parse_mode=telegram.ParseMode.MARKDOWN)
        return CHECK_BOT_TOKEN

def new_script_get_bot_token(update, context):
    """Gets the bot token from the user and runs the script."""
    bot_token = update.message.text.strip()
    user = update.message.from_user
    script_name = context.user_data['script_name'] # Retrieve script_name
    main_file_name = context.user_data['main_file_name']
    script_path = os.path.join(SCRIPTS_DIR, script_name)
    bot_token_var_name = context.user_data.get('bot_token_var_name', "BOT_TOKEN") # Get the detected name or default


    process_id = run_script(user.id, script_name, script_path, main_file_name, bot_token=bot_token, bot_token_env_name=bot_token_var_name)
    if process_id:
        update.message.reply_text(f"🚀 Bot `{script_name}` *started successfully* with Bot Token set as environment variable `{bot_token_var_name}`! (Process ID: `{process_id}`)", parse_mode=telegram.ParseMode.MARKDOWN)
    else:
        update.message.reply_text(f"❌ Failed to start bot `{script_name}`. Check server logs for errors. Make sure the Bot Token is correct.", parse_mode=telegram.ParseMode.MARKDOWN)
    return ConversationHandler.END

def new_script_cancel(update, context):
    """Cancels the /new command conversation."""
    if update.message: # Command initiated via text command
        update.message.reply_text("❌ Bot script upload *cancelled*.", parse_mode=telegram.ParseMode.MARKDOWN)
    elif update.callback_query: # Command initiated via button
        query = update.callback_query
        query.answer() # Acknowledge button press
        query.edit_message_text("❌ Bot script upload *cancelled*.", parse_mode=telegram.ParseMode.MARKDOWN)

    temp_zip_path = context.user_data.get('temp_zip_path')
    if temp_zip_path and os.path.exists(temp_zip_path):
        os.remove(temp_zip_path) # Clean up temp zip if cancelled
    return ConversationHandler.END

# --- /all command ---
def all_bots_command(update, context):
    """Lists all bots hosted by the user with inline buttons to remove."""
    user_id = update.effective_user.id # Use effective_user for both commands and callbacks
    if user_id in user_scripts:
        scripts = user_scripts[user_id]
        if scripts:
            keyboard = []
            bot_list_text = "*Your hosted bots:*\n"
            for name, data in scripts.items():
                bot_list_text += f"- `{name}` (Process ID: `{data.get('process', 'N/A')}`)\n"
                keyboard.append([InlineKeyboardButton(f"Remove {name}", callback_data=f'remove_bot:{name}')]) # Callback data to identify bot to remove

            reply_markup = InlineKeyboardMarkup(keyboard)
            if update.message: # Command initiated via text command
                update.message.reply_text(bot_list_text, reply_markup=reply_markup, parse_mode=telegram.ParseMode.MARKDOWN)
            elif update.callback_query: # Command initiated via button
                query = update.callback_query
                query.answer() # Acknowledge button press
                query.edit_message_text(bot_list_text, reply_markup=reply_markup, parse_mode=telegram.ParseMode.MARKDOWN)

        else:
            if update.message: # Command initiated via text command
                update.message.reply_text("You have *no bots hosted* currently.", parse_mode=telegram.ParseMode.MARKDOWN)
            elif update.callback_query: # Command initiated via button
                query = update.callback_query
                query.answer() # Acknowledge button press
                query.edit_message_text("You have *no bots hosted* currently.", parse_mode=telegram.ParseMode.MARKDOWN)
    else:
        if update.message: # Command initiated via text command
            update.message.reply_text("You have *no bots hosted* currently.", parse_mode=telegram.ParseMode.MARKDOWN)
        elif update.callback_query: # Command initiated via button
            query = update.callback_query
            query.answer() # Acknowledge button press
            query.edit_message_text("You have *no bots hosted* currently.", parse_mode=telegram.ParseMode.MARKDOWN)


# --- /remove command (now handled by callback query from /all) ---
def remove_bot_command_callback(update, context):
    """Stops and removes a bot, triggered by callback from /all command."""
    query = update.callback_query
    query.answer() # Acknowledge button press
    user_id = query.from_user.id
    script_name_to_remove = query.data.split(':')[1] # Extract script_name from callback_data

    if stop_script(user_id, script_name_to_remove):
        script_path_to_remove = user_scripts[user_id].get(script_name_to_remove, {}).get('path')
        if script_path_to_remove:
            shutil.rmtree(script_path_to_remove, ignore_errors=True) # Remove script directory
        query.edit_message_text(f"✅ Bot `{script_name_to_remove}` *stopped and removed*.", parse_mode=telegram.ParseMode.MARKDOWN)
    else:
        query.edit_message_text(f"❌ Could not find or stop bot `{script_name_to_remove}`.", parse_mode=telegram.ParseMode.MARKDOWN)

def remove_help_command(update, context):
    """Help message for remove command (can be triggered from help menu)."""
    query = update.callback_query
    query.answer()
    query.edit_message_text("To remove a bot, first use the `/all` command to list your hosted bots. Then, use the 'Remove' button associated with the bot you want to stop and remove. ", parse_mode=telegram.ParseMode.MARKDOWN)


def error(update, context):
    """Log Errors caused by Updates."""
    print(f'Update {update} caused error {context.error}')

def main():
    """Start the bot."""
    if not os.path.exists(SCRIPTS_DIR):
        os.makedirs(SCRIPTS_DIR)

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("all", all_bots_command))
    dp.add_handler(CommandHandler("remove", remove_help_command)) # Keep /remove for help text, removal is button based now

    # /new command conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('new', new_script_start), CallbackQueryHandler(new_script_start, pattern='^new$')], # Start from button too
        states={
            NEW_SCRIPT_ZIP: [MessageHandler(Filters.document, new_script_zip_file)],
            GET_MAIN_FILE_NAME: [MessageHandler(Filters.text & ~Filters.command, new_script_main_file_name)],
            CHECK_BOT_TOKEN: [CallbackQueryHandler(new_script_check_bot_token)], # Handle button presses for token question
            GET_BOT_TOKEN: [MessageHandler(Filters.text & ~Filters.command, new_script_get_bot_token)],
        },
        fallbacks=[CallbackQueryHandler(new_script_cancel, pattern='^cancel$'), CommandHandler('cancel', new_script_cancel)], # Cancel from button or command
    )
    dp.add_handler(conv_handler)

    dp.add_handler(CallbackQueryHandler(help_command, pattern='^help$')) # Help button
    dp.add_handler(CallbackQueryHandler(all_bots_command, pattern='^all$')) # All bots button
    dp.add_handler(CallbackQueryHandler(remove_bot_command_callback, pattern='^remove_bot:')) # Remove bot button callback
    dp.add_handler(CallbackQueryHandler(remove_help_command, pattern='^remove_help$')) # Help for remove

    dp.add_error_handler(error)

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()