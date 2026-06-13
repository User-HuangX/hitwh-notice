from __future__ import annotations

import logging
from typing import Any

from ._edu_base import setup_browser

logger = logging.getLogger(__name__)

GRADE_COLS_FINAL = [
    "semester", "college", "course_code", "course_name", "course_nature",
    "course_category", "credit", "is_exam", "count_gpa", "makeup_flag",
    "total_score", "final_score", "score_remark", "student_type", "submit_time",
]
GRADE_COLS_SXW = [
    "semester", "college", "course_code", "course_name", "course_nature",
    "course_category", "credit", "is_exam", "count_gpa", "makeup_flag",
    "total_score", "final_score", "score_remark", "student_type", "submit_time",
]


async def fetch_grades(webvpn_base: str, cookie_str: str, timeout: int = 30) -> list[dict[str, Any]]:
    raw = []
    raw.extend(await _fetch_one_path(webvpn_base, cookie_str, "/cjcx/queryQmcj", "final", timeout))
    raw.extend(await _fetch_one_path(webvpn_base, cookie_str, "/cjcx/querySxwcj", "sxw", timeout))
    return raw


async def _fetch_one_path(webvpn_base: str, cookie_str: str, path: str,
                          grade_type: str, timeout: int) -> list[dict[str, Any]]:
    _, browser, page = await setup_browser(webvpn_base, cookie_str)
    all_rows: list[list[str]] = []
    is_sxw = "Sxwcj" in path
    seen_keys: set[str] = set()

    await page.goto(f"{webvpn_base}{path}", wait_until="networkidle", timeout=timeout * 1000)
    await page.wait_for_selector("table tr td", state="attached", timeout=timeout * 1000)

    await _extract_page(page, all_rows, seen_keys, is_sxw)
    page_count = int(await page.evaluate(
        "() => { const pc = document.getElementById('pageCount'); return pc ? pc.value : '1'; }"
    ))
    logger.info("grade_page type=%s page_count=%s", grade_type, page_count)

    current = 1
    while current < page_count:
        next_page = str(current + 1)
        link = page.locator(f'a:has-text("{next_page}")')
        if await link.count() == 0:
            break
        try:
            await page.evaluate(
                "() => { var o = document.getElementById('BOX_overlay'); if(o) o.style.display='none'; }")
            await link.first.click(force=True)
            await page.wait_for_timeout(3000)
            await page.wait_for_selector("table tr td", state="attached", timeout=timeout * 1000)
            await _extract_page(page, all_rows, seen_keys, is_sxw)
            current += 1
            logger.info("grade_page type=%s %s/%s total=%s", grade_type, current, page_count, len(all_rows))
        except Exception:
            logger.exception("grade_page_click_failed type=%s page=%s", grade_type, next_page)
            current += 1

    await browser.close()
    return all_rows


async def _extract_page(page, all_rows: list, seen_keys: set, is_sxw: bool) -> None:
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
            if key not in seen_keys:
                seen_keys.add(key)
                all_rows.append({
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
            if key not in seen_keys:
                seen_keys.add(key)
                all_rows.append({
                    "semester": row[1], "college": row[2], "course_code": row[3],
                    "course_name": row[4], "course_nature": row[5],
                    "course_category": row[6], "credit": row[7],
                    "is_exam": row[8], "count_gpa": row[9],
                    "makeup_flag": row[10], "total_score": row[11],
                    "final_score": row[12], "score_remark": row[13],
                    "student_type": row[14], "submit_time": row[15],
                })
