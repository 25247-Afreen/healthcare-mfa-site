from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_wtf.csrf import CSRFProtect
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user,
    login_required, logout_user, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
import pyotp
import qrcode
import io
import base64
from datetime import datetime
from sqlalchemy import func

app = Flask(__name__)
app.config['SECRET_KEY'] = 'change-this-to-a-random-secret-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///healthcare.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Secure session cookies
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'

# Set True in production with HTTPS
app.config['SESSION_COOKIE_SECURE'] = False
app.config['REMEMBER_COOKIE_SECURE'] = False

# Optional session lifetime
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)
app.config['SECRET_KEY'] = 'change-this-to-a-random-secret-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///healthcare.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

csrf = CSRFProtect(app)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ==================== MODELS ====================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    mfa_secret = db.Column(db.String(32), nullable=True)
    mfa_enabled = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(20), default='patient')  # 'admin', 'doctor', 'patient'
    specialty = db.Column(db.String(50), nullable=True)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def get_totp_uri(self):
        if not self.mfa_secret:
            return None
        return pyotp.totp.TOTP(self.mfa_secret).provisioning_uri(
            name=self.email,
            issuer_name="MediSecure"
        )

    def verify_totp(self, token: str) -> bool:
        if not self.mfa_secret:
            return False
        totp = pyotp.TOTP(self.mfa_secret)
        return totp.verify(token)

class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer)
    condition = db.Column(db.String(200))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'))

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_name = db.Column(db.String(100), nullable=False)
    patient_email = db.Column(db.String(120), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    doctor = db.relationship('User', backref='doctor_appointments')
    date_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='scheduled')

class Prescription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_name = db.Column(db.String(100), nullable=False)
    patient_email = db.Column(db.String(120), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    doctor = db.relationship('User', backref='prescriptions')
    medication = db.Column(db.String(200), nullable=False)
    dosage = db.Column(db.String(100), nullable=False)
    instructions = db.Column(db.Text)
    date_prescribed = db.Column(db.DateTime, default=datetime.utcnow)

class LoginActivity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    login_time = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ==================== ROUTES ====================

@app.route('/')
@app.route('/home')
def home():
    if current_user.is_authenticated:
        if current_user.role == 'patient':
            return redirect(url_for('patient_dashboard'))
        elif current_user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
        elif current_user.role == 'doctor':
            return redirect(url_for('doctor_dashboard'))
    return render_template('home.html')

# DASHBOARDS
@app.route('/patient/dashboard')
@login_required
def patient_dashboard():
    if current_user.role != 'patient':
        flash('Patient access only!', 'danger')
        return redirect(url_for('home'))
    appts = Appointment.query.filter_by(patient_email=current_user.email).order_by(Appointment.date_time.desc()).all()
    return render_template('patient_dashboard.html', appointments=appts)

@app.route('/patient/book-appointment', methods=['GET', 'POST'])
@login_required
def book_appointment():
    if current_user.role != 'patient':
        flash('Patient access only!', 'danger')
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        doctor_email = request.form.get('doctor_email')
        appointment_date = request.form.get('appointment_date')
        appointment_time = request.form.get('appointment_time')
        
        # Find doctor
        doctor = User.query.filter_by(email=doctor_email, role='doctor').first()
        if not doctor:
            flash('Doctor not found!', 'danger')
            return redirect(url_for('book_appointment'))
        
        # Combine date + time
        from datetime import datetime
        date_time = datetime.strptime(f"{appointment_date} {appointment_time}", '%Y-%m-%d %H:%M')
        
        # Check if appointment already exists
        existing = Appointment.query.filter_by(
            patient_email=current_user.email,
            doctor_id=doctor.id,
            date_time=date_time
        ).first()
        
        if existing:
            flash('Appointment already exists!', 'warning')
            return redirect(url_for('book_appointment'))
        
        # Create appointment
        appointment = Appointment(
            patient_name=current_user.full_name,
            patient_email=current_user.email,
            doctor_id=doctor.id,
            date_time=date_time,
            status='scheduled'
        )
        db.session.add(appointment)
        db.session.commit()
        flash('✅ Appointment booked successfully!', 'success')
        return redirect(url_for('patient_dashboard'))
    
    # GET - Show form with doctors list
    doctors = User.query.filter_by(role='doctor').all()
    return render_template('book_appointment.html', doctors=doctors)

@app.route('/patient/appointments')
@login_required
def patient_appointments():
    if current_user.role != 'patient':
        flash('Patient access only!', 'danger')
        return redirect(url_for('home'))

    appointments = Appointment.query.filter_by(
        patient_email=current_user.email
    ).order_by(Appointment.date_time.desc()).all()

    return render_template('patient_appointments.html', appointments=appointments)

@app.route('/patient/medical-records')
@login_required
def patient_medical_records():
    if current_user.role != 'patient':
        flash('Patient access only!', 'danger')
        return redirect(url_for('home'))

    prescriptions = Prescription.query.filter_by(
        patient_email=current_user.email
    ).order_by(Prescription.date_prescribed.desc()).all()

    appointments = Appointment.query.filter_by(
        patient_email=current_user.email
    ).order_by(Appointment.date_time.desc()).all()

    return render_template(
        'patient_medical_records.html',
        prescriptions=prescriptions,
        appointments=appointments
    )

@app.route('/doctor/dashboard')
@login_required
def doctor_dashboard():
    if current_user.role != 'doctor':
        flash('Doctor access only!', 'danger')
        return redirect(url_for('home'))
    appts = Appointment.query.filter_by(doctor_id=current_user.id).order_by(Appointment.date_time).all()
    return render_template('doctor_dashboard.html', appointments=appts)

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        flash('Admin access only!', 'danger')
        return redirect(url_for('home'))

    users = User.query.all()
    appts_count = Appointment.query.count()

    login_logs = LoginActivity.query.order_by(LoginActivity.login_time.desc()).all()

    login_counts = db.session.query(
        LoginActivity.user_email,
        func.count(LoginActivity.id).label('total_logins'),
        func.max(LoginActivity.login_time).label('last_login')
    ).group_by(LoginActivity.user_email).all()

    return render_template(
        'admin_dashboard.html',
        users=users,
        appts_count=appts_count,
        login_logs=login_logs,
        login_counts=login_counts
    )

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'patient':
        return redirect(url_for('patient_dashboard'))
    elif current_user.role == 'doctor':
        return redirect(url_for('doctor_dashboard'))
    elif current_user.role == 'admin':
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('home'))

@app.route('/db-viewer')
@login_required
def db_viewer():
    if current_user.role != 'admin':
        flash('Admin access only!', 'danger')
        return redirect(url_for('home'))
    
    users = User.query.all()
    appts = Appointment.query.all()
    return render_template('db_viewer.html', users=users, appointments=appts)

# DOCTOR FEATURES - ADD THESE ROUTES
@app.route('/doctor/appointments')
@login_required
def doctor_appointments():
    if current_user.role != 'doctor':
        flash('Doctor access only!', 'danger')
        return redirect(url_for('home'))
    
    # Today's appointments only
    from datetime import date, datetime
    today = date.today()
    appts = Appointment.query.filter(
        Appointment.doctor_id == current_user.id,
        Appointment.date_time >= datetime.combine(today, datetime.min.time()),
        Appointment.date_time <= datetime.combine(today, datetime.max.time())
    ).order_by(Appointment.date_time).all()
    
    return render_template('doctor_appointments.html', appointments=appts)

@app.route('/doctor/prescriptions', methods=['GET', 'POST'])
@login_required
def doctor_prescriptions():
    if current_user.role != 'doctor':
        flash('Doctor access only!', 'danger')
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        patient_name = request.form.get('patient_name')
        patient_email = request.form.get('patient_email')
        medication = request.form.get('medication')
        dosage = request.form.get('dosage')
        instructions = request.form.get('instructions')
        
        prescription = Prescription(
            patient_name=patient_name,
            patient_email=patient_email,
            doctor_id=current_user.id,
            medication=medication,
            dosage=dosage,
            instructions=instructions
        )
        db.session.add(prescription)
        db.session.commit()
        flash('✅ Prescription created successfully!', 'success')
        return redirect(url_for('doctor_prescriptions'))
    
    # Show existing prescriptions
    prescriptions = Prescription.query.filter_by(doctor_id=current_user.id).order_by(Prescription.date_prescribed.desc()).all()
    return render_template('doctor_prescriptions.html', prescriptions=prescriptions)


@app.route('/doctor/records')
@login_required
def doctor_records():
    if current_user.role != 'doctor':
        flash('Doctor access only!', 'danger')
        return redirect(url_for('home'))
    
    # All patients this doctor has appointments with
    patients = db.session.query(User).join(Appointment).filter(
        Appointment.doctor_id == current_user.id
    ).distinct().all()
    
    return render_template('doctor_records.html', patients=patients)

# SAMPLE DATA - Run once to test
@app.route('/doctor/test-data')
@login_required
def add_test_data():
    if current_user.role != 'doctor':
        return redirect(url_for('home'))
    
    from datetime import datetime, timedelta
    today = datetime.now().date()
    
    # Add 3 test appointments
    test_appts = [
        Appointment(patient_name='Kay Patient', patient_email='kay@example.com', doctor_id=current_user.id,
                   date_time=datetime.combine(today, datetime.time(9, 0))),
        Appointment(patient_name='John Doe', patient_email='john@example.com', doctor_id=current_user.id,
                   date_time=datetime.combine(today, datetime.time(11, 30))),
        Appointment(patient_name='Sarah Wilson', patient_email='sarah@example.com', doctor_id=current_user.id,
                   date_time=datetime.combine(today, datetime.time(14, 15))),
    ]
    
    for appt in test_appts:
        if not Appointment.query.filter_by(patient_email=appt.patient_email, doctor_id=current_user.id).first():
            db.session.add(appt)
    
    db.session.commit()
    flash('✅ Added 3 test appointments!', 'success')
    return redirect(url_for('doctor_dashboard'))


# AUTH ROUTES (keep all your existing auth code - register, login, mfa, etc.)
@app.route('/register', methods=['GET'])
def register():
    return render_template('register.html')

@app.route('/register-patient', methods=['POST'])
def register_patient():
    full_name = request.form.get('full_name')
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password')

    if not full_name or not email or not password:
        flash('All fields are required.', 'danger')
        return redirect(url_for('register'))

    if len(password) < 8:
        flash('Password must be at least 8 characters.', 'danger')
        return redirect(url_for('register'))

    if User.query.filter_by(email=email).first():
        flash('Email already registered.', 'danger')
        return redirect(url_for('register'))

    user = User(full_name=full_name, email=email, role='patient')
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash('Patient registration successful. Please log in.', 'success')
    return redirect(url_for('login'))

@app.route('/register-doctor', methods=['POST'])
def register_doctor():
    full_name = request.form.get('full_name')
    email = request.form.get('email', '').strip().lower()
    specialty = request.form.get('specialty')
    password = request.form.get('password')

    if not all([full_name, email, specialty, password]):
        flash('All fields are required.', 'danger')
        return redirect(url_for('register'))

    if len(password) < 8:
        flash('Password must be at least 8 characters.', 'danger')
        return redirect(url_for('register'))

    if User.query.filter_by(email=email).first():
        flash('Email already registered.', 'danger')
        return redirect(url_for('register'))

    user = User(
        full_name=full_name, 
        email=email, 
        role='doctor', 
        specialty=specialty
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash('✅ Doctor registration successful! Please login.', 'success')
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()
        if user is None or not user.check_password(password):
            flash('Invalid email or password.', 'danger')
            return redirect(url_for('login'))

        if user.mfa_enabled:
            session['mfa_user_id'] = user.id
            return redirect(url_for('mfa_verify'))

        login_user(user)
        activity = LoginActivity(
            user_email=user.email,
            role=user.role,
            login_time=datetime.now()
        )
        db.session.add(activity)
        db.session.commit()
        flash('Logged in successfully.', 'success')
        return redirect(url_for('home'))

    return render_template('login.html')

@app.route('/mfa-setup', methods=['GET', 'POST'])
@login_required
def mfa_setup():
    user = current_user
    
    if not user.mfa_secret:
        user.mfa_secret = pyotp.random_base32()
        db.session.commit()

    totp = pyotp.TOTP(user.mfa_secret)
    provisioning_uri = totp.provisioning_uri(
        name=user.email,
        issuer_name="MediSecure"
    )

    qr = qrcode.make(provisioning_uri)
    img_io = io.BytesIO()
    qr.save(img_io, 'PNG')
    img_io.seek(0)
    qr_b64 = base64.b64encode(img_io.getvalue()).decode()

    if request.method == 'POST':
        token = request.form.get('token', '').strip()

        if totp.verify(token, valid_window=1):
            user.mfa_enabled = True
            db.session.commit()
            flash('✅ MFA enabled successfully!', 'success')
            return redirect(url_for('home'))

        flash('❌ Invalid MFA code. Try again.', 'danger')

    return render_template('mfa_setup.html', qr_b64=qr_b64, secret=user.mfa_secret)

@app.route('/mfa-verify', methods=['GET', 'POST'])
def mfa_verify():
    user_id = session.get('mfa_user_id')
    if not user_id:
        flash('Session expired, please login again.', 'danger')
        return redirect(url_for('login'))

    user = User.query.get(user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('login'))

    if request.method == 'POST':
        token = request.form.get('token')
        if user.verify_totp(token):
            session.pop('mfa_user_id', None)
            login_user(user)
            activity = LoginActivity(
                user_email=user.email,
                role=user.role
            )
            db.session.add(activity)
            db.session.commit()
            flash('✅ MFA verified! Welcome back.', 'success')
            return redirect(url_for('home'))
        else:
            flash('❌ Invalid MFA code.', 'danger')

    return render_template('mfa_verify.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out.', 'info')
    return redirect(url_for('login'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
