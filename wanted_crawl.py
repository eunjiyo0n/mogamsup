import json
import time
import re
from playwright.sync_api import sync_playwright

# ------------------------------------------------------------------
# debug_structure.py 실행 결과로 확인된 실제 원티드 JSON 경로 (2026-07 기준)
# data['props']['pageProps']['initialData'] 안에 아래 필드들이 들어있음
# ------------------------------------------------------------------
FIELD_TITLE = "position"               # 직무명 필드
FIELD_REQUIREMENTS = "requirements"    # 자격요건 필드
FIELD_PREFERRED = "preferred_points"   # 우대사항 필드


def crawl_wanted_final():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print("--- [1단계] 목록 수집 중 ---")
        url = "https://www.wanted.co.kr/wdlist/all?country=kr&job_sort=job.latest_order&years=-1"
        try:
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(4)
        except Exception:
            pass

        job_links = set()
        for _ in range(10):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1.5)
            links = page.locator('a[href*="/wd/"]').all()
            for link in links:
                href = link.get_attribute('href')
                if href and "/wd/" in href:
                    clean = href.split('?')[0]
                    full = "https://www.wanted.co.kr" + clean if clean.startswith("/") else clean
                    job_links.add(full)
            if len(job_links) >= 55:
                break

        job_links_list = list(job_links)[:55]
        print(f"\n--- [2단계] {len(job_links_list)}개 공고 상세 분석 시작 ---")

        results = []

        for i, link in enumerate(job_links_list):
            try:
                page.goto(link, wait_until="networkidle", timeout=60000)
                time.sleep(2)

                job_title = ""
                company = ""
                category = "일반"
                requirements = ""
                preferred = ""

                # --- 1. __NEXT_DATA__ JSON에서 전부 한 번에 뽑기 (가장 안정적) ---
                # 실제 경로: data['props']['pageProps']['initialData']  (debug_structure.py로 확인됨)
                job_data = None
                try:
                    script_content = page.locator('script#__NEXT_DATA__').inner_html()
                    data = json.loads(script_content)
                    page_props = data.get('props', {}).get('pageProps', {})
                    job_data = page_props.get('initialData')
                except Exception:
                    job_data = None

                if job_data and isinstance(job_data, dict):
                    # 제목 (직무명)
                    job_title = job_data.get(FIELD_TITLE, "")

                    # 회사명
                    company_obj = job_data.get('company', {})
                    if isinstance(company_obj, dict):
                        company = company_obj.get('company_name', '')

                    # 카테고리: parent_tag(대분류) + child_tags(소분류, 리스트)
                    cat_tag = job_data.get('category_tag', {})
                    if isinstance(cat_tag, dict):
                        parent_cat = (cat_tag.get('parent_tag') or {}).get('text', '')
                        child_tags = cat_tag.get('child_tags') or []
                        child_names = [t.get('text', '') for t in child_tags if isinstance(t, dict)]
                        child_cat = " / ".join([c for c in child_names if c])
                        if parent_cat and child_cat:
                            category = f"{parent_cat} · {child_cat}"
                        elif parent_cat:
                            category = parent_cat
                        elif child_cat:
                            category = child_cat

                    # 자격요건 / 우대사항 (initialData 바로 아래에 있음)
                    requirements = job_data.get(FIELD_REQUIREMENTS, "") or ""
                    preferred = job_data.get(FIELD_PREFERRED, "") or ""

                # --- 2. JSON에서 제목/회사명을 못 얻었으면 페이지 타이틀로 폴백 ---
                if not job_title or not company:
                    raw_title = page.title().split(" | 원티드")[0].strip()
                    # 괄호를 제대로 escape (원래 코드의 $$ 오타 수정)
                    match = re.match(r"^\((.*?)\)\s*(.*)", raw_title)
                    if match:
                        company = company or match.group(1).strip()
                        job_title = job_title or match.group(2).strip()
                    else:
                        job_title = job_title or raw_title
                        company = company or "회사명 확인 필요"

                # --- 3. 카테고리를 JSON에서 못 얻었으면 태그로 폴백 ---
                if category == "일반":
                    tags = page.locator('a[href*="/tags/"]').all_inner_texts()
                    if tags:
                        category = tags[0].replace("#", "")

                # --- 4. 자격요건/우대사항을 JSON에서 못 얻었으면 본문 텍스트 파싱으로 폴백 ---
                if not requirements or not preferred:
                    body_text = page.locator('body').inner_text()

                    if not requirements and "자격요건" in body_text:
                        req_raw = body_text.split("자격요건")[1]
                        req_clean = re.split(
                            r"우대사항|상세 정보 더 보기|기술 스택|태그|마감일|근무지역",
                            req_raw
                        )[0]
                        requirements = req_clean.strip()

                    if not preferred and "우대사항" in body_text:
                        pref_raw = body_text.split("우대사항")[1]
                        pref_clean = re.split(
                            r"혜택 및 복지|상세 정보 더 보기|기술 스택|태그|마감일|근무지역",
                            pref_raw
                        )[0]
                        preferred = pref_clean.strip()

                results.append({
                    "job_title": job_title,
                    "company": company,
                    "category": category,
                    "requirements": requirements,
                    "preferred": preferred,
                    "url": link
                })
                print(f"[{i+1}/{len(job_links_list)}] {category} | {company} - {job_title[:20]}")

            except Exception as e:
                print(f"[{i+1}] 분석 실패: {link} ({e})")
                continue

        with open("jobs.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=4)

        print(f"\n✅ 수집 완료: {len(results)}개. jobs.json을 확인하세요.")
        browser.close()


if __name__ == "__main__":
    crawl_wanted_final()