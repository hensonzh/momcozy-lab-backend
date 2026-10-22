"""Standalone ASGI entry point; there is no Product app import."""
from .app import create_app
from .settings import SpecialtySettings

app = create_app(SpecialtySettings.from_env())
