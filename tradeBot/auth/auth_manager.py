# -*- coding: utf-8 -*-
"""
인증 관리자

- JWT 토큰 기반 인증
- bcrypt 암호 해싱
- 세션 관리
"""

import os
import secrets
import hashlib
import base64
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict
from pathlib import Path

from fastapi import HTTPException, Request, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# JWT 설정
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24


class AuthManager:
    """인증 관리자"""

    def __init__(self, users_file: str = None):
        """
        초기화

        Args:
            users_file: 사용자 정보 파일 경로
        """
        if users_file is None:
            # 기본 경로: PGdb/tradeBot/auth/users.json
            users_file = os.path.join(os.path.dirname(__file__), 'users.json')

        self.users_file = users_file
        self.users: Dict[str, dict] = {}
        self.active_tokens: Dict[str, dict] = {}  # token -> {username, expires}

        # JWT 시크릿 키 (환경변수 또는 랜덤 생성)
        self.secret_key = os.getenv('JWT_SECRET_KEY')
        if not self.secret_key:
            # 파일에서 로드 또는 새로 생성
            secret_file = os.path.join(os.path.dirname(__file__), '.secret_key')
            if os.path.exists(secret_file):
                with open(secret_file, 'r') as f:
                    self.secret_key = f.read().strip()
            else:
                self.secret_key = secrets.token_hex(32)
                with open(secret_file, 'w') as f:
                    f.write(self.secret_key)
                logger.info("새 JWT 시크릿 키 생성됨")

        # 사용자 파일 로드
        self._load_users()

        logger.info(f"AuthManager 초기화 완료 (사용자 {len(self.users)}명)")

    def _load_users(self):
        """사용자 파일 로드"""
        if os.path.exists(self.users_file):
            try:
                with open(self.users_file, 'r', encoding='utf-8') as f:
                    self.users = json.load(f)
            except Exception as e:
                logger.error(f"사용자 파일 로드 실패: {e}")
                self.users = {}

    def _save_users(self):
        """사용자 파일 저장"""
        try:
            os.makedirs(os.path.dirname(self.users_file), exist_ok=True)
            with open(self.users_file, 'w', encoding='utf-8') as f:
                json.dump(self.users, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"사용자 파일 저장 실패: {e}")

    def _hash_password(self, password: str, salt: str = None) -> tuple:
        """
        비밀번호 해싱 (PBKDF2-SHA256)

        Args:
            password: 평문 비밀번호
            salt: 솔트 (없으면 새로 생성)

        Returns:
            (해시값, 솔트)
        """
        if salt is None:
            salt = secrets.token_hex(16)

        # PBKDF2 with SHA256, 100000 iterations
        dk = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt.encode('utf-8'),
            100000
        )
        password_hash = base64.b64encode(dk).decode('utf-8')

        return password_hash, salt

    def _verify_password(self, password: str, stored_hash: str, salt: str) -> bool:
        """비밀번호 검증"""
        computed_hash, _ = self._hash_password(password, salt)
        return secrets.compare_digest(computed_hash, stored_hash)

    def create_user(self, username: str, password: str, role: str = 'user') -> bool:
        """
        사용자 생성

        Args:
            username: 사용자명
            password: 비밀번호
            role: 역할 (admin, user)

        Returns:
            성공 여부
        """
        if username in self.users:
            logger.warning(f"사용자 이미 존재: {username}")
            return False

        password_hash, salt = self._hash_password(password)

        self.users[username] = {
            'password_hash': password_hash,
            'salt': salt,
            'role': role,
            'created_at': datetime.now().isoformat(),
            'last_login': None,
            'failed_attempts': 0,
            'locked_until': None
        }

        self._save_users()
        logger.info(f"사용자 생성: {username} (role={role})")
        return True

    def change_password(self, username: str, new_password: str) -> bool:
        """비밀번호 변경"""
        if username not in self.users:
            return False

        password_hash, salt = self._hash_password(new_password)
        self.users[username]['password_hash'] = password_hash
        self.users[username]['salt'] = salt

        self._save_users()
        logger.info(f"비밀번호 변경: {username}")
        return True

    def authenticate(self, username: str, password: str) -> Optional[str]:
        """
        사용자 인증

        Args:
            username: 사용자명
            password: 비밀번호

        Returns:
            JWT 토큰 (실패 시 None)
        """
        if username not in self.users:
            logger.warning(f"로그인 실패 - 존재하지 않는 사용자: {username}")
            return None

        user = self.users[username]

        # 계정 잠금 확인
        if user.get('locked_until'):
            locked_until = datetime.fromisoformat(user['locked_until'])
            if datetime.now() < locked_until:
                remaining = (locked_until - datetime.now()).seconds // 60
                logger.warning(f"계정 잠금 중: {username} ({remaining}분 남음)")
                return None
            else:
                # 잠금 해제
                user['locked_until'] = None
                user['failed_attempts'] = 0

        # 비밀번호 검증
        if not self._verify_password(password, user['password_hash'], user['salt']):
            user['failed_attempts'] = user.get('failed_attempts', 0) + 1

            # 5회 실패 시 30분 잠금
            if user['failed_attempts'] >= 5:
                user['locked_until'] = (datetime.now() + timedelta(minutes=30)).isoformat()
                logger.warning(f"계정 잠금: {username} (5회 실패)")

            self._save_users()
            logger.warning(f"로그인 실패 - 비밀번호 오류: {username} ({user['failed_attempts']}회)")
            return None

        # 로그인 성공
        user['failed_attempts'] = 0
        user['last_login'] = datetime.now().isoformat()
        self._save_users()

        # JWT 토큰 생성
        token = self._create_token(username, user['role'])
        logger.info(f"로그인 성공: {username}")

        return token

    def _create_token(self, username: str, role: str) -> str:
        """JWT 토큰 생성 (간단 구현)"""
        expires = datetime.now() + timedelta(hours=JWT_EXPIRATION_HOURS)

        # 페이로드
        payload = {
            'username': username,
            'role': role,
            'exp': expires.timestamp(),
            'iat': datetime.now().timestamp()
        }

        # Base64 인코딩
        payload_b64 = base64.urlsafe_b64encode(
            json.dumps(payload).encode()
        ).decode().rstrip('=')

        # 서명
        signature = hashlib.sha256(
            f"{payload_b64}.{self.secret_key}".encode()
        ).hexdigest()

        token = f"{payload_b64}.{signature}"

        # 활성 토큰 등록
        self.active_tokens[token] = {
            'username': username,
            'expires': expires.isoformat()
        }

        return token

    def verify_token(self, token: str) -> Optional[dict]:
        """
        토큰 검증

        Args:
            token: JWT 토큰

        Returns:
            페이로드 (실패 시 None)
        """
        if not token or '.' not in token:
            return None

        try:
            parts = token.split('.')
            if len(parts) != 2:
                return None

            payload_b64, signature = parts

            # 서명 검증
            expected_sig = hashlib.sha256(
                f"{payload_b64}.{self.secret_key}".encode()
            ).hexdigest()

            if not secrets.compare_digest(signature, expected_sig):
                logger.warning("토큰 서명 불일치")
                return None

            # 페이로드 디코딩
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += '=' * padding

            payload = json.loads(base64.urlsafe_b64decode(payload_b64))

            # 만료 확인
            if datetime.now().timestamp() > payload.get('exp', 0):
                logger.warning("토큰 만료됨")
                # 활성 토큰에서 제거
                if token in self.active_tokens:
                    del self.active_tokens[token]
                return None

            return payload

        except Exception as e:
            logger.error(f"토큰 검증 실패: {e}")
            return None

    def logout(self, token: str) -> bool:
        """로그아웃 (토큰 무효화)"""
        if token in self.active_tokens:
            del self.active_tokens[token]
            return True
        return False

    def get_user_info(self, username: str) -> Optional[dict]:
        """사용자 정보 조회 (비밀번호 제외)"""
        if username not in self.users:
            return None

        user = self.users[username].copy()
        del user['password_hash']
        del user['salt']
        user['username'] = username

        return user

    def list_users(self) -> list:
        """사용자 목록"""
        return [
            {
                'username': username,
                'role': user.get('role', 'user'),
                'created_at': user.get('created_at'),
                'last_login': user.get('last_login')
            }
            for username, user in self.users.items()
        ]

    def delete_user(self, username: str) -> bool:
        """사용자 삭제"""
        if username not in self.users:
            return False

        del self.users[username]
        self._save_users()
        logger.info(f"사용자 삭제: {username}")
        return True


# 전역 인증 관리자
_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    """AuthManager 인스턴스 반환"""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager


# FastAPI 의존성
security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Optional[dict]:
    """
    현재 사용자 정보 가져오기 (선택적)

    쿠키 또는 Authorization 헤더에서 토큰 추출
    """
    token = None

    # 1. Authorization 헤더 확인
    if credentials:
        token = credentials.credentials

    # 2. 쿠키 확인
    if not token:
        token = request.cookies.get('auth_token')

    if not token:
        return None

    auth = get_auth_manager()
    payload = auth.verify_token(token)

    return payload


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    인증 필수 (미인증 시 401 에러)
    """
    user = await get_current_user(request, credentials)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return user


async def require_admin(user: dict = Depends(require_auth)) -> dict:
    """관리자 권한 필수"""
    if user.get('role') != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자 권한이 필요합니다"
        )
    return user
