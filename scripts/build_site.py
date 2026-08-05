#!/usr/bin/env python3
"""Build static site from content/blog/*.md for Sales-Accountability."""
import html
import pathlib
import re

BASE = pathlib.Path(__file__).resolve().parent.parent
CONTENT = BASE / "content" / "blog"
OUT = BASE / "blog"
SITE = "https://hylten.github.io/Sales-Accountability"
NAV = """<nav class="nav"><div class="container nav-inner">
<a class="brand" href="/Sales-Accountability/">Sales<span>Accountability</span></a>
<div class="nav-links">
<a href="/Sales-Accountability/">Hem</a>
<a href="/Sales-Accountability/blog/">Blogg</a>
<a class="nav-cta" href="/Sales-Accountability/#kontakt">Boka samtal</a>
</div></div></nav>"""
FOOTER = """<footer><div class="container footer-inner">
<div>&copy; 2026 Sales Accountability</div>
<div>F&ouml;r B2B SaaS-grundare och VD:ar i Sverige</div>
</div></footer>"""

MONTHS = {1: "januari", 2: "februari", 3: "mars", 4: "april", 5: "maj", 6: "juni",
          7: "juli", 8: "augusti", 9: "september", 10: "oktober", 11: "november", 12: "december"}


def parse_frontmatter(raw: str):
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    fm, body = {}, parts[2]
    for line in parts[1].strip().splitlines():
        m = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"')
    return fm, body


def fmt_date(d: str) -> str:
    try:
        y, m, dd = d.split("-")
        return f"{int(dd)} {MONTHS[int(m)]} {y}"
    except Exception:
        return d


def md_to_html(body: str) -> str:
    out, i, lines = [], 0, body.strip().splitlines()
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        esc = lambda t: re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(t))
        if s.startswith("## "):
            out.append(f"<h2>{esc(s[3:])}</h2>")
        elif s.startswith("### "):
            out.append(f"<h3>{esc(s[4:])}</h3>")
        elif s.startswith("- "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(f"<li>{esc(lines[i].strip()[2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif re.match(r"^\d+\.\s", s):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s", lines[i].strip()):
                cleaned = re.sub(r"^\d+\.\s", "", lines[i].strip())
                items.append(f"<li>{esc(cleaned)}</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue
        else:
            out.append(f"<p>{esc(s)}</p>")
        i += 1
    return "\n".join(out)


def page(title, desc, canonical, body_html) -> str:
    return f"""<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="stylesheet" href="/Sales-Accountability/assets/style.css">
<link rel="canonical" href="{canonical}">
</head>
<body>
{NAV}
{body_html}
{FOOTER}
</body>
</html>"""


def main():
    OUT.mkdir(exist_ok=True)
    posts = []
    for md in sorted(CONTENT.glob("*.md")):
        fm, body = parse_frontmatter(md.read_text(encoding="utf-8"))
        slug = fm.get("slug", md.stem)
        title = fm.get("title", slug)
        desc = fm.get("description", "")
        canonical = fm.get("canonical_url", f"{SITE}/blog/{slug}/")
        date = fmt_date(fm.get("date", ""))
        author = fm.get("author", "Jonas Hyltén")
        content = md_to_html(body)
        article_html = f"""<div class="container">
<header class="article-header">
<a class="back-link" href="/Sales-Accountability/blog/">&larr; Tillbaka till bloggen</a>
<div class="date">{date} · {html.escape(author)}</div>
<h1>{html.escape(title)}</h1>
</header>
<article class="article-body">{content}</article>
</div>"""
        (OUT / f"{slug}.html").write_text(
            page(title, desc, canonical, article_html), encoding="utf-8")
        posts.append({"title": title, "desc": desc, "date": date, "slug": slug,
                      "canonical": canonical})

    cards = "\n".join(
        f"""<a class="post-card" href="/Sales-Accountability/blog/{p['slug']}.html">
<div class="date">{p['date']}</div>
<h3>{html.escape(p['title'])}</h3>
<p>{html.escape(p['desc'])}</p>
<div class="more">L&auml;s artikel &rarr;</div></a>""" for p in posts)
    index_body = f"""<div class="container">
<header class="article-header">
<a class="back-link" href="/Sales-Accountability/">&larr; Tillbaka till startsidan</a>
<div class="kicker">Blogg</div>
<h1>Sales Accountability</h1>
</header>
<section class="block" style="border-top:none;padding-top:32px">
<div class="posts">{cards}</div>
</section>
</div>"""
    (OUT / "index.html").write_text(
        page("Blogg | Sales Accountability",
             "Artiklar om sales accountability, prognoser, uppföljning och säljdisciplin för B2B SaaS-grundare.",
             f"{SITE}/blog/", index_body), encoding="utf-8")

    urls = [f"<url><loc>{SITE}/</loc></url>",
            f"<url><loc>{SITE}/blog/</loc></url>"] + \
           [f"<url><loc>{p['canonical']}</loc></url>" for p in posts]
    (BASE / "sitemap.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls)}
</urlset>
""", encoding="utf-8")

    print(f"Genererat {len(posts)} artikelsidor + bloggindex + sitemap")


if __name__ == "__main__":
    main()
