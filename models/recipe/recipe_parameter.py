from sqlalchemy import Column, Integer, String, ForeignKey, JSON
from sqlalchemy.orm import relationship
from core.database import Base


class RecipeParameter(Base):
    """
    Detailed parameters for inspection recipes (e.g. ROI coordinates, OCR thresholds).
    """
    __tablename__ = "recipe_parameters"

    parameter_id = Column(Integer, autoincrement=True, primary_key=True)
    recipe_id = Column(Integer, ForeignKey("recipes.recipe_id"), nullable=False)
    parameter_name = Column(String(100), nullable=False)
    current_value = Column(JSON, nullable=False)
    min_value = Column(JSON)
    max_value = Column(JSON)

    recipe = relationship("Recipe", back_populates="parameters")