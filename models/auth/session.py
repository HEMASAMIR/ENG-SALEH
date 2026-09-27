import uuid
from sqlalchemy import Column, Integer, ForeignKey, DateTime, Boolean, text, String
from sqlalchemy.orm import relationship
from core.database import Base


class UserSession(Base):
    """
    Tracks active clinical user sessions for mandatory auto-logout compliance.
    """
    __tablename__ = "user_sessions"

    session_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_activity = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("1")) # SQLite True=1

    user = relationship("User")
