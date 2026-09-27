from typing import TypeVar, Generic, Type, Optional, List, Any
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone

T = TypeVar("T")

class BaseRepository(Generic[T]):
    """
    Base Repository with support for standard CRUD and Soft Deletes.
    """
    def __init__(self, model: Type[T], session: Session):
        self.model = model
        self.session = session

    def _get_pk_name(self) -> str:
        return self.model.__mapper__.primary_key[0].name

    def get_by_id(self, id: Any) -> Optional[T]:
        pk_name = self._get_pk_name()
        query = select(self.model).filter(getattr(self.model, pk_name) == id)
        
        if hasattr(self.model, "is_deleted"):
            query = query.filter(self.model.is_deleted == False)
            
        return self.session.execute(query).scalar_one_or_none()

    def all(self) -> List[T]:
        query = select(self.model)
        if hasattr(self.model, "is_deleted"):
            query = query.filter(self.model.is_deleted == False)
        return self.session.execute(query).scalars().all()

    def create(self, **kwargs) -> T:
        instance = self.model(**kwargs)
        self.session.add(instance)
        self.session.flush()
        return instance

    def update(self, id: Any, **kwargs) -> Optional[T]:
        instance = self.get_by_id(id)
        if not instance:
            return None
        for key, value in kwargs.items():
            setattr(instance, key, value)
        self.session.flush()
        return instance

    def delete_soft(self, id: Any) -> bool:
        instance = self.get_by_id(id)
        if not instance:
            return False
        
        if hasattr(instance, "is_deleted"):
            instance.is_deleted = True
            instance.deleted_at = datetime.now(timezone.utc)
            self.session.flush()
            return True
        return False

    def delete_hard(self, id: Any) -> bool:
        instance = self.get_by_id(id)
        if not instance:
            return False
        self.session.delete(instance)
        self.session.flush()
        return True
