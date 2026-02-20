# slack-emoji-tailor

Slack emoji 업로드에 맞춰 이미지를 자동 정사각형/압축 최적화하는 웹 서비스입니다.

## 주요 기능

- `uv` 1회 실행으로 FastAPI 백엔드 + 웹 UI 동시 구동
- 이미지 업로드 후 Slack-friendly 결과 즉시 미리보기
- 변환 결과 다운로드 제공
- 정적 이미지(PNG/JPG/WebP) + 애니메이션 GIF 변환 지원
- 기본 정책: 최대 128KB, GIF 최대 50프레임

## 로컬 실행 (Windows cmd)

```cmd
cmd /c "uv sync"
cmd /c "uv run slack-emoji-tailor"
```

브라우저에서 `http://127.0.0.1:8000` 접속.

같은 네트워크의 다른 디바이스(모바일 포함)에서 접속하려면:

```cmd
cmd /c "uv run slack-emoji-tailor --host 0.0.0.0 --port 8000"
```

모바일 브라우저에서 `http://<내_PC_IP>:8000` 접속.

## 테스트 / 성능 측정

```cmd
cmd /c "uv run pytest -q"
cmd /c "uv run python scripts/benchmark_convert.py --input test_set --repeat 5"
```

## API

### POST `/api/convert`

- Form fields
  - `file`: 업로드 파일 (필수)
  - `max_kb`: 출력 제한 KB (`1~512`, 기본 `128`)
  - `size`: `auto` 또는 `16~512` 정수
  - `fit`: `stretch | cover | contain`
  - `max_frames`: GIF 프레임 제한 (`1~50`, 기본 `50`)

- Response
  - Body: 변환된 이미지 바이너리
  - Headers:
    - `X-Source-Format`
    - `X-Source-Width`
    - `X-Source-Height`
    - `X-Source-Frame-Count`
    - `X-Source-Byte-Size`
    - `X-Source-Animated`
    - `X-Result-Format`
    - `X-Result-Side`
    - `X-Result-Colors`
    - `X-Result-Frame-Step`
    - `X-Result-Frame-Count`
    - `X-Result-Quality`
    - `X-Result-Byte-Size`
    - `X-Target-Reached`

## 수동 검증 결과(로컬)

- 업로드/변환/미리보기/다운로드 흐름 확인 완료
- 반응형 뷰 확인 완료 (데스크톱 + 모바일)
- 스크린샷:
  - `docs/screenshots/local-desktop.png`
  - `docs/screenshots/local-mobile.png`

## Docker 배포 (운영 VM 기준)

### docker compose 사용

```cmd
cmd /c "docker compose up --build -d"
cmd /c "docker compose ps"
cmd /c "docker compose logs -f"
```

### 직접 빌드/실행

```cmd
cmd /c "docker build -t slack-emoji-tailor:latest ."
cmd /c "docker run --name slack-emoji-tailor -p 8000:8000 --restart unless-stopped slack-emoji-tailor:latest"
```

## 운영 이관 체크리스트

1. VM 리소스 점검 (CPU/메모리/디스크 여유)
2. `docker compose up --build -d` 후 `/healthz` 헬스체크 검증
3. 방화벽/리버스 프록시(Nginx 등)에서 8000 포트/도메인 연결
4. 로그 수집 정책 구성 (`docker compose logs`, 중앙 로그 수집)
5. 대용량 업로드/대형 GIF 부하 테스트 후 워커 수 튜닝
6. 롤백 전략 준비(이전 이미지 태그 보관 및 즉시 전환 절차)
