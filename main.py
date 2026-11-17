# main.py - Basic console extractor test
from extractor import extract_page

if __name__ == "__main__":
    url = input("Enter URL to extract: ").strip() or "https://www.koenig-solutions.com/"

    print("\nExtracting page...\n")
    result = extract_page(url)

    if result["status"] != "success":
        print("Error:", result.get("message", "Unknown error"))
    else:
        print("=" * 70)
        print(f"Title: {result['title']}")
        print(f"URL: {result['url']}")
        print(f"Links found: {result['link_count']}")
        print("=" * 70)

        print("\nMain content preview (first ~3000 chars):")
        print(result['clean_text'][:3000])
        if len(result['clean_text']) > 3000:
            print("... [truncated]")

        print("\nTop 10 detected links:")
        for i, link in enumerate(result['links'][:10], 1):
            print(f"  {i:2d}. {link['text'][:70]:<70} → {link['url']}")