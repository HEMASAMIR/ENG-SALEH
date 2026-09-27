from sqlalchemy import Column, Integer, Text, String, Boolean, DateTime, text
from sqlalchemy.orm import relationship
from core.database import Base

class Role(Base):

    __tablename__ = "roles"
    
    role_id = Column(Integer, autoincrement=True, primary_key=True)
    role_name = Column(String(50), unique=True, nullable=False)
    description = Column(Text)
    is_deleted = Column(Boolean, nullable=False, server_default=text("0"))
    deleted_at = Column(DateTime(timezone=True))

    permissions = relationship("RolePermission", back_populates="role")
    users = relationship("User", back_populates="role")
    