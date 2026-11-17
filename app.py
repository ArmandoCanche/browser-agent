# app.py — DeepBrowse AI
# Multi-level web browser agent — navigate-first architecture

import chainlit as cl
from extractor import extract_page
from llm import ask_groq
import re
import copy

MAX_DEPTH = 3
MAX_LINKS = 20


# ── Page-type detection ───────────────────────────────────────────────────────

def is_course_page(url: str) -> bool:
    u = url.lower().rstrip("/")
    # Specific course-page patterns
    if re.search(r'-(training-course|certification-training|exam-prep|exam-training)($|/)', u):
        return True
    if u.endswith("-course"):
        return True
    if "/course/" in u or "/courses/" in u:
        return True
    return False


def is_category_page(url: str) -> bool:
    u = url.lower().rstrip("/")
    suffixes = [
        "-training", "-courses", "-certification",
        "-training-courses", "-certification-courses",
        "-training-and-certification-courses",
    ]
    return any(u.endswith(s) for s in suffixes) and not is_course_page(url)


# ── LLM prompts ───────────────────────────────────────────────────────────────

def build_summary_prompt(url: str, clean_text: str, title: str, question: str = None) -> str:
    if len(clean_text) > 16000:
        page_text = clean_text[:4000] + "\n[...]\n" + clean_text[-12000:]
    else:
        page_text = clean_text

    if question:
        focus = f"""The user wants to know: "{question}"

Answer using ONLY the content below. Be exhaustive:
- For course pages: include ALL of — overview, duration, level, prerequisites, price,
  full syllabus/topics, delivery modes, upcoming sessions, certification, benefits.
- For category pages: list ALL courses with their names, durations, and any pricing.
- Say "not available on this page" ONCE if a field is missing — never repeat it per item."""

    elif is_course_page(url):
        focus = """Extract ALL information from this course page using this structure:

## [Course Name]

**Overview**
[What the course covers and who it's for]

**Key Details**
- Duration:
- Level:
- Prerequisites:
- Price:
- Certification:

**Full Syllabus / Topics**
[List every module and topic found]

**Delivery Modes**
[Classroom, Live Online, Flexi, 1-on-1, FMAT — list all]

**Upcoming Sessions**
[All dates, times, locations]

**Why take this course**
[Outcomes, career relevance]

Be complete. Do not truncate the syllabus."""

    elif is_category_page(url):
        focus = """This is a course CATEGORY page. List every course found:

For each course:
- **Course name**
- Duration (if shown)
- Short description or key topics
- Price (if shown)

Group by subcategory if the page has sections. Include every course — do not skip any."""

    else:
        focus = """Summarize what this page offers. List all main sections, categories,
products or services. Note any pricing or key metrics visible on the page."""

    return f"""You are a precise web research agent.
URL: {url}
Title: {title}

Page content:
{page_text}

Task: {focus}

Rules:
- Use markdown (headings, bullets, **bold**).
- Be thorough. No filler phrases.
- Do not list navigation links or repeat "not available" per field.
"""


def build_navigation_prompt(question: str, links: list, chat_history: list = []) -> str:
    from urllib.parse import urlparse

    def is_cat_url(url):
        u = url.lower().rstrip("/")
        last = [s for s in urlparse(u).path.split("/") if s]
        if not last: return False
        last = last[-1]
        suffixes = ["-training", "-courses", "-certification",
                    "-training-courses", "-certification-courses"]
        m = next((s for s in suffixes if last.endswith(s)), None)
        if not m: return False
        base = last[: -len(m)]
        return len([w for w in base.split("-") if w]) <= 3

    link_list = "\n".join(
        f"{i+1}. {'[CATEGORY] ' if is_cat_url(l['url']) else ''}{l['text'][:70]} → {l['url']}"
        for i, l in enumerate(links[:40])
    )

    # Tomar los últimos 4 mensajes del historial
    recent = chat_history[-4:] if chat_history else []
    history_text = ""
    if recent:
        lines = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:200]}"
            for m in recent
        )
        history_text = f"""Previous conversation (for context):
{lines}

"""

    return f"""You are a web navigation agent.

{history_text}The user now asks: "{question}"

Available links:
{link_list}

Your job: find the ONE link that will most likely lead to the best answer.

Navigation strategy:
- Use the conversation history to understand what the user ALREADY saw and DIDN'T want.
- If the user rejects something (e.g. "not AWS", "too expensive", "need open source"):
  → avoid links similar to what was already visited
- If the question is about a VENDOR/TOPIC (e.g. "AWS courses", "Cisco training"):
  → prefer the CATEGORY page for that vendor (e.g. /aws-training, /cisco-training)
- If the question is about a SPECIFIC COURSE (e.g. "details of X", "info about X",
  "give me all information of X", "get inside of X"):
  → find the link whose text most closely matches X and navigate there
  → even a partial match on the course name is enough to navigate
- If the question asks to LIST courses for a category:
  → navigate to the category page first — never answer from the homepage
- Respond NONE only if truly no link is relevant

Respond with ONLY the number (e.g. "7") or NONE. No explanation."""


# ── Deduplication helpers ─────────────────────────────────────────────────────

def deduplicate_text(text: str) -> str:
    """Remove consecutive duplicate lines (caused by repeated DOM sections)."""
    lines = text.split("\n")
    seen = set()
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append(line)
            continue
        if stripped not in seen:
            seen.add(stripped)
            result.append(line)
    return "\n".join(result)


# ── Helpers ───────────────────────────────────────────────────────────────────

def format_links_list(links: list) -> str:
    lines = ["**Available links to explore:**\n"]
    for i, link in enumerate(links[:MAX_LINKS], 1):
        text = link["text"][:70] if len(link["text"]) > 70 else link["text"]
        lines.append(f"`{i:2d}.` {text}")
    lines.append("\n---")
    lines.append(
        "**Enter a number** to navigate · **Ask a question** · "
        "**Paste a URL** to restart · `compare https://...` to compare"
    )
    return "\n".join(lines)


def is_number(text: str) -> bool:
    return text.strip().isdigit()


def is_url(text: str) -> bool:
    return text.strip().startswith(("http://", "https://"))


def is_compare(text: str) -> bool:
    return text.strip().lower().startswith("compare ")


def extract_compare_url(text: str) -> str | None:
    m = re.search(r"https?://\S+", text)
    return m.group(0) if m else None


def depth_label(depth: int) -> str:
    return ["Base page", "Level 2", "Level 3"][min(depth, 2)]


# ── State management ──────────────────────────────────────────────────────────

EMPTY_STATE = {
    "depth": 0,
    "current_url": None,
    "current_title": None,
    "current_links": [],       # displayed links (capped at MAX_LINKS)
    "all_links": [],           # all extracted links for navigation decisions
    "root_links": [],          # links from depth-0 page — never overwritten
    "current_summary": "",
    "current_raw_text": "",
    "breadcrumb": [],
    "chat_history": [],
    "root_summary": "",
    "root_raw_text": "",
    "root_title": "",
    "page_cache": {},
    "page_cache_links": {}
}


def get_state() -> dict:
    return cl.user_session.get("nav_state", copy.deepcopy(EMPTY_STATE))

def save_state(state: dict):
    cl.user_session.set("nav_state", state)


def reset_state():
    cl.user_session.set("nav_state", copy.deepcopy(EMPTY_STATE))

# ── Core: navigate and summarize ──────────────────────────────────────────────

async def navigate_to(url: str, question: str = None, depth: int = 0):
    label = depth_label(depth)

    async with cl.Step(name=f"{label} — Scraping page") as step:
        result = extract_page(url)
        if result["status"] != "success":
            step.output = f"Failed: {result.get('message', 'unknown')}"
            await cl.Message(
                content=f"Could not load: `{result.get('message', 'unknown')}`"
            ).send()
            return
        step.output = f"{result['title']} · {result['link_count']} links"

    async with cl.Step(name=f"{label} — Analyzing") as step:
        clean = deduplicate_text(result["clean_text"])
        prompt = build_summary_prompt(url, clean, result["title"], question)
        max_tok = 2500 if (is_course_page(url) or is_category_page(url)) else 2000
        summary = ask_groq(prompt, model="llama-3.3-70b-versatile", max_tokens=max_tok)
        step.output = "Done"

    links = result["links"][:MAX_LINKS]
    parts = [f"### {result['title']}\n", summary]

    if depth < MAX_DEPTH - 1 and links:
        parts.append("\n\n" + format_links_list(links))
    else:
        parts.append(
            "\n\n---\n*Max depth reached. Ask a question or paste a new URL.*"
        )

    await cl.Message(content="\n".join(parts)).send()

    state = get_state()
    state["depth"]            = depth
    state["current_url"]      = url
    state["current_title"]    = result["title"]
    state["current_links"]    = links
    state["all_links"]        = result["links"]
    state["current_summary"]  = summary
    state["current_raw_text"] = clean
    state["breadcrumb"].append({"url": url, "title": result["title"]})
    if question:
        state["chat_history"].append({"role": "user", "content": question})
    state["chat_history"].append({
        "role": "assistant",
        "content": f"[Visited: {result['title']}]"
    })
    state["chat_history"] = state["chat_history"][-6:]
    state["page_cache"][url] = summary
    if depth == 0:
        state["root_links"]    = result["links"]
        state["root_summary"]  = summary
        state["root_raw_text"] = clean
        state["root_title"]    = result["title"]
    state["page_cache_links"][url] = result["links"]  # todos, sin [:MAX_LINKS]
    save_state(state)


# ── Core: find best URL to navigate to ───────────────────────────────────────

async def find_and_navigate(question: str, state: dict) -> bool:
    """
    Ask the LLM which link answers the question best, then navigate there.
    Returns True if navigation happened, False if we should answer from context.

    Search order:
    1. root_links  (top-level categories, vendors — broadest pool)
    2. all_links   (current page links — more specific)
    """
    next_depth = state["depth"] + 1
    if next_depth >= MAX_DEPTH:
        return False

    # Build combined unique pool: root first, then current page
    seen_urls = set()
    search_pool = []
    for link in (state.get("root_links") or []) + (state.get("all_links") or []):
        if link["url"] not in seen_urls:
            seen_urls.add(link["url"])
            search_pool.append(link)

    if not search_pool:
        return False
    # ── Búsqueda directa por texto en TODOS los links almacenados ────────────
    question_lower = question.lower()
    # Extraer palabras clave significativas de la pregunta (más de 3 chars)
    keywords = [w for w in re.sub(r'[^a-z0-9\s-]', '', question_lower).split() 
                if len(w) > 3 and w not in {'give', 'show', 'tell', 'about', 'details', 'course', 'courses', 'what', 'the', 'all', 'more', 'info'}]
    
    if keywords:
        # Buscar en todos los links cacheados (sin límite de tokens)
        all_cached_links = []
        for cached_links in state.get("page_cache_links", {}).values():
            all_cached_links.extend(cached_links)
        
        best_match = None
        best_score = 0
        for link in all_cached_links:
            link_text_lower = link["text"].lower()
            link_url_lower = link["url"].lower()
            # Contar cuántas keywords coinciden en texto o URL
            score = sum(1 for kw in keywords if kw in link_text_lower or kw in link_url_lower)
            if score > best_score and score >= min(2, len(keywords)):
                best_score = score
                best_match = link

        if best_match and best_match["url"] != state.get("current_url"):
            print(f"[DIRECT MATCH] {best_match['text']} → {best_match['url']} (score={best_score})")
            await cl.Message(
                content=(
                    f"*Found direct match...*\n\n"
                    f"**→ {best_match['text'][:80]}**\n`{best_match['url']}`"
                )
            ).send()
            root_urls = {l["url"] for l in (state.get("root_links") or [])}
            target_depth = 1 if best_match["url"] in root_urls else next_depth
            
            if best_match["url"] in state.get("page_cache", {}):
                # Servir desde caché si ya visitamos esa página
                cached_summary = state["page_cache"][best_match["url"]]
                await cl.Message(content=f"*From cache*\n\n{cached_summary}").send()
                state = get_state()
                state["current_url"] = best_match["url"]
                state["current_title"] = best_match["text"]
                state["current_summary"] = cached_summary
                state["depth"] = target_depth
                state["breadcrumb"].append({"url": best_match["url"], "title": best_match["text"]})
                state["chat_history"].append({"role": "user", "content": question})
                state["chat_history"].append({"role": "assistant", "content": f"[Direct match: {best_match['text']}]"})
                state["chat_history"] = state["chat_history"][-6:]
                save_state(state)
                return True
            
            await navigate_to(best_match["url"], question=question, depth=target_depth)
            return True
    # ── Si no hay match directo, usar LLM como antes ──────────────────────────

    async with cl.Step(name="Finding best page to navigate") as step:
        prompt = build_navigation_prompt(question, search_pool, chat_history=state.get("chat_history", []) )
        llm_result = ask_groq(
            prompt, model="llama-3.1-8b-instant", max_tokens=10
        ).strip()
        step.output = f"LLM chose: {llm_result}"

    if not llm_result.isdigit():
        return False

    idx = int(llm_result) - 1
    if idx < 0 or idx >= len(search_pool):
        return False

    chosen = search_pool[idx]

    # Don't navigate to where we already are
    if chosen["url"] == state["current_url"]:
        return False

    await cl.Message(
        content=(
            f"*Navigating to the most relevant page...*\n\n"
            f"**→ {chosen['text']}**\n`{chosen['url']}`"
        )
    ).send()

    # Determine depth: if from root_links, it's depth 1; otherwise current+1
    root_urls = {l["url"] for l in (state.get("root_links") or [])}
    target_depth = 1 if chosen["url"] in root_urls else next_depth

    if chosen["url"] in state.get("page_cache", {}):
        cached_summary = state["page_cache"][chosen["url"]]
        parts = [f"*From cache — {chosen['text']}*\n\n", cached_summary]
        await cl.Message(content="\n".join(parts)).send()
        # Actualizar estado sin navegar
        state = get_state()
        state["current_url"]     = chosen["url"]
        state["current_title"]   = chosen["text"]
        state["current_summary"] = cached_summary
        state["depth"]           = target_depth
        state["breadcrumb"].append({"url": chosen["url"], "title": chosen["text"]})
        state["chat_history"].append({"role": "user", "content": question})
        state["chat_history"].append({"role": "assistant", "content": f"[From cache: {chosen['text']}]"})
        state["chat_history"] = state["chat_history"][-6:]
        save_state(state)
        return True

    await navigate_to(chosen["url"], question=question, depth=target_depth)
    return True


# ── Chainlit handlers ─────────────────────────────────────────────────────────

@cl.on_chat_start
async def start_chat():
    reset_state()
    await cl.Message(content="""# DeepBrowse AI
**Multi-Level Web Intelligence Agent**

---

Paste a URL to begin. I'll scrape the content, summarize it, and let you navigate deeper.

**How to use:**
```
https://example.com
```
Then ask questions or type a number to follow a link.

- Ask **"give me all AWS courses"** → I navigate to `/aws-training` and list them
- Ask **"details of Solutions Architect"** → I navigate to the course page
- Type a **number** to follow a specific link manually
- Type `compare https://other.com` to compare two sites

> 3 levels deep · auto-navigation · real scraping at each level
""").send()


@cl.on_message
async def handle_message(message: cl.Message):
    user_input = message.content.strip()
    state = get_state()

    if not user_input:
        await cl.Message(content="Please enter a URL, a number, or a question.").send()
        return

    lines = user_input.split("\n", 1)
    first_line = lines[0].strip()
    inline_question = lines[1].strip() if len(lines) > 1 else None

    # ── Case 1: URL ───────────────────────────────────────────────────────────
    if is_url(first_line):
        reset_state()
        await navigate_to(first_line, question=inline_question, depth=0)
        return

    # ── Case 2: Compare ───────────────────────────────────────────────────────
    if is_compare(user_input):
        compare_url = extract_compare_url(user_input)
        if not compare_url:
            await cl.Message(
                content="Include a URL: `compare https://other-site.com`"
            ).send()
            return
        if not state["current_url"]:
            await cl.Message(content="Load a page first.").send()
            return

        async with cl.Step(name="Loading comparison site") as step:
            r = extract_page(compare_url)
            if r["status"] != "success":
                await cl.Message(content=f"Failed: `{r.get('message')}`").send()
                return
            step.output = f"{r['title']}"

        async with cl.Step(name="Comparing") as step:
            prompt = f"""Compare these two sites objectively.

Site A: {state['current_url']}
Summary A:
{state['current_summary'][:5000]}

Site B: {compare_url}
Title B: {r['title']}
Content B:
{r['clean_text'][:6000]}

Write a structured comparison covering purpose, depth, topics, audience, strengths/weaknesses.
Use a markdown table for key differences."""
            comparison = ask_groq(prompt, model="llama-3.3-70b-versatile", max_tokens=2500)
            step.output = "Done"

        await cl.Message(content=f"## Site Comparison\n\n{comparison}").send()
        return

    # ── Case 3: Number ────────────────────────────────────────────────────────
    if is_number(user_input):
        if not state["current_links"]:
            await cl.Message(content="No links available. Load a page first.").send()
            return

        idx = int(user_input.strip()) - 1
        if idx < 0 or idx >= len(state["current_links"]):
            await cl.Message(
                content=f"Enter a number between 1 and {len(state['current_links'])}."
            ).send()
            return

        next_depth = state["depth"] + 1
        if next_depth >= MAX_DEPTH:
            await cl.Message(
                content="Max depth reached. Ask a question or paste a new URL."
            ).send()
            return

        chosen = state["current_links"][idx]
        crumb = " → ".join(
            [b["title"][:20] for b in state["breadcrumb"]]
            + [chosen["text"][:20]]
        )
        await cl.Message(
            content=f"*Navigating to:* **{chosen['text']}**\n`{crumb}`"
        ).send()
        await navigate_to(chosen["url"], depth=next_depth)
        return

    # ── Case: back — volver un nivel ─────────────────────────────────────────
    if user_input.strip().lower() == "back":
        if len(state["breadcrumb"]) < 2:
            await cl.Message(content="Already at the first level.").send()
            return
        state["breadcrumb"].pop()
        prev = state["breadcrumb"].pop()
        save_state(state)
        await navigate_to(prev["url"], depth=len(state["breadcrumb"]))
        return

    # ── Case: home — volver al nivel 1 sin re-scrape ─────────────────────────
    if user_input.strip().lower() == "home":
        if not state["breadcrumb"]:
            await cl.Message(content="No page loaded yet.").send()
            return
        state["depth"]            = 0
        state["current_url"]      = state["breadcrumb"][0]["url"]
        state["current_title"]    = state["root_title"]
        state["current_links"]    = state["root_links"][:MAX_LINKS]
        state["all_links"]        = state["root_links"]
        state["current_summary"]  = state["root_summary"]
        state["current_raw_text"] = state["root_raw_text"]
        state["breadcrumb"]       = []
        save_state(state)
        parts = [
            f"### {state['root_title']}\n",
            state["root_summary"],
            "\n\n" + format_links_list(state["root_links"][:MAX_LINKS])
        ]
        await cl.Message(content="\n".join(parts)).send()
        return
    
    # ── Case 4: Question — NAVIGATE FIRST ────────────────────────────────────
    if state["current_url"]:
        # Always try to navigate to a better page before answering
        navigated = await find_and_navigate(user_input, state)
        if navigated:
            return

        # Only reach here if LLM said NONE — answer from current page context
        async with cl.Step(name="Answering from current page") as step:
            raw = state.get("current_raw_text", "")
            if len(raw) > 18000:
                context = raw[:4000] + "\n[...]\n" + raw[-14000:]
            else:
                context = raw

            prompt = f"""You are a web research agent.
Page: {state['current_url']}
Title: {state['current_title']}

Content:
{context}

Question: {user_input}

Answer exhaustively using ONLY the content above.
- List ALL items if the question asks for a list.
- For course details: include duration, price, prerequisites, syllabus, 
  delivery modes, certification, upcoming dates.
- If something is missing, say so in one sentence.
- Use markdown (headings, bullets, **bold**).
"""
            answer = ask_groq(
                prompt, model="llama-3.3-70b-versatile", max_tokens=2500
            )
            step.output = "Done"

        await cl.Message(content=answer).send()
        state = get_state()
        state["chat_history"].append({"role": "user", "content": user_input})
        state["chat_history"].append({"role": "assistant", "content": answer[:300]})
        state["chat_history"] = state["chat_history"][-6:]
        save_state(state)
        return

    await cl.Message(content="Paste a URL to get started.").send()