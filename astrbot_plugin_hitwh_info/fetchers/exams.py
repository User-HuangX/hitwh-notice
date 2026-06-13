from __future__ import annotations

import logging
from typing import Any

from ._edu_base import setup_browser

logger = logging.getLogger(__name__)


async def fetch_exams(webvpn_base: str, cookie_str: str, timeout: int = 30) -> list[dict[str, Any]]:
    _, browser, page = await setup_browser(webvpn_base, cookie_str)

    all_exams: list[dict[str, Any]] = []
    exam_url = f"{webvpn_base}/kscx/queryKcForXs"

    await page.goto(exam_url, wait_until="networkidle", timeout=timeout * 1000)

    await page.evaluate("""() => {
        const selects = document.querySelectorAll('select');
        selects.forEach(s => {
            const opts = s.querySelectorAll('option');
            for (const o of opts) { if (o.text.includes('全部')) { s.selectedIndex = Array.from(opts).indexOf(o); break; } }
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

    logger.info("exams_fetched count=%s", len(all_exams))
    await browser.close()
    return all_exams
