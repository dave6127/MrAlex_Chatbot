import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, LoginManager, login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from google.genai import types
from PIL import Image
import io
import base64
import mistune
import secrets # <--- ለደህንነት ሲባል SECRET_KEYን በዘፈቀደ ለመፍጠር ጨምረናል

# --- 1. Flask and Database Configuration ---
app = Flask(__name__)

# ✅ 1. ወሳኝ የደህንነት ማስተካከያ፡ SECRET_KEYን በጠንካራ ቁልፍ መተካት
app.config['SECRET_KEY'] = secrets.token_hex(32) 
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db' 
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Jinja2 የ mistune ተግባርን እንዲጠቀም ማድረግ
app.jinja_env.filters['markdown_to_html'] = mistune.html 

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' 

# --- 2. Database Models (IMAGE_BASE64 ADDED) ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    role = db.Column(db.String(10), nullable=False) # 'user' or 'ai'
    content = db.Column(db.Text, nullable=False) # Stores Markdown text
    image_base64 = db.Column(db.Text, nullable=True) # Stores image data for history display
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp()) 

    user = db.relationship('User', backref=db.backref('messages', lazy=True))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- 3. Gemini AI Client and Persona Configuration ---
# ✅ 2. የ AI Persona ጥብቅ መመሪያ (Guardrail) ማስተካከያ እዚህ አለ
mr_alex_instruction = (
    "You are 'Mr. Alex Chatbot', a text-based Large Language Model created by Mr. Alex Company. "
    "The language of the user interface is English, but your response MUST match the language of the user's prompt (Amharic or English). "
    
    "**You MUST only share information about your creator (Dawit Kebede) IF the user explicitly asks about the creator or the company (e.g., 'who created you?', 'tell me about your founder', 'who is Dawit Kebede?').** "
    "For all other general or technical questions, answer normally without mentioning the creator or his biography."
    
    "When asked who created you, your response should be based on the following: "
    "'I am created by Mr. Alex Company and I am a text-Based Large Language Model. I was coded by Dawit Kebede, the founder of Mr. Alex Company.' "
    "For Dawit Kebede's detailed personal information, use the following biography and translate it into Amharic if asked in Amharic: "
    "Dawit Kebede was born on July 24, 2002 (Hamle 17, 1995 E.C.) in Addis Ababa. His parents are W/ro Ester Bedada and Ato Kebede Ayele, and he is the youngest child in the family. "
    "Dawit says he learned good character and essential life values from his parents, which serve as guidance for all his life decisions. "
    "After starting his formal education in Addis Ababa, he joined Debre Berhan University for higher education. However, driven by his strong passion for technology and a focus on his own projects, he decided to 'Drop Out' from his university studies. This decision is believed to have been a necessary step for him to realize his vision in innovation and technology. "
    "This life change led Dawit to become the founder of his private company, Mr. Alex. Mr. Alex focuses on Artificial Intelligence (AI) creations (like Mr. Alex AI Bot) and Cyber Security services. "
    "Dawit Kebede explains that the main vision of Mr. Alex is not just personal success, but also contributing to making Ethiopia a leader in technology and cyber security. "
    "Never mention Google, Gemini, or any other company outside of Mr. Alex Company."
)

def initialize_gemini_chat(history=None):
    """Gemini clientን ይፈጥርና አዲስ የቻት ስብስብ ይመሰርታል።"""
    global gemini_chat, client
    
    gemini_history = []
    if history:
        for msg in history:
            clean_content = msg.content
            
            # ጽሑፉ ባዶ ካልሆነ ወይም ምስል ከተላከ ታሪክ ውስጥ ይገባል
            if clean_content or msg.image_base64: 
                # If there was an image-only message, pass a descriptive text to maintain context
                history_content = clean_content if clean_content else "Analyzed an image"
                
                gemini_history.append(types.Content(
                    role="user" if msg.role == "user" else "model", 
                    parts=[types.Part(text=history_content)] 
                ))
            
    try:
        # NOTE: Make sure the GEMINI_API_KEY environment variable is set!
        client = genai.Client() 
        
        gemini_chat = client.chats.create(
            model='gemini-2.5-flash',
            history=gemini_history,
            config=types.GenerateContentConfig(
                system_instruction=mr_alex_instruction
            )
        )
        print(f"Gemini Chat Session initialized successfully. History loaded: {len(gemini_history) // 2} turns.")
        return True
    except Exception as e:
        print(f"Error: Could not connect to the AI service. Details: {e}")
        return False

gemini_initialized = False

# --- 4. Routing (Authentication and Utility) ---
@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('chat_page')) 
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('chat_page')) 
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user is None or not user.check_password(password):
            flash('Invalid username or password', 'danger')
            return redirect(url_for('login'))
        login_user(user)
        return redirect(url_for('chat_page')) 
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('chat_page')) 
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        if User.query.filter_by(username=username).first() or User.query.filter_by(email=email).first():
            flash('This username or email is already registered', 'warning')
            return redirect(url_for('signup'))
        new_user = User(username=username, email=email)
        new_user.set_password(password) 
        db.session.add(new_user)
        db.session.commit()
        flash('Successfully registered! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/about')
@login_required
def about_page():
    return render_template('about.html')

@app.route('/contact')
@login_required
def contact_page():
    return render_template('contact.html')

@app.route('/privacy')
@login_required
def privacy_page():
    return render_template('privacy.html')

@app.route('/clear_history', methods=['POST'])
@login_required
def clear_history():
    ChatMessage.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    initialize_gemini_chat(history=[]) 
    flash('Chat history cleared successfully!', 'success')
    return redirect(url_for('chat_page'))


# --- 5. AI Chat Page & Request Handler ---
@app.route('/chat')
@login_required 
def chat_page():
    chat_history = ChatMessage.query.filter_by(user_id=current_user.id).order_by(ChatMessage.timestamp.asc()).all()
    
    if request.args.get('new') == 'true' or not chat_history:
        initialize_gemini_chat(history=[])
        
        initial_greeting = "Welcome! I am Mr. Alex Chatbot, an AI creation of Mr. Alex Company. How can I help you today?"
        
        if not chat_history:
            greeting_message = ChatMessage(
                user_id=current_user.id,
                role='ai',
                content=initial_greeting
            )
            db.session.add(greeting_message)
            db.session.commit()
            chat_history = [greeting_message]

    else:
        initialize_gemini_chat(history=chat_history)
        
    return render_template('index.html', chat_history=chat_history)


@app.route('/ask', methods=['POST'])
@login_required 
def ask_gemini():
    user_prompt = request.form.get('prompt', '')
    uploaded_file = request.files.get('image')

    if not user_prompt and not uploaded_file:
        return jsonify({'response': 'Please enter a text prompt or upload an image.'})

    content_parts = []
    base64_image = None # For immediate display
    db_base64_content = None # For saving to the database

    # 1. Image Handling
    if uploaded_file:
        try:
            image_bytes = uploaded_file.read()
            image = Image.open(io.BytesIO(image_bytes))
            content_parts.append(image)
            
            # Base64 encoding for display and saving
            encoded_string = base64.b64encode(image_bytes).decode('utf-8')
            mime_type = image.format.lower() if image.format else 'jpeg'
            
            # Base64 URL (data URI)
            base64_url = f"data:image/{mime_type};base64,{encoded_string}" 
            
            base64_image = base64_url # For AJAX response
            db_base64_content = base64_url # For DB storage
            
        except Exception as e:
            ai_response = f"Image Processing Error: {e}"
            return jsonify({'response': ai_response})

    if user_prompt:
        content_parts.append(user_prompt)

    # 2. Save User Message to DB (including the base64 code)
    user_message = ChatMessage(
        user_id=current_user.id,
        role='user',
        content=user_prompt,
        image_base64=db_base64_content # NEW: Base64 data saved here
    )
    db.session.add(user_message)
    db.session.commit()

    # 3. Call Gemini AI
    try:
        response = gemini_chat.send_message(content_parts) 
        
        markdown_text = response.text
        # Convert Markdown to HTML for immediate display (AJAX response)
        ai_response_html = mistune.html(markdown_text) 
        
        # 4. Save AI Response (Markdown) to DB
        ai_message = ChatMessage(
            user_id=current_user.id,
            role='ai',
            content=markdown_text # Save Markdown in DB
        )
        db.session.add(ai_message)
        db.session.commit()
        
    except Exception as e:
        ai_response_html = f"AI Service Error: Could not process request. Details: {e}"
        base64_image = None
        
    # 5. Send back HTML response and image data
    return jsonify({
        'response': ai_response_html, 
        'sent_image': base64_image
    })


# --- 6. Run Application ---
if __name__ == '__main__':
    with app.app_context():
        # WARNING: If you changed the database model, delete 'users.db' first!
        # ለውጦቹ እንዲሰሩ ከዚህ በፊት users.db ን ሰርዘው ይሆናል
        db.create_all()
        
    # NOTE: Set your GEMINI_API_KEY environment variable before running!
    # ✅ 3. አስፈላጊ ማስተካከያ፡ debug=False አድርገናል ለህዝብ ማቅረቢያ
    app.run(debug=False, host='0.0.0.0')