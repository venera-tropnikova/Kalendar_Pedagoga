"""Stage B: evidence-backed structural candidates, isolated from downstream."""
from .interpreter import interpret_document
from .models import StructuralDocument

__all__ = ['interpret_document', 'StructuralDocument']
