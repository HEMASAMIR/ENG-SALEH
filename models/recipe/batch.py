import uuid
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, text
from sqlalchemy.orm import relationship
from core.database import Base


class Batch(Base):
    """
    Represents a clinical production run (Batch) for reporting and traceability (Req #18).
    """
    __tablename__ = "batches"

    batch_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_number = Column(String(100), unique=True, nullable=False)
    recipe_id = Column(Integer, ForeignKey("recipes.recipe_id"), nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    finished_at = Column(DateTime(timezone=True))
    status = Column(String(50), nullable=False, server_default=text("'RUNNING'"))
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    recipe = relationship("Recipe")
    operator = relationship("User")
