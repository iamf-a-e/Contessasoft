import os
import logging
import requests
import random
import string
from datetime import datetime
from flask import Flask, request, jsonify, render_template
import json
import traceback
from enum import Enum
from upstash_redis import Redis
import redis

app = Flask(__name__)

# Environment variables
wa_token = os.environ.get("WA_TOKEN")
phone_id = os.environ.get("PHONE_ID")
gen_api = os.environ.get("GEN_API")
owner_phone = os.environ.get("OWNER_PHONE")
redis_url = os.environ.get("REDIS_URL")
AGENT_NUMBERS = ["+263785019494"]

# Redis client setup
redis_client = Redis(
    url=os.environ.get('UPSTASH_REDIS_URL'),
    token=os.environ.get('UPSTASH_REDIS_TOKEN')
)

# Global variables removed - each conversation will have its own agent and conversation ID


required_vars = ['WA_TOKEN', 'PHONE_ID', 'UPSTASH_REDIS_URL', 'UPSTASH_REDIS_TOKEN']
missing_vars = [var for var in required_vars if not os.getenv(var)]
if missing_vars:
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Test connection
try:
    redis_client.set("foo", "bar")
    print("✅ Upstash Redis connection successful")
except Exception as e:
    print(f"❌ Upstash Redis error: {e}")
    raise
    
logging.basicConfig(level=logging.INFO)

class MainMenuOptions(Enum):
    ABOUT = "Learn about Contessasoft"
    SERVICES = "Our Services"
    QUOTE = "Request a Quote"
    SUPPORT = "Talk to Support"
    CONTACT = "Contact Us"

class AboutOptions(Enum):
    PORTFOLIO = "View our portfolio"
    PROFILE = "Download company profile"
    BACK = "Back to main menu"

class ServiceOptions(Enum):
    DOMAIN = "Domain Registration"
    WEBSITE = "Website Development"
    MOBILE = "Mobile App Development"
    CHATBOT = "WhatsApp Chatbots"
    PAYMENTS = "Payment Integrations"
    AI = "AI and Automation"
    DASHBOARDS = "Custom Dashboards"
    OTHER = "Other"

class ChatbotOptions(Enum):
    QUOTE = "Request a quote"
    SAMPLE = "View sample chatbot"
    BACK = "Back to services"

class QuoteOptions(Enum):
    CALLBACK = "Yes, call me"
    NO_CALLBACK = "No, just send the quote"
    BACK = "Back to main menu"

class SupportOptions(Enum):
    TECH = "Technical support"
    BILLING = "Payment or billing help"
    GENERAL = "General enquiry"
    BACK = "Back to main menu"

class ContactOptions(Enum):
    CALLBACK = "Request a call back"
    AGENT = "Speak to an agent"
    BACK = "Back to main menu"

class User:
    def __init__(self, name, phone):
        self.name = name
        self.phone = phone
        self.email = None
        self.service_type = None
        self.project_description = None
        self.callback_requested = False
        self.support_type = None

    def to_dict(self):
        return {
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "service_type": self.service_type.value if self.service_type else None,
            "project_description": self.project_description,
            "callback_requested": self.callback_requested,
            "support_type": self.support_type.value if self.support_type else None
        }

    @classmethod
    def from_dict(cls, data):
        user = cls(data["name"], data["phone"])
        user.email = data.get("email")
        if data.get("service_type"):
            user.service_type = ServiceOptions(data["service_type"])
        user.project_description = data.get("project_description")
        user.callback_requested = data.get("callback_requested", False)
        if data.get("support_type"):
            user.support_type = SupportOptions(data["support_type"])
        return user

# Phone number normalization function
def normalize_phone_number(phone):
    """Normalize phone number to handle different formats"""
    if not phone:
        return phone
    
    # Remove any non-digit characters except +
    cleaned = ''.join(c for c in phone if c.isdigit() or c == '+')
    
    # Handle Zimbabwe numbers
    if cleaned.startswith('+263'):
        return cleaned
    elif cleaned.startswith('263'):
        return '+' + cleaned
    elif cleaned.startswith('0'):
        return '+263' + cleaned[1:]
    else:
        return cleaned

# Conversation management functions
def generate_conversation_id(phone_number):
    """Generate a unique conversation ID"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    random_suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"conv_{timestamp}_{random_suffix}"

def get_active_conversation_id(phone_number):
    """Get the active conversation ID for a phone number"""
    normalized_phone = normalize_phone_number(phone_number)
    active_conv_key = f"active_conversation:{normalized_phone}"
    conv_id = redis_client.get(active_conv_key)
    return conv_id

def set_active_conversation_id(phone_number, conversation_id):
    """Set the active conversation ID for a phone number"""
    normalized_phone = normalize_phone_number(phone_number)
    active_conv_key = f"active_conversation:{normalized_phone}"
    redis_client.setex(active_conv_key, 86400, conversation_id)

def clear_active_conversation_id(phone_number):
    """Clear the active conversation ID for a phone number"""
    normalized_phone = normalize_phone_number(phone_number)
    active_conv_key = f"active_conversation:{normalized_phone}"
    redis_client.delete(active_conv_key)

def save_conversation_message(conversation_id, role, message, step=None):
    """Save a message to the conversation history"""
    conv_key = f"conversation:{conversation_id}"
    
    # Get existing conversation or create new
    conv_data_raw = redis_client.get(conv_key)
    if conv_data_raw:
        conversation = json.loads(conv_data_raw)
    else:
        conversation = {
            "id": conversation_id,
            "messages": [],
            "start_time": datetime.now().isoformat(),
            "step": step or "welcome"
        }
    
    # Add new message
    message_data = {
        "role": role,  # 'user' or 'bot'
        "message": message,
        "timestamp": datetime.now().isoformat(),
        "step": step or conversation.get("step", "welcome")
    }
    
    conversation["messages"].append(message_data)
    conversation["last_updated"] = datetime.now().isoformat()
    if step:
        conversation["step"] = step
    
    # Save to Redis with 7 day expiration
    redis_client.setex(conv_key, 604800, json.dumps(conversation))
    
    return conversation

def get_conversation(conversation_id):
    """Get the entire conversation by ID"""
    conv_key = f"conversation:{conversation_id}"
    conv_data_raw = redis_client.get(conv_key)
    if conv_data_raw:
        return json.loads(conv_data_raw)
    return None

def get_conversation_messages(conversation_id, limit=None):
    """Get messages from a conversation, optionally limited"""
    conversation = get_conversation(conversation_id)
    if conversation and "messages" in conversation:
        messages = conversation["messages"]
        if limit:
            return messages[-limit:]
        return messages
    return []

# Redis state functions (modified to use conversation context)
def get_user_state(phone_number):
    """Get user state within the context of their active conversation"""
    normalized_phone = normalize_phone_number(phone_number)
    
    # Get active conversation ID
    conversation_id = get_active_conversation_id(normalized_phone)
    
    if conversation_id:
        # Get conversation data
        conversation = get_conversation(conversation_id)
        if conversation:
            # Extract state from conversation
            state = {
                'step': conversation.get('step', 'welcome'),
                'sender': normalized_phone,
                'conversation_id': conversation_id
            }
            
            # Include any additional state data stored in conversation
            if 'user_data' in conversation:
                state.update(conversation['user_data'])
                
            print(f"✅ Retrieved state from conversation {conversation_id}: {state}")
            return state
    
    # No active conversation - create one
    conversation_id = generate_conversation_id(normalized_phone)
    set_active_conversation_id(normalized_phone, conversation_id)
    
    # Initialize conversation
    initial_state = {
        'step': 'welcome', 
        'sender': normalized_phone,
        'conversation_id': conversation_id
    }
    
    save_conversation_message(conversation_id, "system", "Conversation started", "welcome")
    
    print(f"🆕 Created new conversation {conversation_id} for {normalized_phone}")
    return initial_state

def update_user_state(phone_number, updates):
    """Update user state within the context of their active conversation"""
    normalized_phone = normalize_phone_number(phone_number)
    print(f"🔄 Updating state for {normalized_phone}")
    
    current = get_user_state(normalized_phone)
    current.update(updates)
    
    # Ensure required fields
    current['phone_number'] = normalized_phone
    if 'sender' not in current:
        current['sender'] = normalized_phone
    
    conversation_id = current.get('conversation_id')
    if not conversation_id:
        conversation_id = generate_conversation_id(normalized_phone)
        set_active_conversation_id(normalized_phone, conversation_id)
        current['conversation_id'] = conversation_id
    
    # Separate user data from conversation metadata
    user_data = {k: v for k, v in current.items() if k not in ['step', 'sender', 'phone_number', 'conversation_id']}
    step = current.get('step', 'welcome')
    
    # Update conversation with new state
    conv_key = f"conversation:{conversation_id}"
    conv_data_raw = redis_client.get(conv_key)
    
    if conv_data_raw:
        conversation = json.loads(conv_data_raw)
    else:
        conversation = {
            "id": conversation_id,
            "messages": [],
            "start_time": datetime.now().isoformat()
        }
    
    conversation["step"] = step
    conversation["user_data"] = user_data
    conversation["last_updated"] = datetime.now().isoformat()
    
    # Save updated conversation
    redis_client.setex(conv_key, 604800, json.dumps(conversation))
    
    print(f"💾 Updated conversation {conversation_id}, step: {step}")
    print(f"📦 User data: {user_data}")

def send_message(text, recipient, phone_id):
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    headers = {
        'Authorization': f'Bearer {wa_token}',
        'Content-Type': 'application/json'
    }
    
    # Save bot message to conversation
    user_state = get_user_state(recipient)
    conversation_id = user_state.get('conversation_id')
    if conversation_id:
        save_conversation_message(conversation_id, "bot", text, user_state.get('step'))
    
    if len(text) > 3000:
        parts = [text[i:i+3000] for i in range(0, len(text), 3000)]
        for part in parts:
            data = {
                "messaging_product": "whatsapp",
                "to": recipient,
                "type": "text",
                "text": {"body": part}
            }
            try:
                requests.post(url, headers=headers, json=data)
            except requests.exceptions.RequestException as e:
                logging.error(f"Failed to send message: {e}")
        return
    
    data = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": text}
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send message: {e}")

def send_button_message(text, buttons, recipient, phone_id):
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    headers = {
        'Authorization': f'Bearer {wa_token}',
        'Content-Type': 'application/json'
    }
    
    # Save bot message to conversation
    user_state = get_user_state(recipient)
    conversation_id = user_state.get('conversation_id')
    if conversation_id:
        save_conversation_message(conversation_id, "bot", text, user_state.get('step'))
    
    # Validate recipient phone number
    if not recipient or not recipient.strip():
        print(f"Invalid recipient: {recipient}")
        return False
    
    # Ensure recipient is in international format
    original_recipient = recipient
    if recipient.startswith('0'):
        recipient = '+263' + recipient[1:]
    elif not recipient.startswith('+'):
        recipient = '+' + recipient
    
    print(f"Original recipient: {original_recipient}")
    print(f"Normalized recipient: {recipient}")
    
    # Try different phone number formats if the first one fails
    phone_formats = [recipient]
    if recipient.startswith('+263'):
        phone_formats.append(recipient[1:])  # Remove +
        phone_formats.append('0' + recipient[4:])  # Local format
    elif recipient.startswith('263'):
        phone_formats.append('+' + recipient)
        phone_formats.append('0' + recipient[3:])  # Local format
    
    print(f"Phone formats to try: {phone_formats}")
    
    # Try the first format first
    recipient = phone_formats[0]
    
    # WhatsApp button message format
    button_items = []
    for i, button in enumerate(buttons[:3]):  # WhatsApp allows max 3 buttons
        button_id = button.get("id", f"button_{i+1}")
        button_title = button.get("title", "Button")
        
        # Ensure button title is within WhatsApp limits
        if len(button_title) > 20:
            button_title = button_title[:17] + "..."
        
        # Ensure button ID is valid
        if not button_id or len(button_id) > 256:
            button_id = f"button_{i+1}"
        
        button_items.append({
            "type": "reply",
            "reply": {
                "id": button_id,
                "title": button_title
            }
        })
        
        print(f"Button {i+1}: id='{button_id}', title='{button_title}'")
    
    if not button_items:
        print("No valid buttons found, falling back to text message")
        fallback_text = f"{text}\n\n" + "\n".join(f"- {btn.get('title', 'Option')}" for btn in buttons[:3])
        send_message(fallback_text, recipient, phone_id)
        return False
    
    # Ensure text is within WhatsApp limits and clean it
    if len(text) > 1024:
        text = text[:1021] + "..."
    
    # Clean text of any problematic characters
    text = text.replace('\x00', '').replace('\r', '\n').strip()
    
    # Ensure text is not empty
    if not text:
        text = "New message"
    
    print(f"Final text to send: '{text}' (length: {len(text)})")
    
    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": text
            },
            "action": {
                "buttons": button_items
            }
        }
    }
    
    print(f"Final data to send: {json.dumps(data, indent=2)}")
    
    try:
        print(f"Sending button message to {recipient}: {data}")
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        print(f"Button message sent successfully to {recipient}")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send button message: {e}")
        print(f"Button message failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response status: {e.response.status_code}")
            print(f"Response text: {e.response.text}")
            print(f"Response headers: {dict(e.response.headers)}")
            
            # Try to parse the error response
            try:
                error_data = e.response.json()
                print(f"Error details: {error_data}")
                if 'error' in error_data:
                    print(f"Error message: {error_data['error'].get('message', 'Unknown error')}")
                    print(f"Error code: {error_data['error'].get('code', 'Unknown code')}")
            except:
                print("Could not parse error response as JSON")
        
        # Fallback to simple text message
        fallback_text = f"{text}\n\n" + "\n".join(f"- {btn.get('title', 'Option')}" for btn in buttons[:3])
        send_message(fallback_text, recipient, phone_id)
        return False


def send_list_message(text, options, recipient, phone_id):
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    headers = {
        'Authorization': f'Bearer {wa_token}',
        'Content-Type': 'application/json'
    }
    
    # Save bot message to conversation
    user_state = get_user_state(recipient)
    conversation_id = user_state.get('conversation_id')
    if conversation_id:
        save_conversation_message(conversation_id, "bot", text, user_state.get('step'))
    
    # Validate and prepare the list items
    formatted_rows = []
    for i, option in enumerate(options[:10]):  # WhatsApp allows max 10 items
        formatted_rows.append({
            "id": f"option_{i+1}",
            "title": option[:24],  # Max 24 characters for title
            "description": option[24:72] if len(option) > 24 else ""  # Optional description
        })
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {
                "type": "text",
                "text": ""[:60]  # Max 60 chars for header
            },
            "body": {
                "text": text[:1024]  # Max 1024 chars for body
            },
            "footer": {
                "text": " "[:60]  # Max 60 chars for footer
            },
            "action": {
                "button": "Options"[:20],  # Max 20 chars for button text
                "sections": [
                    {
                        "title": "Available Options"[:24],  # Max 24 chars for section title
                        "rows": formatted_rows
                    }
                ]
            }
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        logging.info(f"List message sent successfully to {recipient}")
        return True
    except requests.exceptions.HTTPError as e:
        error_detail = f"Status: {e.response.status_code}, Response: {e.response.text}"
        logging.error(f"Failed to send list message: {error_detail}")
        # Fallback to simple message if list fails
        fallback_msg = f"{text}\n\n" + "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(options[:10]))
        send_message(fallback_msg, recipient, phone_id)
        return False
    except Exception as e:
        logging.error(f"Unexpected error sending list message: {str(e)}")
        return False


# Handlers
def handle_welcome(prompt, user_data, phone_id):
    # Save user message to conversation
    conversation_id = user_data.get('conversation_id')
    if conversation_id and prompt:
        save_conversation_message(conversation_id, "user", prompt, "welcome")
    
    welcome_msg = (
        "🌟 *Welcome to Contessasoft (Private) Limited!* 🌟\n\n"
        "We build intelligent software solutions including websites, mobile apps, chatbots, and business systems.\n\n"
        "Please choose an option to continue:"
    )
    
    menu_options = [option.value for option in MainMenuOptions]
    send_list_message(
        welcome_msg,
        menu_options,
        user_data['sender'],
        phone_id
    )
    
    update_user_state(user_data['sender'], {'step': 'main_menu'})
    return {'step': 'main_menu'}

def handle_restart_confirmation(prompt, user_data, phone_id):
    try:
        text = (prompt or "").strip().lower()

        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "restart_confirmation")

        # Initial entry or unrecognized input -> show Yes/No buttons
        if text == "" or text in ["restart", "start", "menu"]:
            send_button_message(
                "Would you like to go back to main menu?",
                [
                    {"id": "restart_yes", "title": "Yes"},
                    {"id": "restart_no", "title": "No"}
                ],
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'restart_confirmation'})
            return {'step': 'restart_confirmation'}

        # Positive confirmation -> go to welcome flow
        if text in ["yes", "y", "restart_yes", "ok", "sure", "yeah", "yep"]:
            return handle_welcome("", user_data, phone_id)

        # Negative confirmation -> send goodbye and reset to welcome state
        if text in ["no", "n", "restart_no", "nope", "nah"]:
            send_message("Have a good day!", user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {'step': 'welcome'})
            return {'step': 'welcome'}

        # Any other input -> re-send buttons
        send_button_message(
            "Please confirm: would you like to restart with the bot?",
            [
                {"id": "restart_yes", "title": "Yes"},
                {"id": "restart_no", "title": "No"}
            ],
            user_data['sender'],
            phone_id
        )
        return {'step': 'restart_confirmation'}

    except Exception as e:
        logging.error(f"Error in handle_restart_confirmation: {e}")
        send_message("An error occurred. Returning to main menu.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_main_menu(prompt, user_data, phone_id):
    try:
        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "main_menu")
            
        # Normalize input
        normalized = prompt.strip().lower()
        print(f"🧭 handle_main_menu() received prompt: '{prompt}' (normalized: '{normalized}')")

        # Map list reply IDs to menu options (IDs come from send_list_message)
        option_map = {
            "option_1": MainMenuOptions.ABOUT,
            "option_2": MainMenuOptions.SERVICES,
            "option_3": MainMenuOptions.QUOTE,
            "option_4": MainMenuOptions.SUPPORT,
            "option_5": MainMenuOptions.CONTACT
        }

        # Try to match by list ID first
        selected_option = option_map.get(normalized)

        # If not found, try to match by text (handles typed replies or button titles)
        if not selected_option:
            for option in MainMenuOptions:
                opt_text = option.value.lower()[:24]  # WhatsApp truncates to 24 chars
                if normalized in opt_text or opt_text in normalized:
                    selected_option = option
                    break

        # If still not matched, re-prompt user
        if not selected_option:
            print(f"⚠️ No valid match for '{prompt}', staying in main_menu")
            send_message("Please select a valid option from the list.", user_data['sender'], phone_id)
            return {'step': 'main_menu'}

        print(f"✅ Selected option: {selected_option.name}")

        # --- Handle the selected option ---
        if selected_option == MainMenuOptions.ABOUT:
            about_msg = (
                "Contessasoft is a Zimbabwe-based software company established in 2022.\n"
                "We develop custom systems for businesses in finance, education, logistics, retail, and other sectors.\n\n"
                "Would you like to:"
            )
            about_options = [option.value for option in AboutOptions]
            send_list_message(about_msg, about_options, user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {'step': 'about_menu'})
            return {'step': 'about_menu'}

        elif selected_option == MainMenuOptions.SERVICES:
            services_msg = (
                "🔧 *Our Services* 🔧\n\n"
                "We offer complete digital solutions:\n"
                "Select a service to learn more:"
            )
            service_options = [option.value for option in ServiceOptions]
            send_list_message(services_msg, service_options, user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {'step': 'services_menu'})
            return {'step': 'services_menu'}

        elif selected_option == MainMenuOptions.QUOTE:
            send_message("To help us prepare a quote, please provide your full name.", user_data['sender'], phone_id)
            user = User(name="", phone=user_data['sender'])
            update_user_state(user_data['sender'], {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'name'
            })
            return {'step': 'get_quote_info'}

        elif selected_option == MainMenuOptions.SUPPORT:
            support_msg = "Please select the type of support you need:"
            support_options = [option.value for option in SupportOptions]
            send_list_message(support_msg, support_options, user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {'step': 'support_menu'})
            return {'step': 'support_menu'}

        elif selected_option == MainMenuOptions.CONTACT:
            contact_msg = (
                "You can reach Contessasoft through the following:\n\n"
                "📍 Address: 115 ED Mnangagwa Road, Highlands, Harare, Zimbabwe\n"
                "📞 WhatsApp: +263 242 498954\n"
                "✉️ Email: sales@contessasoft.co.zw\n\n"
                "Would you like to:"
            )
            contact_options = [option.value for option in ContactOptions]
            send_list_message(contact_msg, contact_options, user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {'step': 'contact_menu'})
            return {'step': 'contact_menu'}

    except Exception as e:
        logging.error(f"Error in handle_main_menu: {e}\n{traceback.format_exc()}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}


def handle_about_menu(prompt, user_data, phone_id):
    try:
        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "about_menu")
            
        selected_option = None
        for option in AboutOptions:
            if prompt.lower() in option.value.lower():
                selected_option = option
                break
                
        if not selected_option:
            send_message("Invalid selection. Please choose an option from the list.", user_data['sender'], phone_id)
            return {'step': 'about_menu'}
            
        if selected_option == AboutOptions.PORTFOLIO:
            portfolio_msg = (
                "Our portfolio includes:\n"
                "- Banking systems\n"
                "- School management systems\n"
                "- E-commerce platforms\n"
                "- Logistics tracking systems\n"
                "- Custom business automation"
            )
            send_message(portfolio_msg, user_data['sender'], phone_id)
            return handle_welcome("", user_data, phone_id)
            
        elif selected_option == AboutOptions.PROFILE:
            send_message(
                "You can download our company profile from: https://contessasoft.co.zw/profile.pdf\n\n"
                "Would you like to request more information?",
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'request_more_info'})
            return {'step': 'request_more_info'}
            
        elif selected_option == AboutOptions.BACK:
            return handle_welcome("", user_data, phone_id)
            
    except Exception as e:
        logging.error(f"Error in handle_about_menu: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_services_menu(prompt, user_data, phone_id):
    try:
        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "services_menu")
            
        # Clean and normalize input
        clean_input = prompt.strip().lower()
        
        # Improved matching logic
        selected_option = None
        best_match_score = 0
        
        for option in ServiceOptions:
            option_text = option.value.lower()
            
            # Calculate match score (exact match gets highest priority)
            if clean_input == option_text:
                selected_option = option
                break
                
            # Check for partial matches
            match_score = 0
            if clean_input in option_text:
                match_score = len(clean_input) / len(option_text)
            elif any(word in option_text for word in clean_input.split()):
                match_score = 0.5  # Partial word match
                
            if match_score > best_match_score:
                best_match_score = match_score
                selected_option = option

        if not selected_option:
            error_msg = "🚫 Please select a valid service option:"
            service_options = [opt.value for opt in ServiceOptions]
            
            if not send_list_message(error_msg, service_options, user_data['sender'], phone_id):
                send_message(
                    "Please reply with:\n" + "\n".join(f"- {opt.value}" for opt in ServiceOptions),
                    user_data['sender'],
                    phone_id
                )
            return {'step': 'services_menu'}

        # Handle the selected service
        service_info = {
            ServiceOptions.DOMAIN: (
                "🌐 *Domain & Hosting Services*\n\n"
                "• Domain registration (.co.zw, .com, etc.)\n"
                "• Reliable web hosting with 99.9% uptime\n"
                "• Professional email hosting\n"
                "• SSL certificates for security\n"
                "• DNS management\n"
                "• Website migration assistance"
            ),
            ServiceOptions.WEBSITE: (
                "🖥️ *Website Development*\n\n"
                "• Custom business websites\n"
                "• E-commerce stores with payment integration\n"
                "• Content Management Systems (CMS)\n"
                "• Web application development\n"
                "• SEO optimization\n"
                "• Ongoing maintenance packages"
            ),
            ServiceOptions.MOBILE: (
                "📱 *Mobile App Development*\n\n"
                "• Native iOS and Android apps\n"
                "• Cross-platform hybrid apps\n"
                "• App UI/UX design\n"
                "• API integration\n"
                "• App Store and Play Store deployment\n"
                "• Post-launch support"
            ),
            ServiceOptions.CHATBOT: (
                "🤖 *WhatsApp Chatbots*\n\n"
                "• Automated customer service\n"
                "• Bill payment solutions (ZESA, DStv, etc.)\n"
                "• Order processing systems\n"
                "• KYC and registration flows\n"
                "• FAQ and support automation\n"
                "• Integration with business systems"
            ),
            ServiceOptions.PAYMENTS: (
                "💳 *Payment Integrations*\n\n"
                "• Ecocash/OneMoney/ZimSwitch\n"
                "• VISA/Mastercard gateways\n"
                "• PayPal and international payments\n"
                "• Custom payment solutions\n"
                "• PCI-DSS compliant setups\n"
                "• Reconciliation reporting"
            ),
            ServiceOptions.AI: (
                "🧠 *AI & Automation*\n\n"
                "• Intelligent chatbots\n"
                "• Document processing and OCR\n"
                "• Predictive analytics\n"
                "• Process automation\n"
                "• Machine learning models\n"
                "• Data extraction and analysis"
            ),
            ServiceOptions.DASHBOARDS: (
                "📊 *Business Dashboards*\n\n"
                "• Real-time business analytics\n"
                "• Custom reporting tools\n"
                "• Data visualization\n"
                "• KPI tracking\n"
                "• Executive dashboards\n"
                "• Automated report generation"
            ),
            ServiceOptions.OTHER: (
                "✨ *Custom Solutions*\n\n"
                "We develop tailored software for:\n"
                "• Inventory management\n"
                "• School administration\n"
                "• Healthcare systems\n"
                "• Logistics tracking\n"
                "• Financial services\n"
                "• And other business needs"
            )
        }.get(selected_option, "ℹ️ Service information coming soon")

        # Store the selected service for quote reference
        update_user_state(user_data['sender'], {
            'step': 'service_detail',
            'selected_service': selected_option.name,
            'service_description': selected_option.value
        })

        # Prepare the buttons
        buttons = [
            {"type": "reply", "reply": {"id": "quote_btn", "title": "💬 Request Quote"}},
            {"type": "reply", "reply": {"id": "back_btn", "title": "🔙 Back to Services"}}
        ]

        # Send interactive button message
        url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
        headers = {
            'Authorization': f'Bearer {wa_token}',
            'Content-Type': 'application/json'
        }
        
        data = {
            "messaging_product": "whatsapp",
            "to": user_data['sender'],
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": service_info},
                "action": {"buttons": buttons}
            }
        }

        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
            
            # Update state to handle button responses
            update_user_state(user_data['sender'], {
                'step': 'service_detail',
                'selected_service': selected_option.name,
                'service_description': selected_option.value,
                'awaiting_button_response': True
            })
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to send button message: {e}")
            # Fallback to simple message
            send_message(
                f"{service_info}\n\nReply with:\n1. 'Quote' for pricing\n2. 'Back' to return",
                user_data['sender'],
                phone_id
            )
            
        return {
            'step': 'service_detail',
            'selected_service': selected_option.name
        }
            
    except Exception as e:
        logging.error(f"Service menu error: {str(e)}\n{traceback.format_exc()}")
        send_message("⚠️ Please try selecting again or type 'menu'", user_data['sender'], phone_id)
        return {'step': 'services_menu'}


def handle_service_detail(prompt, user_data, phone_id):
    try:
        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "service_detail")
            
        clean_input = prompt.strip().lower()
        
        # Handle button responses
        if clean_input in ["quote_btn", "quote", "request quote", "pricing"]:
            send_message("To get a quote, please provide your full name.", user_data['sender'], phone_id)
            user = User(name="", phone=user_data['sender'])
            user.service_type = ServiceOptions[user_data.get('selected_service', 'OTHER')]
            update_user_state(user_data['sender'], {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'name'
            })
            return {'step': 'get_quote_info'}
            
        elif clean_input in ["back_btn", "back", "services"]:
            return handle_services_menu("", user_data, phone_id)
            
        else:
            # Re-send service info with buttons
            service_desc = user_data.get('service_description', 'selected service')
            send_message(
                f"Still interested in {service_desc}?\n\n"
                "Please use the buttons or reply with:\n"
                "• 'Quote' for pricing\n"
                "• 'Back' for other services",
                user_data['sender'],
                phone_id
            )
            return {'step': 'service_detail'}
            
    except Exception as e:
        logging.error(f"Service detail error: {str(e)}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'services_menu'}


def handle_get_quote_info(prompt, user_data, phone_id):
    try:
        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "get_quote_info")
            
        user_dict = user_data.get('user', {})
        current_field = user_data.get('field', 'name')
        
        if current_field == 'name':
            if not prompt.strip():
                send_message("Please provide your full name to continue.", user_data['sender'], phone_id)
                return {'step': 'get_quote_info'}
                
            user_dict['name'] = prompt.strip()
            send_message("Thank you! Please provide your email address.", user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {
                'user': user_dict,
                'field': 'email'
            })
            return {'step': 'get_quote_info'}
            
        elif current_field == 'email':
            if prompt.strip():
                user_dict['email'] = prompt.strip()
            else:
                user_dict['email'] = "Not provided"
                
            send_message("Please describe your project requirements in detail.", user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {
                'user': user_dict,
                'field': 'project_description'
            })
            return {'step': 'get_quote_info'}
            
        elif current_field == 'project_description':
            if not prompt.strip():
                send_message("Please provide project details so we can prepare an accurate quote.", user_data['sender'], phone_id)
                return {'step': 'get_quote_info'}
                
            user_dict['project_description'] = prompt.strip()
            
            # Complete the user info collection
            user = User.from_dict(user_dict)
            
            # Ask about callback preference
            send_button_message(
                "Would you like us to call you to discuss your project?",
                [
                    {"id": "callback_yes", "title": "Yes, call me"},
                    {"id": "callback_no", "title": "No, just send quote"}
                ],
                user_data['sender'],
                phone_id
            )
            
            update_user_state(user_data['sender'], {
                'step': 'callback_preference',
                'user': user.to_dict()
            })
            return {'step': 'callback_preference'}
            
    except Exception as e:
        logging.error(f"Quote info error: {str(e)}")
        send_message("An error occurred. Let's start over.", user_data['sender'], phone_id)
        return handle_welcome("", user_data, phone_id)


def handle_callback_preference(prompt, user_data, phone_id):
    try:
        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "callback_preference")
            
        user_dict = user_data.get('user', {})
        user = User.from_dict(user_dict)
        
        if prompt.lower() in ['callback_yes', 'yes', 'call me']:
            user.callback_requested = True
            send_message(
                "Great! Our team will call you within 24 hours.\n\n"
                f"📋 *Quote Summary:*\n"
                f"• Name: {user.name}\n"
                f"• Email: {user.email}\n"
                f"• Service: {user.service_type.value if user.service_type else 'Not specified'}\n"
                f"• Callback: ✅ Requested\n\n"
                "We'll prepare your quote and contact you soon!",
                user_data['sender'],
                phone_id
            )
        else:
            user.callback_requested = False
            send_message(
                "Got it! We'll email your quote within 48 hours.\n\n"
                f"📋 *Quote Summary:*\n"
                f"• Name: {user.name}\n"
                f"• Email: {user.email}\n"
                f"• Service: {user.service_type.value if user.service_type else 'Not specified'}\n"
                f"• Callback: ❌ Not requested\n\n"
                "Thank you for your interest!",
                user_data['sender'],
                phone_id
            )
        
        # Notify owner about new quote request
        notify_owner_about_quote(user)
        
        # Return to main menu
        return handle_welcome("", user_data, phone_id)
        
    except Exception as e:
        logging.error(f"Callback preference error: {str(e)}")
        send_message("An error occurred. Returning to main menu.", user_data['sender'], phone_id)
        return handle_welcome("", user_data, phone_id)


def handle_support_menu(prompt, user_data, phone_id):
    try:
        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "support_menu")
            
        selected_option = None
        for option in SupportOptions:
            if prompt.lower() in option.value.lower():
                selected_option = option
                break
                
        if not selected_option:
            send_message("Invalid selection. Please choose from the available options.", user_data['sender'], phone_id)
            return {'step': 'support_menu'}
            
        if selected_option == SupportOptions.BACK:
            return handle_welcome("", user_data, phone_id)
            
        # Store support type and proceed
        user = User(name="", phone=user_data['sender'])
        user.support_type = selected_option
        
        send_message("Please describe your issue in detail and we'll get back to you shortly.", user_data['sender'], phone_id)
        
        update_user_state(user_data['sender'], {
            'step': 'support_details',
            'user': user.to_dict()
        })
        return {'step': 'support_details'}
        
    except Exception as e:
        logging.error(f"Support menu error: {str(e)}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}


def handle_support_details(prompt, user_data, phone_id):
    try:
        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "support_details")
            
        user_dict = user_data.get('user', {})
        user = User.from_dict(user_dict)
        
        if not prompt.strip():
            send_message("Please describe your issue so we can help you.", user_data['sender'], phone_id)
            return {'step': 'support_details'}
            
        # Here you would typically save the support request to your database
        support_summary = (
            f"🆘 *New Support Request*\n\n"
            f"• Type: {user.support_type.value if user.support_type else 'Not specified'}\n"
            f"• Phone: {user.phone}\n"
            f"• Issue: {prompt.strip()}\n\n"
            f"Please follow up within 24 hours."
        )
        
        # Notify support team (you can modify this to use your preferred notification method)
        notify_support_team(support_summary)
        
        send_message(
            "Thank you! Our support team has received your request and will contact you within 24 hours.\n\n"
            "For urgent matters, you can call us at +263 242 498954.",
            user_data['sender'],
            phone_id
        )
        
        return handle_welcome("", user_data, phone_id)
        
    except Exception as e:
        logging.error(f"Support details error: {str(e)}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}


def handle_contact_menu(prompt, user_data, phone_id):
    try:
        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "contact_menu")
            
        selected_option = None
        for option in ContactOptions:
            if prompt.lower() in option.value.lower():
                selected_option = option
                break
                
        if not selected_option:
            send_message("Invalid selection. Please choose from the available options.", user_data['sender'], phone_id)
            return {'step': 'contact_menu'}
            
        if selected_option == ContactOptions.BACK:
            return handle_welcome("", user_data, phone_id)
            
        elif selected_option == ContactOptions.CALLBACK:
            send_message(
                "Please provide your name and we'll call you back within 24 hours.\n\n"
                "Or call us directly at +263 242 498954.",
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'callback_request'})
            return {'step': 'callback_request'}
            
        elif selected_option == ContactOptions.AGENT:
            send_message(
                "Connecting you with our team...\n\n"
                "📞 *Direct Contact:*\n"
                "• WhatsApp: +263 242 498954\n"
                "• Email: sales@contessasoft.co.zw\n"
                "• Address: 115 ED Mnangagwa Road, Highlands, Harare\n\n"
                "Our business hours are Mon-Fri, 8AM-5PM.",
                user_data['sender'],
                phone_id
            )
            return handle_welcome("", user_data, phone_id)
            
    except Exception as e:
        logging.error(f"Contact menu error: {str(e)}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}


def handle_callback_request(prompt, user_data, phone_id):
    try:
        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "callback_request")
            
        if not prompt.strip():
            send_message("Please provide your name for the callback request.", user_data['sender'], phone_id)
            return {'step': 'callback_request'}
            
        # Notify about callback request
        callback_msg = (
            f"📞 *New Callback Request*\n\n"
            f"• Name: {prompt.strip()}\n"
            f"• Phone: {user_data['sender']}\n"
            f"• Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"Please call back within 24 hours."
        )
        notify_owner(callback_msg)
        
        send_message(
            f"Thank you {prompt.strip()}! We'll call you back within 24 hours.\n\n"
            "For immediate assistance, call +263 242 498954.",
            user_data['sender'],
            phone_id
        )
        
        return handle_welcome("", user_data, phone_id)
        
    except Exception as e:
        logging.error(f"Callback request error: {str(e)}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}


def handle_request_more_info(prompt, user_data, phone_id):
    try:
        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "request_more_info")
            
        if prompt.lower() in ['yes', 'y', 'more']:
            send_message(
                "Great! Please let us know what specific information you need:\n"
                "- Product details\n"
                "- Pricing packages\n"
                "- Technical specifications\n"
                "- Case studies\n"
                "- Or any other questions",
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'collect_info_request'})
            return {'step': 'collect_info_request'}
        else:
            send_message("Returning to main menu.", user_data['sender'], phone_id)
            return handle_welcome("", user_data, phone_id)
            
    except Exception as e:
        logging.error(f"More info error: {str(e)}")
        send_message("An error occurred. Returning to main menu.", user_data['sender'], phone_id)
        return handle_welcome("", user_data, phone_id)


def handle_collect_info_request(prompt, user_data, phone_id):
    try:
        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "collect_info_request")
            
        if not prompt.strip():
            send_message("Please describe what information you need.", user_data['sender'], phone_id)
            return {'step': 'collect_info_request'}
            
        # Notify about information request
        info_request = (
            f"📋 *New Information Request*\n\n"
            f"• From: {user_data['sender']}\n"
            f"• Request: {prompt.strip()}\n\n"
            f"Please follow up with relevant information."
        )
        notify_owner(info_request)
        
        send_message(
            "Thank you! We'll send you the requested information within 24 hours.\n\n"
            "For urgent requests, call +263 242 498954.",
            user_data['sender'],
            phone_id
        )
        
        return handle_welcome("", user_data, phone_id)
        
    except Exception as e:
        logging.error(f"Collect info error: {str(e)}")
        send_message("An error occurred. Returning to main menu.", user_data['sender'], phone_id)
        return handle_welcome("", user_data, phone_id)


def handle_unknown_input(prompt, user_data, phone_id):
    try:
        # Save user message to conversation
        conversation_id = user_data.get('conversation_id')
        if conversation_id and prompt:
            save_conversation_message(conversation_id, "user", prompt, "unknown_input")
            
        send_message(
            "I'm not sure what you're looking for. Let me help you get back on track.\n\n"
            "Please choose an option:",
            user_data['sender'],
            phone_id
        )
        
        # Show main menu options
        menu_options = [option.value for option in MainMenuOptions]
        send_list_message(
            "How can I help you today?",
            menu_options,
            user_data['sender'],
            phone_id
        )
        
        update_user_state(user_data['sender'], {'step': 'main_menu'})
        return {'step': 'main_menu'}
        
    except Exception as e:
        logging.error(f"Unknown input handler error: {str(e)}")
        send_message("Let's start over. How can I help you?", user_data['sender'], phone_id)
        return handle_welcome("", user_data, phone_id)


def notify_owner_about_quote(user):
    """Notify the business owner about a new quote request"""
    try:
        if not owner_phone:
            logging.info("No owner phone configured for notifications")
            return
            
        message = (
            f"📊 *New Quote Request*\n\n"
            f"• Name: {user.name}\n"
            f"• Phone: {user.phone}\n"
            f"• Email: {user.email}\n"
            f"• Service: {user.service_type.value if user.service_type else 'Not specified'}\n"
            f"• Callback: {'✅ Yes' if user.callback_requested else '❌ No'}\n"
            f"• Project: {user.project_description[:200]}{'...' if len(user.project_description) > 200 else ''}\n\n"
            f"Please follow up within 24 hours."
        )
        
        send_message(message, owner_phone, phone_id)
        logging.info(f"Quote notification sent to owner for {user.phone}")
        
    except Exception as e:
        logging.error(f"Failed to notify owner about quote: {e}")


def notify_support_team(message):
    """Notify support team about a new request"""
    try:
        if not owner_phone:
            logging.info("No support phone configured for notifications")
            return
            
        send_message(message, owner_phone, phone_id)
        logging.info("Support notification sent")
        
    except Exception as e:
        logging.error(f"Failed to notify support team: {e}")


def notify_owner(message):
    """Generic notification to owner"""
    try:
        if not owner_phone:
            logging.info("No owner phone configured for notifications")
            return
            
        send_message(message, owner_phone, phone_id)
        logging.info("Owner notification sent")
        
    except Exception as e:
        logging.error(f"Failed to notify owner: {e}")


# Conversation flow router
def route_conversation(prompt, user_data, phone_id):
    step = user_data.get('step', 'welcome')
    print(f"🔄 Routing conversation for {user_data['sender']}, step: {step}")
    
    # Map steps to handler functions
    handlers = {
        'welcome': handle_welcome,
        'restart_confirmation': handle_restart_confirmation,
        'main_menu': handle_main_menu,
        'about_menu': handle_about_menu,
        'services_menu': handle_services_menu,
        'service_detail': handle_service_detail,
        'get_quote_info': handle_get_quote_info,
        'callback_preference': handle_callback_preference,
        'support_menu': handle_support_menu,
        'support_details': handle_support_details,
        'contact_menu': handle_contact_menu,
        'callback_request': handle_callback_request,
        'request_more_info': handle_request_more_info,
        'collect_info_request': handle_collect_info_request
    }
    
    handler = handlers.get(step, handle_unknown_input)
    return handler(prompt, user_data, phone_id)


@app.route("/")
def home():
    return "WhatsApp Business Bot is running!"


@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        
        if mode == "subscribe" and token == wa_token:
            print("✅ Webhook verified successfully!")
            return challenge
        else:
            print("❌ Webhook verification failed!")
            return "Verification failed", 403

    elif request.method == "POST":
        try:
            data = request.get_json()
            print(f"📨 Incoming webhook data: {json.dumps(data, indent=2)}")
            
            if not data:
                print("❌ Empty webhook data received")
                return "OK", 200

            # Handle different webhook types
            if 'entry' not in data:
                print("ℹ️ No entries in webhook data")
                return "OK", 200

            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    if change.get('field') == 'messages':
                        value = change.get('value', {})
                        
                        # Check if it's a message (not status update)
                        if 'messages' not in value:
                            print("ℹ️ No messages in this webhook")
                            continue
                            
                        for message in value.get('messages', []):
                            # Only process text messages for now
                            if message.get('type') != 'text':
                                print(f"ℹ️ Ignoring non-text message type: {message.get('type')}")
                                continue
                                
                            # Extract message details
                            sender_phone = message.get('from')
                            message_text = message.get('text', {}).get('body', '').strip()
                            
                            if not sender_phone:
                                print("❌ No sender phone in message")
                                continue
                                
                            print(f"📱 Message from {sender_phone}: {message_text}")
                            
                            # Get user state
                            user_state = get_user_state(sender_phone)
                            print(f"🧠 User state: {user_state}")
                            
                            # Route the conversation
                            new_state = route_conversation(message_text, user_state, phone_id)
                            print(f"🔄 New state after routing: {new_state}")
                            
            return "OK", 200

        except Exception as e:
            logging.error(f"Webhook processing error: {e}\n{traceback.format_exc()}")
            return "OK", 200


@app.route("/conversations/<conversation_id>", methods=["GET"])
def get_conversation_endpoint(conversation_id):
    """API endpoint to retrieve a specific conversation"""
    try:
        conversation = get_conversation(conversation_id)
        if conversation:
            return jsonify({
                "success": True,
                "conversation": conversation
            })
        else:
            return jsonify({
                "success": False,
                "error": "Conversation not found"
            }), 404
    except Exception as e:
        logging.error(f"Error retrieving conversation {conversation_id}: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/conversations/user/<phone_number>", methods=["GET"])
def get_user_conversations(phone_number):
    """API endpoint to retrieve all conversations for a user"""
    try:
        normalized_phone = normalize_phone_number(phone_number)
        
        # Get active conversation ID
        active_conv_id = get_active_conversation_id(normalized_phone)
        
        # In a real implementation, you might want to store a list of all conversation IDs per user
        # For now, we'll return the active conversation if it exists
        conversations = []
        if active_conv_id:
            conversation = get_conversation(active_conv_id)
            if conversation:
                conversations.append(conversation)
        
        return jsonify({
            "success": True,
            "phone_number": normalized_phone,
            "conversations": conversations
        })
    except Exception as e:
        logging.error(f"Error retrieving conversations for {phone_number}: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
