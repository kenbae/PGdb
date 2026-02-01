# -*- coding: utf-8 -*-
"""
SSL 인증서 생성 유틸리티

자체 서명 인증서를 생성합니다.
"""

import os
import subprocess
import logging
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 인증서 저장 경로
SSL_DIR = Path(__file__).parent / 'ssl'
CERT_FILE = SSL_DIR / 'cert.pem'
KEY_FILE = SSL_DIR / 'key.pem'


def generate_self_signed_cert(
    common_name: str = "localhost",
    days: int = 365,
    force: bool = False
) -> tuple:
    """
    자체 서명 인증서 생성

    Args:
        common_name: 도메인 이름 (기본: localhost)
        days: 유효 기간 (일)
        force: 기존 인증서 덮어쓰기

    Returns:
        (cert_path, key_path)
    """
    # 디렉토리 생성
    SSL_DIR.mkdir(parents=True, exist_ok=True)

    # 이미 존재하면 스킵
    if CERT_FILE.exists() and KEY_FILE.exists() and not force:
        logger.info(f"SSL 인증서 이미 존재: {CERT_FILE}")
        return str(CERT_FILE), str(KEY_FILE)

    try:
        # Python cryptography 라이브러리 사용
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        import ipaddress

        # 개인키 생성
        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        # 인증서 정보
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "KR"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Seoul"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Seoul"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TradeBot"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ])

        # SAN (Subject Alternative Names)
        san_list = [
            x509.DNSName("localhost"),
            x509.DNSName(common_name),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            x509.IPAddress(ipaddress.IPv6Address("::1")),
        ]

        # 인증서 생성
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.utcnow())
            .not_valid_after(datetime.utcnow() + timedelta(days=days))
            .add_extension(
                x509.SubjectAlternativeName(san_list),
                critical=False,
            )
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=0),
                critical=True,
            )
            .sign(key, hashes.SHA256())
        )

        # 개인키 저장
        with open(KEY_FILE, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ))

        # 인증서 저장
        with open(CERT_FILE, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        logger.info(f"SSL 인증서 생성 완료: {CERT_FILE}")
        logger.info(f"유효 기간: {days}일")

        return str(CERT_FILE), str(KEY_FILE)

    except ImportError:
        logger.warning("cryptography 라이브러리 없음 - openssl 사용 시도")
        return _generate_with_openssl(common_name, days)


def _generate_with_openssl(common_name: str, days: int) -> tuple:
    """OpenSSL로 인증서 생성 (fallback)"""
    try:
        # OpenSSL 명령어
        cmd = [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(KEY_FILE),
            "-out", str(CERT_FILE),
            "-days", str(days),
            "-nodes",
            "-subj", f"/C=KR/ST=Seoul/L=Seoul/O=TradeBot/CN={common_name}"
        ]

        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"SSL 인증서 생성 완료 (openssl): {CERT_FILE}")

        return str(CERT_FILE), str(KEY_FILE)

    except Exception as e:
        logger.error(f"인증서 생성 실패: {e}")
        raise


def get_ssl_context():
    """SSL Context 반환 (인증서 없으면 생성)"""
    import ssl

    cert_path, key_path = generate_self_signed_cert()

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(cert_path, key_path)

    return ssl_context


def check_ssl_files() -> bool:
    """SSL 파일 존재 확인"""
    return CERT_FILE.exists() and KEY_FILE.exists()


if __name__ == "__main__":
    # 직접 실행 시 인증서 생성
    logging.basicConfig(level=logging.INFO)
    cert, key = generate_self_signed_cert(force=True)
    print(f"인증서: {cert}")
    print(f"개인키: {key}")
