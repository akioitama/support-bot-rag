from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_admin
from app.models.user import User
from app.schemas.user import RoleUpdate, UserAdmin

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _to_admin_user(user: User) -> UserAdmin:
    return UserAdmin(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        role=user.role,
        chat_count=len(user.messages),
        created_at=user.created_at,
    )


@router.get("/users", response_model=list[UserAdmin])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    users = db.query(User).order_by(User.id.asc()).all()
    return [_to_admin_user(user) for user in users]


@router.get("/users/{user_id}", response_model=UserAdmin)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _to_admin_user(user)


@router.patch("/users/{user_id}/role", response_model=UserAdmin)
def update_user_role(
    user_id: int,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.role = payload.role
    db.commit()
    db.refresh(user)
    return _to_admin_user(user)
