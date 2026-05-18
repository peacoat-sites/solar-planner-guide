#!/usr/bin/env python3
"""
Master publish pipeline for Hugo content sites.
Generates articles via Claude API and commits as Markdown to GitHub.
Cloudflare Pages auto-deploys on each commit.

Usage:
  python publish.py --site seniorhealth --count 5
  python publish.py --site all --count 5  # publish to all configured sites
"""

import os
import sys
import json
import time
import random
import argparse
import requests
import anthropic
from datetime import datetime, timezone
from pathlib import Path

# ── CONFIG ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_ORG = os.environ["GITHUB_ORG"]
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
FLUX_KEY = os.environ.get("FLUX_API_KEY", "")  # optional — fal.ai or replicate key
ANTHROPIC_MONTHLY_BUDGET = float(os.environ.get("ANTHROPIC_MONTHLY_BUDGET", "150"))

# Site registry — Claude Code adds entries here for each new site
SITES = json.loads(os.environ.get("SITES_CONFIG", "{}"))

# ── BUDGET MONITORING ────────────────────────────────────────────────────────

def check_api_budget():
    """Check Anthropic usage and warn if approaching limit."""
    try:
        headers = {
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01"
        }
        # Anthropic usage endpoint
        r = requests.get("https://api.anthropic.com/v1/usage", headers=headers, timeout=10)
        if r.status_code == 200:
            usage = r.json()
            spent = usage.get("total_cost_usd", 0)
            remaining = ANTHROPIC_MONTHLY_BUDGET - spent
            pct = (spent / ANTHROPIC_MONTHLY_BUDGET) * 100

            print(f"💰 API Budget: ${spent:.2f} spent / ${ANTHROPIC_MONTHLY_BUDGET:.2f} ({pct:.0f}%)")

            if pct >= 90:
                print("🚨 BUDGET ALERT: 90% of monthly Anthropic budget used!")
                print("⏸️  ACTION REQUIRED: Please increase budget or pause pipeline.")
                # Write alert file for Claude Code to detect
                with open("/tmp/BUDGET_ALERT.txt", "w") as f:
                    f.write(f"ALERT: {pct:.0f}% of ${ANTHROPIC_MONTHLY_BUDGET} budget used. Spent: ${spent:.2f}")
                sys.exit(1)
            elif pct >= 75:
                print(f"⚠️  Budget warning: {pct:.0f}% used. ${remaining:.2f} remaining.")
    except Exception as e:
        print(f"Could not check budget: {e}")

# ── NAMECHEAP DOMAIN CHECK ────────────────────────────────────────────────────

def check_domain_available(domain: str) -> bool:
    """Check if a domain is available via Namecheap API."""
    api_user = os.environ.get("NAMECHEAP_USERNAME")
    api_key = os.environ.get("NAMECHEAP_API_KEY")
    if not api_user or not api_key:
        return False

    params = {
        "ApiUser": api_user,
        "ApiKey": api_key,
        "UserName": api_user,
        "Command": "namecheap.domains.check",
        "ClientIp": requests.get("https://api.ipify.org").text.strip(),
        "DomainList": domain
    }
    r = requests.get("https://api.namecheap.com/xml.response", params=params, timeout=15)
    return "Available=\"true\"" in r.text


def purchase_domain(domain: str) -> bool:
    """
    Prompt user to approve domain purchase, then buy via Namecheap API.
    This is the ONE step that requires human approval.
    """
    print(f"\n🌐 DOMAIN PURCHASE REQUIRED")
    print(f"   Domain: {domain}")
    print(f"   Cost:   ~$12/year")
    print(f"   Available: {check_domain_available(domain)}")
    print(f"\n   Type 'yes' to purchase, or 'skip' to skip this site: ", end="")

    response = input().strip().lower()
    if response != "yes":
        print(f"   Skipping {domain}")
        return False

    api_user = os.environ["NAMECHEAP_USERNAME"]
    api_key = os.environ["NAMECHEAP_API_KEY"]
    client_ip = requests.get("https://api.ipify.org").text.strip()

    sld, tld = domain.rsplit(".", 1)
    params = {
        "ApiUser": api_user, "ApiKey": api_key, "UserName": api_user,
        "Command": "namecheap.domains.create",
        "ClientIp": client_ip,
        "DomainName": domain, "Years": "1",
        "SLD": sld, "TLD": tld,
    }
    r = requests.post("https://api.namecheap.com/xml.response", data=params, timeout=30)
    success = "\"Registered\" Value=\"true\"" in r.text or "DomainCreateResult" in r.text
    print(f"   {'✅ Purchased' if success else '❌ Failed'}: {domain}")
    return success

# ── IMAGE FETCHING ────────────────────────────────────────────────────────────

def fetch_image_pexels(query: str, used_ids: set) -> dict | None:
    """Fetch image from Pexels — primary source. Instant API key, 200 req/hr."""
    if not PEXELS_KEY:
        return None
    headers = {"Authorization": PEXELS_KEY}
    params = {"query": query, "per_page": 15, "orientation": "landscape"}
    try:
        r = requests.get("https://api.pexels.com/v1/search", headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            print(f"⚠️  Pexels error: {r.status_code}")
            return None
        remaining = int(r.headers.get("X-Ratelimit-Remaining", 200))
        if remaining < 5:
            print(f"⚠️  Pexels rate limit low: {remaining} remaining")
            return None
        photos = r.json().get("photos", [])
        unused = [p for p in photos if str(p["id"]) not in used_ids]
        if not unused:
            return None
        photo = random.choice(unused[:5])
        used_ids.add(str(photo["id"]))
        return {
            "url": photo["src"]["large"],
            "credit": photo["photographer"],
            "credit_link": photo["photographer_url"],
            "source": "pexels"
        }
    except Exception as e:
        print(f"⚠️  Pexels fetch failed: {e}")
        return None


def fetch_image_flux(query: str) -> dict | None:
    """
    Generate unique AI image via Flux Schnell (fal.ai).
    ~$0.003/image. Sign up at fal.ai, set FLUX_API_KEY env var.
    """
    if not FLUX_KEY:
        return None
    try:
        headers = {"Authorization": f"Key {FLUX_KEY}", "Content-Type": "application/json"}
        prompt = (
            f"Editorial stock photo style: {query}. "
            "Natural lighting, professional photography, "
            "high resolution, clean composition, no text, no watermarks."
        )
        payload = {
            "input": {
                "prompt": prompt,
                "image_size": "landscape_16_9",
                "num_inference_steps": 4,
                "num_images": 1
            }
        }
        r = requests.post(
            "https://fal.run/fal-ai/flux/schnell",
            headers=headers, json=payload, timeout=30
        )
        if r.status_code == 200:
            url = r.json()["images"][0]["url"]
            return {"url": url, "credit": None, "credit_link": None, "source": "flux"}
    except Exception as e:
        print(f"⚠️  Flux generation failed: {e}")
    return None


def fetch_image(query: str, used_ids: set) -> dict | None:
    """
    Image strategy:
    1. Pexels (primary — instant key, 200 req/hr, no approval needed)
    2. Flux Schnell AI (fallback — unique AI images, ~$0.003 each)
    """
    img = fetch_image_pexels(query, used_ids)
    if img:
        return img

    print(f"   ↩ Pexels miss — generating with Flux AI...")
    img = fetch_image_flux(query)
    if img:
        print(f"   🎨 AI image generated via Flux")
        return img

    print(f"   ⚠️  No image available — article will publish without one")
    return None

# ── ARTICLE GENERATION ────────────────────────────────────────────────────────

def generate_article(keyword: str, site_config: dict) -> dict:
    """Generate a full SEO article via Claude API."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    niche = site_config["niche"]
    affiliate_note = site_config.get("affiliate_note", "")

    system_prompt = f"""You are an expert content writer for a {niche} website.
Write detailed, helpful, SEO-optimized articles that genuinely help readers.
Follow these rules:
- Minimum 1,200 words
- Use proper H2 and H3 headings
- Include a FAQ section at the end with 5 questions and answers
- Include internal linking placeholders like [RELATED: topic]
- Write in a warm, authoritative but approachable tone
- Never use filler phrases like "In conclusion" or "In summary"
- Include practical actionable advice
- Add a comparison table when relevant
{affiliate_note}
Output ONLY the article content in Markdown. No preamble."""

    user_prompt = f"""Write a comprehensive article about: {keyword}

Include:
1. Engaging introduction (no heading)
2. 4-6 H2 sections with detailed content
3. Practical tips or step-by-step guide
4. A comparison table if relevant
5. FAQ section with 5 common questions
6. Brief closing paragraph

Make it genuinely useful and thorough."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt
    )

    content = message.content[0].text

    # Generate meta description
    meta_msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=100,
        messages=[{"role": "user", "content": f"Write a 150-character SEO meta description for an article about: {keyword}. Output only the description, no quotes."}]
    )
    description = meta_msg.content[0].text.strip()[:160]

    return {"content": content, "description": description}

# ── MARKDOWN BUILDER ──────────────────────────────────────────────────────────

def build_markdown(keyword: str, article: dict, image: dict | None, categories: list, tags: list) -> str:
    """Build Hugo-compatible Markdown with frontmatter."""
    slug = keyword.lower().replace(" ", "-").replace("'", "").replace(",", "")
    date = datetime.now(timezone.utc).isoformat()
    image_url = image["url"] if image else ""

    # Inject image credit into content if image has attribution
    content = article["content"]
    if image and image.get("credit") and image.get("credit_link"):
        source_name = "Pexels"
        credit_line = f"\n\n*Photo by [{image['credit']}]({image['credit_link']}) on {source_name}*\n\n"
        content = content + credit_line

    frontmatter = f"""---
title: "{keyword.title()}"
date: {date}
draft: false
description: "{article['description']}"
image: "{image_url}"
categories: {json.dumps(categories)}
tags: {json.dumps(tags)}
author: "Editorial Team"
slug: "{slug}"
---

"""
    return frontmatter + content

# ── GITHUB COMMIT ─────────────────────────────────────────────────────────────

def commit_to_github(repo: str, filename: str, content: str, message: str) -> bool:
    """Commit a Markdown file to a GitHub repo."""
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    url = f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/contents/content/posts/{filename}"

    # Check if file exists (for updates)
    sha = None
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        sha = r.json()["sha"]

    import base64
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(url, headers=headers, json=payload, timeout=30)
    return r.status_code in [200, 201]

# ── SITEMAP SUBMISSION ────────────────────────────────────────────────────────

def submit_sitemap(domain: str):
    """Submit sitemap to Google Search Console via API."""
    # Requires GOOGLE_ACCESS_TOKEN env var (OAuth token)
    token = os.environ.get("GOOGLE_ACCESS_TOKEN")
    if not token:
        print(f"   ⚠️  No Google token — skipping sitemap submission for {domain}")
        return

    sitemap_url = f"https://{domain}/sitemap.xml"
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://www.googleapis.com/webmasters/v3/sites/https%3A%2F%2F{domain}%2F/sitemaps/{requests.utils.quote(sitemap_url, safe='')}"

    r = requests.put(url, headers=headers, timeout=15)
    if r.status_code in [200, 204]:
        print(f"   ✅ Sitemap submitted: {sitemap_url}")
    else:
        print(f"   ⚠️  Sitemap submission failed: {r.status_code}")

# ── KEYWORD LOADER ────────────────────────────────────────────────────────────

def load_keywords(site_name: str) -> list:
    """Load keyword list from site's keywords.csv in the repo."""
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    url = f"https://api.github.com/repos/{GITHUB_ORG}/{site_name}/contents/keywords.csv"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return []

    import base64, csv, io
    content = base64.b64decode(r.json()["content"]).decode()
    reader = csv.DictReader(io.StringIO(content))
    return [row for row in reader]

# ── MAIN ──────────────────────────────────────────────────────────────────────

def publish_site(site_name: str, count: int):
    """Publish N articles to a single site."""
    if site_name not in SITES:
        print(f"❌ Site '{site_name}' not found in SITES_CONFIG")
        return

    site = SITES[site_name]
    repo = site["repo"]
    print(f"\n📝 Publishing {count} articles to {site_name} ({site['domain']})")

    keywords = load_keywords(repo)
    if not keywords:
        print(f"   ⚠️  No keywords found for {site_name}")
        return

    # Pick least-published keywords
    random.shuffle(keywords)
    to_publish = keywords[:count]
    used_image_ids = set()

    for i, kw_row in enumerate(to_publish, 1):
        keyword = kw_row.get("keyword", kw_row.get("Keyword", ""))
        category = kw_row.get("category", kw_row.get("Category", site["niche"]))

        print(f"\n   [{i}/{count}] {keyword}")

        try:
            # Check budget before each article
            check_api_budget()

            # Generate article
            article = generate_article(keyword, site)
            print(f"   ✅ Article generated ({len(article['content'])} chars)")

            # Fetch image
            image = fetch_image(site.get("image_query", keyword), used_image_ids)
            if image:
                print(f"   ✅ Image fetched")
            else:
                print(f"   ⚠️  No image available")

            # Build markdown
            slug = keyword.lower().replace(" ", "-")[:60]
            categories = [category]
            tags = keyword.split()[:5]
            markdown = build_markdown(keyword, article, image, categories, tags)

            # Commit to GitHub
            filename = f"{slug}.md"
            committed = commit_to_github(
                repo, filename, markdown,
                f"Add article: {keyword}"
            )
            print(f"   {'✅ Committed' if committed else '❌ Commit failed'}")

            # Organic pacing: randomized delay between articles (45–90 sec)
            # This avoids API rate limits and makes publish cadence look natural
            delay = random.uniform(45, 90)
            print(f"   ⏳ Waiting {delay:.0f}s before next article...")
            time.sleep(delay)

        except Exception as e:
            print(f"   ❌ Error: {e}")
            continue

    # Submit sitemap after publishing
    submit_sitemap(site["domain"])
    print(f"\n✅ Done: {site_name}")


def main():
    parser = argparse.ArgumentParser(description="Hugo content publisher")
    parser.add_argument("--site", required=True, help="Site name or 'all'")
    parser.add_argument("--count", type=int, default=5, help="Articles per site")
    args = parser.parse_args()

    print("🚀 Hugo Content Publisher")
    print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    check_api_budget()

    if args.site == "all":
        for site_name in SITES:
            publish_site(site_name, args.count)
    else:
        publish_site(args.site, args.count)

    print("\n🎉 Publishing complete!")


if __name__ == "__main__":
    main()
