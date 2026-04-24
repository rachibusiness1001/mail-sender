from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import smtplib, imaplib, email as email_lib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import csv, io, threading, time, random, uuid, re, socket, json, secrets
import urllib.request, urllib.parse, urllib.error
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'mailflow2024supersecret'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///emailtool.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ─── GOOGLE OAUTH CONFIG ───
GOOGLE_CLIENT_ID = "692871759210-mrjr5ib5mvti7mnue5339au83ihvsrok.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"
GOOGLE_REDIRECT_URI = "http://localhost:5000/auth/google/callback"
GMAIL_REDIRECT_URI = "http://localhost:5000/accounts/google/callback"

db = SQLAlchemy(app)

# ─────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), default='')
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(500), default='')
    google_id = db.Column(db.String(200), default='')
    avatar = db.Column(db.String(500), default='')
    plan = db.Column(db.String(50), default='free')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

class EmailAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    password = db.Column(db.String(200), default='')
    smtp_host = db.Column(db.String(200), default='smtp.gmail.com')
    smtp_port = db.Column(db.Integer, default=587)
    imap_host = db.Column(db.String(200), default='imap.gmail.com')
    # OAuth fields
    auth_type = db.Column(db.String(20), default='password')  # 'password' or 'oauth'
    access_token = db.Column(db.Text, default='')
    refresh_token = db.Column(db.Text, default='')
    token_expiry = db.Column(db.DateTime, nullable=True)
    daily_limit = db.Column(db.Integer, default=50)
    sent_today = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    last_reset = db.Column(db.Date, default=datetime.today)
    warmup_enabled = db.Column(db.Boolean, default=False)
    warmup_day = db.Column(db.Integer, default=1)
    warmup_limit = db.Column(db.Integer, default=5)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Campaign(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    subject_a = db.Column(db.String(500), nullable=False)
    body_a = db.Column(db.Text, nullable=False)
    subject_b = db.Column(db.String(500), default='')
    body_b = db.Column(db.Text, default='')
    ab_enabled = db.Column(db.Boolean, default=False)
    ab_split = db.Column(db.Integer, default=50)
    sent_a = db.Column(db.Integer, default=0)
    sent_b = db.Column(db.Integer, default=0)
    open_a = db.Column(db.Integer, default=0)
    open_b = db.Column(db.Integer, default=0)
    reply_a = db.Column(db.Integer, default=0)
    reply_b = db.Column(db.Integer, default=0)
    status = db.Column(db.String(50), default='draft')
    delay_min = db.Column(db.Integer, default=1)
    delay_max = db.Column(db.Integer, default=3)
    total_leads = db.Column(db.Integer, default=0)
    sent_count = db.Column(db.Integer, default=0)
    failed_count = db.Column(db.Integer, default=0)
    open_count = db.Column(db.Integer, default=0)
    reply_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    leads = db.relationship('Lead', backref='campaign', lazy=True)
    followups = db.relationship('FollowUp', backref='campaign', lazy=True, order_by='FollowUp.step')

class FollowUp(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaign.id'), nullable=False)
    step = db.Column(db.Integer, default=1)
    subject = db.Column(db.String(500), nullable=False)
    body = db.Column(db.Text, nullable=False)
    wait_days = db.Column(db.Integer, default=2)

class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(200), default='')
    company = db.Column(db.String(200), default='')
    phone = db.Column(db.String(50), default='')
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaign.id'), nullable=True)
    status = db.Column(db.String(50), default='pending')
    ab_variant = db.Column(db.String(1), default='')
    current_step = db.Column(db.Integer, default=0)
    next_followup_at = db.Column(db.DateTime, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    opened_at = db.Column(db.DateTime, nullable=True)
    replied_at = db.Column(db.DateTime, nullable=True)
    open_count = db.Column(db.Integer, default=0)
    thread_id = db.Column(db.String(200), default='')
    error_msg = db.Column(db.String(500), default='')
    tracking_id = db.Column(db.String(100), default='')
    email_valid = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class InboxReply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=True)
    account_id = db.Column(db.Integer, db.ForeignKey('email_account.id'), nullable=True)
    from_email = db.Column(db.String(200), default='')
    subject = db.Column(db.String(500), default='')
    body = db.Column(db.Text, default='')
    category = db.Column(db.String(50), default='uncategorized')
    is_read = db.Column(db.Boolean, default=False)
    received_at = db.Column(db.DateTime, default=datetime.utcnow)
    lead = db.relationship('Lead', backref='replies')
    account = db.relationship('EmailAccount', backref='replies')

class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True)
    value = db.Column(db.String(500))

running_campaigns = {}

SPAM_WORDS = ['free','winner','won','prize','click here','buy now','order now','limited time','act now','urgent','congratulations','guaranteed','no obligation','risk free','earn money','make money','cash','cheap','discount','save big','amazing','incredible','100% free','bonus','double your','extra income','get paid','million dollars','opportunity','dear friend','promotion','special offer','clearance','lowest price']
DISPOSABLE_DOMAINS = ['mailinator.com','guerrillamail.com','tempmail.com','throwaway.email','yopmail.com','trashmail.com','dispostable.com','maildrop.cc','spam4.me','tempr.email']

# ─────────────────────────────────────────
# AUTH HELPERS
# ─────────────────────────────────────────

import hashlib

def hash_password(p):
    return hashlib.sha256(('mailflow_salt_2024' + p).encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

# ─────────────────────────────────────────
# GOOGLE OAUTH HELPERS
# ─────────────────────────────────────────

def google_get_tokens(code, redirect_uri):
    data = urllib.parse.urlencode({
        'code': code,
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code'
    }).encode()
    req = urllib.request.Request('https://oauth2.googleapis.com/token', data=data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def google_get_userinfo(access_token):
    req = urllib.request.Request('https://www.googleapis.com/oauth2/v2/userinfo')
    req.add_header('Authorization', f'Bearer {access_token}')
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def refresh_access_token(refresh_token):
    data = urllib.parse.urlencode({
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token'
    }).encode()
    req = urllib.request.Request('https://oauth2.googleapis.com/token', data=data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def get_valid_token(account):
    if account.auth_type != 'oauth':
        return None
    if account.token_expiry and datetime.utcnow() >= account.token_expiry - timedelta(minutes=5):
        try:
            tokens = refresh_access_token(account.refresh_token)
            account.access_token = tokens.get('access_token', account.access_token)
            if 'expires_in' in tokens:
                account.token_expiry = datetime.utcnow() + timedelta(seconds=tokens['expires_in'])
            db.session.commit()
        except:
            pass
    return account.access_token

# ─────────────────────────────────────────
# EMAIL HELPERS
# ─────────────────────────────────────────

def get_setting(key, default=''):
    s = Settings.query.filter_by(key=key).first()
    return s.value if s else default

def set_setting(key, value):
    s = Settings.query.filter_by(key=key).first()
    if s:
        s.value = str(value)
    else:
        db.session.add(Settings(key=key, value=str(value)))
    db.session.commit()

def get_available_account():
    today = datetime.today().date()
    for acc in EmailAccount.query.filter_by(is_active=True).all():
        if acc.last_reset != today:
            acc.sent_today = 0
            acc.last_reset = today
            db.session.commit()
        limit = acc.warmup_limit if acc.warmup_enabled else acc.daily_limit
        if acc.sent_today < limit:
            return acc
    return None

def send_via_gmail_api(access_token, to_email, subject, body, thread_id=None, tracking_id=None):
    try:
        import base64
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['To'] = to_email
        if thread_id:
            msg['In-Reply-To'] = thread_id
            msg['References'] = thread_id
        if tracking_id:
            pixel = f'<img src="http://localhost:5000/track/open/{tracking_id}" width="1" height="1" style="display:none"/>'
            html_body = body.replace('\n', '<br>') + pixel
            msg.attach(MIMEText(body, 'plain'))
            msg.attach(MIMEText(html_body, 'html'))
        else:
            msg.attach(MIMEText(body, 'plain'))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        payload = json.dumps({'raw': raw}).encode()
        if thread_id:
            payload = json.dumps({'raw': raw, 'threadId': thread_id}).encode()
        api_url = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'
        req = urllib.request.Request(api_url, data=payload, method='POST')
        req.add_header('Authorization', f'Bearer {access_token}')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
        return True, '', result.get('threadId', str(uuid.uuid4()))
    except Exception as e:
        return False, str(e), ''

def send_email_smtp(account, to_email, subject, body, thread_id=None, tracking_id=None):
    # Use Gmail API if OAuth
    if account.auth_type == 'oauth':
        token = get_valid_token(account)
        if token:
            return send_via_gmail_api(token, to_email, subject, body, thread_id, tracking_id)
    # Fallback to SMTP
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = account.email
        msg['To'] = to_email
        if thread_id:
            msg['In-Reply-To'] = thread_id
            msg['References'] = thread_id
        if tracking_id:
            pixel = f'<img src="http://localhost:5000/track/open/{tracking_id}" width="1" height="1" style="display:none"/>'
            html_body = body.replace('\n', '<br>') + pixel
            msg.attach(MIMEText(body, 'plain'))
            msg.attach(MIMEText(html_body, 'html'))
        else:
            msg.attach(MIMEText(body, 'plain'))
        with smtplib.SMTP(account.smtp_host, account.smtp_port) as server:
            server.ehlo(); server.starttls()
            server.login(account.email, account.password)
            server.sendmail(account.email, to_email, msg.as_string())
        return True, '', msg.get('Message-ID', str(uuid.uuid4()))
    except Exception as e:
        return False, str(e), ''

def process_spintax(text):
    pattern = re.compile(r'\{([^{}]+)\}')
    while pattern.search(text):
        text = pattern.sub(lambda m: random.choice(m.group(1).split('|')), text)
    return text

def personalize(text, lead):
    text = process_spintax(text)
    text = text.replace('{{name}}', lead.name or 'there')
    text = text.replace('{{email}}', lead.email or '')
    text = text.replace('{{company}}', lead.company or '')
    return text

def check_spam_score(subject, body):
    text = (subject + ' ' + body).lower()
    found = [w for w in SPAM_WORDS if w.lower() in text]
    return min(len(found) * 10, 100), found

def verify_email(email_addr):
    try:
        if not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email_addr):
            return False, 'Invalid format'
        domain = email_addr.split('@')[1].lower()
        if domain in DISPOSABLE_DOMAINS:
            return False, 'Disposable email'
        try:
            socket.gethostbyname(domain)
            return True, 'Valid'
        except:
            return False, 'Domain not found'
    except Exception as e:
        return False, str(e)

def categorize_reply(body):
    b = body.lower()
    if any(w in b for w in ['interested','sounds good','tell me more','let\'s connect','schedule','meeting','call','yes','absolutely','would love']):
        return 'interested'
    if any(w in b for w in ['out of office','on vacation','away from','annual leave','auto-reply','automatic reply']):
        return 'ooo'
    if any(w in b for w in ['not interested','unsubscribe','remove me','stop emailing','no thanks','not relevant']):
        return 'not_interested'
    return 'other'

def run_campaign(campaign_id):
    with app.app_context():
        campaign = Campaign.query.get(campaign_id)
        if not campaign: return
        campaign.status = 'running'
        db.session.commit()
        leads = Lead.query.filter_by(campaign_id=campaign_id, status='pending').all()
        total = len(leads)
        for i, lead in enumerate(leads):
            if not running_campaigns.get(campaign_id, False):
                Campaign.query.get(campaign_id).status = 'paused'
                db.session.commit()
                return
            campaign = Campaign.query.get(campaign_id)
            if campaign.status != 'running': return
            account = get_available_account()
            if not account:
                campaign.status = 'paused'
                db.session.commit()
                return
            variant = 'A'
            if campaign.ab_enabled and campaign.subject_b:
                variant = 'A' if (i / total * 100) < campaign.ab_split else 'B'
            subject = personalize(campaign.subject_a if variant == 'A' else campaign.subject_b, lead)
            body = personalize(campaign.body_a if variant == 'A' else campaign.body_b, lead)
            tracking_id = str(uuid.uuid4())
            success, error, msg_id = send_email_smtp(account, lead.email, subject, body, tracking_id=tracking_id)
            if success:
                lead.status = 'sent_followup_pending' if campaign.followups else 'sent'
                lead.sent_at = datetime.utcnow()
                lead.current_step = 1
                lead.tracking_id = tracking_id
                lead.thread_id = msg_id
                lead.ab_variant = variant
                account.sent_today += 1
                campaign.sent_count += 1
                if variant == 'A': campaign.sent_a += 1
                else: campaign.sent_b += 1
                if campaign.followups:
                    lead.next_followup_at = datetime.utcnow() + timedelta(days=campaign.followups[0].wait_days)
            else:
                lead.status = 'failed'
                lead.error_msg = error
                campaign.failed_count += 1
            db.session.commit()
            time.sleep(random.randint(campaign.delay_min * 60, campaign.delay_max * 60))
        campaign.status = 'completed'
        db.session.commit()
        running_campaigns.pop(campaign_id, None)

def run_followups_bg():
    while True:
        try:
            with app.app_context():
                now = datetime.utcnow()
                for lead in Lead.query.filter(Lead.status == 'sent_followup_pending', Lead.next_followup_at <= now).all():
                    if not lead.campaign_id: continue
                    campaign = Campaign.query.get(lead.campaign_id)
                    followups = FollowUp.query.filter_by(campaign_id=lead.campaign_id).order_by(FollowUp.step).all()
                    idx = lead.current_step - 1
                    if idx >= len(followups):
                        lead.status = 'sent'
                        db.session.commit()
                        continue
                    fu = followups[idx]
                    account = get_available_account()
                    if not account: continue
                    tracking_id = str(uuid.uuid4())
                    success, error, msg_id = send_email_smtp(account, lead.email, 'Re: ' + personalize(campaign.subject_a, lead), personalize(fu.body, lead), thread_id=lead.thread_id, tracking_id=tracking_id)
                    if success:
                        lead.current_step += 1
                        account.sent_today += 1
                        campaign.sent_count += 1
                        if lead.current_step - 1 < len(followups):
                            lead.next_followup_at = datetime.utcnow() + timedelta(days=followups[lead.current_step - 1].wait_days)
                        else:
                            lead.status = 'sent'
                            lead.next_followup_at = None
                        db.session.commit()
        except: pass
        time.sleep(60)

def fetch_replies_bg():
    while True:
        try:
            with app.app_context():
                for acc in EmailAccount.query.filter_by(is_active=True).all():
                    try:
                        mail = imaplib.IMAP4_SSL(acc.imap_host)
                        mail.login(acc.email, acc.password)
                        mail.select('inbox')
                        _, data = mail.search(None, 'UNSEEN')
                        for num in data[0].split()[-20:]:
                            _, msg_data = mail.fetch(num, '(RFC822)')
                            msg = email_lib.message_from_bytes(msg_data[0][1])
                            from_email = email_lib.utils.parseaddr(msg['From'])[1]
                            subject = msg.get('Subject', '')
                            body = ''
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() == 'text/plain':
                                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                        break
                            else:
                                body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                            if InboxReply.query.filter_by(from_email=from_email, subject=subject).first(): continue
                            category = categorize_reply(body)
                            lead = Lead.query.filter_by(email=from_email).first()
                            if lead:
                                lead.replied_at = datetime.utcnow()
                                lead.status = 'replied'
                                if lead.campaign_id:
                                    c = Campaign.query.get(lead.campaign_id)
                                    if c:
                                        c.reply_count += 1
                                        if lead.ab_variant == 'A': c.reply_a += 1
                                        elif lead.ab_variant == 'B': c.reply_b += 1
                            db.session.add(InboxReply(lead_id=lead.id if lead else None, account_id=acc.id, from_email=from_email, subject=subject, body=body[:2000], category=category))
                            db.session.commit()
                        mail.logout()
                    except: pass
        except: pass
        time.sleep(300)

# ─────────────────────────────────────────
# AUTH ROUTES — LOGIN/REGISTER
# ─────────────────────────────────────────

@app.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/auth/login', methods=['POST'])
def auth_login():
    data = request.json
    email = data.get('email', '').lower().strip()
    password = data.get('password', '')
    user = User.query.filter_by(email=email).first()
    if not user or user.password_hash != hash_password(password):
        return jsonify({'success': False, 'message': 'Invalid email or password'})
    session['user_id'] = user.id
    session['user_name'] = user.name
    session['user_email'] = user.email
    session['user_avatar'] = user.avatar
    user.last_login = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})

@app.route('/auth/register', methods=['POST'])
def auth_register():
    data = request.json
    name = data.get('name', '').strip()
    email = data.get('email', '').lower().strip()
    password = data.get('password', '')
    if not name or not email or not password:
        return jsonify({'success': False, 'message': 'All fields required'})
    if len(password) < 8:
        return jsonify({'success': False, 'message': 'Password must be 8+ characters'})
    if User.query.filter_by(email=email).first():
        return jsonify({'success': False, 'message': 'Email already registered'})
    user = User(name=name, email=email, password_hash=hash_password(password))
    db.session.add(user)
    db.session.commit()
    session['user_id'] = user.id
    session['user_name'] = user.name
    session['user_email'] = user.email
    return jsonify({'success': True})

# ─── GOOGLE LOGIN OAUTH ───

@app.route('/auth/google')
def auth_google():
    state = secrets.token_urlsafe(16)
    session['oauth_state'] = state
    params = urllib.parse.urlencode({
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'offline',
        'prompt': 'select_account'
    })
    return redirect(f'https://accounts.google.com/o/oauth2/v2/auth?{params}')

@app.route('/auth/google/callback')
def auth_google_callback():
    if request.args.get('state') != session.get('oauth_state'):
        flash('Invalid state', 'error')
        return redirect(url_for('login_page'))
    code = request.args.get('code')
    if not code:
        flash('Google login cancelled', 'error')
        return redirect(url_for('login_page'))
    try:
        tokens = google_get_tokens(code, GOOGLE_REDIRECT_URI)
        user_info = google_get_userinfo(tokens['access_token'])
        email = user_info.get('email', '').lower()
        name = user_info.get('name', '')
        avatar = user_info.get('picture', '')
        google_id = user_info.get('id', '')
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(name=name, email=email, google_id=google_id, avatar=avatar)
            db.session.add(user)
        else:
            user.google_id = google_id
            user.avatar = avatar
            if not user.name: user.name = name
        user.last_login = datetime.utcnow()
        db.session.commit()
        session['user_id'] = user.id
        session['user_name'] = user.name
        session['user_email'] = user.email
        session['user_avatar'] = avatar
        return redirect(url_for('dashboard'))
    except Exception as e:
        flash(f'Google login failed: {str(e)}', 'error')
        return redirect(url_for('login_page'))

# ─── GMAIL ACCOUNT OAUTH ───

@app.route('/accounts/google/connect')
@login_required
def gmail_connect():
    state = secrets.token_urlsafe(16)
    session['gmail_state'] = state
    params = urllib.parse.urlencode({
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': GMAIL_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/userinfo.email',
        'state': state,
        'access_type': 'offline',
        'prompt': 'consent'
    })
    return redirect(f'https://accounts.google.com/o/oauth2/v2/auth?{params}')

@app.route('/accounts/google/callback')
@login_required
def gmail_callback():
    if request.args.get('state') != session.get('gmail_state'):
        flash('Invalid state', 'error')
        return redirect(url_for('accounts'))
    code = request.args.get('code')
    if not code:
        flash('Gmail connection cancelled', 'error')
        return redirect(url_for('accounts'))
    try:
        tokens = google_get_tokens(code, GMAIL_REDIRECT_URI)
        user_info = google_get_userinfo(tokens['access_token'])
        email = user_info.get('email', '')
        name = user_info.get('name', email)
        existing = EmailAccount.query.filter_by(email=email).first()
        if existing:
            existing.access_token = tokens.get('access_token', '')
            existing.refresh_token = tokens.get('refresh_token', existing.refresh_token)
            existing.token_expiry = datetime.utcnow() + timedelta(seconds=tokens.get('expires_in', 3600))
            existing.auth_type = 'oauth'
            existing.is_active = True
            db.session.commit()
            flash(f'{email} reconnected successfully!', 'success')
        else:
            acc = EmailAccount(
                name=name, email=email,
                auth_type='oauth',
                access_token=tokens.get('access_token', ''),
                refresh_token=tokens.get('refresh_token', ''),
                token_expiry=datetime.utcnow() + timedelta(seconds=tokens.get('expires_in', 3600)),
                daily_limit=50
            )
            db.session.add(acc)
            db.session.commit()
            flash(f'{email} connected successfully!', 'success')
        return redirect(url_for('accounts'))
    except Exception as e:
        flash(f'Gmail connection failed: {str(e)}', 'error')
        return redirect(url_for('accounts'))

# ─────────────────────────────────────────
# MAIN ROUTES
# ─────────────────────────────────────────

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/app')
def app_home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login_page'))

@app.route('/dashboard')
@login_required
def dashboard():
    camps = Campaign.query.order_by(Campaign.created_at.desc()).all()
    total_sent = Lead.query.filter(Lead.status.in_(['sent','sent_followup_pending','replied'])).count()
    total_opens = sum(c.open_count for c in camps)
    total_replies = sum(c.reply_count for c in camps)
    open_rate = round(total_opens / total_sent * 100, 1) if total_sent > 0 else 0
    reply_rate = round(total_replies / total_sent * 100, 1) if total_sent > 0 else 0
    return render_template('dashboard.html',
        total_leads=Lead.query.count(),
        total_sent=total_sent,
        total_pending=Lead.query.filter_by(status='pending').count(),
        total_failed=Lead.query.filter_by(status='failed').count(),
        total_replied=Lead.query.filter_by(status='replied').count(),
        total_campaigns=Campaign.query.count(),
        running=Campaign.query.filter_by(status='running').count(),
        accounts=EmailAccount.query.filter_by(is_active=True).count(),
        unread_replies=InboxReply.query.filter_by(is_read=False).count(),
        recent_campaigns=camps[:5],
        open_rate=open_rate, reply_rate=reply_rate,
        user_name=session.get('user_name', ''),
        user_avatar=session.get('user_avatar', '')
    )

@app.route('/leads')
@login_required
def leads():
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')
    search = request.args.get('q', '')
    query = Lead.query
    if status_filter: query = query.filter_by(status=status_filter)
    if search: query = query.filter(Lead.email.contains(search) | Lead.name.contains(search) | Lead.company.contains(search))
    return render_template('leads.html',
        leads=query.order_by(Lead.created_at.desc()).paginate(page=page, per_page=50),
        status_filter=status_filter, search=search,
        campaigns=Campaign.query.order_by(Campaign.created_at.desc()).all()
    )

@app.route('/leads/upload', methods=['POST'])
@login_required
def upload_leads():
    file = request.files.get('file')
    campaign_id = request.form.get('campaign_id')
    verify = request.form.get('verify_emails') == 'on'
    if not file or file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('leads'))
    try:
        stream = io.StringIO(file.stream.read().decode('utf-8', errors='ignore'))
        reader = csv.DictReader(stream)
        reader.fieldnames = [f.lower().strip() for f in (reader.fieldnames or [])]
        count = 0
        invalid = 0
        for row in reader:
            email_val = row.get('email', '').strip()
            if not email_val or '@' not in email_val: continue
            if Lead.query.filter_by(email=email_val).first(): continue
            valid = True
            if verify:
                valid, _ = verify_email(email_val)
                if not valid:
                    invalid += 1
                    continue
            db.session.add(Lead(email=email_val, name=row.get('name','').strip(), company=row.get('company','').strip(), phone=row.get('phone','').strip(), campaign_id=int(campaign_id) if campaign_id else None, status='pending', email_valid=valid))
            count += 1
        if campaign_id:
            c = Campaign.query.get(int(campaign_id))
            if c: c.total_leads = Lead.query.filter_by(campaign_id=int(campaign_id)).count()
        db.session.commit()
        flash(f'{count} leads imported!' + (f' {invalid} invalid removed.' if invalid else ''), 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    return redirect(url_for('leads'))

@app.route('/leads/delete/<int:id>')
@login_required
def delete_lead(id):
    db.session.delete(Lead.query.get_or_404(id))
    db.session.commit()
    return redirect(url_for('leads'))

@app.route('/leads/clear', methods=['POST'])
@login_required
def clear_leads():
    Lead.query.delete()
    db.session.commit()
    flash('All leads cleared', 'success')
    return redirect(url_for('leads'))

@app.route('/campaigns')
@login_required
def campaigns():
    return render_template('campaigns.html', campaigns=Campaign.query.order_by(Campaign.created_at.desc()).all())

@app.route('/campaigns/new', methods=['GET', 'POST'])
@login_required
def new_campaign():
    if request.method == 'POST':
        campaign = Campaign(
            name=request.form.get('name'), subject_a=request.form.get('subject_a'),
            body_a=request.form.get('body_a'), subject_b=request.form.get('subject_b',''),
            body_b=request.form.get('body_b',''), ab_enabled='ab_enabled' in request.form,
            ab_split=int(request.form.get('ab_split',50)),
            delay_min=int(request.form.get('delay_min',1)), delay_max=int(request.form.get('delay_max',3))
        )
        db.session.add(campaign)
        db.session.flush()
        for i,(s,b,d) in enumerate(zip(request.form.getlist('fu_subject[]'), request.form.getlist('fu_body[]'), request.form.getlist('fu_days[]'))):
            if s.strip() and b.strip():
                db.session.add(FollowUp(campaign_id=campaign.id, step=i+1, subject=s, body=b, wait_days=int(d or 2)))
        db.session.commit()
        flash('Campaign created!', 'success')
        return redirect(url_for('campaigns'))
    return render_template('new_campaign.html')

@app.route('/campaigns/<int:id>')
@login_required
def view_campaign(id):
    campaign = Campaign.query.get_or_404(id)
    open_rate = round(campaign.open_count/campaign.sent_count*100,1) if campaign.sent_count>0 else 0
    reply_rate = round(campaign.reply_count/campaign.sent_count*100,1) if campaign.sent_count>0 else 0
    open_rate_a = round(campaign.open_a/campaign.sent_a*100,1) if campaign.sent_a>0 else 0
    open_rate_b = round(campaign.open_b/campaign.sent_b*100,1) if campaign.sent_b>0 else 0
    return render_template('view_campaign.html', campaign=campaign,
        leads=Lead.query.filter_by(campaign_id=id).order_by(Lead.created_at.desc()).all(),
        followups=FollowUp.query.filter_by(campaign_id=id).order_by(FollowUp.step).all(),
        open_rate=open_rate, reply_rate=reply_rate, open_rate_a=open_rate_a, open_rate_b=open_rate_b)

@app.route('/campaigns/<int:id>/start')
@login_required
def start_campaign(id):
    campaign = Campaign.query.get_or_404(id)
    if campaign.status == 'running':
        flash('Already running!', 'warning')
        return redirect(url_for('campaigns'))
    pending = Lead.query.filter_by(campaign_id=id, status='pending').count()
    if pending == 0:
        flash('No pending leads!', 'warning')
        return redirect(url_for('campaigns'))
    running_campaigns[id] = True
    campaign.status = 'running'
    db.session.commit()
    threading.Thread(target=run_campaign, args=(id,), daemon=True).start()
    flash(f'Campaign started! {pending} leads queued.', 'success')
    return redirect(url_for('campaigns'))

@app.route('/campaigns/<int:id>/pause')
@login_required
def pause_campaign(id):
    running_campaigns[id] = False
    c = Campaign.query.get_or_404(id)
    c.status = 'paused'
    db.session.commit()
    flash('Paused!', 'warning')
    return redirect(url_for('campaigns'))

@app.route('/campaigns/<int:id>/delete')
@login_required
def delete_campaign(id):
    FollowUp.query.filter_by(campaign_id=id).delete()
    Lead.query.filter_by(campaign_id=id).delete()
    db.session.delete(Campaign.query.get_or_404(id))
    db.session.commit()
    flash('Deleted!', 'success')
    return redirect(url_for('campaigns'))

@app.route('/campaigns/<int:id>/status')
def campaign_status(id):
    c = Campaign.query.get_or_404(id)
    return jsonify({'status':c.status,'sent':c.sent_count,'failed':c.failed_count,'total':c.total_leads,'opens':c.open_count,'replies':c.reply_count})

@app.route('/analytics')
@login_required
def analytics():
    camps = Campaign.query.order_by(Campaign.created_at.desc()).all()
    total_sent = Lead.query.filter(Lead.status.in_(['sent','sent_followup_pending','replied'])).count()
    total_opens = sum(c.open_count for c in camps)
    total_replies = sum(c.reply_count for c in camps)
    total_failed = Lead.query.filter_by(status='failed').count()
    open_rate = round(total_opens/total_sent*100,1) if total_sent>0 else 0
    reply_rate = round(total_replies/total_sent*100,1) if total_sent>0 else 0
    campaign_data = [{'name':c.name[:20],'sent':c.sent_count,'opens':c.open_count,'replies':c.reply_count,'failed':c.failed_count,'open_rate':round(c.open_count/c.sent_count*100,1) if c.sent_count>0 else 0,'reply_rate':round(c.reply_count/c.sent_count*100,1) if c.sent_count>0 else 0} for c in camps]
    return render_template('analytics.html', camps=camps, total_sent=total_sent, total_opens=total_opens, total_replies=total_replies, total_failed=total_failed, open_rate=open_rate, reply_rate=reply_rate, campaign_data=campaign_data)

@app.route('/inbox')
@login_required
def inbox():
    filter_type = request.args.get('filter','all')
    category = request.args.get('category','')
    query = InboxReply.query
    if filter_type == 'unread': query = query.filter_by(is_read=False)
    if category: query = query.filter_by(category=category)
    return render_template('inbox.html',
        replies=query.order_by(InboxReply.received_at.desc()).all(),
        unread_count=InboxReply.query.filter_by(is_read=False).count(),
        filter_type=filter_type, category=category,
        interested=InboxReply.query.filter_by(category='interested').count(),
        not_interested=InboxReply.query.filter_by(category='not_interested').count(),
        ooo=InboxReply.query.filter_by(category='ooo').count()
    )

@app.route('/inbox/<int:id>/read')
@login_required
def mark_read(id):
    r = InboxReply.query.get_or_404(id)
    r.is_read = True
    db.session.commit()
    return redirect(url_for('inbox'))

@app.route('/inbox/mark-all-read')
@login_required
def mark_all_read():
    InboxReply.query.update({'is_read': True})
    db.session.commit()
    flash('All marked as read!', 'success')
    return redirect(url_for('inbox'))

@app.route('/track/open/<tracking_id>')
def track_open(tracking_id):
    lead = Lead.query.filter_by(tracking_id=tracking_id).first()
    if lead:
        lead.open_count += 1
        if not lead.opened_at:
            lead.opened_at = datetime.utcnow()
            if lead.campaign_id:
                c = Campaign.query.get(lead.campaign_id)
                if c:
                    c.open_count += 1
                    if lead.ab_variant == 'A': c.open_a += 1
                    elif lead.ab_variant == 'B': c.open_b += 1
        db.session.commit()
    return Response(b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;', mimetype='image/gif')

@app.route('/accounts')
@login_required
def accounts():
    return render_template('accounts.html', accounts=EmailAccount.query.all())

@app.route('/accounts/new', methods=['GET', 'POST'])
@login_required
def new_account():
    if request.method == 'POST':
        db.session.add(EmailAccount(
            name=request.form.get('name'), email=request.form.get('email'),
            password=request.form.get('password',''),
            smtp_host=request.form.get('smtp_host','smtp.gmail.com'),
            smtp_port=int(request.form.get('smtp_port',587)),
            imap_host=request.form.get('imap_host','imap.gmail.com'),
            daily_limit=int(request.form.get('daily_limit',50)),
            warmup_enabled='warmup_enabled' in request.form,
            warmup_limit=int(request.form.get('warmup_limit',5))
        ))
        db.session.commit()
        flash('Account added!', 'success')
        return redirect(url_for('accounts'))
    return render_template('new_account.html')

@app.route('/accounts/<int:id>/toggle')
@login_required
def toggle_account(id):
    acc = EmailAccount.query.get_or_404(id)
    acc.is_active = not acc.is_active
    db.session.commit()
    return redirect(url_for('accounts'))

@app.route('/accounts/<int:id>/delete')
@login_required
def delete_account(id):
    db.session.delete(EmailAccount.query.get_or_404(id))
    db.session.commit()
    flash('Deleted!', 'success')
    return redirect(url_for('accounts'))

@app.route('/accounts/<int:id>/test')
@login_required
def test_account(id):
    acc = EmailAccount.query.get_or_404(id)
    if acc.auth_type == 'oauth':
        token = get_valid_token(acc)
        return jsonify({'success': bool(token), 'msg': 'OAuth connected!' if token else 'Token expired'})
    try:
        with smtplib.SMTP(acc.smtp_host, acc.smtp_port) as s:
            s.ehlo(); s.starttls(); s.login(acc.email, acc.password)
        return jsonify({'success': True, 'msg': 'Connected!'})
    except Exception as e:
        return jsonify({'success': False, 'msg': str(e)})

@app.route('/accounts/<int:id>/warmup-toggle')
@login_required
def warmup_toggle(id):
    acc = EmailAccount.query.get_or_404(id)
    acc.warmup_enabled = not acc.warmup_enabled
    db.session.commit()
    flash(f'Warmup {"ON" if acc.warmup_enabled else "OFF"}!', 'success')
    return redirect(url_for('accounts'))

@app.route('/admin')
@login_required
def admin():
    return render_template('admin.html',
        total_leads=Lead.query.count(),
        total_sent=Lead.query.filter(Lead.status.in_(['sent','sent_followup_pending','replied'])).count(),
        total_failed=Lead.query.filter_by(status='failed').count(),
        total_replied=Lead.query.filter_by(status='replied').count(),
        total_campaigns=Campaign.query.count(),
        total_accounts=EmailAccount.query.count(),
        active_accounts=EmailAccount.query.filter_by(is_active=True).count(),
        running=Campaign.query.filter_by(status='running').count(),
        completed=Campaign.query.filter_by(status='completed').count(),
        all_campaigns=Campaign.query.order_by(Campaign.created_at.desc()).all(),
        all_accounts=EmailAccount.query.all(),
        total_users=User.query.count(),
        all_users=User.query.order_by(User.created_at.desc()).all()
    )

@app.route('/settings', methods=['GET','POST'])
@login_required
def settings():
    if request.method == 'POST':
        set_setting('delay_min', request.form.get('delay_min','1'))
        set_setting('delay_max', request.form.get('delay_max','3'))
        set_setting('daily_limit', request.form.get('daily_limit','50'))
        flash('Saved!', 'success')
        return redirect(url_for('settings'))
    return render_template('settings.html',
        delay_min=get_setting('delay_min','1'),
        delay_max=get_setting('delay_max','3'),
        daily_limit=get_setting('daily_limit','50'))

@app.route('/api/spam-check', methods=['POST'])
def spam_check():
    data = request.json
    score, found = check_spam_score(data.get('subject',''), data.get('body',''))
    return jsonify({'score': score, 'words': found})

@app.route('/api/spintax-preview', methods=['POST'])
def spintax_preview():
    text = request.json.get('text','')
    return jsonify({'previews': [process_spintax(text) for _ in range(3)]})

@app.route('/api/verify-email', methods=['POST'])
def api_verify_email():
    email_val = request.json.get('email','')
    valid, reason = verify_email(email_val)
    return jsonify({'valid': valid, 'reason': reason})

@app.route('/api/stats')
def api_stats():
    return jsonify({
        'total_leads': Lead.query.count(),
        'sent': Lead.query.filter(Lead.status.in_(['sent','sent_followup_pending','replied'])).count(),
        'pending': Lead.query.filter_by(status='pending').count(),
        'failed': Lead.query.filter_by(status='failed').count(),
        'replied': Lead.query.filter_by(status='replied').count(),
        'running': Campaign.query.filter_by(status='running').count(),
        'unread_inbox': InboxReply.query.filter_by(is_read=False).count()
    })

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(email='admin@mailflow.com').first():
            db.session.add(User(name='Admin', email='admin@mailflow.com', password_hash=hash_password('admin1234')))
            db.session.commit()
            print("Admin: admin@mailflow.com / admin1234")
    threading.Thread(target=run_followups_bg, daemon=True).start()
    threading.Thread(target=fetch_replies_bg, daemon=True).start()
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port, use_reloader=False)
