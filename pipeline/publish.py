#!/usr/bin/env python3
"""
Master publish pipeline for Hugo content sites.
Generates articles via Claude API and commits as Markdown to GitHub.
Cloudflare Pages auto-deploys on each commit.

Features:
- Per-site personas with rotating author bylines
- YMYL disclaimers for legal/medical niches
- Anti-AI-detection writing rules
- Mixed article length based on keyword priority
- Amazon affiliate link injection per niche
- Authoritative external reference links per niche
- High-priority keyword ordering with published tracking
"""

import os
import sys
import json
import time
import random
import argparse
import requests
import base64
import csv
import io
import anthropic
from datetime import datetime, timezone

# ── CONFIG ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY       = os.environ["ANTHROPIC_API_KEY"]
GITHUB_TOKEN            = os.environ.get("PIPELINE_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
GITHUB_ORG              = os.environ.get("PORTFOLIO_ORG") or os.environ.get("GITHUB_ORG", "peacoat-sites")
PEXELS_KEY              = os.environ.get("PEXELS_API_KEY", "")
FLUX_KEY                = os.environ.get("FLUX_API_KEY", "")
ANTHROPIC_MONTHLY_BUDGET = float(os.environ.get("ANTHROPIC_MONTHLY_BUDGET", "150"))
AMAZON_TRACKING_ID      = os.environ.get("AMAZON_TRACKING_ID", "")

SITES = json.loads(os.environ.get("SITES_CONFIG", "{}"))

GH_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
    "Content-Type": "application/json",
}

# ── SITE PERSONAS ─────────────────────────────────────────────────────────────
# Each site has a pool of author personas. Articles rotate through them randomly
# so the site reads like a multi-contributor publication, not a bot.

SITE_PERSONAS = {
    "injury-victim-guide": {
        "tone": (
            "You are a consumer advocate who spent 12 years as an insurance adjuster before "
            "switching sides to help injury victims. You explain legal concepts the way a "
            "knowledgeable friend would over coffee -- clear, direct, no jargon unless you "
            "define it immediately. You are empathetic but practical. You never give legal "
            "advice but you make sure readers understand their rights and options."
        ),
        "authors": [
            {"name": "James Harmon", "bio": "Former insurance adjuster with 15 years in claims. Now a consumer advocate helping injury victims understand the process."},
            {"name": "Sarah Chen", "bio": "Paralegal specializing in personal injury. Helped process over 400 claims across 3 law firms."},
            {"name": "Michael Torres", "bio": "Legal educator and personal injury claims consultant. Writes to help everyday people navigate the system."},
            {"name": "Rachel Webb", "bio": "Consumer rights advocate with a background in insurance claims and settlement negotiation."},
        ],
        "ymyl": True,
        "disclaimer": (
            "*This article is for general informational purposes only and does not constitute legal advice. "
            "Laws vary by state. Consult a licensed personal injury attorney in your jurisdiction for advice "
            "specific to your situation. Most personal injury attorneys offer free consultations.*"
        ),
    },
    "medicare-starter": {
        "tone": (
            "You are a patient, reassuring Medicare counselor with 20 years helping seniors navigate "
            "their benefits. You write like you are explaining Medicare to a trusted family member -- "
            "warm, clear, never condescending, always thorough. You use plain English, define acronyms "
            "immediately, and always point readers to official resources like Medicare.gov."
        ),
        "authors": [
            {"name": "Linda Morrison", "bio": "Certified Medicare counselor with 20 years helping seniors understand their benefits options."},
            {"name": "Robert Hughes", "bio": "Retired insurance broker who specialized in Medicare supplements for over 18 years."},
            {"name": "Carol Davenport", "bio": "Senior health advocate and Medicare education specialist. Volunteers with State Health Insurance Assistance Programs (SHIP)."},
            {"name": "David Park", "bio": "Benefits counselor focused on helping Medicare-eligible Americans understand their coverage options."},
        ],
        "ymyl": True,
        "disclaimer": (
            "*This article is for informational purposes only. Medicare rules change annually. "
            "Always verify current plan details at Medicare.gov or by calling 1-800-MEDICARE (1-800-633-4227). "
            "This site does not sell insurance or recommend specific plans.*"
        ),
    },
    "solar-planner-guide": {
        "tone": (
            "You are a solar consultant who has helped hundreds of homeowners go solar. You are "
            "data-driven and honest -- you tell readers what installers might not, including the "
            "downsides of going solar in certain situations. You back claims with real numbers from "
            "sources like SEIA, EnergySage, and NREL. You write for the curious homeowner who wants "
            "to make a smart, informed decision."
        ),
        "authors": [
            {"name": "Derek Walsh", "bio": "Solar installation project manager with 10+ years in residential solar across 6 states."},
            {"name": "Amanda Park", "bio": "Home energy analyst and solar consultant. Specializes in helping homeowners evaluate solar ROI."},
            {"name": "Chris Navarro", "bio": "Certified energy auditor and NABCEP-trained solar advisor. Has evaluated over 500 home solar proposals."},
            {"name": "Priya Mehta", "bio": "Renewable energy researcher and homeowner solar advocate. Focuses on financing, incentives, and long-term savings."},
        ],
        "ymyl": False,
        "disclaimer": "",
    },
    "solar-home-planner": {
        "tone": (
            "You are a licensed electrician who transitioned into residential solar installation and "
            "DIY solar consulting. You write for the hands-on homeowner who wants the technical details, "
            "not just the marketing pitch. You cover permits, HOA rules, system sizing, and real contractor "
            "red flags. You are practical, specific, and respect your reader's intelligence."
        ),
        "authors": [
            {"name": "Tom Briggs", "bio": "Licensed electrician and solar installer with 12 years experience in DIY and residential solar projects."},
            {"name": "Jennifer Liu", "bio": "Green building specialist and NABCEP certified solar professional. Focuses on permits, codes, and contractor selection."},
            {"name": "Marcus Reed", "bio": "Off-grid systems designer and solar workshop instructor. Helps homeowners plan systems from sizing to battery backup."},
            {"name": "Kevin Walsh", "bio": "Residential solar contractor turned educator. Writes to help homeowners avoid common installation pitfalls."},
        ],
        "ymyl": False,
        "disclaimer": "",
    },
}

# ── AMAZON AFFILIATE PRODUCTS (by niche key) ─────────────────────────────────
# ASINs verified as real, high-review products in each category.
# Replace tracking tag after Amazon Associates approval.

NICHE_ASINS = {
    "personal injury law": [
        ("B08MBF3WNH", "Avery Durable Binder with Medical Records Organizer Pockets"),
        ("B09NQT9VXR", "Erin Condren Hardbound Journal for Personal Records"),
        ("1413330045", "Nolo's Guide: How to Win Your Personal Injury Claim (Book)"),
        ("B01N7IXNDR", "Pendaflex Portable File Box for Legal Documents"),
        ("B00L1JXTSK", "Le Legal Document Organizer Accordion File Folder"),
    ],
    "medicare & senior health insurance": [
        ("B08R14NKBC", "iHealth Track Blood Pressure Monitor"),
        ("B07RFQPNXS", "Weekly Pill Organizer with AM/PM Compartments"),
        ("B09B4QDYXP", "Balance Board for Stability Training (Seniors)"),
        ("B08LGQ6NMR", "AARP Medicare Guide to Your Benefits (Book)"),
        ("B07VD8G5NL", "Copper Compression Knee Support Sleeve"),
    ],
    "residential solar & home energy": [
        ("B09ZJ1WVGK", "Emporia Vue 2 Home Energy Monitor"),
        ("B08B4C9R5J", "Jackery Explorer 300 Portable Power Station"),
        ("B0BVXGN3WK", "Solar Panel Cleaning Brush Kit for Rooftop Panels"),
        ("B098PPB3TN", "Kill A Watt Electricity Usage Monitor"),
        ("B07YTL2HFN", "Renogy 100W Flexible Solar Panel for RV and Roof"),
    ],
}
# Partial-match aliases so any niche string resolves to a key above
NICHE_ALIAS = {
    "personal injury": "personal injury law",
    "injury law":      "personal injury law",
    "medicare":        "medicare & senior health insurance",
    "senior health":   "medicare & senior health insurance",
    "solar":           "residential solar & home energy",
    "home energy":     "residential solar & home energy",
}

# ── AUTHORITATIVE REFERENCES (linked naturally in articles) ──────────────────

NICHE_REFERENCES = {
    "personal injury law": [
        ("the CDC's injury statistics", "https://www.cdc.gov/injury/wisqars/"),
        ("the American Bar Association's guidance", "https://www.americanbar.org/groups/public_education/"),
        ("Nolo's personal injury resources", "https://www.nolo.com/legal-encyclopedia/personal-injury"),
        ("the Insurance Information Institute", "https://www.iii.org/"),
    ],
    "medicare & senior health insurance": [
        ("Medicare.gov", "https://www.medicare.gov/"),
        ("the Centers for Medicare & Medicaid Services", "https://www.cms.gov/"),
        ("the State Health Insurance Assistance Program (SHIP)", "https://www.shiphelp.org/"),
        ("AARP's Medicare resource center", "https://www.aarp.org/health/medicare-insurance/"),
    ],
    "residential solar & home energy": [
        ("the Solar Energy Industries Association (SEIA)", "https://www.seia.org/"),
        ("the National Renewable Energy Laboratory (NREL)", "https://www.nrel.gov/"),
        ("EnergySage's market data", "https://news.energysage.com/"),
        ("the U.S. Department of Energy", "https://www.energy.gov/eere/solar/homeowners-guide-going-solar"),
    ],
}

# ── ARTICLE LENGTH TARGETS (by keyword priority) ─────────────────────────────

LENGTH_TARGETS = {
    "high":   {"min": 1600, "max": 2400, "label": "long-form"},
    "medium": {"min": 1000, "max": 1500, "label": "mid-length"},
    "low":    {"min":  700, "max": 1000, "label": "concise"},
}

# ── BUDGET MONITORING ─────────────────────────────────────────────────────────

def check_api_budget():
    try:
        r = requests.get(
            "https://api.anthropic.com/v1/usage",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
            timeout=10
        )
        if r.status_code == 200:
            spent = r.json().get("total_cost_usd", 0)
            pct   = (spent / ANTHROPIC_MONTHLY_BUDGET) * 100
            print(f"  Budget: ${spent:.2f} / ${ANTHROPIC_MONTHLY_BUDGET:.2f} ({pct:.0f}%)")
            if pct >= 90:
                print("BUDGET ALERT: 90% of monthly budget used -- stopping.")
                sys.exit(1)
            elif pct >= 75:
                print(f"  Budget warning: {pct:.0f}% used.")
    except Exception as e:
        print(f"  Budget check skipped: {e}")

# ── AMAZON AFFILIATE ──────────────────────────────────────────────────────────

def get_products_for_niche(niche: str, count: int = 3) -> list:
    niche_key = niche.lower().strip()
    products = NICHE_ASINS.get(niche_key)
    if not products:
        for alias, key in NICHE_ALIAS.items():
            if alias in niche_key:
                products = NICHE_ASINS.get(key, [])
                break
    if not products:
        return []
    shuffled = products[:]
    random.shuffle(shuffled)
    return shuffled[:count]


def build_affiliate_url(asin: str) -> str:
    tag = AMAZON_TRACKING_ID or "injuryvictim-20"
    return f"https://www.amazon.com/dp/{asin}?tag={tag}"


def inject_affiliate_links(content: str, niche: str) -> str:
    """
    Inject 1 inline recommendation after the 2nd H2, plus a
    compact product box at the end. Max 3 products per article.
    Only runs when AMAZON_TRACKING_ID is set.
    """
    if not AMAZON_TRACKING_ID:
        return content

    products = get_products_for_niche(niche, 3)
    if not products:
        return content

    # Inline pick after 2nd H2
    asin0, name0 = products[0]
    url0 = build_affiliate_url(asin0)
    inline = (
        f"\n> **Helpful resource:** [{name0}]({url0}) is a top-rated option for this. "
        f"*(As an Amazon Associate this site earns from qualifying purchases.)*\n\n"
    )
    h2_count = 0
    lines = content.split("\n")
    new_lines = []
    injected = False
    for line in lines:
        new_lines.append(line)
        if line.startswith("## ") and not injected:
            h2_count += 1
            if h2_count == 2:
                new_lines.append(inline)
                injected = True
    content = "\n".join(new_lines)

    # Product box at end (2-3 items, subtle)
    rec = "\n\n## Helpful Resources\n\n"
    rec += "*As an Amazon Associate this site earns from qualifying purchases.*\n\n"
    for asin, name in products[:3]:
        url = build_affiliate_url(asin)
        rec += f"- **[{name}]({url})**\n"
    content += rec
    return content

# ── REFERENCE LINK INJECTION ──────────────────────────────────────────────────

def get_references_for_niche(niche: str) -> list:
    niche_key = niche.lower().strip()
    refs = NICHE_REFERENCES.get(niche_key)
    if not refs:
        for alias, key in NICHE_ALIAS.items():
            if alias in niche_key:
                refs = NICHE_REFERENCES.get(key, [])
                break
    return refs or []

# ── IMAGE FETCHING ────────────────────────────────────────────────────────────

def fetch_image_pexels(query: str, used_ids: set) -> dict | None:
    if not PEXELS_KEY:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": query, "per_page": 15, "orientation": "landscape"},
            timeout=10
        )
        if r.status_code != 200:
            return None
        if int(r.headers.get("X-Ratelimit-Remaining", 200)) < 5:
            return None
        photos = [p for p in r.json().get("photos", []) if str(p["id"]) not in used_ids]
        if not photos:
            return None
        photo = random.choice(photos[:5])
        used_ids.add(str(photo["id"]))
        return {
            "url":          photo["src"]["large"],
            "credit":       photo["photographer"],
            "credit_link":  photo["photographer_url"],
            "source":       "pexels",
        }
    except Exception as e:
        print(f"  Pexels error: {e}")
        return None


def fetch_image_flux(query: str) -> dict | None:
    if not FLUX_KEY:
        return None
    try:
        r = requests.post(
            "https://fal.run/fal-ai/flux/schnell",
            headers={"Authorization": f"Key {FLUX_KEY}", "Content-Type": "application/json"},
            json={"input": {"prompt": f"Editorial photo: {query}. Natural lighting, no text, no watermarks.", "image_size": "landscape_16_9", "num_inference_steps": 4, "num_images": 1}},
            timeout=30
        )
        if r.status_code == 200:
            return {"url": r.json()["images"][0]["url"], "credit": None, "credit_link": None, "source": "flux"}
    except Exception as e:
        print(f"  Flux error: {e}")
    return None


def fetch_image(query: str, used_ids: set) -> dict | None:
    img = fetch_image_pexels(query, used_ids)
    if img:
        return img
    print("  Pexels miss -- trying Flux...")
    return fetch_image_flux(query)

# ── PUBLISHED KEYWORD TRACKING ────────────────────────────────────────────────

def get_published_slugs(repo: str) -> set:
    """Fetch list of already-published article filenames from GitHub."""
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/contents/content/posts",
            headers=GH_HEADERS,
            timeout=15
        )
        if r.status_code == 200:
            return {f["name"].replace(".md", "") for f in r.json() if isinstance(f, dict)}
    except Exception:
        pass
    return set()


def keyword_to_slug(keyword: str) -> str:
    import re
    slug = keyword.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug[:70]

# ── ARTICLE GENERATION ────────────────────────────────────────────────────────

def generate_article(keyword: str, site_config: dict, persona: dict, priority: str) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    niche         = site_config["niche"]
    affiliate_note = site_config.get("affiliate_note", "")
    site_persona  = SITE_PERSONAS.get(site_config.get("repo", ""), {})
    tone          = site_persona.get("tone", "")
    ymyl          = site_persona.get("ymyl", False)
    references    = get_references_for_niche(niche)
    length_target = LENGTH_TARGETS.get(priority, LENGTH_TARGETS["medium"])

    # Pick 1-2 reference links to weave in naturally
    ref_instruction = ""
    if references:
        picked_refs = random.sample(references, min(2, len(references)))
        ref_links = ", ".join(f'[{name}]({url})' for name, url in picked_refs)
        ref_instruction = (
            f"- Naturally reference 1-2 authoritative sources in the text: {ref_links}. "
            "Do not list them -- weave them into a sentence as supporting evidence."
        )

    ymyl_instruction = ""
    if ymyl:
        ymyl_instruction = (
            "- This is a YMYL (Your Money / Your Life) topic. Be factually accurate, "
            "balanced, and note that professional consultation is always advisable. "
            "Do not make guarantees or quote specific dollar figures as typical outcomes."
        )

    system_prompt = f"""{tone}

You are writing an article for a {niche} website. The author byline will be {persona['name']}.

Length target: {length_target['min']}-{length_target['max']} words ({length_target['label']}).

Writing rules (follow every one):
- Write in a natural, human voice. Vary sentence length -- mix short punchy sentences with longer explanatory ones.
- Never use em dashes (-- or ---). Use commas, colons, or a new sentence instead.
- Avoid these overused AI phrases: "In conclusion", "In summary", "It's worth noting", "Firstly", "Furthermore", "Moreover", "Delve into", "Navigating", "It is important to note", "This article will explore".
- Use contractions naturally (you'll, it's, don't, can't).
- Be specific. Use real numbers, named concepts, and concrete examples instead of vague generalities.
- Occasional first person is fine ("In my experience...", "I've seen clients...") -- it makes the author persona feel real.
- Do not pad with filler. Every paragraph must earn its place.
{ymyl_instruction}
{ref_instruction}
{affiliate_note}

Output ONLY the article body in Markdown. No preamble, no title (title comes from frontmatter)."""

    user_prompt = f"""Write a thorough, reader-first article about: {keyword}

Structure:
1. Opening paragraph -- hook the reader with a specific scenario or surprising fact. No heading.
2. 4-6 H2 sections covering the topic comprehensively
3. At least one practical step-by-step section or comparison table where relevant
4. FAQ section (5 Q&A pairs as H3 subheadings)
5. Brief closing paragraph (no heading, no summary label)

Make it genuinely useful for someone dealing with this exact situation right now."""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )
    content = msg.content[0].text

    # Meta description
    meta = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=80,
        messages=[{"role": "user", "content": f"Write a 140-155 character SEO meta description for an article titled '{keyword}'. Plain text only, no quotes."}]
    )
    description = meta.content[0].text.strip()[:160]

    return {"content": content, "description": description}

# ── MARKDOWN BUILDER ──────────────────────────────────────────────────────────

def build_markdown(
    keyword: str,
    article: dict,
    image: dict | None,
    categories: list,
    tags: list,
    persona: dict,
    ymyl: bool,
    disclaimer: str,
) -> str:
    slug = keyword_to_slug(keyword)
    date = datetime.now(timezone.utc).isoformat()
    image_url = image["url"] if image else ""

    content = article["content"]

    # Append photo credit
    if image and image.get("credit") and image.get("credit_link"):
        content += f"\n\n*Photo: [{image['credit']}]({image['credit_link']}) via Pexels*"

    # Append YMYL disclaimer
    if ymyl and disclaimer:
        content += f"\n\n---\n\n{disclaimer}"

    frontmatter = f"""---
title: "{keyword.title()}"
date: {date}
draft: false
description: "{article['description']}"
image: "{image_url}"
categories: {json.dumps(categories)}
tags: {json.dumps(tags)}
author: "{persona['name']}"
author_bio: "{persona['bio']}"
slug: "{slug}"
affiliate_disclosure: {"true" if AMAZON_TRACKING_ID else "false"}
---

"""
    return frontmatter + content

# ── GITHUB COMMIT ─────────────────────────────────────────────────────────────

def commit_to_github(repo: str, filename: str, content: str, message: str) -> bool:
    url = f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/contents/content/posts/{filename}"
    sha = None
    r = requests.get(url, headers=GH_HEADERS)
    if r.status_code == 200:
        sha = r.json()["sha"]
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=GH_HEADERS, json=payload, timeout=30)
    return r.status_code in [200, 201]

# ── SITEMAP SUBMISSION ────────────────────────────────────────────────────────

def submit_sitemap(domain: str):
    token = os.environ.get("GOOGLE_ACCESS_TOKEN")
    if not token:
        return
    sitemap_url = f"https://{domain}/sitemap.xml"
    url = f"https://www.googleapis.com/webmasters/v3/sites/https%3A%2F%2F{domain}%2F/sitemaps/{requests.utils.quote(sitemap_url, safe='')}"
    r = requests.put(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    print(f"  Sitemap: {'submitted' if r.status_code in [200,204] else 'skipped (' + str(r.status_code) + ')'}")

# ── KEYWORD LOADER ────────────────────────────────────────────────────────────

def load_keywords(repo: str) -> list:
    """Load keywords.csv from GitHub repo, sorted high > medium > low priority."""
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/contents/keywords.csv",
        headers=GH_HEADERS
    )
    if r.status_code != 200:
        return []
    content = base64.b64decode(r.json()["content"]).decode()
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda x: order.get(x.get("priority", "low").lower(), 2))
    return rows

# ── MAIN PUBLISHER ────────────────────────────────────────────────────────────

def publish_site(site_name: str, count: int):
    if site_name not in SITES:
        print(f"Site '{site_name}' not in SITES_CONFIG")
        return

    site = SITES[site_name]
    repo = site["repo"]
    niche = site["niche"]
    print(f"\nPublishing {count} articles to {site_name} ({site['domain']})")

    # Load keywords + already-published slugs
    keywords       = load_keywords(repo)
    published      = get_published_slugs(repo)
    persona_pool   = SITE_PERSONAS.get(repo, {}).get("authors", [{"name": "Editorial Team", "bio": "Content team."}])
    ymyl           = SITE_PERSONAS.get(repo, {}).get("ymyl", False)
    disclaimer     = SITE_PERSONAS.get(repo, {}).get("disclaimer", "")

    if not keywords:
        print(f"  No keywords found")
        return

    # Skip already-published keywords
    unpublished = [
        kw for kw in keywords
        if keyword_to_slug(kw.get("keyword", "")) not in published
    ]

    if not unpublished:
        print(f"  All keywords already published!")
        return

    to_publish   = unpublished[:count]
    used_img_ids = set()

    for i, kw_row in enumerate(to_publish, 1):
        keyword  = kw_row.get("keyword", "")
        category = kw_row.get("category", niche)
        priority = kw_row.get("priority", "medium").lower()
        persona  = random.choice(persona_pool)

        print(f"\n  [{i}/{count}] {keyword} (priority: {priority}, author: {persona['name']})")

        try:
            check_api_budget()

            # Generate article
            article = generate_article(keyword, site, persona, priority)
            print(f"    Article: {len(article['content'])} chars")

            # Inject affiliate links
            article["content"] = inject_affiliate_links(article["content"], niche)

            # Fetch image
            image = fetch_image(site.get("image_query", keyword), used_img_ids)
            print(f"    Image: {'ok' if image else 'none'}")

            # Build markdown
            tags = [w for w in keyword.split() if len(w) > 3][:5]
            markdown = build_markdown(
                keyword, article, image,
                categories=[category],
                tags=tags,
                persona=persona,
                ymyl=ymyl,
                disclaimer=disclaimer,
            )

            # Commit
            filename  = keyword_to_slug(keyword) + ".md"
            committed = commit_to_github(repo, filename, markdown, f"Add: {keyword}")
            print(f"    Commit: {'ok' if committed else 'FAILED'}")

            # Organic delay between articles
            delay = random.randint(45, 90)
            print(f"    Waiting {delay}s...")
            time.sleep(delay)

        except Exception as e:
            print(f"    Error: {e}")
            continue

    submit_sitemap(site["domain"])
    print(f"\nDone: {site_name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site",  required=True, help="Site name or 'all'")
    parser.add_argument("--count", type=int, default=2, help="Articles per site (default: 2)")
    args = parser.parse_args()

    print(f"Content Publisher | {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    check_api_budget()

    if args.site == "all":
        for site_name in SITES:
            publish_site(site_name, args.count)
    else:
        publish_site(args.site, args.count)

    print("\nPublishing complete.")


if __name__ == "__main__":
    main()
