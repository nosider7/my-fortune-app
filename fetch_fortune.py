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
    "calendar": "solar", # 양력 (1 또는 solar)
    "year": "1978",
    "month": "12",
    "day": "07",
    "hour_code": "04"    # 진시 (07:30 ~ 09:30)
}

async def scrape_single_day(page, target_date: datetime):
    date_str = target_date.strftime("%Y-%m-%d")
    t_year = target_date.strftime("%Y")
    t_month = str(target_date.month)
    t_day = str(target_date.day)

    try:
        # 1. 데스크톱 화면 크기로 신한라이프 오늘의 운세 페이지 접속
        await page.goto("https://www.shinhanlife.co.kr/hp/cdhg0140.do", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3500)

        # 2. 운세 컨텐츠 프레임 탐색 (내부 iframe이 있을 경우 해당 프레임으로 전환)
        target_frame = page.main_frame
        frame_urls = [f.url for f in page.frames]
        for frame in page.frames:
            if frame != page.main_frame and any(k in frame.url.lower() for k in ["unse", "fortune", "saju", "life", "sinbiun", "godowon"]):
                target_frame = frame
                break

        # 만약 별도 컨텐츠 iframe이 하나만 더 있다면 그 프레임을 선택
        if target_frame == page.main_frame and len(page.frames) > 1:
            for frame in page.frames[1:]:
                try:
                    select_count = await frame.locator("select").count()
                    if select_count >= 3:
                        target_frame = frame
                        break
                except Exception:
                    pass

        # 3. 자바스크립트 내부에서 직접 폼 입력 및 '보이는' 조회 버튼 클릭 처리
        # (Playwright의 Locator.click 타임아웃을 원천 방지)
        click_result = await target_frame.evaluate(f"""() => {{
            const isVisible = (el) => {{
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            }};

            // (A) 성별 및 양/음력 라디오 버튼 설정
            const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
            radios.forEach(r => {{
                const labelText = (r.labels?.[0]?.innerText || r.parentElement?.innerText || r.value || '').trim();
                if (labelText.includes('남') || labelText.includes('양력')) {{
                    r.checked = true;
                    r.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            }});

            // (B) 생년월일시 및 목표일자 select 설정
            const selects = Array.from(document.querySelectorAll('select')).filter(isVisible);
            const selectInfo = [];

            selects.forEach((sel, idx) => {{
                const attr = (sel.name + ' ' + sel.id + ' ' + sel.title + ' ' + sel.className).toLowerCase();
                const optionValues = Array.from(sel.options).map(o => o.value);
                const optionTexts = Array.from(sel.options).map(o => o.text);

                const setVal = (candidates) => {{
                    for (const c of candidates) {{
                        const found = Array.from(sel.options).find(o => o.value == c || o.text.includes(c));
                        if (found) {{
                            sel.value = found.value;
                            sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            return true;
                        }}
                    }}
                    return false;
                }};

                if (optionValues.includes('1978') || optionTexts.some(t => t.includes('1978'))) {{
                    if (attr.includes('target') || attr.includes('to') || attr.includes('운세')) {{
                        setVal(['{t_year}']);
                    }} else {{
                        setVal(['1978']);
                    }}
                }} else if (attr.includes('month') || attr.includes('월') || idx === 1) {{
                    setVal(['12', '12월']);
                }} else if (attr.includes('day') || attr.includes('일') || idx === 2) {{
                    setVal(['07', '7', '7일', '07일']);
                }} else if (attr.includes('hour') || attr.includes('time') || attr.includes('시') || idx === 3) {{
                    setVal(['진시', '07:30', '08', '04', '4', '辰']);
                }}

                selectInfo.push({{ id: sel.id, name: sel.name, title: sel.title, val: sel.value }});
            }});

            // (C) 상단 메뉴(data-menu-nm 등)를 제외하고 실제로 화면에 보이는 조회 버튼만 찾아서 클릭
            const candidates = Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"], a'));
            let clickedBtnText = null;

            for (const el of candidates) {{
                if (!isVisible(el)) continue;
                if (el.hasAttribute('data-menu-nm') || el.hasAttribute('data-menu-id')) continue; // 상단 GNB 메뉴 제외
                if (el.closest('header, nav, .gnb, .lnb, #header, #gnb')) continue; // 헤더 영역 제외

                const txt = (el.innerText || el.value || el.title || '').trim();
                if (['운세보기', '운세 보기', '조회', '결과보기', '결과 보기', '운세조회', '확인'].some(k => txt.includes(k))) {{
                    el.click();
                    clickedBtnText = txt;
                    break;
                }}
            }}

            return {{
                clicked: clickedBtnText,
                selectCount: selects.length,
                selectInfo: selectInfo
            }};
        }}""")

        # 결과 렌더링 대기
        await page.wait_for_timeout(3000)

        # 4. 결과 텍스트 및 핵심 항목 추출 (메인 페이지 전체 + 타겟 프레임 모두 수집)
        extracted = await target_frame.evaluate("""() => {
            // 헤더/푸터/메뉴를 제외한 본문 영역 우선 탐색
            const mainArea = document.querySelector('#contents, #container, .contents, .content, main, .unse_wrap, .fortune_wrap') || document.body;
            const fullText = (mainArea.innerText || document.body.innerText || "").trim();

            // 총점(숫자+점) 패턴 추출
            const scoreMatch = fullText.match(/([0-9]{2,3})\\s*점/);
            const score = scoreMatch ? parseInt(scoreMatch[1], 10) : null;

            // 행운의 색 추출
            const colorMatch = fullText.match(/행운의\\s*색[상]?[\\s:：]*([가-힣a-zA-Z]+)/);
            const luckyColor = colorMatch ? colorMatch[1].trim() : null;

            // 의미 있는 운세 문단 추출 (메뉴 텍스트 제외)
            const paragraphs = Array.from(mainArea.querySelectorAll('p, .txt, .desc, .unse_txt, dd, td, li'))
                .map(el => (el.innerText || '').trim())
                .filter(t => t.length >= 35 && !t.includes('개인정보처리방침') && !t.includes('신한라이프생명보험'));

            // 중복 문단 제거
            const uniqueParagraphs = [...new Set(paragraphs)];

            return {
                score: score,
                luckyColor: luckyColor,
                paragraphs: uniqueParagraphs.slice(0, 8),
                rawText: fullText.substring(0, 4000)
            };
        }""")

        return {
            "date": date_str,
            "status": "success",
            "score": extracted["score"],
            "luckyColor": extracted["luckyColor"],
            "paragraphs": extracted["paragraphs"],
            "rawText": extracted["rawText"],
            "debugInfo": {
                "frameUrl": target_frame.url,
                "allFrames": frame_urls,
                "formAction": click_result
            }
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
        # 모바일 메뉴 숨김 현상을 방지하기 위해 데스크톱 브라우저 해상도로 접속
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
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
