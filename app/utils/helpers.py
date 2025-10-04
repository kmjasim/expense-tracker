from flask import request

def get_page_title(default="Page"):
    """Return a human-friendly page title based on current path."""
    if not request:
        return default
    path = request.path.strip("/")
    if not path:
        return "Dashboard"
    return path.replace("-", " ").title()
