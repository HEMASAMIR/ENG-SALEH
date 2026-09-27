import enum
import uuid
from sqlalchemy import Column, Integer, DateTime, ForeignKey, Enum, Text, text, String, JSON
from sqlalchemy.orm import relationship
from core.database import Base


class AuditActionType(enum.Enum):
    APPEND = 'APPEND'
    EDIT = 'EDIT'
    DELETE = 'DELETE'
    LOG_IN = 'LOG_IN'
    LOG_OUT = 'LOG_OUT'
    NAVIGATE = 'NAVIGATE'
    EXPORT = 'EXPORT'
    BACKUP = 'BACKUP'
    RESTORE = 'RESTORE'


class AuditTrail(Base):

    __tablename__ = "audit_trail"

    audit_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_timestamp = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action_type = Column(Enum(AuditActionType), nullable=False)
    location_screen = Column(String(255))
    old_value = Column(JSON)
    new_value = Column(JSON)
    reason = Column(Text)
    signature_format = Column(Text, nullable=False, default="None")
    signature_value = Column(Text)
    signature_meaning = Column(Text)

    user = relationship("User", back_populates="audits")
    