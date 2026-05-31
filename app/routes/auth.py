
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_current_user
from app.dependencies import get_db_pool, get_supabase
from app.limiter import limiter
from app.models.auth import LoginRequest, ResetPasswordRequest, SignupRequest, TokenResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

ALLOWED_DOMAINS = {"sprinklr.com", "gmail.com"}


def _validate_email_domain(email: str) -> None:
    parts = email.rsplit("@", 1)
    if len(parts) != 2 or parts[1].lower() not in ALLOWED_DOMAINS:
        raise HTTPException(
            status_code=400,
            detail=f"Email domain not allowed. Only @sprinklr.com and @gmail.com are accepted.",
        )


@router.post("/signup")
@limiter.limit("5/minute")
async def signup(
    request: Request,
    body: SignupRequest,
    supabase=Depends(get_supabase),
    pool=Depends(get_db_pool),
) -> Dict[str, Any]:
    _validate_email_domain(body.email)
    try:
        response = supabase.auth.sign_up({"email": body.email, "password": body.password})
    except Exception as exc:
        logger.warning("Supabase signup error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))

    if not (hasattr(response, "user") and response.user):
        raise HTTPException(status_code=400, detail="Signup failed")

    user_id = str(response.user.id)

    # Create profile row immediately so the user lands in onboarding, not a broken state
    try:
        await pool.execute(
            """
            INSERT INTO profiles (id)
            VALUES ($1)
            ON CONFLICT (id) DO NOTHING
            """,
            user_id,
        )
    except Exception as exc:
        logger.warning("Profile row creation failed for %s: %s", user_id, exc)

    return {
        "message": "Signup successful. Please check your email to confirm your account.",
        "user_id": user_id,
        "email": response.user.email,
    }


@router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, supabase=Depends(get_supabase)) -> TokenResponse:
    try:
        response = supabase.auth.sign_in_with_password(
            {"email": body.email, "password": body.password}
        )
    except Exception as exc:
        logger.warning("Supabase login error: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not (hasattr(response, "session") and response.session):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    session = response.session
    user_data = None
    if hasattr(response, "user") and response.user:
        user_data = {"id": str(response.user.id), "email": response.user.email}

    return TokenResponse(
        access_token=session.access_token,
        token_type="bearer",
        user=user_data,
    )


@router.post("/logout")
async def logout(
    current_user: Dict[str, Any] = Depends(get_current_user),
    supabase=Depends(get_supabase),
) -> Dict[str, str]:
    try:
        supabase.auth.sign_out()
    except Exception as exc:
        logger.warning("Supabase logout error: %s", exc)
    return {"message": "Logged out successfully"}


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest, supabase=Depends(get_supabase)
) -> Dict[str, str]:
    try:
        supabase.auth.reset_password_email(body.email)
    except Exception as exc:
        logger.warning("Supabase reset-password error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": "Password reset email sent if the address is registered."}


@router.get("/me")
async def me(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    return current_user
