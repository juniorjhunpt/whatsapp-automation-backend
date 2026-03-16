import os
import pyotp
import secrets
import hashlib
import bcrypt as _bcrypt_lib
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Config
SECRET_KEY = os.getenv("JWT_SECRET", "changeme-use-a-real-secret-in-production-please")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


# ── Schemas ──────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class TwoFARequest(BaseModel):
    temp_token: str
    code: str

class SetupRequest(BaseModel):
    password: str  # current password to confirm

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class RecoveryRequest(BaseModel):
    username: str
    recovery_code: str
    new_password: str

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    requires_2fa: bool = False
    temp_token: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(password: str) -> bytes:
    """Pre-hash to avoid bcrypt 72-byte limit."""
    return hashlib.sha256(password.encode()).digest()

def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt_lib.checkpw(_sha256(plain), hashed.encode())

def hash_password(password: str) -> str:
    return _bcrypt_lib.hashpw(_sha256(password), _bcrypt_lib.gensalt()).decode()

def create_token(data: dict, expires_hours: int = ACCESS_TOKEN_EXPIRE_HOURS) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(hours=expires_hours)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id or payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Token inválido")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Utilizador não encontrado")
    return user


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos")

    if user.totp_secret and user.totp_enabled:
        # Issue temp token for 2FA step
        temp = create_token({"sub": user.id, "type": "temp_2fa"}, expires_hours=0.083)  # 5 min
        return {"requires_2fa": True, "temp_token": temp}

    token = create_token({"sub": user.id, "type": "access"})
    return {"access_token": token, "token_type": "bearer", "requires_2fa": False}


@router.post("/verify-2fa")
async def verify_2fa(body: TwoFARequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = jwt.decode(body.temp_token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "temp_2fa":
            raise HTTPException(status_code=401, detail="Token inválido")
        user_id = payload["sub"]
    except JWTError:
        raise HTTPException(status_code=401, detail="Token expirado")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Utilizador não encontrado")

    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(status_code=401, detail="Código 2FA incorreto")

    token = create_token({"sub": user.id, "type": "access"})
    return {"access_token": token, "token_type": "bearer"}


@router.get("/setup-2fa")
async def setup_2fa(current_user: User = Depends(get_current_user)):
    """Generate a new TOTP secret and QR code URI."""
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=current_user.username, issuer_name="WA Hub")
    return {"secret": secret, "uri": uri}


@router.post("/enable-2fa")
async def enable_2fa(body: dict, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    secret = body.get("secret")
    code = body.get("code")
    if not secret or not code:
        raise HTTPException(400, "secret e code são obrigatórios")
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=1):
        raise HTTPException(400, "Código incorreto — tente novamente")
    # Generate recovery codes
    recovery_codes = [secrets.token_hex(6).upper() for _ in range(8)]
    current_user.totp_secret = secret
    current_user.totp_enabled = True
    current_user.recovery_codes = ",".join(recovery_codes)
    await db.commit()
    return {"ok": True, "recovery_codes": recovery_codes}


@router.post("/disable-2fa")
async def disable_2fa(body: dict, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    code = body.get("code")
    if not code:
        raise HTTPException(400, "Código 2FA obrigatório")
    totp = pyotp.TOTP(current_user.totp_secret)
    if not totp.verify(code, valid_window=1):
        raise HTTPException(400, "Código incorreto")
    current_user.totp_secret = None
    current_user.totp_enabled = False
    current_user.recovery_codes = None
    await db.commit()
    return {"ok": True}


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(400, "Senha atual incorreta")
    if len(body.new_password) < 6:
        raise HTTPException(400, "Nova senha deve ter pelo menos 6 caracteres")
    current_user.password_hash = hash_password(body.new_password)
    await db.commit()
    return {"ok": True}


@router.post("/recover")
async def recover_account(body: RecoveryRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if not user or not user.recovery_codes:
        raise HTTPException(400, "Código de recuperação inválido")
    codes = user.recovery_codes.split(",")
    if body.recovery_code.upper() not in codes:
        raise HTTPException(400, "Código de recuperação inválido")
    # Remove used code
    codes.remove(body.recovery_code.upper())
    user.recovery_codes = ",".join(codes)
    user.password_hash = hash_password(body.new_password)
    user.totp_enabled = False
    user.totp_secret = None
    await db.commit()
    return {"ok": True, "message": "Senha redefinida. 2FA desativado — reative depois."}


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "totp_enabled": current_user.totp_enabled,
    }
