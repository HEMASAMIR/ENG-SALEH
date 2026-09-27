import bcrypt
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple, Any
from sqlalchemy.orm import Session
from models.auth.user import User
from models.auth.session import UserSession
from models.audit.audit_trail import AuditActionType
from repositories.user_repository import UserRepository
from repositories.audit_repository import AuditRepository
from repositories.session_repository import SessionRepository
from core.config import settings
from datetime import datetime, timezone, timedelta

class AuthService:
    """
    Service for clinical-grade authentication and user lifecycle management.
    """
    def __init__(self, session: Session):
        self.session = session
        self.user_repo = UserRepository(session)
        self.audit_repo = AuditRepository(session)
        self.session_repo = SessionRepository(session)

    def _hash_password(self, password: str) -> str:
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

    def _verify_password(self, password: str, hashed_password: str) -> bool:
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))

    def get_password_errors(self, password: str) -> list[str]:
        """Validates password and returns a list of missing requirements."""
        errors = []
        if not password:
            return ["Password cannot be empty."]
            
        if len(password) < settings.AUTH_MIN_PASSWORD_LENGTH:
            errors.append(f"• minimum {settings.AUTH_MIN_PASSWORD_LENGTH} characters")
            
        if not re.search(r"[A-Z]", password):
            errors.append("• at least one uppercase letter (A-Z)")
            
        if not re.search(r"[a-z]", password):
            errors.append("• at least one lowercase letter (a-z)")
            
        if not re.search(r"\d", password):
            errors.append("• at least one numeric digit (0-9)")
            
        if not re.search(r"[!@#$%^&*(),.?\":{}|_<>]", password):
            errors.append("• at least one special character (!@#$%^&*_, etc.)")
            
        return errors

    @staticmethod
    def get_password_complexity_status(password: str) -> dict:
        """Returns a status dictionary for each complexity requirement. No DB session required."""
        from core.config import settings
        import re
        if not password:
            return {
                "length": False,
                "uppercase": False,
                "lowercase": False,
                "digit": False,
                "special": False
            }
        return {
            "length": len(password) >= settings.AUTH_MIN_PASSWORD_LENGTH,
            "uppercase": bool(re.search(r"[A-Z]", password)),
            "lowercase": bool(re.search(r"[a-z]", password)),
            "digit": bool(re.search(r"\d", password)),
            "special": bool(re.search(r"[!@#$%^&*(),.?\":{}|_<>]", password))
        }

    def _is_password_complex(self, password: str) -> bool:
        return len(self.get_password_errors(password)) == 0

    def authenticate(self, user_name: str, password: str) -> Tuple[Optional[UserSession], str]:
        """
        Authenticates a user and establishes a clinical session with an inactivity timeout.
        """
        user = self.user_repo.get_by_username(user_name)
        
        if not user:
            return None, "Invalid credentials."

        if user.is_disabled:
            return None, "Account is disabled. Contact system administrator."

        if self._verify_password(password, user.password_hash):
            # Reset failed attempts
            user.failed_attempts = 0
            user.last_login = datetime.utcnow()
            
            # Clinical Standard: Invalidate previous sessions to prevent hijacking
            self.session_repo.deactivate_all_for_user(user.id)
            
            # Establish new clinical session
            expires_at = datetime.utcnow() + timedelta(minutes=settings.AUTH_SESSION_TIMEOUT_MINUTES)
            auth_session = self.session_repo.create(
                user_id=user.id,
                expires_at=expires_at,
                last_activity=datetime.utcnow(),
                is_active=True
            )
            
            self.audit_repo.create_audit(
                user_id=user.id,
                action_type=AuditActionType.LOG_IN,
                location_screen="Login Page",
                reason="Clinical session established (Success)"
            )
            self.session.flush()
            return auth_session, "Success"
        else:
            # Increment failed attempts and trigger lockout if necessary
            user.failed_attempts += 1
            if user.failed_attempts >= settings.AUTH_MAX_FAILED_ATTEMPTS:
                user.is_disabled = True
                reason = f"Account locked after {settings.AUTH_MAX_FAILED_ATTEMPTS} failed attempts."
            else:
                reason = "Invalid password attempt."

            # Log failed activity (critical for forensic trail)
            self.audit_repo.create_audit(
                user_id=user.id,
                action_type=AuditActionType.LOG_IN,
                location_screen="Login Page",
                reason=reason
            )
            self.session.flush()
            return None, reason

    def verify_credentials(self, user_name: str, password: str, location_screen: str = "Authorization Dialog") -> Tuple[Optional[User], str]:
        """
        Verifies credentials for privileged action authorization without creating
        or invalidating user sessions.
        """
        user = self.user_repo.get_by_username(user_name)
        if not user:
            return None, "Invalid credentials."

        if user.is_disabled:
            return None, "Account is disabled. Contact system administrator."

        if self._verify_password(password, user.password_hash):
            # Successful privileged re-auth: reset failed attempts only.
            user.failed_attempts = 0
            self.audit_repo.create_audit(
                user_id=user.id,
                action_type=AuditActionType.LOG_IN,
                location_screen=location_screen,
                reason="Credential re-authorization success."
            )
            self.session.flush()
            return user, "Success"

        user.failed_attempts += 1
        if user.failed_attempts >= settings.AUTH_MAX_FAILED_ATTEMPTS:
            user.is_disabled = True
            reason = f"Account locked after {settings.AUTH_MAX_FAILED_ATTEMPTS} failed attempts."
        else:
            reason = "Invalid password attempt."

        self.audit_repo.create_audit(
            user_id=user.id,
            action_type=AuditActionType.LOG_IN,
            location_screen=location_screen,
            reason=f"Credential re-authorization failed. {reason}"
        )
        self.session.flush()
        return None, reason

    def register_user(self, creator_id: int, user_data: dict) -> Tuple[Optional[User], str]:
        """
        Registers a new user ensureing clinical password complexity.
        """
        password = user_data.get("password")
        errors = self.get_password_errors(password)
        if errors:
            msg = "Password does not meet requirements:\n" + "\n".join(errors)
            return None, msg

        hashed = self._hash_password(password)
        
        user = self.user_repo.create_user(
            user_name=user_data["user_name"],
            password_hash=hashed,
            full_name=user_data["full_name"],
            role_id=user_data["role_id"],
            created_by=creator_id
        )

        reason = user_data.get("reason", "")
        self.audit_repo.create_audit(
            user_id=creator_id,
            action_type=AuditActionType.APPEND,
            location_screen="User Administration",
            new_value={"created_user": user.user_name},
            reason=f"New user registered: {user.user_name}. Reason: {reason}" if reason else f"New user registered: {user.user_name}"
        )
        self.session.flush()
        return user, "Success"

    def change_password(self, user_id: int, current_password: str, new_password: str) -> Tuple[bool, str]:
        """
        Updates a user's password with verification of clinical complexity.
        """
        user = self.user_repo.get_by_id(user_id)
        if not user or not self._verify_password(current_password, user.password_hash):
            return False, "Current password incorrect."

        errors = self.get_password_errors(new_password)
        if errors:
            msg = "New password does not meet requirements:\n" + "\n".join(errors)
            return False, msg

        user.password_hash = self._hash_password(new_password)
        user.password_last_change = datetime.utcnow()
        
        self.audit_repo.create_audit(
            user_id=user_id,
            action_type=AuditActionType.EDIT,
            location_screen="User Security Profile",
            reason="User self-updated password."
        )
        self.session.flush()
        return True, "Success"

    def validate_session(self, session_id: Any) -> Tuple[Optional[UserSession], str]:
        """
        Mandatory 21 CFR Part 11 check for session validity and inactivity timeouts.
        """
        auth_session = self.session_repo.get_by_id(session_id)
        if not auth_session or not auth_session.is_active:
            return None, "Session invalid or already logged out."

        now = datetime.utcnow()
        
        # Rigorous Clinical Inactivity Check
        if now > auth_session.expires_at:
            auth_session.is_active = False
            self.audit_repo.create_audit(
                user_id=auth_session.user_id,
                action_type=AuditActionType.LOG_OUT,
                location_screen="System Background",
                reason="Automatic clinical logout due to inactivity threshold reached."
            )
            self.session.flush()
            return None, "Session expired due to inactivity."

        # Extend session on activity
        auth_session.last_activity = now
        auth_session.expires_at = now + timedelta(minutes=settings.AUTH_SESSION_TIMEOUT_MINUTES)
        self.session.flush()
        return auth_session, "Session Extended"

    def logout(self, session_id: Any) -> bool:
        """
        Formally terminates a clinical session and records the forensic event.
        """
        auth_session = self.session_repo.get_by_id(session_id)
        if auth_session and auth_session.is_active:
            auth_session.is_active = False
            self.audit_repo.create_audit(
                user_id=auth_session.user_id,
                action_type=AuditActionType.LOG_OUT,
                location_screen="System Hub",
                reason="Manual user logout performed."
            )
            self.session.flush()
            return True
        return False
