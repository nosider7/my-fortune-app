import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright

# 한국 표준시(KST) 설정
KST = timezone(timedelta(hours=9))

# 고정 사주 정보: 1978년 12월 7일 (양력) 오전 8시(진시)
USER_INFO = {
    "gender": "1",       # 1: 남자(건명), 2: 여자(곤명)
    "calendar": "solar", # 양력
    "year": "1978",
    "month": "12",
    "day": "07",
    "hour_code": "04"    # 진시 (07:31 ~ 09:30)
}

async def scrape_single_day(page, target_date: datetime):
    date_str = target_date.strftime("%Y-%m-%d")
    t_year = target_date.strftime("%Y")
    t_month = str(target_date.month)
    t_day = str(target_date.day)

    try:
        # 신한라이프 오늘의 운세 페이지 접속
        await page.goto("https://www.shinhanlife.co.kr/hp/cdhg0140.do", wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(2000)

        # 운세 컨텐츠 프레임 탐색 (내부 iframe이 있을 경우 전환)
        target_frame = page.main_frame
        for frame in page.frames:
            if any(k in frame.url.lower() for k in ["unse", "fortune", "saju"]):
                target_frame = frame
                break

        # 사주 정보 및 조회 대상 일자(오늘/내일) 폼 입력
        await target_frame.evaluate(f"""() => {{
            const setSelect = (keywords, val) => {{
                const selects = Array.from(document.querySelectorAll('select'));
                for (const sel of selects) {{
                    const attr = (sel.name + sel.id + sel.title + sel.className).toLowerCase();
                    if (keywords.some(k => attr.includes(k))) {{
                        sel.value = val;
                        sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                }}
            }};
            // 생년월일시 설정
            setSelect(['byear', 'birth_y', '생년', 'year'], '{USER_INFO["year"]}');
            setSelect(['bmonth', 'birth_m', '생월', 'month'], '{int(USER_INFO["month"])}');
            setSelect(['bday', 'birth_d', '생일', 'day'], '{int(USER_INFO["day"])}');
            setSelect(['bhour', 'birth_h', '생시', 'hour', 'time'], '{USER_INFO["hour_code"]}');
            
            // 조회 목표일(오늘/내일) 선택 폼이 분리된 경우 대응
            setSelect(['tyear', 'target_y'], '{t_year}');
            setSelect(['tmonth', 'target_m'], '{t_month}');
            setSelect(['tday', 'target_d'], '{t_day}');
        }}""")

        # 조회/운세보기 버튼 클릭
        btn = target_frame.locator("button:has-text('운세'), a:has-text('운세'), input[type='submit'], .btn_submit, .btn_search").first
        if await btn.count() > 0:
            await btn.click()
            await page.wait_for_timeout(2500)

        # 결과 텍스트 및 핵심 항목 추출
        extracted = await target_frame.evaluate("""() => {
            const fullText = document.body.innerText || "";
            
            // 총점(숫자+점) 패턴 추출
            const scoreMatch = fullText.match(/([0-9]{2,3})\s*점/);
            const score = scoreMatch ? parseInt(scoreMatch[1], 10) : null;

            // 행운의 색 추출
            const colorMatch = fullText.match(/행운의\s*색[상]?[\s:：]*([가-힣a-zA-Z]+)/);
            const luckyColor = colorMatch ? colorMatch[1].trim() : null;

            // 주요 문단 추출
            const paragraphs = Array.from(document.querySelectorAll('p, .txt, .desc, .unse_txt, dd'))
                .map(el => el.innerText.trim())
                .filter(t => t.length > 25);

            return {
                score: score,
                luckyColor: luckyColor,
                paragraphs: paragraphs.slice(0, 6),
                rawText: fullText.substring(0, 3000)
            };
        }""")

        return {
            "date": date_str,
            "status": "success",
            "score": extracted["score"],
            "luckyColor": extracted["luckyColor"],
            "paragraphs": extracted["paragraphs"],
            "rawText": extracted["rawText"]
        }

    except Exception as e:
        return {
            "date": date_str,
            "status": "error",
            "message": str(e)
        }

async def main():
    now_kst = datetime.now(KST)
    tomorrow_kst = now_kst + timedelta(days=1)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            user_agent="Mozilla/5.0 (Linux; Android 14; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"
        )
        page = await context.new_page()

        today_data = await scrape_single_day(page, now_kst)
        tomorrow_data = await scrape_single_day(page, tomorrow_kst)

        await browser.close()

    output = {
        "updatedAtKST": now_kst.strftime("%Y-%m-%d %H:%M:%S"),
        "birthProfile": "1978-12-07 08:00 (양력/진시)",
        "today": today_data,
        "tomorrow": tomorrow_data
    }

    with open("fortune_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[{output['updatedAtKST']}] fortune_data.json 업데이트 완료")

if __name__ == "__main__":
    asyncio.run(main())
