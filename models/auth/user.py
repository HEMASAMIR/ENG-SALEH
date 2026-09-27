from sqlalchemy import Column, Integer, ForeignKey, DateTime, String, Text, Boolean, text
from sqlalchemy.orm import relationship
from core.database import Base

class User(Base):

    __tablename__ = "users"

    id = Column(Integer, autoincrement=True, primary_key=True)
    user_name = Column(String(50), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    full_name = Column(String(255), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.role_id"), nullable=False)
    last_login = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    password_last_change = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    failed_attempts = Column(Integer, server_default=text("0"))
    is_disabled = Column(Boolean, server_default=text("0"))
    is_first_login = Column(Boolean, server_default=text("1"), default=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    is_deleted = Column(Boolean, nullable=False, server_default=text("0"))
    deleted_at = Column(DateTime(timezone=True))


    role = relationship("Role", back_populates="users")
    creator = relationship("User", remote_side=[id], passive_updates=True)
    permissions = relationship("UserPermission", back_populates="users")
    audits = relationship("AuditTrail", back_populates="user")
    created_recipes = relationship("Recipe", back_populates="creator")