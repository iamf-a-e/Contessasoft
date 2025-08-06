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
    CHATBOTS = "🤖 Chatbots"
    DOMAIN_HOSTING = "🌐 Domain & Hosting"
    WEBSITE_DEV = "💻 Website Development"
    MOBILE_APP_DEV = "📱 Mobile App Development"
    OTHER = "✨ Other Services"

class ChatbotService(Enum):
    APPOINTMENT = "📅 Appointment Bookings"
    SALES_ORDER = "🛒 Sales & Order Processing"
    LOAN_MGMT = "💰 Loan Management"
    SURVEYS = "📊 Customer Surveys"
    PROPERTY = "🏠 Property Inquiries"
    AI_SUPPORT = "🤖 AI Chat Support"
    UTILITY = "💡 Utility Payments"
    TICKETING = "🎟️ Event Ticketing"
    ECOMMERCE = "🛍️ E-commerce Bots"
    HR = "👥 HR & Recruitment"
    TRAVEL = "✈️ Travel Booking"
    VOTING = "🗳️ Voting & Polling"
    COMPLAINT = "📝 Complaint Management"
    EDUCATION = "🎓 Educational Bots"
    RESTAURANT = "🍽️ Restaurant Ordering"

class MobileAppType(Enum):
    IOS = "🍏 iOS App"
    ANDROID = "🤖 Android App"
    HYBRID = "📱 Hybrid (iOS & Android)"
    GAME = "🎮 Mobile Game"
    ENTERPRISE = "🏢 Enterprise App"
    OTHER = "❓ Other App Type"

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
        "🌟 *Welcome to Contessasoft Services!* 🌟\n\n"
        "We offer a wide range of digital solutions. Please select a service type:"
    )
    
    service_options = [service.value for service in ServiceType]
    send_list_message(
        welcome_msg,
        service_options,
        user_data['sender'],
        phone_id
    )
    
    update_user_state(user_data['sender'], {'step': 'select_service_type'})
    return {'step': 'select_service_type'}

def handle_select_service_type(prompt, user_data, phone_id):
    try:
        # Find the selected service type
        selected_service = None
        for service in ServiceType:
            if prompt.lower() in service.value.lower():
                selected_service = service
                break
                
        if not selected_service:
            send_message("Invalid selection. Please choose a service from the list.", user_data['sender'], phone_id)
            return {'step': 'select_service_type'}
        
        user = User(user_data.get('name', 'User'), user_data['sender'])
        user.service_type = selected_service
        
        if selected_service == ServiceType.CHATBOTS:
            chatbot_options = [service.value for service in ChatbotService]
            send_list_message(
                "Select a chatbot service:",
                chatbot_options,
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {
                'step': 'select_chatbot_service',
                'user': user.to_dict()
            })
            return {
                'step': 'select_chatbot_service',
                'user': user.to_dict()
            }
            
        elif selected_service == ServiceType.MOBILE_APP_DEV:
            app_types = [app_type.value for app_type in MobileAppType]
            send_list_message(
                "What type of mobile app do you need?",
                app_types,
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {
                'step': 'select_app_type',
                'user': user.to_dict()
            })
            return {
                'step': 'select_app_type',
                'user': user.to_dict()
            }
            
        elif selected_service == ServiceType.DOMAIN_HOSTING:
            send_message("Please enter the domain name you're interested in (e.g., mybusiness.com):", 
                        user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {
                'step': 'get_domain_query',
                'user': user.to_dict()
            })
            return {
                'step': 'get_domain_query',
                'user': user.to_dict()
            }
            
        else:
            send_message("Please describe your requirements:", user_data['sender'], phone_id)
            update_user_state(user_data['sender'], {
                'step': 'get_other_request',
                'user': user.to_dict()
            })
            return {
                'step': 'get_other_request',
                'user': user.to_dict()
            }
            
    except Exception as e:
        logging.error(f"Error in handle_select_service_type: {e}")
        send_message("An error occurred. Please try again.", user_data['sender'], phone_id)
        return {'step': 'welcome'}

def handle_select_chatbot_service(prompt, user_data, phone_id):
    try:
        user = User.from_dict(user_data['user'])
        
        # Find the selected chatbot service
        selected_service = None
        for service in ChatbotService:
            if prompt.lower() in service.value.lower():
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
            appointment_types = [
                "💇 Salon Booking",
                "🏥 Doctor Visit",
                "🍽️ Restaurant Reservation",
                "✂️ Barber Appointment",
                "👔 Business Meeting"
            ]
            send_list_message(
                "Select appointment type:",
                appointment_types,
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {
                'step': 'get_appointment_type',
                'user': user.to_dict()
            })
            return {
                'step': 'get_appointment_type',
                'user': user.to_dict()
            }
                
        elif selected_service == ChatbotService.SALES_ORDER:
            product_categories = [
                "🛍️ Retail Products",
                "🍽️ Food & Beverage",
                "📱 Electronics",
                "👕 Fashion & Apparel",
                "🏠 Home & Garden"
            ]
            send_list_message(
                "Select your product category:",
                product_categories,
                user_data['sender'],
                phone_id
            )
            update_user_state(user_data['sender'], {
                'step': 'get_order_details',
                'user': user.to_dict()
            })
            return {
                'step': 'get_order_details',
                'user': user.to_dict()
            }
                
        elif selected_service == ChatbotService.LOAN_MGMT:
            loan_types = [
                "💰 Microfinance",
                "🏦 Bank Loans",
                "🏠 Mortgage",
                "🚗 Vehicle Loan",
                "🎓 Education Loan"
            ]
            send_list_message(
                "Select loan type:",
                loan_types,
                user_data['sender'],
                phone_id
            )
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
            if prompt.lower() in app_type.value.lower():
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
        
        features = [
            "📱 Basic App",
            "🛒 E-commerce",
            "🗺️ Location Services",
            "💳 Payment Gateway",
            "📊 Analytics Dashboard"
        ]
        send_list_message(
            f"Select features for your {selected_type.value}:",
            features,
            user_data['sender'],
            phone_id
        )
        
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
            message = f"🎉 Great news! {prompt} is available!"
            buttons = ["✅ Register Now", "👨‍💼 Talk to Agent", "🔍 Check Another"]
        else:
            message = f"😞 {prompt} is already taken."
            buttons = ["🔍 Similar Domains", "👨‍💼 Talk to Agent", "🔄 Check Another"]
            
        send_button_message(
            message,
            buttons,
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
        send_message_to_agent(user, phone_id)
        
        send_button_message(
            "Thank you! Your request has been forwarded to our team. Would you like to request another service?",
            ["✅ Yes", "❌ No"],
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
        
        time_slots = [
            "🕘 9:00 AM - 11:00 AM",
            "🕛 11:00 AM - 1:00 PM",
            "🕑 2:00 PM - 4:00 PM",
            "🕔 4:00 PM - 6:00 PM",
            "🕗 6:00 PM - 8:00 PM"
        ]
        send_list_message(
            "Select preferred time slot:",
            time_slots,
            user_data['sender'],
            phone_id
        )
        
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
        
        notes_options = [
            "None",
            "Wheelchair Access Needed",
            "Prefer Female Specialist",
            "Bring Documents",
            "Special Dietary Requirements"
        ]
        send_list_message(
            "Select any additional notes:",
            notes_options,
            user_data['sender'],
            phone_id
        )
        
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
        
        confirm_msg = (
            "📅 *Appointment Summary*\n\n"
            f"Type: {user.appointment_details['type']}\n"
            f"Time: {user.appointment_details['time']}\n"
            f"Notes: {user.appointment_details.get('notes', 'None')}"
        )
        
        send_button_message(
            confirm_msg,
            ["✅ Confirm Booking", "✏️ Edit Details"],
            user_data['sender'],
            phone_id
        )
        
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
        if "confirm" in prompt.lower():
            user = User.from_dict(user_data['user'])
            
            # Generate appointment ID
            appointment_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            
            # Send confirmation to user
            confirm_msg = (
                "🎉 *Appointment Confirmed!*\n\n"
                f"📋 ID: {appointment_id}\n"
                f"📅 Type: {user.appointment_details['type']}\n"
                f"🕒 Time: {user.appointment_details['time']}\n\n"
                "An agent will contact you shortly to confirm details."
            )
            
            send_button_message(
                confirm_msg,
                ["✅ Request Another Service", "❌ Done"],
                user_data['sender'],
                phone_id
            )
            
            # Notify admin
            admin_msg = (
                "📋 *New Appointment Booking*\n\n"
                f"👤 Client: {user.name} ({user.phone})\n"
                f"📋 ID: {appointment_id}\n"
                f"📅 Type: {user.appointment_details['type']}\n"
                f"🕒 Time: {user.appointment_details['time']}\n"
                f"📝 Notes: {user.appointment_details.get('notes', 'None')}"
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
            send_message("Let's start over. Select appointment type:", 
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
        user.order_details['category'] = prompt
        
        quantity_options = [
            "1-10 products",
            "11-50 products",
            "51-100 products",
            "100+ products"
        ]
        send_list_message(
            "Select estimated product quantity:",
            quantity_options,
            user_data['sender'],
            phone_id
        )
        
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
        
        send_button_message(
            "Do you need payment integration?",
            ["💳 Yes, with Payment", "🚫 No Payment Needed"],
            user_data['sender'],
            phone_id
        )
        
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
        user.order_details['payment_integration'] = "yes" in prompt.lower()
        
        confirm_msg = (
            "🛒 *Order Summary*\n\n"
            f"📦 Category: {user.order_details['category']}\n"
            f"🔢 Quantity: {user.order_details['quantity']}\n"
            f"💳 Payment: {'Yes' if user.order_details['payment_integration'] else 'No'}"
        )
        
        send_button_message(
            confirm_msg,
            ["✅ Confirm Order", "✏️ Edit Details"],
            user_data['sender'],
            phone_id
        )
        
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
        if "confirm" in prompt.lower():
            user = User.from_dict(user_data['user'])
            
            # Generate order ID
            order_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            
            # Send confirmation to user
            confirm_msg = (
                "🎉 *Order Request Received!*\n\n"
                f"📋 ID: {order_id}\n"
                f"📦 Category: {user.order_details['category']}\n"
                f"🔢 Quantity: {user.order_details['quantity']}\n"
                f"💳 Payment: {'Yes' if user.order_details['payment_integration'] else 'No'}\n\n"
                "An agent will contact you shortly."
            )
            
            send_button_message(
                confirm_msg,
                ["✅ Request Another Service", "❌ Done"],
                user_data['sender'],
                phone_id
            )
            
            # Notify admin
            admin_msg = (
                "🛒 *New Order Request*\n\n"
                f"👤 Client: {user.name} ({user.phone})\n"
                f"📋 ID: {order_id}\n"
                f"📦 Category: {user.order_details['category']}\n"
                f"🔢 Quantity: {user.order_details['quantity']}\n"
                f"💳 Payment: {'Yes' if user.order_details['payment_integration'] else 'No'}"
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
            send_message("Let's start over. Select product category:", 
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
        
        amount_ranges = [
            "$1,000 - $5,000",
            "$5,001 - $10,000",
            "$10,001 - $50,000",
            "$50,000+"
        ]
        send_list_message(
            "Select loan amount range:",
            amount_ranges,
            user_data['sender'],
            phone_id
        )
        
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
        
        send_button_message(
            "Do you need automated payment reminders?",
            ["🔔 Yes, Send Reminders", "🚫 No Reminders Needed"],
            user_data['sender'],
            phone_id
        )
        
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
        user.loan_details['reminders'] = "yes" in prompt.lower()
        
        confirm_msg = (
            "💰 *Loan Summary*\n\n"
            f"🏦 Type: {user.loan_details['type']}\n"
            f"💵 Amount: {user.loan_details['amount']}\n"
            f"🔔 Reminders: {'Yes' if user.loan_details['reminders'] else 'No'}"
        )
        
        send_button_message(
            confirm_msg,
            ["✅ Confirm Loan Request", "✏️ Edit Details"],
            user_data['sender'],
            phone_id
        )
        
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
        if "confirm" in prompt.lower():
            user = User.from_dict(user_data['user'])
            
            # Generate loan request ID
            request_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            
            # Send confirmation to user
            confirm_msg = (
                "🎉 *Loan Request Received!*\n\n"
                f"📋 ID: {request_id}\n"
                f"🏦 Type: {user.loan_details['type']}\n"
                f"💵 Amount: {user.loan_details['amount']}\n"
                f"🔔 Reminders: {'Yes' if user.loan_details['reminders'] else 'No'}\n\n"
                "An agent will contact you shortly."
            )
            
            send_button_message(
                confirm_msg,
                ["✅ Request Another Service", "❌ Done"],
                user_data['sender'],
                phone_id
            )
            
            # Notify admin
            admin_msg = (
                "💰 *New Loan Request*\n\n"
                f"👤 Client: {user.name} ({user.phone})\n"
                f"📋 ID: {request_id}\n"
                f"🏦 Type: {user.loan_details['type']}\n"
                f"💵 Amount: {user.loan_details['amount']}\n"
                f"🔔 Reminders: {'Yes' if user.loan_details['reminders'] else 'No'}"
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
            send_message("Let's start over. Select loan type:", 
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
            f"🤖 *New {service_type} Request*\n\n"
            f"👤 Client: {user.name} ({user.phone})\n"
            f"📝 Requirements: {prompt}"
        )
        send_message(agent_msg, owner_phone, phone_id)
        
        send_button_message(
            f"Thank you for your {service_type} request! Would you like to request another service?",
            ["✅ Yes", "❌ No"],
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
            f"📱 *New {app_type} Request*\n\n"
            f"👤 Client: {user.name} ({user.phone})\n"
            f"📝 Requirements: {prompt}"
        )
        send_message(agent_msg, owner_phone, phone_id)
        
        send_button_message(
            f"Thank you for your {app_type} request! Would you like to request another service?",
            ["✅ Yes", "❌ No"],
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
        
        if "register" in prompt.lower():
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
            
        elif "agent" in prompt.lower():
            send_message_to_agent(user, phone_id)
            send_button_message(
                "A sales agent will contact you shortly. Would you like to request another service?",
                ["✅ Yes", "❌ No"],
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
            
        elif "another" in prompt.lower() or "check" in prompt.lower():
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
            send_message("Invalid option. Please choose from the buttons.", user_data['sender'], phone_id)
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
        send_button_message(
            f"Thank you! Your domain registration request (ID: {reg_id}) has been received. Would you like to request another service?",
            ["✅ Yes", "❌ No"],
            user_data['sender'],
            phone_id
        )
        
        # Notify admin
        admin_msg = (
            f"🌐 *New Domain Registration*\n\n"
            f"👤 Client: {user.name} ({user.phone})\n"
            f"📋 ID: {reg_id}\n"
            f"🌍 Domain: {user.domain_query}\n"
            f"📧 Email: {prompt}"
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
    if "yes" in prompt.lower():
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
        message = f"👤 *New Client Request*\n\nFrom: {user.name} ({user.phone})\n\n"
        
        if user.service_type:
            message += f"📋 Service: {user.service_type.value}\n"
            
            if user.service_type == ServiceType.CHATBOTS and user.chatbot_service:
                message += f"🤖 Chatbot Type: {user.chatbot_service.value}\n"
                
                if user.chatbot_service == ChatbotService.APPOINTMENT and user.appointment_details:
                    message += f"📅 Appointment: {user.appointment_details}\n"
                elif user.chatbot_service == ChatbotService.SALES_ORDER and user.order_details:
                    message += f"🛒 Order Details: {user.order_details}\n"
                elif user.chatbot_service == ChatbotService.LOAN_MGMT and user.loan_details:
                    message += f"💰 Loan Details: {user.loan_details}\n"
                    
            elif user.service_type == ServiceType.MOBILE_APP_DEV and user.mobile_app_type:
                message += f"📱 App Type: {user.mobile_app_type.value}\n"
                
            elif user.service_type == ServiceType.DOMAIN_HOSTING and user.domain_query:
                message += f"🌍 Domain: {user.domain_query}\n"
                
            elif user.service_type == ServiceType.OTHER and user.other_request:
                message += f"📝 Request: {user.other_request}\n"
        
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
    text = prompt.strip().lower()

    if text in ["hi", "hello", "hey", "start"]:
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
            entry = data["entry"][0]
            changes = entry["changes"][0]
            value = changes["value"]
            phone_id = value["metadata"]["phone_number_id"]

            messages = value.get("messages", [])
            if messages:
                message = messages[0]
                sender = message["from"]

                if "text" in message:
                    prompt = message["text"]["body"].strip()
                    message_handler(prompt, sender, phone_id)
                elif "button" in message:
                    button_response = message["button"]["text"]
                    message_handler(button_response, sender, phone_id)
                elif "interactive" in message and message["interactive"]["type"] == "list_reply":
                    list_response = message["interactive"]["list_reply"]["title"]
                    message_handler(list_response, sender, phone_id)
                else:
                    send_message("Please select an option from the buttons", sender, phone_id)
        except Exception as e:
            logging.error(f"Error processing webhook: {e}", exc_info=True)

        return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(debug=True, port=8000)
