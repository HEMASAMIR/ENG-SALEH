from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from models.auth.role import Role
from .base_repository import BaseRepository

class RoleRepository(BaseRepository[Role]):
    def __init__(self, session: Session):
        super().__init__(Role, session)

    def get_by_name(self, role_name: str) -> Optional[Role]:
        query = select(Role).filter(Role.role_name == role_name, Role.is_deleted == False)
        return self.session.execute(query).scalar_one_or_none()
