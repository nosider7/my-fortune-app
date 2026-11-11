import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright

KST = timezone(timedelta(hours=9))

# 고정 사주 정보: 1978년 12월 7일 (양력) 오전 8시(진시)
USER_INFO = {
    "gender": "1",       # 1: 남자, 2: 여자
    "calendar": "solar", # 양력
    "year": "1978",
    "month": "12",
    "day": "07",
    "hour_code": "04"    # 진시 (07:30 ~ 09:30)
}

async def find_and_open_fortune_frame(page):
    """
    신한라이프 cdhg0130.do / cdhg0140.do 페이지 내부에서
    실제 생년월일 입력 폼(운세 엔진)을 깨우거나 iframe URL을 찾아 진입하는 함수
    """
    await page.goto("https://www.shinhanlife.co.kr/hp/cdhg0130.do", wait_until="networkidle", timeout=45000)
    await page.wait_for_timeout(3000)

    # 1. 현재 페이지의 HTML 소스 및 iframe/버튼 구조 정밀 스캔
    page_scan = await page.evaluate("""() => {
        const iframes = Array.from(document.querySelectorAll('iframe')).map(f => ({
            id: f.id,
            name: f.name,
            src: f.getAttribute('src'),
            dataSrc: f.getAttribute('data-src') || f.dataset?.src || ''
        }));

        // 본문(#contents 등) 내부의 클릭 가능한 탭/버튼/링크 탐색
        const contentRoot = document.querySelector('#contents, #container, .contents, .content, main') || document.body;
        const clickable = Array.from(contentRoot.querySelectorAll('a, button, li, span, div[role="button"]'))
            .map(el => ({
                tag: el.tagName,
                text: (el.innerText || '').trim().replace(/\\s+/g, ' '),
                href: el.getAttribute('href') || '',
                onclick: el.getAttribute('onclick') || '',
                id: el.id || '',
                cls: el.className || ''
            }))
            .filter(item => item.text.length > 0 && item.text.length < 35);

        // 스크립트 내부에 숨겨진 외부 운세 솔루션 URL 패턴 탐색
        const htmlSource = document.documentElement.innerHTML;
        const urlMatches = htmlSource.match(/https?:\\/\\/[^"'\\s<>]+(?:unse|fortune|saju|sinbiun|godowon|shinhan)[^"'\\s<>]*/gi) || [];
        const doMatches = htmlSource.match(/\\/hp\\/cdhg[0-9a-zA-Z_]+\\.do/gi) || [];

        return {
            currentUrl: location.href,
            iframes,
            clickable: clickable.slice(0, 50),
            urlMatches: [...new Set(urlMatches)],
            doMatches: [...new Set(doMatches)]
        };
    }""")

    # 2. 만약 본문 안에 '오늘의 운세', '오늘의 총운', '평생운세' 등 시작 버튼/탭이 있다면 클릭하여 iframe 로드 유도
    await page.evaluate("""() => {
        const contentRoot = document.querySelector('#contents, #container, .contents, .content, main') || document.body;
        const els = Array.from(contentRoot.querySelectorAll('a, button, li'));
        for (const el of els) {
            if (el.closest('header, nav, footer, #header, #footer, .gnb')) continue;
            const t = (el.innerText || '').trim();
            if (['오늘의 운세', '오늘의 총운', '일일운세', '오늘운세', '운세보기', '바로가기'].some(k => t.includes(k))) {
                el.click();
                break;
            }
        }
    }""")
    await page.wait_for_timeout(3000)

    # 3. 유효한 iframe 또는 입력 폼(select/input)이 있는 프레임 찾기
    target_frame = page.main_frame
    for frame in page.frames:
        try:
            cnt = await frame.locator("select, input[type='text'], input[type='tel'], input[type='number']").count()
            if cnt >= 2:
                target_frame = frame
                break
        except Exception:
            pass

    return target_frame, page_scan

async def scrape_single_day(page, target_date: datetime):
    date_str = target_date.strftime("%Y-%m-%d")
    t_year = target_date.strftime("%Y")
    t_month = str(target_date.month)
    t_day = str(target_date.day)

    try:
        target_frame, page_scan = await find_and_open_fortune_frame(page)

        # 4. 폼 입력 (select 방식 및 text input 방식 모두 대응)
        form_action = await target_frame.evaluate(f"""() => {{
            const isVisible = (el) => {{
                if (!el) return false;
                const s = window.getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden' && el.getBoundingClientRect().width > 0;
            }};

            // (A) 모든 input / select 요소 스캔
            const inputs = Array.from(document.querySelectorAll('input, select')).map(el => ({{
                tag: el.tagName,
                type: el.type,
                name: el.name,
                id: el.id,
                title: el.title,
                placeholder: el.placeholder || ''
            }}));

            // (B) 생년월일 텍스트 입력창(예: 19781207)이 있는 경우 자동 입력
            const textInputs = Array.from(document.querySelectorAll('input[type="text"], input[type="tel"], input[type="number"]')).filter(isVisible);
            textInputs.forEach(inp => {{
                const meta = (inp.name + inp.id + inp.title + inp.placeholder).toLowerCase();
                if (meta.includes('birth') || meta.includes('생년') || meta.includes('ymd') || inp.maxLength === 8) {{
                    inp.value = '19781207';
                    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            }});

            // (C) Select 드롭다운이 있는 경우 자동 선택
            const selects = Array.from(document.querySelectorAll('select')).filter(isVisible);
            selects.forEach((sel, idx) => {{
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
                if (!setVal(['1978'])) {{
                    if (idx === 1) setVal(['12', '12월']);
                    if (idx === 2) setVal(['07', '7', '7일']);
                    if (idx === 3) setVal(['진시', '07:30', '08', '04', '4']);
                }}
            }});

            // (D) 본문 영역 내 조회 버튼 클릭
            let clicked = null;
            const btns = Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"], a')).filter(isVisible);
            for (const b of btns) {{
                if (b.closest('header, nav, footer, #header, #footer, .gnb')) continue;
                if (b.hasAttribute('data-menu-nm')) continue;
                const txt = (b.innerText || b.value || b.title || '').trim();
                if (['운세보기', '운세 보기', '조회', '확인', '결과보기'].some(k => txt.includes(k))) {{
                    b.click();
                    clicked = txt;
                    break;
                }}
            }}

            return {{
                clicked,
                inputCount: inputs.length,
                inputs: inputs.slice(0, 30)
            }};
        }}""")

        await page.wait_for_timeout(3000)

        # 5. 헤더/사이드메뉴(보험계약조회 등)를 완전히 제거한 순수 본문 텍스트만 추출
        extracted = await target_frame.evaluate("""() => {
            // 사이트 전체 메뉴(사이버창구 등)가 섞이지 않도록 복제본에서 메뉴 태그 제거
            const clone = document.body.cloneNode(true);
            clone.querySelectorAll('header, nav, footer, #header, #footer, .gnb, .lnb, .snb, #gnb, script, style').forEach(el => el.remove());

            const cleanText = (clone.innerText || '').trim();

            const scoreMatch = cleanText.match(/([0-9]{2,3})\\s*점/);
            const score = scoreMatch ? parseInt(scoreMatch[1], 10) : null;

            const colorMatch = cleanText.match(/행운의\\s*색[상]?[\\s:：]*([가-힣a-zA-Z]+)/);
            const luckyColor = colorMatch ? colorMatch[1].trim() : null;

            const paragraphs = Array.from(clone.querySelectorAll('p, .txt, .desc, .unse_txt, dd, td'))
                .map(el => (el.innerText || '').trim())
                .filter(t => t.length >= 30 && !t.includes('보험계약조회') && !t.includes('개인정보처리방침'));

            return {
                score,
                luckyColor,
                paragraphs: [...new Set(paragraphs)].slice(0, 8),
                rawText: cleanText.substring(0, 3500)
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
                "allFrames": [f.url for f in page.frames],
                "pageScan": page_scan,
                "formAction": form_action
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

if __name__ == "__main__":
    asyncio.run(main())
