from typing import Optional, List, Any
from sqlalchemy.orm import Session
from sqlalchemy import select
from models.recipe.batch import Batch
from .base_repository import BaseRepository

class BatchRepository(BaseRepository[Batch]):
    """
    Repository for managing clinical production batches.
    """
    def __init__(self, session: Session):
        super().__init__(Batch, session)

    def get_by_number(self, batch_number: str) -> Optional[Batch]:
        """
        Retrieves a batch by its unique clinical batch number.
        """
        query = select(Batch).filter(Batch.batch_number == batch_number)
        return self.session.execute(query).scalar_one_or_none()

    def list_active(self) -> List[Batch]:
        """
        Lists all batches currently in production.
        """
        query = select(Batch).filter(Batch.status == 'RUNNING')
        return self.session.execute(query).scalars().all()
