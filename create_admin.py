from app import app, db, User

with app.app_context():
    # Delete old admin if exists
    old_admin = User.query.filter_by(email='admin@medisecure.com').first()
    if old_admin:
        db.session.delete(old_admin)
    
    # Create new admin
    admin = User(full_name='Admin User', email='admin@medisecure.com', role='admin')
    admin.set_password('admin123')
    db.session.add(admin)
    db.session.commit()
    
    print("✅ ADMIN CREATED!")
    print("📧 Email: admin@medisecure.com")
    print("🔑 Password: admin123")
    print("🎉 Ready to login!")
