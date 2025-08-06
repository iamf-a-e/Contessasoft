import os
import logging
import requests
import random
import string
from datetime import datetime
from flask import Flask, request, jsonify, render_template
import redis
import json
import traceback
from enum import Enum

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Environment variables
wa_token = os.environ.get("WA_TOKEN")
phone_id = os.environ.get("PHONE_ID")
gen_api = os.environ.get("GEN_API")
owner_phone = os.environ.get("OWNER_PHONE")
redis_url = os.environ.get("REDIS_URL")

# Redis client setup
redis_client = redis.StrictRedis.from_url(redis_url, decode_responses=True)

class ServiceType(Enum):
    CHATBOTS = "Chatbots"
    DOMAIN_HOSTING = "Domain Registration & Web Hosting"
    WEBSITE_DEV = "Website Development"
    MOBILE_APP_DEV = "Mobile App Development"
    OTHER = "Other Services"

class ChatbotService(Enum):
    APPOINTMENT = "Appointment bookings"
    SALES_ORDER = "Sales & order processing"
    LOAN_MGMT = "Loan management"
    SURVEYS = "Customer satisfaction surveys"
    PROPERTY = "Property & stands inquiries"
    AI_SUPPORT = "AI-powered chat support"
    UTILITY = "Council Utility payments"
    TICKETING = "Event ticketing bots"
    ECOMMERCE = "E-commerce bots"
    HR = "HR & recruitment bots"
    TRAVEL = "Travel & booking bots"
    VOTING = "Voting & polling bots"
    COMPLAINT = "Complaint management bots"
    EDUCATION = "Educational bots"
    RESTAURANT = "Restaurant ordering bots"

class MobileAppType(Enum):
    IOS = "iOS App"
    ANDROID = "Android App"
    HYBRID = "Hybrid (iOS & Android)"
    GAME = "Mobile Game"
    ENTERPRISE = "Enterprise App"
    OTHER = "Other"

class User:
    def __init__(self, name, phone):
        self.name = name
        self.phone = phone
        self.service_type = None
        self.chatbot_service = None
        self.mobile_app_type = None
        self.domain_query = None
        self.other_request = None
        self.appointment_details = {}
        self.order_details = {}
        self.loan_details = {}

    def to_dict(self):
        return {
            "name": self.name,
            "phone": self.phone,
            "service_type": self.service_type.value if self.service_type else None,
            "chatbot_service": self.chatbot_service.value if self.chatbot_service else None,
            "mobile_app_type": self.mobile_app_type.value if self.mobile_app_type else None,
            "domain_query": self.domain_query,
            "other_request": self.other_request,
            "appointment_details": self.appointment_details,
            "order_details": self.order_details,
            "loan_details": self.loan_details
        }

    @classmethod
    def from_dict(cls, data):
        user = cls(data["name"], data["phone"])
        if data.get("service_type"):
            user.service_type = ServiceType(data["service_type"])
        if data.get("chatbot_service"):
            user.chatbot_service = ChatbotService(data["chatbot_service"])
        if data.get("mobile_app_type"):
            user.mobile_app_type = MobileAppType(data["mobile_app_type"])
        user.domain_query = data.get("domain_query")
        user.other_request = data.get("other_request")
        user.appointment_details = data.get("appointment_details", {})
        user.order_details = data.get("order_details", {})
        user.loan_details = data.get("loan_details", {})
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
    
    # Check if the text is too long and needs to be split
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
    
    # Button titles with emojis
    emoji_buttons = {
        "App Development": "📱 App Development",
        "Domain Hosting": "🌐 Domain Hosting",
        "Other": "✨ Other Services",
        "Chatbots": "🤖 Chatbots",
        "Website Dev": "💻 Website Dev",
        "Mobile App": "📲 Mobile App",
        "Register": "✅ Register",
        "Transfer to Agent": "👨‍💼 Transfer to Agent",
        "Check Another": "🔄 Check Another"
    }
    
    button_items = []
    for i, button in enumerate(buttons[:3]):  # WhatsApp allows max 3 buttons
        # Use emoji version if available, otherwise use original
        button_title = emoji_buttons.get(button, button)
        
        button_items.append({
            "type": "reply",
            "reply": {
                "id": f"button_{i+1}",
                "title": button_title
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
        "🌟 Welcome to Contessasoft Services! 🌟\n\n"
        "We offer a wide range of digital solutions. Please select a service type:\n\n"
        "1. App Development\n"
        "2. Domain Registration & Web Hosting\n"
        "3. Other Services"       
    )
    
    send_button_message(
        welcome_msg,
        ["App Development", "Domain Hosting", "Other"],
        user_data['sender'],
        phone_id
    )
    
    update_user_state(user_data['sender'], {'step': 'select_service_type'})
    return {'step': 'select_service_type'}

def handle_select_service_type(prompt, user_data, phone_id):
    try:
        # Map all possible inputs to service types
        service_map = {
            # Button responses
            "app": ServiceType.MOBILE_APP_DEV,
            "domain": ServiceType.DOMAIN_HOSTING,
            "other": ServiceType.OTHER,
            
            # Text inputs
            "1": ServiceType.MOBILE_APP_DEV,
            "2": ServiceType.DOMAIN_HOSTING,
            "3": ServiceType.OTHER,
            "mobile": ServiceType.MOBILE_APP_DEV,
            "website": ServiceType.WEBSITE_DEV,
            "chatbot": ServiceType.CHATBOTS,
            
            # Add other variations as needed
        }
        
        service_type = service_map.get(prompt.lower())
        if not service_type:
            # If invalid input, resend current options without resetting to welcome
            current_step = user_data.get('step', 'select_service_type')
            return get_action(current_step, prompt, user_data, phone_id)
        
        user = User(user_data.get('name', 'User'), user_data['sender'])
        user.service_type = service_type
        
        # Rest of your existing service type handling logic...
        # [Keep all your existing if/elif branches here]
        
    except Exception as e:
        logging.error(f"Error in handle_select_service_type: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'select_service_type'}  # Don't reset to welcome on error


def handle_select_chatbot_service(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        
        # Find the selected chatbot service
        selected_service = None
        for service in ChatbotService:
            if prompt.lower() in service.value.lower() or prompt == str(list(ChatbotService).index(service)+1):
                selected_service = service
                break
                
        if not selected_service:
            send_message("Invalid selection. Please choose a chatbot service from the list.", 
                         user_data['sender'], phone_id)
            return {
                'step': 'select_chatbot_service',
                'user': user.to_dict()
            }
            
        user.chatbot_service = selected_service
        
        if selected_service == ChatbotService.APPOINTMENT:
            send_message("Please enter the type of appointment (e.g., 'Salon booking', 'Doctor visit'):", 
                         user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {
                'step': 'get_appointment_type',
                'user': user.to_dict()
            })
            return {
                'step': 'get_appointment_type',
                'user': user.to_dict()
            }
                
        elif selected_service == ChatbotService.SALES_ORDER:
            send_message("Please describe the products or services you want to sell:", 
                         user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {
                'step': 'get_order_details',
                'user': user.to_dict()
            })
            return {
                'step': 'get_order_details',
                'user': user.to_dict()
            }
                
        elif selected_service == ChatbotService.LOAN_MGMT:
            send_message("Please specify the type of loan management needed (e.g., 'Microfinance', 'Bank loans'):", 
                         user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {
                'step': 'get_loan_details',
                'user': user.to_dict()
            })
            return {
                'step': 'get_loan_details',
                'user': user.to_dict()
            }
                
        else:
            send_message(f"Please describe your requirements for {selected_service.value}:", 
                         user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {
                'step': 'get_chatbot_details',
                'user': user.to_dict()
            })
            return {
                'step': 'get_chatbot_details',
                'user': user.to_dict()
            }
            
    except Exception as e:
        logging.error(f"Error in handle_select_chatbot_service: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_select_app_type(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        
        # Find the selected app type
        selected_type = None
        for app_type in MobileAppType:
            if prompt.lower() in app_type.value.lower() or prompt == str(list(MobileAppType).index(app_type)+1):
                selected_type = app_type
                break
                
        if not selected_type:
            send_message("Invalid selection. Please choose an app type from the list.", 
                         user_data['sender'], phone_id)
            return {
                'step': 'select_app_type',
                'user': user.to_dict()
            }
            
        user.mobile_app_type = selected_type
        send_message(f"Please describe your {selected_type.value} requirements:", 
                     user_data['sender'], phone_id)
        update_user_state(user_data['sender'], {
            'step': 'get_app_requirements',
            'user': user.to_dict()
        })
        return {
            'step': 'get_app_requirements',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_select_app_type: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_domain_query(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        user.domain_query = prompt
        
        # Check domain availability (mock implementation)
        domain_available = random.choice([True, False])
        
        if domain_available:
            message = (
                f"🎉 Great news! {prompt} is available!\n\n"
                "Would you like to:\n"
                "1. Register this domain now\n"
                "2. Transfer to a sales agent\n"
                "3. Check another domain"
            )
        else:
            message = (
                f"😞 {prompt} is already taken. Would you like to:\n"
                "1. Check similar available domains\n"
                "2. Transfer to a sales agent\n"
                "3. Check another domain"
            )
            
        send_button_message(
            message,
            ["Register", "Transfer to Agent", "Check Another"],
            user_data['sender'],
            phone_id
        )
        
        update_user_state(user_data['sender'], {
            'step': 'handle_domain_response',
            'user': user.to_dict()
        })
        return {
            'step': 'handle_domain_response',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_domain_query: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_other_request(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        user.other_request = prompt
        
        # Transfer to human agent
        send_message_to_agent(user)
        
        send_message(
            "Thank you! Your request has been forwarded to our team. "
            "An agent will contact you shortly. Would you like to request another service? (yes/no)",
            user_data['sender'],
            phone_id
        )
        
        update_user_state(user_data['sender'], {
            'step': 'ask_another_service',
            'user': user.to_dict()
        })
        return {
            'step': 'ask_another_service',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_other_request: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_appointment_type(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        user.appointment_details['type'] = prompt
        
        send_message("Please enter preferred date and time (e.g., 'June 15 at 2pm'):", 
                     user_data['sender'], phone_id)
        update_user_state(user_data['sender'], {
            'step': 'get_appointment_time',
            'user': user.to_dict()
        })
        return {
            'step': 'get_appointment_time',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_appointment_type: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_appointment_time(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        user.appointment_details['time'] = prompt
        
        send_message("Please enter any additional notes for the appointment:", 
                     user_data['sender'], phone_id)
        update_user_state(user_data['sender'], {
            'step': 'get_appointment_notes',
            'user': user.to_dict()
        })
        return {
            'step': 'get_appointment_notes',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_appointment_time: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_appointment_notes(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        user.appointment_details['notes'] = prompt
        
        # Confirm appointment details
        confirm_msg = (
            "Please confirm your appointment booking:\n\n"
            f"Type: {user.appointment_details['type']}\n"
            f"Time: {user.appointment_details['time']}\n"
            f"Notes: {user.appointment_details.get('notes', 'None')}\n\n"
            "Is this correct? (yes/no)"
        )
        
        send_message(confirm_msg, user_data['sender'], phone_id)
        update_user_state(user_data['sender'], {
            'step': 'confirm_appointment',
            'user': user.to_dict()
        })
        return {
            'step': 'confirm_appointment',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_appointment_notes: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_confirm_appointment(prompt, user_data, phone_id):
    try:
        if prompt.lower() in ['yes', 'y']:
            user = User.from_dict(user_data['user'])
            
            # Generate appointment ID
            appointment_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            
            # Send confirmation to user
            confirm_msg = (
                "🎉 Your appointment has been booked!\n\n"
                f"Appointment ID: {appointment_id}\n"
                f"Type: {user.appointment_details['type']}\n"
                f"Time: {user.appointment_details['time']}\n\n"
                "An agent will contact you shortly to confirm details. "
                "Would you like to request another service? (yes/no)"
            )
            
            send_message(confirm_msg, user_data['sender'], phone_id)
            
            # Notify admin
            admin_msg = (
                "New Appointment Booking\n\n"
                f"Client: {user.name} ({user.phone})\n"
                f"Appointment ID: {appointment_id}\n"
                f"Type: {user.appointment_details['type']}\n"
                f"Time: {user.appointment_details['time']}\n"
                f"Notes: {user.appointment_details.get('notes', 'None')}"
            )
            send_message(admin_msg, owner_phone, phone_id)
            
            update_user_state(user_data['sender'], {
                'step': 'ask_another_service',
                'user': user.to_dict()
            })
            return {
                'step': 'ask_another_service',
                'user': user.to_dict()
            }
            
        else:
            send_message("Let's try again. Please enter the appointment type:", 
                         user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {
                'step': 'get_appointment_type',
                'user': user_data['user']
            })
            return {
                'step': 'get_appointment_type',
                'user': user_data['user']
            }
            
    except Exception as e:
        logging.error(f"Error in handle_confirm_appointment: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_order_details(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        user.order_details['description'] = prompt
        
        send_message("Please enter the estimated number of products:", 
                     user_data['sender'], phone_id)
        update_user_state(user_data['sender'], {
            'step': 'get_order_quantity',
            'user': user.to_dict()
        })
        return {
            'step': 'get_order_quantity',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_order_details: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_order_quantity(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        user.order_details['quantity'] = prompt
        
        send_message("Do you need payment integration? (yes/no)", 
                     user_data['sender'], phone_id)
        update_user_state(user_data['sender'], {
            'step': 'get_payment_integration',
            'user': user.to_dict()
        })
        return {
            'step': 'get_payment_integration',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_order_quantity: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_payment_integration(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        user.order_details['payment_integration'] = prompt.lower() in ['yes', 'y']
        
        # Confirm order details
        confirm_msg = (
            "Please confirm your order processing requirements:\n\n"
            f"Products: {user.order_details['description']}\n"
            f"Quantity: {user.order_details['quantity']}\n"
            f"Payment Integration: {'Yes' if user.order_details['payment_integration'] else 'No'}\n\n"
            "Is this correct? (yes/no)"
        )
        
        send_message(confirm_msg, user_data['sender'], phone_id)
        update_user_state(user_data['sender'], {
            'step': 'confirm_order_details',
            'user': user.to_dict()
        })
        return {
            'step': 'confirm_order_details',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_payment_integration: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_confirm_order_details(prompt, user_data, phone_id):
    try:
        if prompt.lower() in ['yes', 'y']:
            user = User.from_dict(user_data['user'])
            
            # Generate order ID
            order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            
            # Send confirmation to user
            confirm_msg = (
                "🎉 Your order processing request has been received!\n\n"
                f"Order ID: {order_id}\n"
                f"Products: {user.order_details['description']}\n"
                f"Quantity: {user.order_details['quantity']}\n"
                f"Payment Integration: {'Yes' if user.order_details['payment_integration'] else 'No'}\n\n"
                "An agent will contact you shortly to discuss next steps. "
                "Would you like to request another service? (yes/no)"
            )
            
            send_message(confirm_msg, user_data['sender'], phone_id)
            
            # Notify admin
            admin_msg = (
                "New Order Processing Request\n\n"
                f"Client: {user.name} ({user.phone})\n"
                f"Order ID: {order_id}\n"
                f"Products: {user.order_details['description']}\n"
                f"Quantity: {user.order_details['quantity']}\n"
                f"Payment Integration: {'Yes' if user.order_details['payment_integration'] else 'No'}"
            )
            send_message(admin_msg, owner_phone, phone_id)
            
            update_user_state(user_data['sender'], {
                'step': 'ask_another_service',
                'user': user.to_dict()
            })
            return {
                'step': 'ask_another_service',
                'user': user.to_dict()
            }
            
        else:
            send_message("Let's try again. Please describe the products or services you want to sell:", 
                         user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {
                'step': 'get_order_details',
                'user': user_data['user']
            })
            return {
                'step': 'get_order_details',
                'user': user_data['user']
            }
            
    except Exception as e:
        logging.error(f"Error in handle_confirm_order_details: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_loan_details(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        user.loan_details['type'] = prompt
        
        send_message("Please enter the estimated loan amount range (e.g., '$1000-$5000'):", 
                     user_data['sender'], phone_id)
        update_user_state(user_data['sender'], {
            'step': 'get_loan_amount',
            'user': user.to_dict()
        })
        return {
            'step': 'get_loan_amount',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_loan_details: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_loan_amount(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        user.loan_details['amount'] = prompt
        
        send_message("Do you need automated payment reminders? (yes/no)", 
                     user_data['sender'], phone_id)
        update_user_state(user_data['sender'], {
            'step': 'get_reminder_preference',
            'user': user.to_dict()
        })
        return {
            'step': 'get_reminder_preference',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_loan_amount: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_reminder_preference(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        user.loan_details['reminders'] = prompt.lower() in ['yes', 'y']
        
        # Confirm loan details
        confirm_msg = (
            "Please confirm your loan management requirements:\n\n"
            f"Loan Type: {user.loan_details['type']}\n"
            f"Amount Range: {user.loan_details['amount']}\n"
            f"Payment Reminders: {'Yes' if user.loan_details['reminders'] else 'No'}\n\n"
            "Is this correct? (yes/no)"
        )
        
        send_message(confirm_msg, user_data['sender'], phone_id)
        update_user_state(user_data['sender'], {
            'step': 'confirm_loan_details',
            'user': user.to_dict()
        })
        return {
            'step': 'confirm_loan_details',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_reminder_preference: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_confirm_loan_details(prompt, user_data, phone_id):
    try:
        if prompt.lower() in ['yes', 'y']:
            user = User.from_dict(user_data['user'])
            
            # Generate loan request ID
            request_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            
            # Send confirmation to user
            confirm_msg = (
                "🎉 Your loan management request has been received!\n\n"
                f"Request ID: {request_id}\n"
                f"Loan Type: {user.loan_details['type']}\n"
                f"Amount Range: {user.loan_details['amount']}\n"
                f"Payment Reminders: {'Yes' if user.loan_details['reminders'] else 'No'}\n\n"
                "An agent will contact you shortly to discuss next steps. "
                "Would you like to request another service? (yes/no)"
            )
            
            send_message(confirm_msg, user_data['sender'], phone_id)
            
            # Notify admin
            admin_msg = (
                "New Loan Management Request\n\n"
                f"Client: {user.name} ({user.phone})\n"
                f"Request ID: {request_id}\n"
                f"Loan Type: {user.loan_details['type']}\n"
                f"Amount Range: {user.loan_details['amount']}\n"
                f"Payment Reminders: {'Yes' if user.loan_details['reminders'] else 'No'}"
            )
            send_message(admin_msg, owner_phone, phone_id)
            
            update_user_state(user_data['sender'], {
                'step': 'ask_another_service',
                'user': user.to_dict()
            })
            return {
                'step': 'ask_another_service',
                'user': user.to_dict()
            }
            
        else:
            send_message("Let's try again. Please specify the type of loan management needed:", 
                         user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {
                'step': 'get_loan_details',
                'user': user_data['user']
            })
            return {
                'step': 'get_loan_details',
                'user': user_data['user']
            }
            
    except Exception as e:
        logging.error(f"Error in handle_confirm_loan_details: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_chatbot_details(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        service_type = user.chatbot_service.value
        
        # Transfer to human agent with all details
        agent_msg = (
            f"New {service_type} Request\n\n"
            f"Client: {user.name} ({user.phone})\n"
            f"Requirements: {prompt}"
        )
        send_message(agent_msg, owner_phone, phone_id)
        
        send_message(
            f"Thank you for your {service_type} request! An agent will contact you shortly. "
            "Would you like to request another service? (yes/no)",
            user_data['sender'],
            phone_id
        )
        
        update_user_state(user_data['sender'], {
            'step': 'ask_another_service',
            'user': user.to_dict()
        })
        return {
            'step': 'ask_another_service',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_chatbot_details: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_app_requirements(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        app_type = user.mobile_app_type.value
        
        # Transfer to human agent with all details
        agent_msg = (
            f"New {app_type} Development Request\n\n"
            f"Client: {user.name} ({user.phone})\n"
            f"Requirements: {prompt}"
        )
        send_message(agent_msg, owner_phone, phone_id)
        
        send_message(
            f"Thank you for your {app_type} development request! An agent will contact you shortly. "
            "Would you like to request another service? (yes/no)",
            user_data['sender'],
            phone_id
        )
        
        update_user_state(user_data['sender'], {
            'step': 'ask_another_service',
            'user': user.to_dict()
        })
        return {
            'step': 'ask_another_service',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_app_requirements: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_domain_response(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        
        if prompt.lower() in ['1', 'register']:
            send_message(
                "Please provide your email address to complete domain registration:",
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {
                'step': 'get_domain_email',
                'user': user.to_dict()
            })
            return {
                'step': 'get_domain_email',
                'user': user.to_dict()
            }
            
        elif prompt.lower() in ['2', 'transfer']:
            send_message_to_agent(user)
            send_message(
                "A sales agent will contact you shortly about your domain query. "
                "Would you like to request another service? (yes/no)",
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {
                'step': 'ask_another_service',
                'user': user.to_dict()
            })
            return {
                'step': 'ask_another_service',
                'user': user.to_dict()
            }
            
        elif prompt.lower() in ['3', 'check another']:
            send_message(
                "Please enter another domain name to check (e.g., mybusiness.com):",
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {
                'step': 'get_domain_query',
                'user': user.to_dict()
            })
            return {
                'step': 'get_domain_query',
                'user': user.to_dict()
            }
            
        else:
            send_message("Invalid option. Please choose 1-3.", user_data['sender'], phone_id)
            return {
                'step': 'handle_domain_response',
                'user': user.to_dict()
            }
            
    except Exception as e:
        logging.error(f"Error in handle_domain_response: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_get_domain_email(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        user.domain_query = prompt  # Using domain_query to store email in this case
        
        # Generate registration ID
        reg_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        
        # Send confirmation
        send_message(
            f"Thank you! Your domain registration request (ID: {reg_id}) has been received. "
            "An agent will contact you shortly to complete the process. "
            "Would you like to request another service? (yes/no)",
            user_data['sender'],
            phone_id
        )
        
        # Notify admin
        admin_msg = (
            f"New Domain Registration Request\n\n"
            f"Client: {user.name} ({user.phone})\n"
            f"Request ID: {reg_id}\n"
            f"Domain: {user.domain_query}\n"
            f"Email: {prompt}"
        )
        send_message(admin_msg, owner_phone, phone_id)
        
        update_user_state(user_data['sender'], {
            'step': 'ask_another_service',
            'user': user.to_dict()
        })
        return {
            'step': 'ask_another_service',
            'user': user.to_dict()
        }
        
    except Exception as e:
        logging.error(f"Error in handle_get_domain_email: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_ask_another_service(prompt, user_data, phone_id):
    if prompt.lower() in ['yes', 'y']:
        update_user_state(user_data['sender'], {'step': 'welcome'})
        return handle_welcome("", {'sender': user_data['sender']}, phone_id)
    else:
        send_message(
            "Thank you for using Contessasoft Services! We'll be in touch soon. "
            "Type 'hi' anytime if you need assistance.",
            user_data['sender'],
            phone_id
        )
        update_user_state(user_data['sender'], {'step': 'welcome'})
        return {'step': 'welcome'}

def send_message_to_agent(user, phone_id):
    try:
        # Format the message to send to agent
        message = f"New client request from {user.name} ({user.phone}):\n\n"
        
        if user.service_type:
            message += f"Service Type: {user.service_type.value}\n"
            
            if user.service_type == ServiceType.CHATBOTS and user.chatbot_service:
                message += f"Chatbot Service: {user.chatbot_service.value}\n"
                
                if user.chatbot_service == ChatbotService.APPOINTMENT and user.appointment_details:
                    message += f"Appointment Details: {user.appointment_details}\n"
                elif user.chatbot_service == ChatbotService.SALES_ORDER and user.order_details:
                    message += f"Order Details: {user.order_details}\n"
                elif user.chatbot_service == ChatbotService.LOAN_MGMT and user.loan_details:
                    message += f"Loan Details: {user.loan_details}\n"
                    
            elif user.service_type == ServiceType.MOBILE_APP_DEV and user.mobile_app_type:
                message += f"App Type: {user.mobile_app_type.value}\n"
                
            elif user.service_type == ServiceType.DOMAIN_HOSTING and user.domain_query:
                message += f"Domain Query: {user.domain_query}\n"
                
            elif user.service_type == ServiceType.OTHER and user.other_request:
                message += f"Request: {user.other_request}\n"
        
        # Send to agent
        send_message(message, owner_phone, phone_id)
        
    except Exception as e:
        logging.error(f"Error sending message to agent: {e}")

# Action mapping
action_mapping = {
    "welcome": handle_welcome,
    "select_service_type": handle_select_service_type,
    "select_chatbot_service": handle_select_chatbot_service,
    "select_app_type": handle_select_app_type,
    "get_domain_query": handle_get_domain_query,
    "get_other_request": handle_get_other_request,
    "get_appointment_type": handle_get_appointment_type,
    "get_appointment_time": handle_get_appointment_time,
    "get_appointment_notes": handle_get_appointment_notes,
    "confirm_appointment": handle_confirm_appointment,
    "get_order_details": handle_get_order_details,
    "get_order_quantity": handle_get_order_quantity,
    "get_payment_integration": handle_get_payment_integration,
    "confirm_order_details": handle_confirm_order_details,
    "get_loan_details": handle_get_loan_details,
    "get_loan_amount": handle_get_loan_amount,
    "get_reminder_preference": handle_get_reminder_preference,
    "confirm_loan_details": handle_confirm_loan_details,
    "get_chatbot_details": handle_get_chatbot_details,
    "get_app_requirements": handle_get_app_requirements,
    "handle_domain_response": handle_domain_response,
    "get_domain_email": handle_get_domain_email,
    "ask_another_service": handle_ask_another_service
}

def get_action(current_state, prompt, user_data, phone_id):
    handler = action_mapping.get(current_state, handle_welcome)
    return handler(prompt, user_data, phone_id)

# Message handler
def message_handler(prompt, sender, phone_id):
    # Clean and normalize the prompt
    clean_prompt = prompt.lower().strip()
    clean_prompt = clean_prompt.replace("📱", "").replace("🌐", "").replace("✨", "").replace("🤖", "").strip()
    
    # Handle session reset commands
    if clean_prompt in ["hi", "hello", "hey", "start", "menu"]:
        user_state = {'step': 'welcome', 'sender': sender}
        update_user_state(sender, user_state)
        return get_action('welcome', "", user_state, phone_id)
    
    # Get current user state
    user_state = get_user_state(sender)
    user_state['sender'] = sender  # Ensure sender is always set
    
    # Map button responses to consistent values
    button_mappings = {
        "app development": "app",
        "domain hosting": "domain",
        "other services": "other",
        "register": "register",
        "transfer to agent": "transfer",
        "check another": "check",
        # Add other button mappings as needed
    }
    
    # Check if prompt matches any button text
    processed_prompt = clean_prompt
    for button_text, mapped_value in button_mappings.items():
        if button_text in clean_prompt:
            processed_prompt = mapped_value
            break
    
    # Get current step and process action
    step = user_state.get('step', 'welcome')
    return get_action(step, processed_prompt, user_state, phone_id)


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
            entry = data["entry"][0]
            changes = entry["changes"][0]
            value = changes["value"]
            phone_id = value["metadata"]["phone_number_id"]

            messages = value.get("messages", [])
            if messages:
                message = messages[0]
                sender = message["from"]

                # Handle button replies
                if message.get("type") == "interactive" and message["interactive"].get("type") == "button_reply":
                    button_response = message["interactive"]["button_reply"]["title"]
                    message_handler(button_response, sender, phone_id)
                    return jsonify({"status": "ok"}), 200
                
                # Handle list replies
                elif message.get("type") == "interactive" and message["interactive"].get("type") == "list_reply":
                    list_response = message["interactive"]["list_reply"]["title"]
                    message_handler(list_response, sender, phone_id)
                    return jsonify({"status": "ok"}), 200
                
                # Handle regular text messages
                elif "text" in message:
                    prompt = message["text"]["body"].strip()
                    message_handler(prompt, sender, phone_id)
                    return jsonify({"status": "ok"}), 200
                
                else:
                    send_message("Please send a text message or select an option", sender, phone_id)
                    return jsonify({"status": "ok"}), 200

        except Exception as e:
            logging.error(f"Error processing webhook: {e}", exc_info=True)
            return jsonify({"status": "error"}), 500

        return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(debug=True, port=8000)
