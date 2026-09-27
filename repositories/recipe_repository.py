from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import select
from models.recipe.recipe import Recipe, RecipeStatus
from .base_repository import BaseRepository

class RecipeRepository(BaseRepository[Recipe]):
    def __init__(self, session: Session):
        super().__init__(Recipe, session)

    def get_by_number_and_version(self, recipe_number: int, recipe_version: int) -> Optional[Recipe]:
        query = select(Recipe).filter(
            Recipe.recipe_number == recipe_number,
            Recipe.recipe_version == recipe_version,
            Recipe.is_deleted == False
        )
        return self.session.execute(query).scalar_one_or_none()

    def get_active_recipe(self, recipe_number: int) -> Optional[Recipe]:
        query = select(Recipe).filter(
            Recipe.recipe_number == recipe_number,
            Recipe.status == RecipeStatus.ACTIVE,
            Recipe.is_deleted == False
        )
        return self.session.execute(query).scalar_one_or_none()

    def list_by_status(self, status: RecipeStatus) -> List[Recipe]:
        query = select(Recipe).filter(
            Recipe.status == status,
            Recipe.is_deleted == False
        )
        return self.session.execute(query).scalars().all()
