from typing import List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime
from models.audit.audit_trail import AuditTrail, AuditActionType

class AuditRepository:
    """
    Append-only repository for audit trails to maintain clinical data integrity.
    """
    def __init__(self, session: Session):
        self.session = session

    def create_audit(self, user_id: int, action_type: AuditActionType, location_screen: str, 
                     old_value: dict = None, new_value: dict = None, reason: str = None, 
                     signature_format: str = "plain", signature_value: str = None, 
                     signature_meaning: str = None) -> AuditTrail:
        
        audit = AuditTrail(
            user_id=user_id,
            action_type=action_type,
            location_screen=location_screen,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            signature_format=signature_format,
            signature_value=signature_value,
            signature_meaning=signature_meaning
        )
        self.session.add(audit)
        self.session.flush()
        return audit

    def get_by_id(self, audit_id: Any) -> Optional[AuditTrail]:
        query = select(AuditTrail).filter(AuditTrail.audit_id == audit_id)
        return self.session.execute(query).scalar_one_or_none()

    def get_by_user(self, user_id: int) -> List[AuditTrail]:
        query = select(AuditTrail).filter(AuditTrail.user_id == user_id).order_by(AuditTrail.audit_timestamp.desc())
        return self.session.execute(query).scalars().all()

    def list_all(self, limit: int = 100) -> List[AuditTrail]:
        query = select(AuditTrail).order_by(AuditTrail.audit_timestamp.desc()).limit(limit)
        return self.session.execute(query).scalars().all()

    def list_by_date_range(self, start_date: datetime, end_date: datetime, limit: int = 500) -> List[AuditTrail]:
        query = (
            select(AuditTrail)
            .filter(
                AuditTrail.audit_timestamp >= start_date,
                AuditTrail.audit_timestamp <= end_date
            )
            .order_by(AuditTrail.audit_timestamp.desc())
            .limit(limit)
        )
        return self.session.execute(query).scalars().all()

    def get_last_action(self, action_type: AuditActionType) -> Optional[AuditTrail]:
        """Returns the most recent audit entry for a specific action."""
        query = (
            select(AuditTrail)
            .filter(AuditTrail.action_type == action_type)
            .order_by(AuditTrail.audit_timestamp.desc())
        )
        return self.session.execute(query).scalars().first()
