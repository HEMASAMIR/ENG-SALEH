from typing import List, Optional, Any
from sqlalchemy.orm import Session
from models.recipe.recipe import Recipe, RecipeStatus
from models.recipe.recipe_parameter import RecipeParameter
from models.audit.audit_trail import AuditActionType
from repositories.recipe_repository import RecipeRepository
from repositories.recipe_parameter_repository import RecipeParameterRepository
from repositories.audit_repository import AuditRepository

class RecipeService:
    """
    Service for clinical recipe lifecycle management, including versioning 
    and mutual exclusion for active statuses.
    """
    def __init__(self, session: Session):
        self.session = session
        self.recipe_repo = RecipeRepository(session)
        self.param_repo = RecipeParameterRepository(session)
        self.audit_repo = AuditRepository(session)

    def _validate_bounds(self, value: Any, min_val: Any, max_val: Any) -> bool:
        """
        Ensures a parameter value is within its medically prescribed range.
        Note: Supports JSONB/Numeric comparison logic.
        """
        if min_val is not None and value < min_val:
            return False
        if max_val is not None and value > max_val:
            return False
        return True

    def create_recipe(self, user_id: int, recipe_number: int, parameters_data: List[dict], reason: str = "") -> Recipe:
        """
        Initializes v1 of a recipe with its associated parameters.
        """
        recipe = self.recipe_repo.create(
            recipe_number=recipe_number,
            recipe_version=1,
            created_by=user_id,
            status=RecipeStatus.INACTIVE
        )

        for p in parameters_data:
            # Enforce range safety guardrails on initialization
            if not self._validate_bounds(p['current_value'], p.get('min_value'), p.get('max_value')):
                raise ValueError(f"Initial parameter '{p['parameter_name']}' is out of bounds.")

            self.param_repo.create(
                recipe_id=recipe.recipe_id,
                parameter_name=p['parameter_name'],
                current_value=p['current_value'],
                min_value=p.get('min_value'),
                max_value=p.get('max_value')
            )

        self.audit_repo.create_audit(
            user_id=user_id,
            action_type=AuditActionType.APPEND,
            location_screen="Recipe Management",
            new_value={"recipe_number": recipe_number, "version": 1},
            reason=f"Clinical recipe {recipe_number} (v1) successfully initialized. Reason: {reason}" if reason else f"Clinical recipe {recipe_number} (v1) successfully initialized."
        )
        self.session.flush()
        return recipe

    def create_new_version(self, user_id: int, recipe_number: int, parameter_overrides: List[dict], reason: str = "") -> Recipe:
        """
        Increments version and copies all parameters from the previous version 
        according to standard clinical practice.
        """
        # Fetch latest version to use as baseline
        all_versions = self.session.query(Recipe).filter(
            Recipe.recipe_number == recipe_number,
            Recipe.is_deleted == False
        ).order_by(Recipe.recipe_version.desc()).all()
        
        if not all_versions:
            raise ValueError(f"Base recipe {recipe_number} not found.")

        base_version = all_versions[0]
        new_version_num = base_version.recipe_version + 1

        # Create new version record
        new_recipe = self.recipe_repo.create(
            recipe_number=recipe_number,
            recipe_version=new_version_num,
            created_by=user_id,
            status=RecipeStatus.INACTIVE
        )

        # Map all parameters from previous version and apply overrides
        base_params = self.param_repo.get_by_recipe(base_version.recipe_id)
        overrides_dict = {p['parameter_name']: p for p in parameter_overrides}

        for bp in base_params:
            override = overrides_dict.get(bp.parameter_name, {})
            new_val = override.get('current_value', bp.current_value)
            min_val = override.get('min_value', bp.min_value)
            max_val = override.get('max_value', bp.max_value)

            # Enforce safety guardrails on modifications
            if not self._validate_bounds(new_val, min_val, max_val):
                raise ValueError(f"Override for '{bp.parameter_name}' is outside clinical bounds.")

            self.param_repo.create(
                recipe_id=new_recipe.recipe_id,
                parameter_name=bp.parameter_name,
                current_value=new_val,
                min_value=min_val,
                max_value=max_val
            )

        self.audit_repo.create_audit(
            user_id=user_id,
            action_type=AuditActionType.APPEND,
            location_screen="Recipe Administration",
            new_value={"recipe_number": recipe_number, "version": new_version_num},
            reason=f"Incremented version for recipe {recipe_number} to v{new_version_num}. Reason: {reason}" if reason else f"Incremented version for recipe {recipe_number} to v{new_version_num}."
        )
        self.session.flush()
        return new_recipe

    def activate_recipe(self, user_id: int, recipe_id: int) -> bool:
        """
        Sets a recipe to ACTIVE and guarantees mutual exclusion of the 'ACTIVE' status 
        within the same recipe number.
        """
        target_recipe = self.recipe_repo.get_by_id(recipe_id)
        if not target_recipe:
            return False

        # Deactivate ALL currently active recipes globally (only one active recipe allowed)
        active_recipes = self.session.query(Recipe).filter(
            Recipe.recipe_id != recipe_id,
            Recipe.status == RecipeStatus.ACTIVE,
            Recipe.is_deleted == False
        ).all()

        for r in active_recipes:
            r.status = RecipeStatus.INACTIVE

        target_recipe.status = RecipeStatus.ACTIVE

        # Formally record activation signature
        self.audit_repo.create_audit(
            user_id=user_id,
            action_type=AuditActionType.EDIT,
            location_screen="Clinical Activation Page",
            reason=f"Approved Activation: Recipe {target_recipe.recipe_number} (v{target_recipe.recipe_version})",
            signature_meaning="APPROVED"
        )
        self.session.flush()
        return True

    def deactivate_all(self, user_id: int):
        """
        Deactivates ALL active recipes across the entire system.
        Used primarily during system startup to ensure no recipe is active 
        until explicitly selected.
        """
        active_recipes = self.session.query(Recipe).filter(
            Recipe.status == RecipeStatus.ACTIVE,
            Recipe.is_deleted == False
        ).all()

        for r in active_recipes:
            r.status = RecipeStatus.INACTIVE

        if active_recipes:
            self.audit_repo.create_audit(
                user_id=user_id,
                action_type=AuditActionType.EDIT,
                location_screen="System Startup",
                reason="Automatic deactivation of all recipes for security on startup.",
                signature_meaning="SYSTEM"
            )
            self.session.flush()

    def deactivate_recipe(self, user_id: int, recipe_id: int, reason: str = "") -> bool:
        """
        Deactivates a specific active recipe and records the audit entry with a reason.
        """
        recipe = self.recipe_repo.get_by_id(recipe_id)
        if not recipe or recipe.status != RecipeStatus.ACTIVE:
            return False

        recipe.status = RecipeStatus.INACTIVE

        self.audit_repo.create_audit(
            user_id=user_id,
            action_type=AuditActionType.EDIT,
            location_screen="Batch Page",
            reason=f"Closed Batch: Recipe {recipe.recipe_number} (v{recipe.recipe_version}). Reason: {reason}" if reason else f"Closed Batch: Recipe {recipe.recipe_number} (v{recipe.recipe_version}).",
            signature_meaning="CLOSED"
        )
        self.session.flush()
        return True

    def delete_recipe(self, user_id: int, recipe_id: int) -> bool:
        """
        Soft-deletes a recipe and automatically deactivates it if it was active.
        """
        recipe = self.recipe_repo.get_by_id(recipe_id)
        if not recipe:
            return False

        # Clinical Safety: Deactivate before soft-deletion
        if recipe.status == RecipeStatus.ACTIVE:
            recipe.status = RecipeStatus.INACTIVE

        success = self.recipe_repo.delete_soft(recipe_id)
        if success:
            self.audit_repo.create_audit(
                user_id=user_id,
                action_type=AuditActionType.DELETE,
                location_screen="System Cleanup",
                reason=f"Soft-deleted Recipe {recipe.recipe_number} (v{recipe.recipe_version})."
            )
            self.session.flush()
    def increment_recipe_counts(self, recipe_id: int, is_good: bool):
        """
        Increments the clinical processing counts for an active recipe.
        Standard for pharmaceutical validation monitoring.
        """
        def _inc(name):
            param = self.param_repo.get_by_name(recipe_id, name)
            if param:
                try:
                    # Robust handling: cast to int in case it was stored as a string by mistake
                    val = int(param.current_value.get('value', 0))
                except (ValueError, TypeError):
                    val = 0
                
                # We update the dictionary as it is JSONB
                param.current_value = {"value": val + 1}

        # Always increment total
        _inc("total_count")
        
        if is_good:
            _inc("good_count")
        else:
            _inc("wrong_count")
            
        self.session.flush()
