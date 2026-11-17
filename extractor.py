# extractor.py
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time


def extract_page(url: str, timeout: int = 20, max_scroll_attempts: int = 4) -> dict:
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=options
    )

    try:
        print(f"[EXTRACTOR] → {url}")
        driver.get(url)

        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(2)

        # Scroll to trigger lazy content
        if max_scroll_attempts > 0:
            last_h = driver.execute_script("return document.body.scrollHeight")
            for _ in range(max_scroll_attempts):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(3)
                new_h = driver.execute_script("return document.body.scrollHeight")
                if new_h == last_h:
                    break
                last_h = new_h

        time.sleep(2)

        html = driver.page_source
        # Prefer lxml if available, but fall back to the builtin parser to avoid crashes
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        title = soup.title.string.strip() if soup.title else "No title"

        # Remove noise
        for tag in soup.find_all(["nav", "header", "footer"]):
            tag.decompose()

        for id_name in ["lgMenu", "SmMenu", "headContactBg", "opaque"]:
            t = soup.find(id=id_name)
            if t:
                t.decompose()

        for tag in soup.find_all(
            True,
            {"class": lambda c: c and any(
                kw in " ".join(c).lower()
                for kw in [
                    "cookie", "popup", "modal", "overlay", "banner", "toast",
                    "breadcrumb", "social-", "share-", "back-to-top",
                    "chatbot", "intercom", "chat-widget",
                ]
            )},
        ):
            tag.decompose()

        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        main_content = (
            soup.find("main")
            or soup.find("article")
            or soup.find(id="content")
            or soup.find(id="main")
            or soup.find(attrs={"role": "main"})
            or soup.body
        )
        text = main_content.get_text(separator="\n", strip=True) if main_content else ""

        # Extract internal links
        base_domain = urlparse(url).netloc
        seen_urls: set[str] = set()
        links: list[dict] = []

        unwanted = {
            "email", "whatsapp", "chat only", "request more information",
            "show all", "view more", "read more", "click here",
            "privacy policy", "terms", "login", "sign in", "register",
            "contact us", "about us", "home",
        }

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:", "tel:", "mailto:")):
                continue
            link_text = a.get_text(strip=True)
            if len(link_text) < 4:
                continue
            if any(kw in link_text.lower() for kw in unwanted):
                continue
            full_url = urljoin(url, href)
            parsed = urlparse(full_url)
            if parsed.netloc not in (base_domain, ""):
                continue
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            links.append({"text": link_text[:140], "url": full_url})

        # Prioritize: categories → courses → other
        cat, course, other = [], [], []
        for link in links:
            u = link["url"].lower().rstrip("/")
            if any(u.endswith(s) for s in [
                "-training", "-courses", "-certification",
                "-training-courses", "-certification-courses",
                "-training-and-certification-courses",
            ]):
                cat.append(link)
            elif any(kw in u for kw in [
                "-training-course", "-certification-training",
                "-exam-prep", "-exam-training",
                "aws-certified-", "azure-", "cisco-", "vmware-",
                "oracle-", "comptia-", "microsoft-",
            ]) or u.endswith("-course"):
                course.append(link)
            else:
                other.append(link)

        cat.sort(key=lambda x: x["text"].lower())
        course.sort(key=lambda x: x["text"].lower())
        links = cat + course + other

        return {
            "url": url,
            "title": title,
            "clean_text": text[:35000],
            "links": links[:50],
            "link_count": len(links),
            "status": "success",
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        driver.quit()