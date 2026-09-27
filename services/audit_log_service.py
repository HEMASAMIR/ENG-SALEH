import hashlib
import json
from datetime import datetime
from typing import List, Optional, Tuple, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, and_
from models.audit.audit_trail import AuditTrail, AuditActionType
from repositories.audit_repository import AuditRepository
from core.config import settings

class AuditLogService:
    """
    Administrative service to manage, search, and verify the integrity 
    of clinical audit trails (21 CFR Part 11).
    """
    def __init__(self, session: Session):
        self.session = session
        self.audit_repo = AuditRepository(session)

    def _calculate_row_hash(self, audit: AuditTrail) -> str:
        """
        Creates a deterministic hash of the audit record content as a 10/10 medical standard.
        """
        # We hash the critical clinical data that should remain immutable
        data = {
            "timestamp": audit.audit_timestamp.isoformat() if audit.audit_timestamp else "none",
            "user_id": audit.user_id,
            "action": audit.action_type.value if hasattr(audit.action_type, 'value') else str(audit.action_type),
            "old_val": audit.old_value,
            "new_val": audit.new_value,
            "reason": audit.reason
        }
        # Secure serialization for deterministic hashing
        encoded = json.dumps(data, sort_keys=True).encode('utf-8')
        return hashlib.sha256(encoded + settings.HASH_SECRET_KEY.encode('utf-8')).hexdigest()

    def search_logs(
        self,
        user_id: int = None,
        user_ids: List[int] = None,
        action_type: AuditActionType = None,
        start_date: datetime = None,
        end_date: datetime = None,
        limit: int = 100
    ) -> List[AuditTrail]:
        """
        Advanced clinical search across the immutable audit log.
        """
        query = select(AuditTrail)
        filters = []
        if user_id:
            filters.append(AuditTrail.user_id == user_id)
        if user_ids:
            filters.append(AuditTrail.user_id.in_(user_ids))
        if action_type:
            filters.append(AuditTrail.action_type == action_type)
        if start_date:
            filters.append(AuditTrail.audit_timestamp >= start_date)
        if end_date:
            filters.append(AuditTrail.audit_timestamp <= end_date)

        if filters:
            query = query.filter(and_(*filters))

        query = query.order_by(AuditTrail.audit_timestamp.desc()).limit(limit)
        return self.session.execute(query).scalars().all()

    def verify_audit_integrity(self, audit_id: Any) -> Tuple[bool, str]:
        """
        Forensic verification of a specific audit entry to detect tampering.
        """
        audit = self.audit_repo.get_by_id(audit_id)
        if not audit:
            return False, "Audit record not found."

        if not audit.signature_value:
            return False, "Incomplete record: Cryptographic signature missing."

        current_actual_hash = self._calculate_row_hash(audit)
        if audit.signature_value == current_actual_hash:
            return True, "Integrity Verified: Record is authentic."
        else:
            return False, "CRITICAL: Integrity Compromised. Record has been altered after signing."

    def sign_audit_entry(self, audit_id: Any) -> bool:
        """
        Finalizes an audit entry by calculating and applying the forensic hash.
        """
        audit = self.audit_repo.get_by_id(audit_id)
        if audit:
            audit.signature_value = self._calculate_row_hash(audit)
            self.session.flush()
            return True
        return False

    def get_compliance_review_data(self, start_date: datetime, end_date: datetime) -> List[AuditTrail]:
        """
        Specialized structured data for clinical regulatory reviews (Focuses on edits/deletes).
        """
        query = select(AuditTrail).filter(
            and_(
                AuditTrail.audit_timestamp >= start_date,
                AuditTrail.audit_timestamp <= end_date,
                AuditTrail.action_type.in_([AuditActionType.EDIT, AuditActionType.DELETE])
            )
        ).order_by(AuditTrail.audit_timestamp.desc())
        
        return self.session.execute(query).scalars().all()
