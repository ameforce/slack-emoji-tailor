#!/usr/bin/env python3
"""One-off web app verification script. Run: uv run --with playwright python scripts/verify_webapp_screenshots.py"""
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8000"
UPLOAD_FILE = Path(r"C:/Workspace/Daeng/Git/Project/slack-emoji-tailor/test_set/unnamed.gif")
OUTPUT_DIR = Path(r"C:/Workspace/Daeng/Git/Project/slack-emoji-tailor/docs/screenshots")
DESKTOP_PATH = OUTPUT_DIR / "local-desktop.png"
MOBILE_PATH = OUTPUT_DIR / "local-mobile.png"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not UPLOAD_FILE.exists():
        raise FileNotFoundError(f"Upload file not found: {UPLOAD_FILE}")

    observations = []
    passed = True

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            # 1) 접속 및 UI 확인
            page.goto(URL, wait_until="networkidle", timeout=10000)
            page.wait_for_selector("#convert-form", timeout=5000)
            observations.append("페이지 로드 및 convert-form 렌더링 확인됨")

            # 2) 파일 업로드
            page.set_input_files("#file-input", str(UPLOAD_FILE))
            page.wait_for_timeout(500)

            # 3) 변환 실행
            page.click("#submit-btn")
            page.wait_for_selector("#status-box.success", timeout=15000)
            success_text = page.locator("#status-box.success").text_content()
            if "완료" in (success_text or ""):
                observations.append("변환 성공 문구 표시됨: " + (success_text or "")[:50])
            else:
                observations.append("변환 성공 문구 미확인")
                passed = False

            result_img = page.locator("#result-preview.ready")
            result_img.wait_for(state="visible", timeout=5000)
            if result_img.get_attribute("src"):
                observations.append("결과 미리보기 이미지 표시됨")
            else:
                observations.append("결과 미리보기 미표시")
                passed = False

            download_btn = page.locator("#download-btn")
            if not download_btn.get_attribute("disabled"):
                observations.append("다운로드 버튼 활성화됨")
            else:
                observations.append("다운로드 버튼 비활성")
                passed = False

            # 4) 데스크톱 스크린샷
            page.screenshot(path=str(DESKTOP_PATH))
            observations.append(f"데스크톱 스크린샷 저장: {DESKTOP_PATH}")

            # 5) 모바일 뷰 (390x844)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(300)
            page.screenshot(path=str(MOBILE_PATH))
            observations.append(f"모바일 스크린샷 저장: {MOBILE_PATH}")

        except Exception as e:
            passed = False
            observations.append(f"오류: {e}")
        finally:
            browser.close()

    print("VERIFICATION:", "PASS" if passed else "FAIL")
    for o in observations:
        print("  -", o)
    print("SCREENSHOTS:")
    print("  -", DESKTOP_PATH)
    print("  -", MOBILE_PATH)
    return 0 if passed else 1


if __name__ == "__main__":
    exit(main())
