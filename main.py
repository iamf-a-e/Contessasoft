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
AGENT_NUMBERS = ["+263785019494", "263785019494", "0785019494"]

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

# Redis state functions
def get_user_state(phone_number):
    state_json = redis_client.get(f"user_state:{phone_number}")
    if state_json:
        state = json.loads(state_json)
        print(f"Retrieved state for {phone_number}: {state}")
        return state
    default_state = {'step': 'welcome', 'sender': phone_number}
    print(f"No state found for {phone_number}, returning default: {default_state}")
    return default_state

def update_user_state(phone_number, updates):
    print("#########################")
    print(f"Updating state for {phone_number}")
    print(f"Updates: {updates}")
    current = get_user_state(phone_number)
    print(f"Current state: {current}")
    current.update(updates)
    current['phone_number'] = phone_number
    if 'sender' not in current:
        current['sender'] = phone_number
    print(f"Final state to save: {current}")
    redis_client.setex(f"user_state:{phone_number}", 86400, json.dumps(current))
    print(f"State saved for {phone_number}")

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

def handle_main_menu(prompt, user_data, phone_id):
    try:
        selected_option = None
        for option in MainMenuOptions:
            if prompt.lower() in option.value.lower():
                selected_option = option
                break
                
        if not selected_option:
            send_message("Fae selection. Please choose an option from the list.", user_data['sender'], phone_id)
            return {'step': 'main_menu'}
        
        if selected_option == MainMenuOptions.ABOUT:
            about_msg = (
                "Contessasoft is a Zimbabwe-based software company established in 2022.\n"
                "We develop custom systems for businesses in finance, education, logistics, retail, and other sectors.\n\n"
                "Would you like to:"
            )
            
            about_options = [option.value for option in AboutOptions]
            send_list_message(
                about_msg,
                about_options,
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'about_menu'})
            return {'step': 'about_menu'}
            
        elif selected_option == MainMenuOptions.SERVICES:
            services_msg = (
                "🔧 *Our Services* 🔧\n\n"
                "We offer complete digital solutions:\n"
                "Select a service to learn more:"
            )
            service_options = [option.value for option in ServiceOptions]
            
            # Ensure options aren't too long for WhatsApp limits
            service_options = [opt[:72] for opt in service_options]
            
            send_list_message(
                services_msg,
                service_options,
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'services_menu'})
            return {'step': 'services_menu'}
            
            
        elif selected_option == MainMenuOptions.QUOTE:
            send_message(
                "To help us prepare a quote, please provide your full name.",
                user_data['sender'],
                phone_id
            )
            # Initialize empty user object
            user = User(name="", phone=user_data['sender'])
            update_user_state(user_data['sender'], {
                'step': 'get_quote_info',
                'user': user.to_dict(),
                'field': 'name'  # First field to collect
            })
            return {'step': 'get_quote_info'}
            
        elif selected_option == MainMenuOptions.SUPPORT:
            support_msg = "Please select the type of support you need:"
            support_options = [option.value for option in SupportOptions]
            send_list_message(
                support_msg,
                support_options,
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'support_menu'})
            return {'step': 'support_menu'}
            
        elif selected_option == MainMenuOptions.CONTACT:
            contact_msg = (
                "You can reach Contessasoft through the following:\n\n"
                "Address: 115 ED Mnangagwa Road, Highlands, Harare, Zimbabwe\n"
                "WhatsApp: +263 242 498954\n"
                "Email: sales@contessasoft.co.zw\n\n"
                "Would you like to:"
            )
            
            contact_options = [option.value for option in ContactOptions]
            send_list_message(
                contact_msg,
                contact_options,
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'contact_menu'})
            return {'step': 'contact_menu'}
            
    except Exception as e:
        logging.error(f"Error in handle_main_menu: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

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
        # Clean the input and check for button responses
        clean_input = prompt.strip().lower()
        
        # Handle "Request Quote" button or text
        if "quote" in clean_input or "request quote" in clean_input or "💬" in prompt:
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
        

def handle_chatbot_menu(prompt, user_data, phone_id):
    try:
        selected_option = None
        for option in ChatbotOptions:
            if prompt.lower() in option.value.lower():
                selected_option = option
                break
                
        if not selected_option:
            send_message("Invalid selection. Please choose an option from the list.", user_data['sender'], phone_id)
            return {'step': 'chatbot_menu'}
            
        if selected_option == ChatbotOptions.QUOTE:
            send_message(
                "To help us prepare a quote, please provide your full name.",
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'get_chatbot_quote'})
            return {'step': 'get_chatbot_quote'}
            
        elif selected_option == ChatbotOptions.SAMPLE:
            send_message(
                "You can view a sample chatbot at: https://wa.me/263242498954?text=sample\n\n"
                "Would you like to request a quote for a similar solution?",
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'get_quote_info'})
            return {'step': 'get_quote_info'}
            
        elif selected_option == ChatbotOptions.BACK:
            return handle_main_menu(MainMenuOptions.SERVICES.value, user_data, phone_id)
            
    except Exception as e:
        logging.error(f"Error in handle_chatbot_menu: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

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
            send_message("Thank you. Please provide your email or WhatsApp number:", user_data['sender'], phone_id)
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
                'field': 'service_type'
            })
            send_message("Please specify the type of service you need:", user_data['sender'], phone_id)
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
            quote_options = [option.value for option in QuoteOptions]
            
            send_list_message(
                "Would you like a call back after submitting?",
                quote_options,
                user_data['sender'],
                phone_id
            )
            
            # Send info to admin
            admin_msg = (
                "📋 *New Quote Request*\n\n"
                f"👤 Name: {user.name}\n"
                f"📞 Phone: {user.phone}\n"
                f"📧 Email: {user.email}\n"
                f"🛠️ Service: {user.service_type.value if user.service_type else 'Other'}\n"
                f"📝 Description: {user.project_description}"
            )
            send_message(admin_msg, owner_phone, phone_id)
            
            update_user_state(user_data['sender'], {
                'step': 'quote_followup',
                'user': user.to_dict()
            })
            return {
                'step': 'quote_followup',
                'user': user.to_dict()
            }
            
             
    except Exception as e:
        logging.error(f"Error in handle_get_quote_info: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}
        

def handle_quote_followup(prompt, user_data, phone_id):
    try:
        selected_option = None
        for option in QuoteOptions:
            if prompt.lower() in option.value.lower():
                selected_option = option
                break
                
        if not selected_option:
            send_message("Invalid selection. Please choose an option from the list.", user_data['sender'], phone_id)
            return {
                'step': 'quote_followup',
                'user': user_data.get('user', {})
            }
            
        user = User.from_dict(user_data['user'])
        
        if selected_option == QuoteOptions.CALLBACK:
            user.callback_requested = True
            send_message(
                "Thank you! Your request has been submitted. Our team will call you within 24 hours.\n\n"
                "Reference: #" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6)),
                user_data['sender'],
                phone_id
            )
            
            # Notify admin about callback request
            admin_msg = f"📞 Callback requested by {user.name} ({user.phone}) for quote #{user.project_description[:10]}..."
            send_message(admin_msg, owner_phone, phone_id)
            
        elif selected_option == QuoteOptions.NO_CALLBACK:
            send_message(
                "Thank you! Your request has been submitted. You'll receive the quote via WhatsApp/email within 24 hours.",
                user_data['sender'],
                phone_id
            )
            
        elif selected_option == QuoteOptions.BACK:
            return handle_main_menu(MainMenuOptions.QUOTE.value, user_data, phone_id)
            
        return handle_welcome("", user_data, phone_id)
        
    except Exception as e:
        logging.error(f"Error in handle_quote_followup: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

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
            
        user = User(user_data.get('name', 'User'), user_data['sender'])
        user.support_type = selected_option
        
        if selected_option == SupportOptions.TECH:
            send_message(
                "Please describe your technical issue:\n"
                "1. System/feature having issues\n"
                "2. Error messages received\n"
                "3. Steps to reproduce the issue",
                user_data['sender'],
                phone_id
            )
            
        elif selected_option == SupportOptions.BILLING:
            send_message(
                "Please provide:\n"
                "1. Invoice/transaction number\n"
                "2. Payment method used\n"
                "3. Description of the issue",
                user_data['sender'],
                phone_id
            )
            
        elif selected_option == SupportOptions.GENERAL:
            send_message(
                "Please describe your enquiry:",
                user_data['sender'],
                phone_id
            )
            
        update_user_state(user_data['sender'], {
            'step': 'get_support_details',
            'user': user.to_dict()
        })
        return {
            'step': 'get_support_details',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_support_menu: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_support_details(prompt, user_data, phone_id):
   
    try:
        user = User.from_dict(user_data['user'])
        
        # Send support request to admin
        admin_msg = (
            f"🆘 *New Support Request* ({user.support_type.value})\n\n"
            f"👤 From: {user.name} ({user.phone})\n"
            f"📝 Details: {prompt}"
        )
        send_message(admin_msg, owner_phone, phone_id)
        
        send_message(
            "Thank you! Your support request has been logged. Our team will respond shortly.\n"
            "Reference: #" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6)),
            user_data['sender'],
            phone_id
        )
        
        # Call human_agent to set up the agent handover
        return human_agent("", user_data, phone_id)
        
    except Exception as e:
        logging.error(f"Error in handle_get_support_details: {e}")
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
                "Please provide your full name.\n",
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'get_callback_details'})
            return {'step': 'get_callback_details'}
            
        elif selected_option == ContactOptions.AGENT:
            send_message(
                "Connecting you to an agent...\n"
                "If no one is available immediately, your message will be forwarded and you'll receive a response soon.",
                user_data['sender'],
                phone_id
            )
            
            # Notify admin
            admin_msg = f"👤 {user_data['sender']} requested to speak with an agent."
            send_message(admin_msg, owner_phone, phone_id)
            
            return human_agent("", user_data, phone_id)
            
        elif selected_option == ContactOptions.BACK:
            return handle_welcome("", user_data, phone_id)
            
    except Exception as e:
        logging.error(f"Error in handle_contact_menu: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_callback_details(prompt, user_data, phone_id):
    try:
        if 'name' not in user_data:
            update_user_state(user_data['sender'], {
                'step': 'get_callback_details',
                'name': prompt,
                'field': 'time'
            })
            send_message("Thank you. Please provide the best time to call:", user_data['sender'], phone_id)
            return {
                'step': 'get_callback_details',
                'name': prompt,
                'field': 'time'
            }
            
        elif user_data.get('field') == 'time':
            # Send callback request to admin
            admin_msg = (
                "📞 *Callback Request*\n\n"
                f"👤 Name: {user_data['name']}\n"
                f"📞 Phone: {user_data['sender']}\n"
                f"⏰ Preferred Time: {prompt}"
            )
            send_message(admin_msg, owner_phone, phone_id)
            
            send_message(
                "Thank you! We'll call you at the requested time.\n"
                "Reference: #" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6)),
                user_data['sender'],
                phone_id
            )
            
            return handle_welcome("", user_data, phone_id)
            
    except Exception as e:
        logging.error(f"Error in handle_get_callback_details: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}


def human_agent(prompt, user_data, phone_id):
    """Handles the handover process to a human agent."""
    try:
        if not AGENT_NUMBERS:
            send_message(
                "Sorry, no agents are currently available. Please try again later.",
                user_data['sender'],
                phone_id
            )
            return handle_welcome("", user_data, phone_id)

        # Pick a random agent
        selected_agent = random.choice(AGENT_NUMBERS)

        # Create a conversation ID
        conversation_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        conversation_data = {
            'customer': user_data['sender'],
            'agent': selected_agent,
            'active': False
        }
        print(f"Creating conversation {conversation_id} with data: {conversation_data}")
        redis_client.setex(f"agent_conversation:{conversation_id}", 86400, json.dumps(conversation_data))
        print(f"Conversation {conversation_id} saved to Redis")

        # Save customer state
        update_user_state(user_data['sender'], {
            'step': 'human_agent',
            'assigned_agent': selected_agent,
            'agent_handover': True,
            'conversation_id': conversation_id
        })

        # Save agent state so they can respond to Accept/Reject
        agent_state = {
            'step': 'agent_response',
            'conversation_id': conversation_id,
            'awaiting_agent_response': True,
            'sender': selected_agent
        }
        print(f"Setting agent state for {selected_agent}: {agent_state}")
        update_user_state(selected_agent, agent_state)
        
        # Also try to set state for normalized version
        normalized_agent = normalize_phone_number(selected_agent)
        if normalized_agent != selected_agent:
            print(f"Also setting state for normalized agent number: {normalized_agent}")
            update_user_state(normalized_agent, agent_state)

        # Notify customer
        send_message(
            f"🚀 Connecting you to an agent...\n\n"
            f"Your conversation ID: {conversation_id}\n"
            "Please wait for the agent to accept your request.",
            user_data['sender'],
            phone_id
        )

        # Ask agent to accept/reject
        print(f"Sending button message to agent: {selected_agent}")
        print(f"Agent number type: {type(selected_agent)}")
        print(f"Phone ID: {phone_id}")
        print(f"Agent number in AGENT_NUMBERS: {selected_agent in AGENT_NUMBERS}")
        print(f"All AGENT_NUMBERS: {AGENT_NUMBERS}")
        
        button_sent = send_button_message(
            f"New Chat Request\n\n"
            f"From: {user_data.get('name', 'Customer')} ({user_data['sender']})\n"
            f"Conversation ID: {conversation_id}",
            [
                {"id": "accept_chat", "title": "Accept Chat"},
                {"id": "reject_chat", "title": "Reject Chat"}
            ],
            selected_agent,
            phone_id
        )
        
        if not button_sent:
            print(f"Failed to send button message to agent {selected_agent}")
            # Fallback to simple text message
            send_message(
                f"New Chat Request\n\n"
                f"From: {user_data.get('name', 'Customer')} ({user_data['sender'])}\n"
                f"Conversation ID: {conversation_id}\n\n"
                f"Reply with 'accept' to accept or 'reject' to reject.",
                selected_agent,
                phone_id
            )
        print("*******************************")
        return {
            'step': 'agent_response',
            'assigned_agent': selected_agent,
            'conversation_id': conversation_id,
            'awaiting_agent_response': True
        }

    except Exception as e:
        logging.error(f"Error in human_agent: {e}")
        send_message("An error occurred during agent transfer. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}


def agent_response(prompt, user_data, phone_id):
    """Handles agent accept/reject and forwards chat messages."""
    try:
        print(f"Agent response called with prompt: '{prompt}' and user_data: {user_data}")
        
        # Handle accept/reject chat request
        if user_data.get('awaiting_agent_response'):
            print(f"Agent is awaiting response, prompt: '{prompt}'")
            print(f"User data keys: {list(user_data.keys())}")
            print(f"Conversation ID: {user_data.get('conversation_id')}")
            print(f"Agent phone: {user_data.get('sender')}")
            
            # Check for accept/reject buttons (both exact match and partial)
            if prompt == "accept_chat" or "accept" in prompt.lower():
                print("Processing accept chat request")
                conversation_id = user_data.get('conversation_id')
                print(f"Looking for conversation: {conversation_id}")
                conv_data_raw = redis_client.get(f"agent_conversation:{conversation_id}")
                print(f"Conversation data raw: {conv_data_raw}")
                if not conv_data_raw:
                    print(f"❌ Conversation {conversation_id} not found in Redis")
                    print(f"Available keys in Redis: {redis_client.keys('agent_conversation:*')}")
                    send_message("❌ Conversation not found or expired.", user_data['sender'], phone_id)
                    return {'step': 'agent_response'}

                conv_data = json.loads(conv_data_raw)
                print(f"Conversation data: {conv_data}")
                customer_number = conv_data.get('customer')
                print(f"Customer number: {customer_number}")

                send_message(
                    "Agent has joined the conversation. You can now chat directly.\n"
                    "Type 'exit' at any time to end the conversation.",
                    customer_number,
                    phone_id
                )
                send_message(
                    "✅ You are now connected to the customer.\n"
                    "Type 'exit' to end the conversation and return to the bot.",
                    user_data['sender'],
                    phone_id
                )

                conv_data['active'] = True
                redis_client.setex(f"agent_conversation:{conversation_id}", 86400, json.dumps(conv_data))

                return {
                    'step': 'agent_response',
                    'conversation_id': conversation_id,
                    'active_chat': True
                }

            elif prompt == "reject_chat" or "reject" in prompt.lower():
                print("Processing reject chat request")
                conversation_id = user_data.get('conversation_id')
                conv_data_raw = redis_client.get(f"agent_conversation:{conversation_id}")
                if conv_data_raw:
                    conv_data = json.loads(conv_data_raw)
                    customer_number = conv_data.get('customer')
                    send_message(
                        "Sorry, the agent is unable to take your chat at this time. "
                        "Please try again later or leave a message.",
                        customer_number,
                        phone_id
                    )
                    redis_client.delete(f"agent_conversation:{conversation_id}")
                    return handle_welcome("", {'sender': customer_number}, phone_id)
                return {'step': 'agent_response'}
            else:
                # If we reach here, the prompt didn't match accept or reject
                print(f"Unexpected prompt in agent_response: '{prompt}'")
                send_message("Invalid selection. Please choose 'Accept Chat' or 'Reject Chat'.", user_data['sender'], phone_id)
                return {'step': 'agent_response'}

        # Handle active chat messages
        conversation_id = user_data.get('conversation_id')
        if conversation_id:
            conv_data_raw = redis_client.get(f"agent_conversation:{conversation_id}")
            if conv_data_raw:
                conv_data = json.loads(conv_data_raw)

                # Exit command ends chat
                if prompt.lower() == "exit":
                    if user_data['sender'] == conv_data['agent']:
                        send_message(
                            "You’ve ended the conversation. The customer will now return to the bot.",
                            conv_data['agent'],
                            phone_id
                        )
                        send_message(
                            "The agent has ended the conversation. You’re now back with the bot.",
                            conv_data['customer'],
                            phone_id
                        )
                    else:
                        send_message(
                            "You’ve ended the conversation with the agent. You’re now back with the bot.",
                            conv_data['customer'],
                            phone_id
                        )
                        send_message(
                            "The customer has ended the conversation.",
                            conv_data['agent'],
                            phone_id
                        )

                    redis_client.delete(f"agent_conversation:{conversation_id}")
                    return handle_welcome("", {'sender': conv_data['customer']}, phone_id)

                # Forward messages
                if user_data['sender'] == conv_data['agent']:
                    send_message(f"👨‍💼 Agent: {prompt}", conv_data['customer'], phone_id)
                else:
                    send_message(f"👤 Customer: {prompt}", conv_data['agent'], phone_id)

                return user_data

        return handle_welcome("", user_data, phone_id)

    except Exception as e:
        logging.error(f"Error in agent_response: {e}")
        send_message("An error occurred in agent communication. Returning to main menu.", user_data['sender'], phone_id)
        return {'step': 'welcome'}


# Action mapping
action_mapping = {
    "welcome": handle_welcome,
    "main_menu": handle_main_menu,
    "about_menu": handle_about_menu,
    "services_menu": handle_services_menu,
    "service_detail": handle_service_detail,
    "chatbot_menu": handle_chatbot_menu,
    "get_quote_info": handle_get_quote_info,
    "quote_followup": handle_quote_followup,
    "support_menu": handle_support_menu,
    "get_support_details": handle_get_support_details,
    "contact_menu": handle_contact_menu,
    "get_callback_details": handle_get_callback_details,
    "human_agent": agent_response,
    "agent_response": agent_response
}

def get_action(current_state, prompt, user_data, phone_id):
    # Determine which handler will be used
    handler = action_mapping.get(current_state, handle_welcome)

    # Log the routing info
    logging.info(f"[get_action] State: {current_state}, Prompt: {prompt}, "
                 f"Handler: {handler.__name__}, Sender: {user_data.get('sender')}")

    try:
        return handler(prompt, user_data, phone_id)
    except Exception as e:
        logging.error(f"[get_action] Error in handler {handler.__name__} for state {current_state}: {e}", exc_info=True)
        # Fallback to welcome
        return handle_welcome("", user_data, phone_id)


# Message handler
def message_handler(prompt, sender, phone_id):
    text = prompt.strip().lower()

    # If the sender is an agent, set them to agent mode on first contact
    normalized_sender = normalize_phone_number(sender)
    print(f"Checking if {sender} (normalized: {normalized_sender}) is in AGENT_NUMBERS: {AGENT_NUMBERS}")
    print(f"Sender type: {type(sender)}, AGENT_NUMBERS types: {[type(x) for x in AGENT_NUMBERS]}")
    
    if normalized_sender in AGENT_NUMBERS or sender in AGENT_NUMBERS:
        print(f"Agent message received: '{prompt}' from {sender}")
        print(f"AGENT_NUMBERS: {AGENT_NUMBERS}")
        state = get_user_state(sender)
        print(f"Current agent state: {state}")
        
        # Also try to get state for normalized sender
        if state.get('step') != 'agent_response':
            normalized_state = get_user_state(normalized_sender)
            print(f"Normalized sender state: {normalized_state}")
            if normalized_state.get('step') == 'agent_response':
                state = normalized_state
                print(f"Using normalized sender state: {state}")
        
        if state.get('step') != 'agent_response':
            print(f"Updating agent state to agent_response")
            update_user_state(sender, {
                'step': 'agent_response',
                'sender': sender
            })
            state = get_user_state(sender)  # refresh after update
            print(f"Updated agent state: {state}")
    
        # 🚀 Directly call the agent_response() function
        print(f"Calling agent_response with prompt: '{prompt}' and state: {state}")
        updated_state = agent_response(prompt, state, phone_id)
        print(f"Agent response returned: {updated_state}")
        update_user_state(sender, updated_state)
        return

    # Normal user handling
    if text in ["hi", "hello", "hie", "hey", "start"]:
        user_state = {'step': 'welcome', 'sender': sender}
        updated_state = get_action('welcome', "", user_state, phone_id)
        update_user_state(sender, updated_state)
        return

    user_state = get_user_state(sender)
    user_state['sender'] = sender

    step = user_state.get('step') or 'welcome'
    updated_state = get_action(step, prompt, user_state, phone_id)
    update_user_state(sender, updated_state)


@app.route("/", methods=["GET"])
def index():
    return render_template("connected.html")

@app.route("/webhook", methods=["POST"])
def webhook():
    if request.method == "POST":
        try:
            data = request.get_json()
            if not data:
                logging.warning("Empty webhook request")
                return jsonify({"status": "ok"}), 200

            entries = data.get("entry", [])
            if not entries:
                logging.info("No entries in webhook")
                return jsonify({"status": "ok"}), 200

            for entry in entries:
                changes = entry.get("changes", [])
                for change in changes:
                    value = change.get("value", {})
                    metadata = value.get("metadata", {})
                    phone_id = metadata.get("phone_number_id")
                    
                    if not phone_id:
                        continue
                        
                    messages = value.get("messages", [])
                    if not messages:
                        continue
                        
                    message = messages[0]
                    sender = message.get("from")
                    if not sender:
                        continue
                    
                    print(f"Webhook received message from: {sender} (type: {type(sender)})")

                    # Handle different message types
                    if "text" in message:
                        text = message["text"].get("body", "").strip()
                        if text:
                            message_handler(text, sender, phone_id)
                    elif "interactive" in message:
                        interactive = message["interactive"]
                        print(f"Interactive message received: {interactive}")
                        
                        # Handle list replies
                        if interactive.get("type") == "list_reply":
                            list_reply = interactive.get("list_reply", {})
                            reply_title = list_reply.get("title", "").strip()
                            if reply_title:
                                message_handler(reply_title, sender, phone_id)

                        
                        # Handle button replies
                        elif interactive.get("type") == "button_reply":
                            button_reply = interactive.get("button_reply", {})
                            button_id = button_reply.get("id")
                            button_title = button_reply.get("title", "").strip()
                            
                            print(f"Button reply received - ID: '{button_id}', Title: '{button_title}', Sender: {sender}")
                            print(f"Full button_reply data: {button_reply}")
                        
                            # Pass Accept/Reject IDs directly
                            if button_id in ["accept_chat", "reject_chat"]:
                                prompt = button_id
                                print(f"Setting prompt to button_id: '{prompt}'")
                            elif button_id == "quote_btn":
                                prompt = "Request Quote"
                            elif button_id == "back_btn":
                                prompt = "Back to Services"
                            else:
                                prompt = button_title
                        
                            if prompt:
                                print(f"Calling message_handler with prompt: '{prompt}' for sender: {sender}")
                                message_handler(prompt, sender, phone_id)


        except Exception as e:
            logging.error(f"Webhook processing error: {str(e)}\n{traceback.format_exc()}")
            return jsonify({"status": "error", "message": str(e)}), 500

        return jsonify({"status": "ok"}), 200
        

if __name__ == "__main__":
    app.run(debug=True, port=8000)
