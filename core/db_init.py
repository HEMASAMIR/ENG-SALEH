import bcrypt
from sqlalchemy.orm import Session
from core.database import engine, Base, SessionLocal
from models.auth.role import Role
from models.auth.permission import Permission
from models.auth.role_permission import RolePermission
from models.auth.user import User

# Ensure all models are imported so Base.metadata knows about them
from models.audit.audit_trail import AuditTrail
from models.auth.session import UserSession
from models.auth.user_permission import UserPermission
from models.recipe.recipe import Recipe
from models.recipe.batch import Batch
from models.recipe.recipe_parameter import RecipeParameter

def _hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def seed_initial_data(session: Session):
    # 1. Check if roles exist
    if session.query(Role).first():
        print("[db_init] Database already seeded. Skipping...")
        return

    print("[db_init] Seeding initial data...")

    # 2. Roles
    roles = {
        "root": Role(role_name="root", description="Highest access role."),
        "administrator": Role(role_name="administrator", description="Elevated access role."),
        "supervisor": Role(role_name="supervisor", description="Medium access role."),
        "operator": Role(role_name="operator", description="Standard access role.")
    }
    for r in roles.values(): session.add(r)
    session.flush()

    # 3. Permissions
    perms_list = [
        ("CREATE USER", "The ability to create new users for the system."), # 1
        ("EDIT USER", "The ability to edit existing users for the system."), # 2
        ("DELETE USER", "The ability to delete existing users for the system."), # 3
        ("SHOW USER", "The ability to see existing users for the system."), # 4
        ("GIVE PERMISSIONS", "The ability to give other users permissions for system features."), # 5
        ("CHANGE TIME", "The ability to change systems current time."), # 6
        ("SHOW RECIPE", "The ability to see machine current recipe."), # 7
        ("EDIT RECIPE", "The ability to edit machine current recipe parameters."), # 8
        ("START MACHINE", "The ability to start stopped machine."), # 9
        ("STOP MACHINE", "The ability to stop working machine."), # 10
        ("CREATE BACKUP", "The ability to create backup for the database."), # 11
        ("DEPLOY BACKUP", "The ability to use old backups on the system."), # 12
        ("SEND BACKUP", "The ability to share backups to other devices."), # 13
        ("SHOW RECIPE REPORTS", "The ability to display exported recipe reports."), # 14
        ("CREATE RECIPE REPORTS", "The ability to create recipe reports."), # 15
        ("SHOW AUDIT REPORTS", "The ability to display exported audit reports."), # 16
        ("CREATE AUDIT REPORTS", "The ability to create audit reports.") # 17
    ]
    
    perms_objs = []
    for name, desc in perms_list:
        p = Permission(permission_name=name, description=desc)
        session.add(p)
        perms_objs.append(p)
    session.flush()

    # 4. Role-Permissions (Root gets everything)
    for p in perms_objs:
        session.add(RolePermission(role_id=roles["root"].role_id, permission_id=p.permission_id))
    
    # Admin gets some
    admin_perms = [1, 2, 4, 7, 9, 10, 11, 13, 14, 15, 16, 17]
    for p_id in admin_perms:
        session.add(RolePermission(role_id=roles["administrator"].role_id, permission_id=p_id))

    # Supervisor gets some
    supervisor_perms = [7, 9, 10, 14, 15, 16, 17]
    for p_id in supervisor_perms:
        session.add(RolePermission(role_id=roles["supervisor"].role_id, permission_id=p_id))

    # Operator gets some
    operator_perms = [7, 9, 10, 14, 15, 16]
    for p_id in operator_perms:
        session.add(RolePermission(role_id=roles["operator"].role_id, permission_id=p_id))

    session.flush()

    # 5. users
    initial_users = [
        ("root", "root", "Root User", roles["root"].role_id),
        ("administrator", "administrator", "Administrator User", roles["administrator"].role_id),
        ("Engsalehalenbawi@gmail", "Sa_30415018053", "Saleh Alenbawi", roles["administrator"].role_id),
        ("supervisor", "supervisor", "Supervisor User", roles["supervisor"].role_id),
        ("operator", "operator", "Operator User", roles["operator"].role_id)
    ]

    for uname, pwd, fname, rid in initial_users:
        u = User(
            user_name=uname,
            password_hash=_hash_password(pwd),
            full_name=fname,
            role_id=rid,
            is_disabled=False,
            is_deleted=False,
            is_first_login=False
        )
        session.add(u)
    
    session.commit()
    print("[db_init] Initial data seeded successfully.")

def initialize_database():
    print("[db_init] Initializing SQLite Database...")
    Base.metadata.create_all(engine)
    
    # Ensure is_first_login column exists in users table (SQLite)
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_first_login BOOLEAN NOT NULL DEFAULT 1"))
            print("[db_init] Added column is_first_login to users table.")
    except Exception:
        # If it already exists, ignore
        pass

    with SessionLocal() as session:
        seed_initial_data(session)

if __name__ == "__main__":
    initialize_database()
