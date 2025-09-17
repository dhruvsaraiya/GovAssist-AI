"""Form field management system for step-by-step form filling.

This module handles form schema parsing, field sequencing, and form state management
for interactive form filling where the AI asks for one field at a time.
"""

import json
import os
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FormField:
    """Represents a single form field with its metadata."""
    id: str
    label: str
    type: str
    required: bool
    options: Optional[List[str]] = None
    validation: Optional[Dict[str, Any]] = None


@dataclass
class FormSession:
    """Manages the state of an active form filling session."""
    form_id: str
    title: str
    fields: List[FormField]
    current_field_index: int = 0
    completed_fields: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.completed_fields is None:
            self.completed_fields = {}
    
    @property
    def current_field(self) -> Optional[FormField]:
        """Get the current field to be filled."""
        if 0 <= self.current_field_index < len(self.fields):
            return self.fields[self.current_field_index]
        return None
    
    @property
    def is_complete(self) -> bool:
        """Check if all required fields are completed."""
        return self.current_field_index >= len(self.fields)
    
    @property
    def progress_percentage(self) -> float:
        """Get completion percentage."""
        if not self.fields:
            return 100.0
        return (self.current_field_index / len(self.fields)) * 100
    
    def set_field_value(self, field_id: str, value: Any) -> bool:
        """Set a field value and advance to next field if current."""
        if self.current_field and self.current_field.id == field_id:
            self.completed_fields[field_id] = value
            self.current_field_index += 1
            return True
        return False
    
    def get_next_field_prompt(self) -> Optional[str]:
        """Generate a natural language prompt for the next field."""
        if self.current_field is None:
            return None
        
        field = self.current_field
        prompt = f"What is your {field.label.lower()}?"
        
        if field.type == "date":
            prompt += " (Please provide in YYYY-MM-DD format)"
        elif field.type == "number":
            prompt += " (Please provide a number)"
        elif field.options:
            prompt += f" (Choose from: {', '.join(field.options)})"
        
        if field.required:
            prompt += " (Required)"
        
        return prompt


class FormFieldManager:
    """Manages form schemas and active form sessions."""
    
    def __init__(self, schemas_path: str = "form_schemas"):
        self.schemas_path = Path(schemas_path)
        self.active_sessions: Dict[str, FormSession] = {}  # user_id -> FormSession
        self._form_schemas_cache: Dict[str, Dict] = {}
    
    def load_form_schema(self, form_name: str) -> Optional[Dict]:
        """Load form schema from JSON file."""
        if form_name in self._form_schemas_cache:
            return self._form_schemas_cache[form_name]
        
        # Map form names to schema file names
        form_file_mapping = {
            "income": "formIncome",
            "mudra": "formIncome", 
            "aadhaar": "formAadhaar",
            "aadhar": "formAadhaar"
        }
        
        schema_filename = form_file_mapping.get(form_name, form_name)
        schema_file = self.schemas_path / f"{schema_filename}.json"
        
        if not schema_file.exists():
            return None
        
        try:
            with open(schema_file, 'r', encoding='utf-8') as f:
                schema = json.load(f)
                self._form_schemas_cache[form_name] = schema
                return schema
        except (json.JSONDecodeError, FileNotFoundError):
            return None
    
    def create_form_session(self, user_id: str, form_name: str) -> Optional[FormSession]:
        """Create a new form session for a user."""
        schema = self.load_form_schema(form_name)
        if not schema:
            print(f"[DEBUG] Failed to load schema for form: {form_name}")
            return None
        
        print(f"[DEBUG] Creating form session for user {user_id}, form {form_name}, schema loaded with {len(schema.get('fields', []))} fields")
        
        fields = []
        for field_data in schema.get("fields", []):
            field = FormField(
                id=field_data["id"],
                label=field_data["label"],
                type=field_data["type"],
                required=field_data.get("required", False),
                options=field_data.get("options"),
                validation=field_data.get("validation")
            )
            fields.append(field)
        
        session = FormSession(
            form_id=schema["form_id"],
            title=schema["title"],
            fields=fields
        )
        
        self.active_sessions[user_id] = session
        return session
    
    def get_active_session(self, user_id: str) -> Optional[FormSession]:
        """Get the active form session for a user."""
        return self.active_sessions.get(user_id)
    
    def process_user_answer(self, user_id: str, answer: str) -> Dict[str, Any]:
        """Process user's answer to current field and return response data."""
        session = self.get_active_session(user_id)
        if not session or not session.current_field:
            return {
                "success": False,
                "error": "No active form session or no current field"
            }
        
        current_field = session.current_field
        
        # Basic validation and type conversion
        processed_value = self._validate_and_convert_value(current_field, answer)
        if processed_value is None:
            return {
                "success": False,
                "error": f"Invalid value for {current_field.label}. Expected {current_field.type}.",
                "field": {
                    "id": current_field.id,
                    "label": current_field.label,
                    "type": current_field.type
                }
            }
        
        # Set the field value and advance
        success = session.set_field_value(current_field.id, processed_value)
        
        response = {
            "success": success,
            "completed_field": {
                "id": current_field.id,
                "label": current_field.label,
                "value": processed_value
            },
            "form_progress": {
                "current_index": session.current_field_index,
                "total_fields": len(session.fields),
                "percentage": session.progress_percentage,
                "is_complete": session.is_complete
            }
        }
        
        # Add next field information if not complete
        if not session.is_complete:
            next_field = session.current_field
            response["next_field"] = {
                "id": next_field.id,
                "label": next_field.label,
                "type": next_field.type,
                "required": next_field.required,
                "prompt": session.get_next_field_prompt()
            }
        else:
            # Form is complete
            response["completed_form"] = {
                "form_id": session.form_id,
                "title": session.title,
                "data": session.completed_fields
            }
        
        return response
    
    def _validate_and_convert_value(self, field: FormField, value: str) -> Any:
        """Validate and convert field value based on field type."""
        value = value.strip()
        
        if field.type == "text":
            return value if value else None
        
        elif field.type == "number":
            try:
                return int(value) if value.isdigit() else float(value)
            except ValueError:
                return None
        
        elif field.type == "date":
            # Basic date validation (YYYY-MM-DD format)
            if len(value) == 10 and value.count('-') == 2:
                parts = value.split('-')
                if len(parts) == 3 and all(part.isdigit() for part in parts):
                    return value
            return None
        
        elif field.type == "email":
            # Basic email validation
            if '@' in value and '.' in value.split('@')[-1]:
                return value
            return None
        
        elif field.options:
            # Check if value is in allowed options (case-insensitive)
            lower_options = [opt.lower() for opt in field.options]
            if value.lower() in lower_options:
                # Return the original case from options
                idx = lower_options.index(value.lower())
                return field.options[idx]
            return None
        
        return value
    
    def get_form_data_for_frontend(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get current form data formatted for frontend updates."""
        session = self.get_active_session(user_id)
        if not session:
            return None
        
        return {
            "form_id": session.form_id,
            "completed_fields": session.completed_fields,
            "current_field_id": session.current_field.id if session.current_field else None,
            "is_complete": session.is_complete
        }
    
    def clear_session(self, user_id: str) -> bool:
        """Clear the active form session for a user."""
        if user_id in self.active_sessions:
            del self.active_sessions[user_id]
            return True
        return False


# Global instance
form_field_manager = FormFieldManager()