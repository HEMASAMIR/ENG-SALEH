from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from models.auth.permission import Permission
from .base_repository import BaseRepository

class PermissionRepository(BaseRepository[Permission]):
    def __init__(self, session: Session):
        super().__init__(Permission, session)

    def get_by_name(self, permission_name: str) -> Optional[Permission]:
        query = select(Permission).filter(Permission.permission_name == permission_name, Permission.is_deleted == False)
        return self.session.execute(query).scalar_one_or_none()
