from __future__ import annotations

import logging
from typing import Any

from ._edu_base import setup_browser

logger = logging.getLogger(__name__)


async def fetch_plan(webvpn_base: str, cookie_str: str, timeout: int = 30) -> list[dict[str, Any]]:
    _, browser, page = await setup_browser(webvpn_base, cookie_str)

    all_plans: list[dict[str, Any]] = []
    plan_url = f"{webvpn_base}/zxjh/queryZxkc"
    seen_keys: set[str] = set()

    await page.goto(plan_url, wait_until="networkidle", timeout=timeout * 1000)
    await page.wait_for_selector("table tr td", state="attached", timeout=timeout * 1000)

    page_count = int(await page.evaluate(
        "() => { const pc = document.getElementById('pageCount'); return pc ? pc.value : '1'; }"
    ))
    logger.info("plan_page_count=%s", page_count)

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
        logger.info("plan_page %s/%s total=%s", current, page_count, len(all_plans))
        current += 1

    await browser.close()
    return all_plans
