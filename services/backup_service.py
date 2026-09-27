import os
import sys
import shutil
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.orm import Session
from core.config import settings
from models.audit.audit_trail import AuditActionType
from repositories.audit_repository import AuditRepository

class BackupService:
    def __init__(self, session: Session):
        self.session = session
        self.audit_repo = AuditRepository(session)

    def _get_app_base_path(self):
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def perform_backup(self, user_id: int, reason: str = "Manual Backup") -> Optional[str]:
        """Performs a backup of the SQLite database."""
        try:
            base_dir = self._get_app_base_path()
            backup_dir = os.path.join(base_dir, "backup")
            os.makedirs(backup_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_filename = f"backup_{timestamp}.db"
            backup_path = os.path.join(backup_dir, backup_filename)

            # Resolve DB path from settings
            db_path = settings.DATABASE_URL.replace("sqlite:///", "")
            if not os.path.isabs(db_path):
                db_path = os.path.join(base_dir, db_path)

            if os.path.exists(db_path):
                shutil.copy2(db_path, backup_path)
                
                # Audit the action
                self.audit_repo.create_audit(
                    user_id=user_id,
                    action_type=AuditActionType.BACKUP,
                    location_screen="System/Service",
                    new_value={"backup_file": backup_path},
                    reason=reason
                )
                self.session.commit()
                return backup_path
            return None
        except Exception as e:
            print(f"Backup Error: {e}")
            return None

    def restore_backup(self, user_id: int, backup_file_path: str, reason: str = "Manual Restore") -> bool:
        """Restores a database from a backup file."""
        try:
            if not os.path.exists(backup_file_path):
                return False

            base_dir = self._get_app_base_path()
            db_path = settings.DATABASE_URL.replace("sqlite:///", "")
            if not os.path.isabs(db_path):
                db_path = os.path.join(base_dir, db_path)

            # Audit the action BEFORE the file is replaced (if possible) or after.
            # Since SQLite might lock, we do it in a transaction.
            self.audit_repo.create_audit(
                user_id=user_id,
                action_type=AuditActionType.RESTORE,
                location_screen="System/Restore",
                old_value={"db_path": db_path},
                new_value={"restored_from": backup_file_path},
                reason=reason
            )
            self.session.commit()

            # Perform the copy
            shutil.copy2(backup_file_path, db_path)
            return True
        except Exception as e:
            print(f"Restore Error: {e}")
            return False

    def check_and_trigger_auto_backup(self, user_id: int = 1) -> Optional[str]:
        """Checks if 30 days have passed since the last backup and triggers one if so."""
        try:
            last_backup = self.audit_repo.get_last_action(AuditActionType.BACKUP)
            
            now = datetime.now(timezone.utc)
            should_backup = False
            
            if not last_backup:
                should_backup = True
            else:
                # Check if 30 days passed
                last_time = last_backup.audit_timestamp
                if last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=timezone.utc)
                
                if (now - last_time) >= timedelta(days=30):
                    should_backup = True

            if should_backup:
                return self.perform_backup(user_id, reason="Monthly Automatic Backup")
        except Exception as e:
            print(f"[BackupService] Error in auto-check: {e}")
            
        return None
