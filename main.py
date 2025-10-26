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

# Conversation state functions
def get_conversation_state(phone_number):
    normalized_phone = normalize_phone_number(phone_number)
    conversation_key = f"conversation:{normalized_phone}"
    state_json = redis_client.get(conversation_key)
    if state_json:
        state = json.loads(state_json)
        print(f"✅ Retrieved conversation for {normalized_phone}: {state}")
        return state
    default_state = {'step': 'welcome', 'sender': normalized_phone}
    print(f"❌ No conversation found for {normalized_phone}, returning default: {default_state}")
    return default_state

def update_conversation_state(phone_number, updates):
    normalized_phone = normalize_phone_number(phone_number)
    conversation_key = f"conversation:{normalized_phone}"
    print(f"🔄 Updating conversation for {normalized_phone}")
    
    current = get_conversation_state(normalized_phone)
    current.update(updates)
    current['phone_number'] = normalized_phone
    if 'sender' not in current:
        current['sender'] = normalized_phone
        
    print(f"💾 Saving to Redis key: {conversation_key}")
    print(f"📦 Data: {current}")
    
    try:
        result = redis_client.setex(conversation_key, 86400, json.dumps(current))
        print(f"✅ Redis save result: {result}")
        
        # Immediate verification
        verify = redis_client.get(conversation_key)
        if verify:
            verified_data = json.loads(verify)
            print(f"✅ Verified save successful: {verified_data.get('step', 'unknown')}")
        else:
            print(f"❌ Verification failed - key not found")
            
    except Exception as e:
        print(f"❌ Redis error: {e}")

def delete_conversation(phone_number):
    """Delete conversation state for a phone number"""
    normalized_phone = normalize_phone_number(phone_number)
    conversation_key = f"conversation:{normalized_phone}"
    try:
        result = redis_client.delete(conversation_key)
        print(f"🗑️ Deleted conversation for {normalized_phone}: {result}")
        return result
    except Exception as e:
        print(f"❌ Error deleting conversation: {e}")
        return 0

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
def handle_welcome(prompt, conversation_data, phone_id):
    welcome_msg = (
        "🌟 *Welcome to Contessasoft (Private) Limited!* 🌟\n\n"
        "We build intelligent software solutions including websites, mobile apps, chatbots, and business systems.\n\n"
        "Please choose an option to continue:"
    )
    
    menu_options = [option.value for option in MainMenuOptions]
    send_list_message(
        welcome_msg,
        menu_options,
        conversation_data['sender'],
        phone_id
    )
    
    update_conversation_state(conversation_data['sender'], {'step': 'main_menu'})
    return {'step': 'main_menu'}

def handle_restart_confirmation(prompt, conversation_data, phone_id):
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
                conversation_data['sender'],
                phone_id
            )
            update_conversation_state(conversation_data['sender'], {'step': 'restart_confirmation'})
            return {'step': 'restart_confirmation'}

        # Positive confirmation -> go to welcome flow
        if text in ["yes", "y", "restart_yes", "ok", "sure", "yeah", "yep"]:
            return handle_welcome("", conversation_data, phone_id)

        # Negative confirmation -> send goodbye and reset to welcome state
        if text in ["no", "n", "restart_no", "nope", "nah"]:
            send_message("Have a good day!", conversation_data['sender'], phone_id)
            update_conversation_state(conversation_data['sender'], {'step': 'welcome'})
            return {'step': 'welcome'}

        # Any other input -> re-send buttons
        send_button_message(
            "Please confirm: would you like to restart with the bot?",
            [
                {"id": "restart_yes", "title": "Yes"},
                {"id": "restart_no", "title": "No"}
            ],
            conversation_data['sender'],
            phone_id
        )
        return {'step': 'restart_confirmation'}

    except Exception as e:
        logging.error(f"Error in handle_restart_confirmation: {e}")
        send_message("An error occurred. Returning to main menu.", conversation_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_main_menu(prompt, conversation_data, phone_id):
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
            send_message("Please select a valid option from the list.", conversation_data['sender'], phone_id)
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
            send_list_message(about_msg, about_options, conversation_data['sender'], phone_id)
            update_conversation_state(conversation_data['sender'], {'step': 'about_menu'})
            return {'step': 'about_menu'}

        elif selected_option == MainMenuOptions.SERVICES:
            services_msg = (
                "🔧 *Our Services* 🔧\n\n"
                "We offer complete digital solutions:\n"
                "Select a service to learn more:"
            )
            service_options = [option.value for option in ServiceOptions]
            send_list_message(services_msg, service_options, conversation_data['sender'], phone_id)
            update_conversation_state(conversation_data['sender'], {'step': 'services_menu'})
            return {'step': 'services_menu'}

        elif selected_option == MainMenuOptions.QUOTE:
            send_message("To help us prepare a quote, please provide your full name.", conversation_data['sender'], phone_id)
            user = User(name="", phone=conversation_data['sender'])
            update_conversation_state(conversation_data['sender'], {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'name'
            })
            return {'step': 'get_quote_info'}

        elif selected_option == MainMenuOptions.SUPPORT:
            support_msg = "Please select the type of support you need:"
            support_options = [option.value for option in SupportOptions]
            send_list_message(support_msg, support_options, conversation_data['sender'], phone_id)
            update_conversation_state(conversation_data['sender'], {'step': 'support_menu'})
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
            send_list_message(contact_msg, contact_options, conversation_data['sender'], phone_id)
            update_conversation_state(conversation_data['sender'], {'step': 'contact_menu'})
            return {'step': 'contact_menu'}

    except Exception as e:
        logging.error(f"Error in handle_main_menu: {e}\n{traceback.format_exc()}")
        send_message("An error occurred. Please try again.", conversation_data['sender'], phone_id)
        return {'step': 'welcome'}


def handle_about_menu(prompt, conversation_data, phone_id):
    try:
        selected_option = None
        for option in AboutOptions:
            if prompt.lower() in option.value.lower():
                selected_option = option
                break
                
        if not selected_option:
            send_message("Invalid selection. Please choose an option from the list.", conversation_data['sender'], phone_id)
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
            send_message(portfolio_msg, conversation_data['sender'], phone_id)
            return handle_welcome("", conversation_data, phone_id)
            
        elif selected_option == AboutOptions.PROFILE:
            send_message(
                "You can download our company profile from: https://contessasoft.co.zw/profile.pdf\n\n"
                "Would you like to request more information?",
                conversation_data['sender'],
                phone_id
            )
            update_conversation_state(conversation_data['sender'], {'step': 'request_more_info'})
            return {'step': 'request_more_info'}
            
        elif selected_option == AboutOptions.BACK:
            return handle_welcome("", conversation_data, phone_id)
            
    except Exception as e:
        logging.error(f"Error in handle_about_menu: {e}")
        send_message("An error occurred. Please try again.", conversation_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_services_menu(prompt, conversation_data, phone_id):
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
            
            if not send_list_message(error_msg, service_options, conversation_data['sender'], phone_id):
                send_message(
                    "Please reply with:\n" + "\n".join(f"- {opt.value}" for opt in ServiceOptions),
                    conversation_data['sender'],
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
        update_conversation_state(conversation_data['sender'], {
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
            "to": conversation_data['sender'],
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
            
            # Update conversation to handle button responses
            update_conversation_state(conversation_data['sender'], {
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
                conversation_data['sender'],
                phone_id
            )
            
        return {
            'step': 'service_detail',
            'selected_service': selected_option.name
        }
            
    except Exception as e:
        logging.error(f"Service menu error: {str(e)}\n{traceback.format_exc()}")
        send_message("⚠️ Please try selecting again or type 'menu'", conversation_data['sender'], phone_id)
        return {'step': 'services_menu'}


def handle_service_detail(prompt, conversation_data, phone_id):
    try:
        # Clean the input and check for button responses
        clean_input = prompt.strip().lower()
        
        # Handle "Request Quote" button or text
        if "quote" in clean_input or "request quote" in clean_input or "💬" in prompt:
            # Initialize user object for quote collection
            user = User(name="", phone=conversation_data['sender'])
            update_conversation_state(conversation_data['sender'], {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'name',  # First field to collect
                'selected_service': conversation_data.get('selected_service'),
                'service_description': conversation_data.get('service_description')
            })
            send_message("To help us prepare a quote, please provide your full name:", conversation_data['sender'], phone_id)
            return {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'name'
            }
            
        # Handle "Back to Services" button or text
        elif "back" in clean_input or "services" in clean_input or "🔙" in prompt:
            services_msg = (
                "🔧 *Our Services* 🔧\n\n"
                "We offer complete digital solutions:\n"
                "Select a service to learn more:"
            )
            service_options = [option.value for option in ServiceOptions]
            send_list_message(
                services_msg,
                service_options,
                conversation_data['sender'],
                phone_id
            )
            update_conversation_state(conversation_data['sender'], {'step': 'services_menu'})
            return {'step': 'services_menu'}
            
        # If the input doesn't match any expected option
        else:
            # Resend the service info with buttons
            service_info = (
                f"ℹ️ *{conversation_data.get('service_description', 'Selected Service')}*\n\n"
                "Please choose an option:"
            )
            send_button_message(
                service_info,
                [
                    {"id": "quote_btn", "title": "💬 Request Quote"},
                    {"id": "back_btn", "title": "🔙 Back to Services"}
                ],
                conversation_data['sender'],
                phone_id
            )
            return {'step': 'service_detail'}
            
    except Exception as e:
        logging.error(f"Error in handle_service_detail: {e}")
        send_message("An error occurred. Please try again.", conversation_data['sender'], phone_id)
        return {'step': 'services_menu'}
        

def handle_chatbot_menu(prompt, conversation_data, phone_id):
    try:
        selected_option = None
        for option in ChatbotOptions:
            if prompt.lower() in option.value.lower():
                selected_option = option
                break
                
        if not selected_option:
            send_message("Invalid selection. Please choose an option from the list.", conversation_data['sender'], phone_id)
            return {'step': 'chatbot_menu'}
            
        if selected_option == ChatbotOptions.QUOTE:
            send_message(
                "To help us prepare a quote, please provide your full name.",
                conversation_data['sender'],
                phone_id
            )
            update_conversation_state(conversation_data['sender'], {'step': 'get_chatbot_quote'})
            return {'step': 'get_chatbot_quote'}
            
        elif selected_option == ChatbotOptions.SAMPLE:
            send_message(
                "You can view a sample chatbot at: https://wa.me/263242498954?text=sample\n\n"
                "Would you like to request a quote for a similar solution?",
                conversation_data['sender'],
                phone_id
            )
            update_conversation_state(conversation_data['sender'], {'step': 'get_quote_info'})
            return {'step': 'get_quote_info'}
            
        elif selected_option == ChatbotOptions.BACK:
            return handle_main_menu(MainMenuOptions.SERVICES.value, conversation_data, phone_id)
            
    except Exception as e:
        logging.error(f"Error in handle_chatbot_menu: {e}")
        send_message("An error occurred. Please try again.", conversation_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_quote_info(prompt, conversation_data, phone_id):
    try:
        user = User.from_dict(conversation_data['user'])
        current_field = conversation_data.get('field')
        
        if current_field == 'name':
            user.name = prompt
            update_conversation_state(conversation_data['sender'], {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'email'
            })
            send_message("Thank you. Please provide your email or WhatsApp number:", conversation_data['sender'], phone_id)
            return {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'email'
            }
            
        elif current_field == 'email':
            user.email = prompt
            update_conversation_state(conversation_data['sender'], {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'service_type'
            })
            send_message("Please specify the type of service you need:", conversation_data['sender'], phone_id)
            return {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'service_type'
            }
            
        elif current_field == 'service_type':
            try:
                user.service_type = ServiceOptions(prompt)
            except ValueError:
                user.service_type = ServiceOptions.OTHER
            update_conversation_state(conversation_data['sender'], {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'description'
            })
            send_message("Please provide a short description of your project:", conversation_data['sender'], phone_id)
            return {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'description'
            }
            
        elif current_field == 'description':
            user.project_description = prompt
            update_conversation_state(conversation_data['sender'], {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'callback'
            })
            send_message(
                "Would you like us to call you to discuss your project?",
                conversation_data['sender'],
                phone_id
            )
            return {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'callback'
            }
            
        elif current_field == 'callback':
            if prompt.lower() in ['yes', 'y', 'sure', 'ok']:
                user.callback_requested = True
                send_message(
                    "Thank you! Our team will contact you within 24 hours.",
                    conversation_data['sender'],
                    phone_id
                )
            else:
                user.callback_requested = False
                send_message(
                    "Thank you! We'll send your quote via WhatsApp.",
                    conversation_data['sender'],
                    phone_id
                )
            
            # Send notification to owner
            send_quote_notification(user, conversation_data.get('selected_service'))
            
            # Reset to main menu
            return handle_welcome("", conversation_data, phone_id)
            
    except Exception as e:
        logging.error(f"Error in handle_get_quote_info: {e}")
        send_message("An error occurred. Please try again.", conversation_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_support_menu(prompt, conversation_data, phone_id):
    try:
        selected_option = None
        for option in SupportOptions:
            if prompt.lower() in option.value.lower():
                selected_option = option
                break
                
        if not selected_option:
            send_message("Invalid selection. Please choose an option from the list.", conversation_data['sender'], phone_id)
            return {'step': 'support_menu'}
            
        if selected_option == SupportOptions.BACK:
            return handle_welcome("", conversation_data, phone_id)
            
        # Store support type and request details
        user = User(name="", phone=conversation_data['sender'])
        user.support_type = selected_option
        
        update_conversation_state(conversation_data['sender'], {
            'step': 'get_support_details',
            'user': user.to_dict(),
            'support_type': selected_option.value
        })
        
        send_message(
            f"Please describe your {selected_option.value.lower()} issue in detail:",
            conversation_data['sender'],
            phone_id
        )
        return {
            'step': 'get_support_details',
            'user': user.to_dict(),
            'support_type': selected_option.value
        }
            
    except Exception as e:
        logging.error(f"Error in handle_support_menu: {e}")
        send_message("An error occurred. Please try again.", conversation_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_contact_menu(prompt, conversation_data, phone_id):
    try:
        selected_option = None
        for option in ContactOptions:
            if prompt.lower() in option.value.lower():
                selected_option = option
                break
                
        if not selected_option:
            send_message("Invalid selection. Please choose an option from the list.", conversation_data['sender'], phone_id)
            return {'step': 'contact_menu'}
            
        if selected_option == ContactOptions.CALLBACK:
            send_message(
                "Please provide your name and preferred callback time:",
                conversation_data['sender'],
                phone_id
            )
            update_conversation_state(conversation_data['sender'], {'step': 'get_callback_details'})
            return {'step': 'get_callback_details'}
            
        elif selected_option == ContactOptions.AGENT:
            send_message(
                "Connecting you with an agent... Please wait while we transfer your request.",
                conversation_data['sender'],
                phone_id
            )
            # Notify agents
            notify_agents(conversation_data['sender'])
            return handle_welcome("", conversation_data, phone_id)
            
        elif selected_option == ContactOptions.BACK:
            return handle_welcome("", conversation_data, phone_id)
            
    except Exception as e:
        logging.error(f"Error in handle_contact_menu: {e}")
        send_message("An error occurred. Please try again.", conversation_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_support_details(prompt, conversation_data, phone_id):
    try:
        user = User.from_dict(conversation_data['user'])
        user.project_description = prompt
        
        # Send support request notification
        send_support_notification(user)
        
        send_message(
            "Thank you! Our support team will contact you shortly.",
            conversation_data['sender'],
            phone_id
        )
        
        return handle_welcome("", conversation_data, phone_id)
        
    except Exception as e:
        logging.error(f"Error in handle_get_support_details: {e}")
        send_message("An error occurred. Please try again.", conversation_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_callback_details(prompt, conversation_data, phone_id):
    try:
        # Send callback request notification
        send_callback_notification(prompt, conversation_data['sender'])
        
        send_message(
            "Thank you! Our team will call you as requested.",
            conversation_data['sender'],
            phone_id
        )
        
        return handle_welcome("", conversation_data, phone_id)
        
    except Exception as e:
        logging.error(f"Error in handle_get_callback_details: {e}")
        send_message("An error occurred. Please try again.", conversation_data['sender'], phone_id)
        return {'step': 'welcome'}

def send_quote_notification(user, selected_service=None):
    try:
        service_info = selected_service or (user.service_type.value if user.service_type else "Not specified")
        
        notification_msg = (
            f"📋 *New Quote Request*\n\n"
            f"👤 *Name:* {user.name}\n"
            f"📞 *Phone:* {user.phone}\n"
            f"📧 *Email:* {user.email or 'Not provided'}\n"
            f"🔧 *Service:* {service_info}\n"
            f"📝 *Project:* {user.project_description}\n"
            f"📞 *Callback:* {'Yes' if user.callback_requested else 'No'}\n"
            f"⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # Send to owner phone
        if owner_phone:
            send_message(notification_msg, owner_phone, phone_id)
            
        # Send to agent numbers
        for agent in AGENT_NUMBERS:
            send_message(notification_msg, agent, phone_id)
            
    except Exception as e:
        logging.error(f"Error sending quote notification: {e}")

def send_support_notification(user):
    try:
        notification_msg = (
            f"🆘 *New Support Request*\n\n"
            f"👤 *Name:* {user.name or 'Not provided'}\n"
            f"📞 *Phone:* {user.phone}\n"
            f"🔧 *Type:* {user.support_type.value if user.support_type else 'Not specified'}\n"
            f"📝 *Issue:* {user.project_description}\n"
            f"⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # Send to owner phone
        if owner_phone:
            send_message(notification_msg, owner_phone, phone_id)
            
        # Send to agent numbers
        for agent in AGENT_NUMBERS:
            send_message(notification_msg, agent, phone_id)
            
    except Exception as e:
        logging.error(f"Error sending support notification: {e}")

def send_callback_notification(details, phone):
    try:
        notification_msg = (
            f"📞 *New Callback Request*\n\n"
            f"📞 *From:* {phone}\n"
            f"📝 *Details:* {details}\n"
            f"⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # Send to owner phone
        if owner_phone:
            send_message(notification_msg, owner_phone, phone_id)
            
        # Send to agent numbers
        for agent in AGENT_NUMBERS:
            send_message(notification_msg, agent, phone_id)
            
    except Exception as e:
        logging.error(f"Error sending callback notification: {e}")

def notify_agents(user_phone):
    try:
        notification_msg = (
            f"🔔 *New Agent Transfer Request*\n\n"
            f"📞 *User:* {user_phone}\n"
            f"⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Please contact this user directly."
        )
        
        # Send to agent numbers
        for agent in AGENT_NUMBERS:
            send_message(notification_msg, agent, phone_id)
            
    except Exception as e:
        logging.error(f"Error notifying agents: {e}")

# Main message handler
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
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
            print(f"📨 Incoming webhook data: {json.dumps(data, indent=2)}")

            if not data:
                print("❌ Empty request body")
                return "OK", 200

            # Handle incoming messages
            if 'entry' in data and data['entry']:
                for entry in data['entry']:
                    if 'changes' in entry and entry['changes']:
                        for change in entry['changes']:
                            if 'value' in change and 'messages' in change['value']:
                                for message in change['value']['messages']:
                                    if message['type'] == 'text':
                                        sender = message['from']
                                        prompt = message['text']['body']
                                        print(f"💬 Message from {sender}: {prompt}")

                                        # Get conversation state
                                        conversation_data = get_conversation_state(sender)
                                        print(f"📊 Conversation state: {conversation_data}")

                                        # Handle restart commands
                                        if prompt.lower() in ['restart', 'menu', 'start']:
                                            conversation_data = handle_restart_confirmation(prompt, conversation_data, phone_id)
                                        else:
                                            # Route based on current step
                                            step = conversation_data.get('step', 'welcome')
                                            print(f"📍 Current step: {step}")

                                            if step == 'welcome':
                                                conversation_data = handle_welcome(prompt, conversation_data, phone_id)
                                            elif step == 'restart_confirmation':
                                                conversation_data = handle_restart_confirmation(prompt, conversation_data, phone_id)
                                            elif step == 'main_menu':
                                                conversation_data = handle_main_menu(prompt, conversation_data, phone_id)
                                            elif step == 'about_menu':
                                                conversation_data = handle_about_menu(prompt, conversation_data, phone_id)
                                            elif step == 'services_menu':
                                                conversation_data = handle_services_menu(prompt, conversation_data, phone_id)
                                            elif step == 'service_detail':
                                                conversation_data = handle_service_detail(prompt, conversation_data, phone_id)
                                            elif step == 'chatbot_menu':
                                                conversation_data = handle_chatbot_menu(prompt, conversation_data, phone_id)
                                            elif step == 'get_quote_info':
                                                conversation_data = handle_get_quote_info(prompt, conversation_data, phone_id)
                                            elif step == 'support_menu':
                                                conversation_data = handle_support_menu(prompt, conversation_data, phone_id)
                                            elif step == 'contact_menu':
                                                conversation_data = handle_contact_menu(prompt, conversation_data, phone_id)
                                            elif step == 'get_support_details':
                                                conversation_data = handle_get_support_details(prompt, conversation_data, phone_id)
                                            elif step == 'get_callback_details':
                                                conversation_data = handle_get_callback_details(prompt, conversation_data, phone_id)
                                            else:
                                                # Default to welcome if step is unknown
                                                conversation_data = handle_welcome(prompt, conversation_data, phone_id)

                                        # Update conversation state
                                        update_conversation_state(sender, conversation_data)

            return "OK", 200

        except Exception as e:
            logging.error(f"Webhook error: {str(e)}\n{traceback.format_exc()}")
            return "OK", 200

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
