from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from models.recipe.recipe_parameter import RecipeParameter
from .base_repository import BaseRepository

class RecipeParameterRepository(BaseRepository[RecipeParameter]):
    def __init__(self, session: Session):
        super().__init__(RecipeParameter, session)

    def get_by_recipe(self, recipe_id: int) -> List[RecipeParameter]:
        query = select(RecipeParameter).filter(
            RecipeParameter.recipe_id == recipe_id
        )
        return self.session.execute(query).scalars().all()

    def get_by_name(self, recipe_id: int, parameter_name: str) -> Optional[RecipeParameter]:
        query = select(RecipeParameter).filter(
            RecipeParameter.recipe_id == recipe_id,
            RecipeParameter.parameter_name == parameter_name
        )
        return self.session.execute(query).scalar_one_or_none()
