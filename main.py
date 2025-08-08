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

# Redis client setup
redis_client = Redis(
    url=os.environ.get('UPSTASH_REDIS_URL'),
    token=os.environ.get('UPSTASH_REDIS_TOKEN')
)

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
    DOMAIN = "Domain Registration & Web Hosting"
    WEBSITE = "Website and Web App Development"
    MOBILE = "Mobile App Development"
    CHATBOT = "WhatsApp Chatbots"
    PAYMENTS = "Payment Integrations"
    AI = "AI and Automation"
    DASHBOARDS = "Custom Dashboards"
    OTHER = "Something else - Write what you want in reply"

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

# Redis state functions
def get_user_state(phone_number):
    state_json = redis_client.get(f"user_state:{phone_number}")
    if state_json:
        return json.loads(state_json)
    return {'step': 'welcome', 'sender': phone_number}

def update_user_state(phone_number, updates):
    current = get_user_state(phone_number)
    current.update(updates)
    current['phone_number'] = phone_number
    if 'sender' not in current:
        current['sender'] = phone_number
    redis_client.setex(f"user_state:{phone_number}", 86400, json.dumps(current))

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
    
    button_items = []
    for i, button in enumerate(buttons[:3]):  # WhatsApp allows max 3 buttons
        button_items.append({
            "type": "reply",
            "reply": {
                "id": f"button_{i+1}",
                "title": button
            }
        })
    
    data = {
        "messaging_product": "whatsapp",
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
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send button message: {e}")

def send_list_message(text, options, recipient, phone_id):
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    headers = {
        'Authorization': f'Bearer {wa_token}',
        'Content-Type': 'application/json'
    }
    
    sections = [{
        "title": "Select an option",
        "rows": [{"id": str(i+1), "title": opt, "description": ""} for i, opt in enumerate(options[:10])]  # Max 10 items
    }]
    
    data = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {
                "text": text
            },
            "action": {
                "button": "Choose option",
                "sections": sections
            }
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send list message: {e}")

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
            send_message("Invalid selection. Please choose an option from the list.", user_data['sender'], phone_id)
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
            services_msg = "We offer the following services. Choose one to learn more."
            service_options = [option.value for option in ServiceOptions]
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
        selected_option = None
        for option in ServiceOptions:
            if prompt.lower() in option.value.lower():
                selected_option = option
                break
                
        if not selected_option:
            send_message("Invalid selection. Please choose an option from the list.", user_data['sender'], phone_id)
            return {'step': 'services_menu'}
            
        if selected_option == ServiceOptions.CHATBOT:
            chatbot_msg = (
                "We build automated WhatsApp bots for:\n"
                "- Bill payments (ZESA, DStv, school fees)\n"
                "- Customer service\n"
                "- Order processing\n"
                "- KYC and registration\n"
                "- Ticketing and support"
            )
            
            chatbot_options = [option.value for option in ChatbotOptions]
            send_list_message(
                chatbot_msg,
                chatbot_options,
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'chatbot_menu'})
            return {'step': 'chatbot_menu'}
            
        elif selected_option == ServiceOptions.OTHER:
            send_message(
                "Please describe the service you're looking for:",
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'get_custom_service'})
            return {'step': 'get_custom_service'}
            
        else:
            service_desc = {
                ServiceOptions.DOMAIN: "We provide domain registration and reliable web hosting services with 99.9% uptime.",
                ServiceOptions.WEBSITE: "Custom website and web application development tailored to your business needs.",
                ServiceOptions.MOBILE: "Native and hybrid mobile app development for iOS and Android platforms.",
                ServiceOptions.PAYMENTS: "Secure payment gateway integrations with local and international providers.",
                ServiceOptions.AI: "AI-powered solutions including chatbots, data analysis, and process automation.",
                ServiceOptions.DASHBOARDS: "Custom business dashboards for real-time data visualization and reporting."
            }.get(selected_option, "Service information not available.")
            
            send_button_message(
                service_desc,
                ["📌 Request Quote", "🔙 Back to Services"],
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {'step': 'service_detail'})
            return {'step': 'service_detail'}
            
    except Exception as e:
        logging.error(f"Error in handle_services_menu: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

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
        
        return handle_welcome("", user_data, phone_id)
        
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

# Action mapping
action_mapping = {
    "welcome": handle_welcome,
    "main_menu": handle_main_menu,
    "about_menu": handle_about_menu,
    "services_menu": handle_services_menu,
    "chatbot_menu": handle_chatbot_menu,
    "get_quote_info": handle_get_quote_info,
    "quote_followup": handle_quote_followup,
    "support_menu": handle_support_menu,
    "get_support_details": handle_get_support_details,
    "contact_menu": handle_contact_menu,
    "get_callback_details": handle_get_callback_details
}

def get_action(current_state, prompt, user_data, phone_id):
    handler = action_mapping.get(current_state, handle_welcome)
    return handler(prompt, user_data, phone_id)

# Message handler
def message_handler(prompt, sender, phone_id):
    text = prompt.strip().lower()

    if text in ["hi", "hello", "hie",  "hey", "start"]:
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

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == "BOT":
            return challenge, 200
        return "Failed", 403

    elif request.method == "POST":
        data = request.get_json()
        logging.info(f"Incoming webhook data: {data}")

        try:
            if not data or "entry" not in data:
                logging.warning("Empty or invalid webhook data")
                return jsonify({"status": "ok"}), 200

            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if not value:
                        continue

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

                    if "text" in message:
                        text = message["text"]
                        prompt = text.get("body", "").strip()
                        if prompt:
                            message_handler(prompt, sender, phone_id)
                    elif "button" in message:
                        button = message["button"]
                        button_response = button.get("text")
                        if button_response:
                            message_handler(button_response, sender, phone_id)
                    elif "interactive" in message:
                        interactive = message["interactive"]
                        if interactive.get("type") == "list_reply":
                            list_reply = interactive.get("list_reply", {})
                            list_response = list_reply.get("title")
                            if list_response:
                                message_handler(list_response, sender, phone_id)

        except Exception as e:
            logging.error(f"Error processing webhook: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500

        return jsonify({"status": "ok"}), 200
        

if __name__ == "__main__":
    app.run(debug=True, port=8000)
