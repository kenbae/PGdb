"""
서버 모듈

분리된 서버 아키텍처:
1. analyzer_server.py - 분석 + DB 저장 + 텔레그램 (스케줄러)
2. web_server.py - API + 대시보드 (포트 8888)
3. order_server.py - 주문 실행 (포트 8889, 기존 위치)

실행 방법:
    # 분석 서버 (백그라운드)
    python -m servers.analyzer_server

    # 웹 서버
    python -m servers.web_server

    # 주문 서버 (기존)
    python order_server.py
"""
