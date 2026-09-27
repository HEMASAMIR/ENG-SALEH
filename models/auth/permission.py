from sqlalchemy import Column, Integer, Text, String, Boolean, DateTime, text
from sqlalchemy.orm import relationship
from core.database import Base

class Permission(Base):

    __tablename__ = "permissions"

    permission_id = Column(Integer, autoincrement=True, primary_key=True)
    permission_name = Column(String(255), unique=True, nullable=False)
    description = Column(Text)
    is_deleted = Column(Boolean, nullable=False, server_default=text("false"))
    deleted_at = Column(DateTime(timezone=True))

    roles = relationship("RolePermission", back_populates="permissions")