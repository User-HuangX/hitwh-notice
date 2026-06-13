from __future__ import annotations

import logging
from typing import Any

from ._edu_base import setup_browser

logger = logging.getLogger(__name__)


async def fetch_schedule(webvpn_base: str, cookie_str: str, timeout: int = 30) -> list[dict[str, Any]]:
    _, browser, page = await setup_browser(webvpn_base, cookie_str)

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

    logger.info("schedule_fetched count=%s semester=%s", len(all_schedules), semester)
    await browser.close()
    return all_schedules
