from sqlalchemy import Column, Integer, ForeignKey
from sqlalchemy.orm import relationship
from core.database import Base

class RolePermission(Base):

    __tablename__ = "role_permission"

    role_id = Column(Integer, ForeignKey("roles.role_id", ondelete="CASCADE"), primary_key=True)
    permission_id = Column(Integer, ForeignKey("permissions.permission_id", ondelete="CASCADE"), primary_key=True)

    role = relationship("Role", back_populates="permissions")
    permissions = relationship("Permission", back_populates="roles")