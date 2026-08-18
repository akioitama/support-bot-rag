from app.routes.admin import router as admin_router
from app.routes.auth import router as auth_router
from app.routes.chat import router as chat_router
from app.routes.users import router as users_router

__all__ = ["admin_router", "auth_router", "chat_router", "users_router"]
