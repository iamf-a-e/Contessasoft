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
AGENT_NUMBERS = ["+263772210415"]

# Redis client setup
redis_client = Redis(
    url=os.environ.get('UPSTASH_REDIS_URL'),
    token=os.environ.get('UPSTASH_REDIS_TOKEN')
)

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

# User state functions (for bot flow state)
def get_user_state(phone_number):
    normalized_phone = normalize_phone_number(phone_number)
    state_json = redis_client.get(f"user_state:{normalized_phone}")
    if state_json:
        state = json.loads(state_json)
        print(f"✅ Retrieved user state for {normalized_phone}: {state}")
        return state
    default_state = {'step': 'welcome', 'sender': normalized_phone}
    print(f"❌ No user state found for {normalized_phone}, returning default: {default_state}")
    return default_state

def update_user_state(phone_number, updates):
    normalized_phone = normalize_phone_number(phone_number)
    print(f"🔄 Updating user state for {normalized_phone}")
    
    current = get_user_state(normalized_phone)
    current.update(updates)
    current['phone_number'] = normalized_phone
    if 'sender' not in current:
        current['sender'] = normalized_phone
        
    key = f"user_state:{normalized_phone}"
    print(f"💾 Saving user state to Redis key: {key}")
    print(f"📦 User state data: {current}")
    
    try:
        result = redis_client.setex(key, 86400, json.dumps(current))
        print(f"✅ User state save result: {result}")
        
        # Immediate verification
        verify = redis_client.get(key)
        if verify:
            verified_data = json.loads(verify)
            print(f"✅ Verified user state save successful: {verified_data.get('step', 'unknown')}")
        else:
            print(f"❌ User state verification failed - key not found")
            
    except Exception as e:
        print(f"❌ Redis error saving user state: {e}")

# Conversation history functions (for message history)
def save_conversation_message(phone_number, message, is_user=True):
    """Save a message to conversation history (max 100 messages)"""
    normalized_phone = normalize_phone_number(phone_number)
    conversation_key = f"conversation:{normalized_phone}"
    
    try:
        # Get existing conversation
        conversation_json = redis_client.get(conversation_key)
        if conversation_json:
            conversation = json.loads(conversation_json)
        else:
            conversation = []
        
        # Create message object
        message_obj = {
            'timestamp': datetime.now().isoformat(),
            'is_user': is_user,
            'message': message,
            'step': get_user_state(normalized_phone).get('step', 'unknown')
        }
        
        # Add to conversation
        conversation.append(message_obj)
        
        # Keep only last 100 messages
        if len(conversation) > 100:
            conversation = conversation[-100:]
        
        # Save back to Redis
        redis_client.setex(conversation_key, 86400, json.dumps(conversation))
        print(f"💾 Saved conversation message for {normalized_phone}, total messages: {len(conversation)}")
        
    except Exception as e:
        print(f"❌ Error saving conversation message: {e}")

def get_conversation_history(phone_number, limit=100):
    """Get conversation history for a user"""
    normalized_phone = normalize_phone_number(phone_number)
    conversation_key = f"conversation:{normalized_phone}"
    
    try:
        conversation_json = redis_client.get(conversation_key)
        if conversation_json:
            conversation = json.loads(conversation_json)
            return conversation[-limit:] if limit else conversation
        return []
    except Exception as e:
        print(f"❌ Error getting conversation history: {e}")
        return []

def get_full_conversation_history(phone_number):
    """Get full conversation history (all 100 messages)"""
    return get_conversation_history(phone_number, limit=100)

# Quote request functions
def generate_quote_reference():
    """Generate a unique quote reference (e.g., 3CPHLV59)"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def save_quote_request(quote_reference, quote_data):
    """Save quote request to Redis with quote reference as key"""
    quote_key = f"quote:{quote_reference}"
    
    try:
        # Add timestamp and reference to quote data
        quote_data['timestamp'] = datetime.now().isoformat()
        quote_data['quote_reference'] = quote_reference
        
        # Save to Redis with longer expiration (30 days for quotes)
        result = redis_client.setex(quote_key, 2592000, json.dumps(quote_data))
        print(f"💾 Saved quote request to Redis key: {quote_key}")
        print(f"📦 Quote data: {quote_data}")
        return result
    except Exception as e:
        print(f"❌ Error saving quote request: {e}")
        return False

def get_quote_request(quote_reference):
    """Get quote request by reference"""
    quote_key = f"quote:{quote_reference}"
    
    try:
        quote_json = redis_client.get(quote_key)
        if quote_json:
            quote_data = json.loads(quote_json)
            print(f"✅ Retrieved quote request: {quote_reference}")
            return quote_data
        print(f"❌ Quote request not found: {quote_reference}")
        return None
    except Exception as e:
        print(f"❌ Error getting quote request: {e}")
        return None

def get_all_quote_requests():
    """Get all quote requests (admin function)"""
    try:
        # Note: This might be inefficient for large datasets
        # In production, you might want to use Redis search or a separate database
        keys = redis_client.keys("quote:*")
        quotes = []
        for key in keys:
            quote_json = redis_client.get(key)
            if quote_json:
                quote_data = json.loads(quote_json)
                quotes.append(quote_data)
        
        # Sort by timestamp (newest first)
        quotes.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return quotes
    except Exception as e:
        print(f"❌ Error getting all quote requests: {e}")
        return []

def send_message(text, recipient, phone_id):
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    headers = {
        'Authorization': f'Bearer {wa_token}',
        'Content-Type': 'application/json'
    }
    
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
        print(f"✅ Message sent to {recipient}")
        
        # Save bot response to conversation history
        save_conversation_message(recipient, text, is_user=False)
        
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send message: {e}")

def send_button_message(text, buttons, recipient, phone_id):
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    headers = {
        'Authorization': f'Bearer {wa_token}',
        'Content-Type': 'application/json'
    }
    
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
        print(f"Sending button message to {recipient}")
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        print(f"✅ Button message sent successfully to {recipient}")
        
        # Save bot response to conversation history
        save_conversation_message(recipient, text, is_user=False)
        
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send button message: {e}")
        print(f"Button message failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response status: {e.response.status_code}")
            print(f"Response text: {e.response.text}")
        
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
                "text": "Menu Options"[:60]  # Max 60 chars for header
            },
            "body": {
                "text": text[:1024]  # Max 1024 chars for body
            },
            "footer": {
                "text": "Choose an option"[:60]  # Max 60 chars for footer
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
        logging.info(f"✅ List message sent successfully to {recipient}")
        
        # Save bot response to conversation history
        save_conversation_message(recipient, text, is_user=False)
        
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

# New function to ask if user needs anything else
def handle_anything_else(prompt, user_data, phone_id):
    """Ask if user needs anything else after completing a flow"""
    try:
        text = (prompt or "").strip().lower()

        # Initial entry - ask if anything else is needed
        if text == "":
            send_button_message(
                "Is there anything else I can help you with?",
                [
                    {"id": "yes_more", "title": "Yes"},
                    {"id": "no_done", "title": "No"}
                ],
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'anything_else'})
            return {'step': 'anything_else'}

        # Positive response - show main menu with different message
        if text in ["yes", "y", "yes_more", "ok", "sure", "yeah", "yep"]:
            menu_msg = "Please select an option:"
            menu_options = [option.value for option in MainMenuOptions]
            send_list_message(menu_msg, menu_options, user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {'step': 'main_menu'})
            return {'step': 'main_menu'}

        # Negative response - end conversation
        if text in ["no", "n", "no_done", "nope", "nah"]:
            send_message("Have a good day! 😊", user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {'step': 'welcome'})
            return {'step': 'welcome'}

        # Any other input - re-send buttons
        send_button_message(
            "Please confirm: is there anything else I can help you with?",
            [
                {"id": "yes_more", "title": "Yes"},
                {"id": "no_done", "title": "No"}
            ],
            user_data['sender'],
            phone_id
        )
        return {'step': 'anything_else'}

    except Exception as e:
        logging.error(f"Error in handle_anything_else: {e}")
        send_message("An error occurred. Returning to main menu.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

# Updated handle_get_quote_info to include "anything else" after completion
def handle_get_quote_info(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        current_field = user_data.get('field')
        
        if current_field == 'name':
            user.name = prompt
            update_user_state(user_data['sender'], {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'email'
            })
            send_message("Thank you. Please provide your email address:", user_data['sender'], phone_id)
            return {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'email'
            }
            
        elif current_field == 'email':
            user.email = prompt
            update_user_state(user_data['sender'], {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'description'
            })
            send_message("Please provide a short description of your project:", user_data['sender'], phone_id)
            return {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'description'
            }
            
        elif current_field == 'description':
            user.project_description = prompt
            
            # Generate quote reference
            quote_reference = generate_quote_reference()
            
            # Prepare quote data
            quote_data = {
                'user': user.to_dict(),
                'service_type': user_data.get('service_description', 'General'),
                'selected_service': user_data.get('selected_service'),
                'quote_reference': quote_reference,
                'status': 'submitted'
            }
            
            # Save quote request to separate Redis key
            save_quote_request(quote_reference, quote_data)
            
            # Send quote request to admin
            quote_msg = (
                f"📋 *New Quote Request* - {quote_reference}\n\n"
                f"👤 Name: {user.name}\n"
                f"📞 Phone: {user.phone}\n"
                f"📧 Email: {user.email}\n"
                f"🛠️ Service: {user_data.get('service_description', 'General')}\n"
                f"📝 Description: {user.project_description}\n"
                f"⏰ Submitted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            if owner_phone:
                send_message(quote_msg, owner_phone, phone_id)
            
            # Send confirmation to user
            send_message(
                f"Thank you! Your quote request has been submitted.\n\n"
                f"📋 *Quote Reference:* {quote_reference}\n"
                f"⏰ We'll contact you within 24 hours.\n"
                f"📞 For urgent inquiries, call: +263 242 498954",
                user_data['sender'],
                phone_id
            )
            
            # After quote completion, ask if anything else is needed
            return handle_anything_else("", user_data, phone_id)
            
    except Exception as e:
        logging.error(f"Error in handle_get_quote_info: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

# Updated other handlers to include "anything else" after completion
def handle_get_support_details(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        user.project_description = prompt
        
        # Send support request to admin
        support_msg = (
            f"🆘 *New Support Request*\n\n"
            f"👤 From: {user.name or 'Customer'} - {user.phone}\n"
            f"🔧 Type: {user.support_type.value if user.support_type else 'General'}\n"
            f"📝 Details: {prompt}"
        )
        
        if owner_phone:
            send_message(support_msg, owner_phone, phone_id)
        
        send_message(
            "Thank you! Your support request has been logged. Our team will respond shortly.\n"
            "Reference: #" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6)),
            user_data['sender'],
            phone_id
        )
        
        # After support completion, ask if anything else is needed
        return handle_anything_else("", user_data, phone_id)
        
    except Exception as e:
        logging.error(f"Error in handle_get_support_details: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_callback_details(prompt, user_data, phone_id):
    try:
        # Send callback request to admin
        callback_msg = (
            f"📞 *Callback Request*\n\n"
            f"📞 From: {user_data['sender']}\n"
            f"📝 Details: {prompt}"
        )
        
        if owner_phone:
            send_message(callback_msg, owner_phone, phone_id)
        
        send_message(
            "Thank you! We'll call you at the requested time.\n"
            "Reference: #" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6)),
            user_data['sender'],
            phone_id
        )
        
        # After callback completion, ask if anything else is needed
        return handle_anything_else("", user_data, phone_id)
        
    except Exception as e:
        logging.error(f"Error in handle_get_callback_details: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

# Updated about menu portfolio to include "anything else"
def handle_about_menu(prompt, user_data, phone_id):
    try:
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
            # After showing portfolio, ask if anything else is needed
            return handle_anything_else("", user_data, phone_id)
            
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

# Agent message handler
def handle_agent_message(prompt, sender, phone_id):
    """Handle messages from agents when no chat is transferred"""
    try:
        print(f"🔧 Agent message from {sender}: '{prompt}'")
        
        # Check if this agent has any active conversations
        active_conversations = []
        try:
            # Look for any active conversations where this agent is assigned
            conversation_keys = redis_client.keys("agent_conversation:*")
            for key in conversation_keys:
                conv_data_raw = redis_client.get(key)
                if conv_data_raw:
                    conv_data = json.loads(conv_data_raw)
                    if conv_data.get('agent') == sender and conv_data.get('active'):
                        active_conversations.append(conv_data)
        except Exception as e:
            print(f"❌ Error checking agent conversations: {e}")
        
        if not active_conversations:
            # No active conversations - inform agent to wait
            send_message(
                "⏳ Please wait for a customer to request to speak to an agent.\n\n"
                "You will receive a notification when a customer requests agent assistance.",
                sender,
                phone_id
            )
            print(f"ℹ️ Agent {sender} has no active conversations")
        else:
            # Agent has active conversations - remind them of the conversation IDs
            conversation_info = "\n".join([f"- {conv.get('conversation_id')} (Customer: {conv.get('customer')})" 
                                         for conv in active_conversations[:3]])  # Show max 3
            send_message(
                f"🤝 You have active conversations:\n{conversation_info}\n\n"
                f"Reply with the conversation ID to continue chatting, or type 'exit' to end a conversation.",
                sender,
                phone_id
            )
            
    except Exception as e:
        logging.error(f"Error in handle_agent_message: {e}")
        send_message("An error occurred processing your message.", sender, phone_id)

# All other handler functions remain the same...
def handle_welcome(prompt, user_data, phone_id):
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
            return {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'name'
            }

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

def handle_services_menu(prompt, user_data, phone_id):
    try:
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
            {"id": "quote_btn", "title": "💬 Request Quote"},
            {"id": "back_btn", "title": "🔙 Back to Services"}
        ]

        # Send interactive button message
        send_button_message(
            service_info,
            buttons,
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
        # Clean the input and check for button responses
        clean_input = prompt.strip().lower()
        
        # Handle "Request Quote" button or text
        if "quote" in clean_input or "request quote" in clean_input or "💬" in prompt or prompt == "quote_btn":
            # Initialize user object for quote collection
            user = User(name="", phone=user_data['sender'])
            update_user_state(user_data['sender'], {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'name',  # First field to collect
                'selected_service': user_data.get('selected_service'),
                'service_description': user_data.get('service_description')
            })
            send_message("To help us prepare a quote, please provide your full name:", user_data['sender'], phone_id)
            return {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'name'
            }
            
        # Handle "Back to Services" button or text
        elif "back" in clean_input or "services" in clean_input or "🔙" in prompt or prompt == "back_btn":
            services_msg = (
                "🔧 *Our Services* 🔧\n\n"
                "We offer complete digital solutions:\n"
                "Select a service to learn more:"
            )
            service_options = [option.value for option in ServiceOptions]
            send_list_message(
                services_msg,
                service_options,
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'services_menu'})
            return {'step': 'services_menu'}
            
        # If the input doesn't match any expected option
        else:
            # Resend the service info with buttons
            service_info = (
                f"ℹ️ *{user_data.get('service_description', 'Selected Service')}*\n\n"
                "Please choose an option:"
            )
            send_button_message(
                service_info,
                [
                    {"id": "quote_btn", "title": "💬 Request Quote"},
                    {"id": "back_btn", "title": "🔙 Back to Services"}
                ],
                user_data['sender'],
                phone_id
            )
            return {'step': 'service_detail'}
            
    except Exception as e:
        logging.error(f"Error in handle_service_detail: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'services_menu'}

def handle_support_menu(prompt, user_data, phone_id):
    try:
        selected_option = None
        for option in SupportOptions:
            if prompt.lower() in option.value.lower():
                selected_option = option
                break
                
        if not selected_option:
            send_message("Invalid selection. Please choose an option from the list.", user_data['sender'], phone_id)
            return {'step': 'support_menu'}
            
        if selected_option == SupportOptions.BACK:
            return handle_welcome("", user_data, phone_id)
            
        user = User(name="", phone=user_data['sender'])
        user.support_type = selected_option
        
        update_user_state(user_data['sender'], {
            'step': 'get_support_details',
            'user': user.to_dict()
        })
        
        send_message(
            "Please describe your issue in detail:",
            user_data['sender'],
            phone_id
        )
        
        return {
            'step': 'get_support_details',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_support_menu: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_contact_menu(prompt, user_data, phone_id):
    try:
        selected_option = None
        for option in ContactOptions:
            if prompt.lower() in option.value.lower():
                selected_option = option
                break
                
        if not selected_option:
            send_message("Invalid selection. Please choose an option from the list.", user_data['sender'], phone_id)
            return {'step': 'contact_menu'}
            
        if selected_option == ContactOptions.CALLBACK:
            send_message(
                "Please provide your name and the best time to call you:",
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'get_callback_details'})
            return {'step': 'get_callback_details'}
            
        elif selected_option == ContactOptions.AGENT:
            send_message(
                "Please wait while we connect you with an agent...",
                user_data['sender'],
                phone_id
            )
            # Notify agents
            agent_msg = f"🔔 New agent request from: {user_data['sender']}"
            for agent in AGENT_NUMBERS:
                send_message(agent_msg, agent, phone_id)
            
            return handle_welcome("", user_data, phone_id)
            
        elif selected_option == ContactOptions.BACK:
            return handle_welcome("", user_data, phone_id)
            
    except Exception as e:
        logging.error(f"Error in handle_contact_menu: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

# Action mapping
action_mapping = {
    "welcome": handle_welcome,
    "restart_confirmation": handle_restart_confirmation,
    "main_menu": handle_main_menu,
    "about_menu": handle_about_menu,
    "services_menu": handle_services_menu,
    "service_detail": handle_service_detail,
    "get_quote_info": handle_get_quote_info,
    "support_menu": handle_support_menu,
    "get_support_details": handle_get_support_details,
    "contact_menu": handle_contact_menu,
    "get_callback_details": handle_get_callback_details,
    "anything_else": handle_anything_else
}

def get_action(current_state, prompt, user_data, phone_id):
    handler = action_mapping.get(current_state, handle_welcome)
    print(f"🔄 Routing to handler: {handler.__name__} for state: {current_state}")

    try:
        return handler(prompt, user_data, phone_id)
    except Exception as e:
        logging.error(f"Error in handler {handler.__name__}: {e}", exc_info=True)
        return handle_welcome("", user_data, phone_id)

# Message handler
def message_handler(prompt, sender, phone_id):
    text = prompt.strip().lower()
    print(f"💬 Message from {sender}: '{prompt}'")

    # Check if sender is an agent
    normalized_sender = normalize_phone_number(sender)
    if normalized_sender in AGENT_NUMBERS or sender in AGENT_NUMBERS:
        print(f"🔧 Agent message received from {sender}")
        # Handle agent message separately
        handle_agent_message(prompt, sender, phone_id)
        return

    # Save user message to conversation history
    save_conversation_message(sender, prompt, is_user=True)
    
    # Get user state
    user_data = get_user_state(sender)
    user_data['sender'] = sender
    
    print(f"📊 User state: {user_data}")

    # Handle start commands
    if text in ["hi", "hello", "hie", "hey", "start"]:
        user_data = {'step': 'welcome', 'sender': sender}
        updated_state = get_action('welcome', "", user_data, phone_id)
        update_user_state(sender, updated_state)
        return

    # Handle restart commands
    if text in ["restart", "menu"]:
        user_data = handle_restart_confirmation("", user_data, phone_id)
        update_user_state(sender, user_data)
        return

    step = user_data.get('step') or 'welcome'
    print(f"📍 Current step: {step}")
    
    updated_state = get_action(step, prompt, user_data, phone_id)
    update_user_state(sender, updated_state)

# Admin endpoints
@app.route("/conversation/<phone_number>", methods=["GET"])
def get_conversation(phone_number):
    """Admin endpoint to get conversation history"""
    try:
        conversation = get_full_conversation_history(phone_number)
        return jsonify({
            "phone_number": phone_number,
            "conversation": conversation,
            "total_messages": len(conversation)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/quote/<quote_reference>", methods=["GET"])
def get_quote(quote_reference):
    """Admin endpoint to get specific quote request"""
    try:
        quote_data = get_quote_request(quote_reference)
        if quote_data:
            return jsonify({
                "quote_reference": quote_reference,
                "quote_data": quote_data
            })
        else:
            return jsonify({"error": "Quote not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/quotes", methods=["GET"])
def get_all_quotes():
    """Admin endpoint to get all quote requests"""
    try:
        quotes = get_all_quote_requests()
        return jsonify({
            "total_quotes": len(quotes),
            "quotes": quotes
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/", methods=["GET"])
def index():
    return render_template("connected.html")

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        # Webhook verification
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        
        if mode == "subscribe" and token == "contessasoft":
            print("✅ Webhook verified successfully!")
            return challenge
        else:
            print("❌ Webhook verification failed!")
            return "Verification failed", 403

    elif request.method == "POST":
        try:
            data = request.get_json()
            print(f"📨 Webhook received: {json.dumps(data, indent=2)[:500]}...")

            if not data:
                print("❌ Empty webhook request")
                return jsonify({"status": "ok"}), 200

            entries = data.get("entry", [])
            if not entries:
                print("❌ No entries in webhook")
                return jsonify({"status": "ok"}), 200

            for entry in entries:
                changes = entry.get("changes", [])
                for change in changes:
                    value = change.get("value", {})
                    metadata = value.get("metadata", {})
                    current_phone_id = metadata.get("phone_number_id")
                    
                    if not current_phone_id:
                        print("❌ No phone ID in webhook")
                        continue
                        
                    messages = value.get("messages", [])
                    if not messages:
                        print("❌ No messages in webhook")
                        continue
                        
                    message = messages[0]
                    sender = message.get("from")
                    if not sender:
                        print("❌ No sender in message")
                        continue
                    
                    print(f"📱 Message from: {sender}")

                    # Handle different message types
                    if "text" in message:
                        text = message["text"].get("body", "").strip()
                        if text:
                            print(f"💬 Text message: {text}")
                            message_handler(text, sender, current_phone_id)
                    elif "interactive" in message:
                        interactive = message["interactive"]
                        print(f"🔘 Interactive message: {interactive}")
                        
                        # Handle list replies
                        if interactive.get("type") == "list_reply":
                            list_reply = interactive.get("list_reply", {})
                            reply_id = list_reply.get("id", "")
                            reply_title = list_reply.get("title", "").strip()
                            print(f"📋 List reply - ID: {reply_id}, Title: {reply_title}")
                            if reply_title:
                                message_handler(reply_title, sender, current_phone_id)
                        
                        # Handle button replies
                        elif interactive.get("type") == "button_reply":
                            button_reply = interactive.get("button_reply", {})
                            button_id = button_reply.get("id", "")
                            button_title = button_reply.get("title", "").strip()
                            print(f"🔘 Button reply - ID: {button_id}, Title: {button_title}")
                            
                            if button_id:
                                message_handler(button_id, sender, current_phone_id)
                            elif button_title:
                                message_handler(button_title, sender, current_phone_id)

        except Exception as e:
            logging.error(f"❌ Webhook processing error: {str(e)}\n{traceback.format_exc()}")
            return jsonify({"status": "error", "message": str(e)}), 500

        return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(debug=True, port=8000)
