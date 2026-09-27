from typing import Optional, List, Any
from sqlalchemy.orm import Session
from sqlalchemy import select
from models.auth.session import UserSession
from .base_repository import BaseRepository

class SessionRepository(BaseRepository[UserSession]):
    """
    Repository for managing clinical user sessions.
    """
    def __init__(self, session: Session):
        super().__init__(UserSession, session)

    def get_active_by_user(self, user_id: int) -> Optional[UserSession]:
        """
        Retrieves the current active session for a specific user.
        """
        query = select(UserSession).filter(
            UserSession.user_id == user_id,
            UserSession.is_active == True
        )
        return self.session.execute(query).scalar_one_or_none()

    def deactivate_all_for_user(self, user_id: int):
        """
        Invalidates all existing sessions for a user (e.g., on new lockout or login).
        """
        query = select(UserSession).filter(
            UserSession.user_id == user_id,
            UserSession.is_active == True
        )
        sessions = self.session.execute(query).scalars().all()
        for s in sessions:
            s.is_active = False
        self.session.flush()
