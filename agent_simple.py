# agent_simple.py
from extractor import extract_page
from llm import ask_groq
import json


def summarize_page(url: str, question: str = None) -> str:
    """
    Extract page content and use LLM to summarize or answer the user's question.
    """
    result = extract_page(url)

    if result["status"] != "success":
        return result.get("message", "Extraction failed")

    prompt = f"""You are a precise and concise web research agent.

Analyzed URL: {url}
Title: {result['title']}

Extracted main content:
{result['clean_text'][:14000]}

Relevant internal links (select the most important ones if needed):
{json.dumps([f"{i+1}. {link['text']} → {link['url']}" for i, link in enumerate(result['links'][:18])], indent=2)}

User question: {question or "Provide a clear summary of the main page content and list the key categories or sections (e.g., course categories, certifications offered)."}

Respond in clear, professional English.
Structure your answer neatly.
If more details are needed to fully answer, suggest 2–4 specific links (by number) that should be explored next.
"""

    answer = ask_groq(prompt)
    return answer


if __name__ == "__main__":
    url = input("Enter URL to analyze: ").strip() or "https://www.koenig-solutions.com/"
    question = input("What would you like to know? (press Enter for general summary): ").strip()

    print("\nProcessing...\n")
    response = summarize_page(url, question or None)
    print("=" * 80)
    print(response)
    print("=" * 80)