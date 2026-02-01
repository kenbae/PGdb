# -*- coding: utf-8 -*-
"""
보안 미들웨어 및 유틸리티

- Rate Limiting (요청 제한)
- IP 화이트리스트
- 보안 헤더
"""

import os
import time
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Set, Dict, Optional, Callable

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Rate Limiter (요청 제한)

    IP별 요청 횟수를 추적하여 제한 초과 시 차단
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
        login_attempts_per_hour: int = 10
    ):
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self.login_attempts_per_hour = login_attempts_per_hour

        # IP별 요청 기록: {ip: [(timestamp, path), ...]}
        self.request_log: Dict[str, list] = defaultdict(list)
        self.login_log: Dict[str, list] = defaultdict(list)

        # 차단된 IP: {ip: unblock_time}
        self.blocked_ips: Dict[str, datetime] = {}

    def _cleanup_old_entries(self, ip: str):
        """오래된 기록 정리"""
        now = time.time()
        hour_ago = now - 3600
        minute_ago = now - 60

        # 1시간 이전 기록 삭제
        self.request_log[ip] = [
            (ts, path) for ts, path in self.request_log[ip]
            if ts > hour_ago
        ]
        self.login_log[ip] = [
            ts for ts in self.login_log[ip]
            if ts > hour_ago
        ]

    def is_blocked(self, ip: str) -> bool:
        """IP 차단 여부 확인"""
        if ip in self.blocked_ips:
            if datetime.now() < self.blocked_ips[ip]:
                return True
            else:
                del self.blocked_ips[ip]
        return False

    def block_ip(self, ip: str, duration_minutes: int = 30):
        """IP 차단"""
        self.blocked_ips[ip] = datetime.now() + timedelta(minutes=duration_minutes)
        logger.warning(f"IP 차단: {ip} ({duration_minutes}분)")

    def check_rate_limit(self, ip: str, path: str) -> tuple[bool, str]:
        """
        요청 제한 확인

        Returns:
            (허용 여부, 에러 메시지)
        """
        # 0 = 무제한 (rate limiting 비활성화)
        if self.requests_per_minute == 0 and self.requests_per_hour == 0:
            return True, ""

        if self.is_blocked(ip):
            remaining = (self.blocked_ips[ip] - datetime.now()).seconds // 60
            return False, f"IP가 차단되었습니다. {remaining}분 후 재시도하세요."

        self._cleanup_old_entries(ip)

        now = time.time()
        minute_ago = now - 60

        # 분당 요청 확인 (0이면 스킵)
        if self.requests_per_minute > 0:
            recent_requests = [
                ts for ts, _ in self.request_log[ip]
                if ts > minute_ago
            ]

            if len(recent_requests) >= self.requests_per_minute:
                return False, f"요청이 너무 많습니다. 잠시 후 재시도하세요. (분당 {self.requests_per_minute}회 제한)"

        # 시간당 요청 확인 (0이면 스킵)
        if self.requests_per_hour > 0:
            if len(self.request_log[ip]) >= self.requests_per_hour:
                self.block_ip(ip, 10)  # 10분 차단
                return False, "요청이 너무 많습니다. 10분 후 재시도하세요."

        # 요청 기록 (로깅용으로 유지)
        self.request_log[ip].append((now, path))

        return True, ""

    def check_login_limit(self, ip: str) -> tuple[bool, str]:
        """
        로그인 시도 제한 확인
        """
        # 0 = 무제한
        if self.login_attempts_per_hour == 0:
            return True, ""

        if self.is_blocked(ip):
            remaining = (self.blocked_ips[ip] - datetime.now()).seconds // 60
            return False, f"IP가 차단되었습니다. {remaining}분 후 재시도하세요."

        self._cleanup_old_entries(ip)

        # 시간당 로그인 시도 확인
        if len(self.login_log[ip]) >= self.login_attempts_per_hour:
            self.block_ip(ip, 60)  # 1시간 차단
            return False, "로그인 시도가 너무 많습니다. 1시간 후 재시도하세요."

        return True, ""

    def record_login_attempt(self, ip: str):
        """로그인 시도 기록"""
        self.login_log[ip].append(time.time())


class IPWhitelist:
    """
    IP 화이트리스트

    허용된 IP만 접근 가능
    """

    def __init__(self, enabled: bool = False, allowed_ips: Set[str] = None):
        self.enabled = enabled
        self.allowed_ips = allowed_ips or set()

        # localhost는 항상 허용
        self.allowed_ips.update(['127.0.0.1', '::1', 'localhost'])

        # 환경변수에서 허용 IP 로드
        env_ips = os.getenv('ALLOWED_IPS', '')
        if env_ips:
            self.allowed_ips.update(ip.strip() for ip in env_ips.split(','))

        logger.info(f"IP 화이트리스트: {'활성화' if enabled else '비활성화'} ({len(self.allowed_ips)}개 IP)")

    def is_allowed(self, ip: str) -> bool:
        """IP 허용 여부 확인"""
        if not self.enabled:
            return True

        # CIDR 표기 지원 (예: 192.168.1.0/24)
        for allowed in self.allowed_ips:
            if '/' in allowed:
                if self._check_cidr(ip, allowed):
                    return True
            elif ip == allowed:
                return True

        return False

    def _check_cidr(self, ip: str, cidr: str) -> bool:
        """CIDR 범위 확인"""
        try:
            import ipaddress
            network = ipaddress.ip_network(cidr, strict=False)
            return ipaddress.ip_address(ip) in network
        except:
            return False

    def add_ip(self, ip: str):
        """IP 추가"""
        self.allowed_ips.add(ip)
        logger.info(f"IP 화이트리스트 추가: {ip}")

    def remove_ip(self, ip: str):
        """IP 제거"""
        self.allowed_ips.discard(ip)
        logger.info(f"IP 화이트리스트 제거: {ip}")


class SecurityMiddleware(BaseHTTPMiddleware):
    """
    보안 미들웨어

    - Rate Limiting
    - IP 화이트리스트
    - 보안 헤더 추가
    """

    def __init__(
        self,
        app,
        rate_limiter: RateLimiter = None,
        ip_whitelist: IPWhitelist = None,
        exclude_paths: Set[str] = None
    ):
        super().__init__(app)
        self.rate_limiter = rate_limiter or RateLimiter()
        self.ip_whitelist = ip_whitelist or IPWhitelist()
        self.exclude_paths = exclude_paths or {'/api/health', '/favicon.ico'}

    def get_client_ip(self, request: Request) -> str:
        """클라이언트 IP 추출"""
        # X-Forwarded-For 헤더 확인 (프록시 뒤에 있을 때)
        forwarded = request.headers.get('X-Forwarded-For')
        if forwarded:
            return forwarded.split(',')[0].strip()

        # X-Real-IP 헤더 확인
        real_ip = request.headers.get('X-Real-IP')
        if real_ip:
            return real_ip

        # 직접 연결된 클라이언트 IP
        if request.client:
            return request.client.host

        return '127.0.0.1'

    async def dispatch(self, request: Request, call_next):
        client_ip = self.get_client_ip(request)
        path = request.url.path

        # 제외 경로는 검사 스킵
        if path in self.exclude_paths:
            response = await call_next(request)
            return self._add_security_headers(response)

        # IP 화이트리스트 확인
        if not self.ip_whitelist.is_allowed(client_ip):
            logger.warning(f"비허용 IP 접근 시도: {client_ip}")
            return JSONResponse(
                status_code=403,
                content={"detail": "접근이 거부되었습니다"}
            )

        # Rate Limiting 확인
        allowed, error_msg = self.rate_limiter.check_rate_limit(client_ip, path)
        if not allowed:
            logger.warning(f"Rate limit 초과: {client_ip} - {path}")
            return JSONResponse(
                status_code=429,
                content={"detail": error_msg}
            )

        # 로그인 엔드포인트 추가 검사
        if path == '/api/auth/login' and request.method == 'POST':
            login_allowed, login_error = self.rate_limiter.check_login_limit(client_ip)
            if not login_allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": login_error}
                )
            self.rate_limiter.record_login_attempt(client_ip)

        # 요청 처리
        response = await call_next(request)

        # 보안 헤더 추가
        return self._add_security_headers(response)

    def _add_security_headers(self, response):
        """보안 헤더 추가"""
        # XSS 보호
        response.headers['X-XSS-Protection'] = '1; mode=block'
        # MIME 스니핑 방지
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # 클릭재킹 방지
        response.headers['X-Frame-Options'] = 'DENY'
        # Referrer 정책
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

        return response


# 전역 인스턴스
_rate_limiter: Optional[RateLimiter] = None
_ip_whitelist: Optional[IPWhitelist] = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(
            requests_per_minute=int(os.getenv('RATE_LIMIT_PER_MINUTE', '60')),
            requests_per_hour=int(os.getenv('RATE_LIMIT_PER_HOUR', '1000')),
            login_attempts_per_hour=int(os.getenv('LOGIN_ATTEMPTS_PER_HOUR', '10'))
        )
    return _rate_limiter


def get_ip_whitelist() -> IPWhitelist:
    global _ip_whitelist
    if _ip_whitelist is None:
        _ip_whitelist = IPWhitelist(
            enabled=os.getenv('IP_WHITELIST_ENABLED', 'false').lower() == 'true'
        )
    return _ip_whitelist
