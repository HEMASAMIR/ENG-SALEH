from typing import Optional, List, Any
from sqlalchemy.orm import Session
from sqlalchemy import select
from models.auth.user import User
from .base_repository import BaseRepository

class UserRepository(BaseRepository[User]):
    def __init__(self, session: Session):
        super().__init__(User, session)

    def get_by_username(self, user_name: str) -> Optional[User]:
        query = select(User).filter(User.user_name == user_name, User.is_deleted == False)
        return self.session.execute(query).scalar_one_or_none()

    def create_user(self, user_name: str, password_hash: str, full_name: str, role_id: int, created_by: int) -> User:
        return self.create(
            user_name=user_name,
            password_hash=password_hash,
            full_name=full_name,
            role_id=role_id,
            created_by=created_by
        )