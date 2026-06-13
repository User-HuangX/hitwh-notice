from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


async def _setup_context(webvpn_base: str, cookie_str: str):
    import playwright.async_api as pw

    domain = urlparse(webvpn_base).hostname or "webvpn.hitwh.edu.cn"

    playwright = await pw.async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context()

    for cookie_part in cookie_str.split("; "):
        if "=" in cookie_part:
            name, _, value = cookie_part.partition("=")
            await context.add_cookies([{
                "name": name.strip(),
                "value": value.strip(),
                "domain": domain,
                "path": "/",
            }])

    page = await context.new_page()
    return playwright, browser, page


async def fetch_all_grades_pw(
    webvpn_base: str,
    cookie_str: str,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    all_grades = await _fetch_page_grades(webvpn_base, cookie_str, "/cjcx/queryQmcj", timeout, "final")
    sxw_grades = await _fetch_page_grades(webvpn_base, cookie_str, "/cjcx/querySxwcj", timeout, "sxw")
    all_grades.extend(sxw_grades)
    return all_grades


async def fetch_plan_pw(
    webvpn_base: str,
    cookie_str: str,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    _, browser, page = await _setup_context(webvpn_base, cookie_str)

    all_plans: list[dict[str, Any]] = []
    plan_url = f"{webvpn_base}/zxjh/queryZxkc"
    seen_keys: set[str] = set()

    await page.goto(plan_url, wait_until="networkidle", timeout=timeout * 1000)
    await page.wait_for_selector("table tr td", state="attached", timeout=timeout * 1000)

    page_count = int(await page.evaluate(
        "() => { const pc = document.getElementById('pageCount'); return pc ? pc.value : '1'; }"
    ))
    logger.info("pw_plan_page_count=%s", page_count)

    current = 1
    while current <= page_count:
        if current > 1:
            await page.evaluate(f"""() => {{
                var o = document.getElementById('BOX_overlay');
                if(o) o.style.display='none';
                document.getElementById('pageNo').value = {current};
                var pgform = document.forms['page'];
                if (!pgform) {{
                    pgform = document.forms[0];
                }}
                pgform.submit();
            }}""")
            await page.wait_for_timeout(3000)
            await page.wait_for_selector("table tr td", state="attached", timeout=timeout * 1000)

        rows = await page.evaluate("""() => {
            const trs = document.querySelectorAll('table tr');
            return Array.from(trs).map(tr => {
                const tds = tr.querySelectorAll('td');
                return Array.from(tds).map(td => td.textContent.trim());
            }).filter(cells => cells.length >= 13 && cells[0].length >= 5);
        }""")
        for row in rows:
            if len(row) >= 13:
                key = f"{row[0]}|{row[1]}|{row[3]}|{row[4]}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_plans.append({
                        "course_code": row[0], "course_name": row[1],
                        "course_name_en": row[2], "school_year": row[3],
                        "semester": row[4], "college": row[5],
                        "course_nature": row[6], "course_category": row[8],
                        "credit": row[10], "hours": row[11], "is_exam": row[12],
                    })
        logger.info("pw_plan_page %s/%s total=%s", current, page_count, len(all_plans))
        current += 1

    await browser.close()
    return all_plans


async def _fetch_page_grades(
    webvpn_base: str,
    cookie_str: str,
    path: str,
    timeout: int,
    grade_type: str,
) -> list[dict[str, Any]]:
    _, browser, page = await _setup_context(webvpn_base, cookie_str)

    all_grades: list[dict[str, Any]] = []
    grade_url = f"{webvpn_base}{path}"
    seen_keys: set[str] = set()
    is_sxw = "Sxwcj" in path

    await page.goto(grade_url, wait_until="networkidle", timeout=timeout * 1000)
    await page.wait_for_selector("table tr td", state="attached", timeout=timeout * 1000)

    await _extract_page_grades(page, all_grades, seen_keys, is_sxw)
    page_count = int(await page.evaluate(
        "() => { const pc = document.getElementById('pageCount'); return pc ? pc.value : '1'; }"
    ))
    logger.info("pw_grade_page type=%s page_count=%s", grade_type, page_count)

    current = 1
    while current < page_count:
        next_page = str(current + 1)
        link = page.locator(f'a:has-text("{next_page}")')
        if await link.count() == 0:
            break
        try:
            await page.evaluate("() => { var o = document.getElementById('BOX_overlay'); if(o) o.style.display='none'; }")
            await link.first.click(force=True)
            await page.wait_for_timeout(3000)
            await page.wait_for_selector("table tr td", state="attached", timeout=timeout * 1000)
            await _extract_page_grades(page, all_grades, seen_keys, is_sxw)
            current += 1
            logger.info("pw_grade_page type=%s %s/%s total=%s", grade_type, current, page_count, len(all_grades))
        except Exception:
            logger.exception("pw_grade_page_click_failed type=%s page=%s", grade_type, next_page)
            current += 1

    await browser.close()
    return all_grades


async def _extract_page_grades(page, all_grades: list, seen_keys: set, is_sxw: bool) -> None:
    rows = await page.evaluate("""() => {
        const trs = document.querySelectorAll('table tr');
        return Array.from(trs).map(tr => {
            const tds = tr.querySelectorAll('td');
            return Array.from(tds).map(td => td.textContent.trim());
        }).filter(cells => cells.length >= 10 && /^\\d+$/.test(cells[0]));
    }""")
    for row in rows:
        if is_sxw:
            key = f"sxw|{row[1]}|{row[2]}|{row[10]}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_grades.append({
                "semester": row[1], "college": "", "course_code": row[2] if len(row) > 2 else "",
                "course_name": row[3] if len(row) > 3 else "", "course_nature": row[4] if len(row) > 4 else "",
                "course_category": row[5] if len(row) > 5 else "", "credit": row[6] if len(row) > 6 else "",
                "is_exam": row[7] if len(row) > 7 else "", "count_gpa": row[8] if len(row) > 8 else "",
                "makeup_flag": row[9] if len(row) > 9 else "", "total_score": row[10] if len(row) > 10 else "",
                "final_score": row[11] if len(row) > 11 else "", "score_remark": row[12] if len(row) > 12 else "",
                "student_type": "", "submit_time": "",
            })
        else:
            if len(row) < 16:
                continue
            key = f"final|{row[1]}|{row[3]}|{row[12]}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_grades.append({
                "semester": row[1], "college": row[2], "course_code": row[3],
                "course_name": row[4], "course_nature": row[5],
                "course_category": row[6], "credit": row[7],
                "is_exam": row[8], "count_gpa": row[9],
                "makeup_flag": row[10], "total_score": row[11],
                "final_score": row[12], "score_remark": row[13],
                "student_type": row[14], "submit_time": row[15],
            })


async def fetch_schedule_pw(
    webvpn_base: str,
    cookie_str: str,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    _, browser, page = await _setup_context(webvpn_base, cookie_str)

    all_schedules: list[dict[str, Any]] = []
    schedule_url = f"{webvpn_base}/kbcx/queryGrkb"

    await page.goto(schedule_url, wait_until="networkidle", timeout=timeout * 1000)

    semester = await page.evaluate("""() => {
        const sel = document.querySelector('select[name="xnxq"], select');
        if (sel) return sel.options[sel.selectedIndex]?.textContent?.trim() || '';
        return '';
    }""")

    rows = await page.evaluate("""() => {
        const trs = document.querySelectorAll('table tr');
        const data = [];
        for (let i = 2; i < trs.length; i++) {
            const cells = Array.from(trs[i].querySelectorAll('td, th')).map(c => c.textContent.trim());
            data.push(cells);
        }
        return data;
    }""")

    time_slots = ["1-2", "3-4", "5-6", "7-8", "9-10", "11-12"]
    for row_idx, row in enumerate(rows):
        if len(row) < 2:
            continue
        time_slot = row[1] if row[1] in time_slots else (
            time_slots[row_idx] if row_idx < len(time_slots) else ""
        )
        for col_idx in range(2, min(len(row), 9)):
            content = row[col_idx].strip()
            if content and not content.startswith(tuple("一二三四五六日")):
                day_of_week = col_idx - 1
                all_schedules.append({
                    "semester": semester,
                    "day_of_week": day_of_week,
                    "time_slot": time_slot,
                    "raw_content": content,
                })

    logger.info("pw_schedule_fetched count=%s semester=%s", len(all_schedules), semester)
    await browser.close()
    return all_schedules


async def fetch_exams_pw(
    webvpn_base: str,
    cookie_str: str,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    _, browser, page = await _setup_context(webvpn_base, cookie_str)

    all_exams: list[dict[str, Any]] = []
    exam_url = f"{webvpn_base}/kscx/queryKcForXs"

    await page.goto(exam_url, wait_until="networkidle", timeout=timeout * 1000)

    await page.evaluate("""() => {
        const selects = document.querySelectorAll('select');
        selects.forEach(s => {
            const opts = s.querySelectorAll('option');
            for (const o of opts) { if (o.text.includes('全部')) s.selectedIndex = Array.from(opts).indexOf(o); break; }
        });
        document.querySelectorAll('input[type="text"]').forEach(i => i.value = '');
        if (typeof queryLike === 'function') queryLike();
    }""")
    await page.wait_for_timeout(3000)
    await page.wait_for_selector("table tr td", state="attached", timeout=timeout * 1000)

    rows = await page.evaluate("""() => {
        const trs = document.querySelectorAll('table tr');
        return Array.from(trs).filter(tr => {
            const tds = tr.querySelectorAll('td');
            return tds.length >= 5 && /^\\d+$/.test(tds[0]?.textContent?.trim() || '');
        }).map(tr => Array.from(tr.querySelectorAll('td')).map(c => c.textContent.trim()));
    }""")

    for row in rows:
        if len(row) >= 6:
            all_exams.append({
                "course_name": row[1] if len(row) > 1 else "",
                "course_code": row[2] if len(row) > 2 else "",
                "exam_location": row[3] if len(row) > 3 else "",
                "seat_number": row[4] if len(row) > 4 else "",
                "exam_time": row[5] if len(row) > 5 else "",
            })

    logger.info("pw_exams_fetched count=%s", len(all_exams))
    await browser.close()
    return all_exams
