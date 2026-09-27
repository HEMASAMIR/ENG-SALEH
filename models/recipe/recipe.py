import enum
from sqlalchemy import Column, Integer, Enum, Text, ForeignKey, DateTime, text, UniqueConstraint, Index, Boolean
from sqlalchemy.orm import relationship
from core.database import Base


class RecipeStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class Recipe(Base):

    __tablename__ = "recipes"

    recipe_id = Column(Integer, autoincrement=True, primary_key=True)
    recipe_number = Column(Integer, nullable=False)
    recipe_version = Column(Integer, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    status = Column(Enum(RecipeStatus), nullable=False)
    is_deleted = Column(Boolean, nullable=False, server_default=text("0"))
    deleted_at = Column(DateTime(timezone=True))

    parameters = relationship("RecipeParameter", back_populates="recipe")
    creator = relationship("User", back_populates="created_recipes")

    __table_args__ = (
        UniqueConstraint(
            "recipe_number",
            "recipe_version",
            name="uq_recipe_number_version"
        ),
    )