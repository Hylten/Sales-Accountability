#!/usr/bin/env python3
"""
MR WRITER EDITORIAL QA — deterministisk audit-grind före build/publish.

Regler (R1-R10) enligt directives/mr-writer-editorial-qa.md.
Körs per repo. Exit: 0 = PASS (inga BLOCK), 1 = BLOCKED, 2 = felaktig användning.

Användning:
  python3 mr_writer_audit.py <repo_path> [--fix] [--report out.md] [--file <rel_path>]
"""
import argparse
import datetime
import json
import pathlib
import re
import sys

# --------------------------------------------------------------------------
# GEMENSAMMA FÖRBJUDNA MÖNSTER (BLOG_VOICE_MASTER + MASTER_DIRECTIVE + veto)
# --------------------------------------------------------------------------
LEAK_PATTERNS = [
    r"system_prompt", r"system prompt", r"internal_logic", r"vault_briefing",
    r"INTERNAL TONE", r"PRINCIPAL VOICE", r"THE 25% LINK RULE",
    r"Expanded analytical section", r"INSTRUCTION:", r"instruction:",
    r"Three truths", r"THREE TRUTHS", r"Four truths", r"FOUR TRUTHS",
    r"Five truths", r"FIVE TRUTHS", r"PHASE 1:", r"PHASE 2:", r"PHASE 3:",
    r"You are an AI", r"Du är en AI", r"as an AI assistant", r"som AI-assistent",
    r"<!--", r"-->", r"TODO:", r"FIXME:", r"läs detta först", r"follow these instructions",
    r"context:", r"systemmeddelande", r"prompten säger",
]
BLEED_PATTERNS = [
    r"Step I:", r"Step II:", r"Step III:", r"Step 1:", r"Step 2:", r"Step 3:",
]
VETO_PHRASES = [
    "viktigt att komma ihåg", "det beror på", "resan mot", "låsa upp potentialen",
    "ingen universallösning", "sammanfattningsvis", "tydlig slutsats", "key takeaways",
    "in conclusion", "in today's world", "unlock potential", "it's worth noting",
    "drives results", "cutting-edge", "game-changer", "seamless", "robust",
    "we offer", "we provide", "we specialize", "mandate discipline", "threshold integrity",
    "closing for principals",
]
HEDGE_SV = ["kanske", "troligen", "tenderar", "skulle kunna", "möjligen", "ofta"]
HEDGE_EN = ["sometimes", "often", "perhaps", "might", "usually", "tender"]
BRAND_BANS = {
    # per repo-namn (mappnamn under "AI Agents =)/")
    "Roials-Capital": [
        (r"proverbs \d+:\d+", "bibelvers (förbjuden på Roials)"),
        (r"psalms \d+:\d+", "bibelvers (förbjuden på Roials)"),
        (r"luke \d+:\d+", "bibelvers (förbjuden på Roials)"),
        (r"\bNAEO\w*\b", "NAEO-referens (förbjuden)"),
        (r"\bVCP\b", "VCP-referens (förbjuden)"),
        (r"the mandat e", "trunkeringsfel 'The Mandat E'"),
        (r"to which we serve as a strategic partner", "följefras (förbjuden)"),
    ],
    "OpenClaw-Sverige": [
        (r"\brecapitalization\b", "recapitalization (förbjudet ämne för OpenClaw)"),
        (r"\brefinansiering", "refinansiering (förbjudet ämne för OpenClaw)"),
        (r"\bprivate equity\b", "private equity (förbjudet ämne för OpenClaw)"),
        (r"\bcapital markets\b", "capital markets (förbjudet ämne för OpenClaw)"),
        (r"\bM&A\b", "M&A (förbjudet ämne för OpenClaw)"),
    ],
}
CTA_PATTERNS = [
    r"boka ett samtal", r"boka en demo", r"book a call", r"schedule a",
    r"kontakta oss", r"kontakta mig", r"reach out", r"hör av dig",
    r"zcal", r"calendly", r"boka 5-minuters", r"access is restricted",
    r"approved mandates", r"book a meeting", r"boka ett möte",
]
CURRENCY = [r"\bUSD\b", r"\bEUR\b", r"\bGBP\b", r"€", r"£", r"\$\d"]

REQUIRED_FM = ["title", "slug", "description", "date", "tags", "categories",
               "canonical_url", "meta_title", "meta_description", "featured_image",
               "draft", "author"]

SV_STOP = ["och", "att", "det", "som", "för", "inte", "är", "en", "på", "med",
           "har", "den", "de", "till", "av", "ska", "kan", "men", "var", "när"]
EN_STOP = ["the", "and", "that", "this", "with", "for", "are", "have", "from",
           "not", "will", "can", "but", "was", "when", "its", "their", "into"]


def detect_language(body: str) -> str:
    words = re.findall(r"[a-zåäöA-ZÅÄÖ]+", body)
    if not words:
        return "sv"
    low = [w.lower() for w in words]
    sv = sum(1 for w in low if w in SV_STOP)
    en = sum(1 for w in low if w in EN_STOP)
    return "sv" if sv >= en else "en"


def parse_frontmatter(text: str):
    """Returnera (frontmatter-dict, body). Tolerant YAML (line-parser + yaml om möjligt)."""
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    fm_raw, body = parts[1], parts[2]
    fm = {}
    try:
        import yaml
        fm = yaml.safe_load(fm_raw) or {}
    except Exception:
        for line in fm_raw.splitlines():
            m = re.match(r"^([a-z_]+):\s*(.*)$", line)
            if m:
                fm[m.group(1)] = m.group(2).strip().strip('"')
    if not isinstance(fm, dict):
        fm = {}
    return fm, body


class Audit:
    def __init__(self, repo: pathlib.Path, do_fix: bool = False):
        self.repo = repo
        self.do_fix = do_fix
        self.repo_name = repo.name
        self.results = []  # (file, severity, rule, line, msg, fix)
        self.fixed_files = 0

    def fail(self, f, rule, line, msg, fix=None):
        self.results.append((str(f), "BLOCK", rule, line, msg, fix))

    def warn(self, f, rule, line, msg):
        self.results.append((str(f), "WARN", rule, line, msg, None))

    # -- fix-hjälpare -------------------------------------------------------
    def fix_text(self, f, old, new, rule, line, msg):
        if not self.do_fix:
            return False
        p = pathlib.Path(f)
        t = p.read_text(encoding="utf-8")
        if old in t:
            p.write_text(t.replace(old, new), encoding="utf-8")
            self.fixed_files += 1
            return True
        return False

    # -- R2: struktur -------------------------------------------------------
    def check_structure(self, f, body):
        lines = body.splitlines()
        for i, ln in enumerate(lines, 1):
            if re.search(r"\bI: s\b|\bI:s\b", ln):
                self.fail(f, "R2", i, "I:s-syntax (ska vara 'is')", "ersätt 'I: s'/'I:s' med 'is'")
            if re.search(r"\bsectio n\b", ln):
                self.fail(f, "R2", i, "trasigt ord 'sectio n'", "ersätt med 'section'")
            if re.search(r"^#{1,6}[^ #]", ln):
                self.fail(f, "R2", i, "rubrik utan mellanslag efter #", "lägg mellanslag: '# Rubrik'")
        # brutna romerska siffror / dubblerade
        for m in re.finditer(r"\b(I|II|III|IV|V)\b\.\s+\1\b", body):
            self.fail(f, "R2", 0, f"dubblerad rubriknummerering: '{m.group(0)}'", "rätta sekvensen")
        # lösa parenteser
        for o, c, name in [("(", ")", "parentes"), ("[", "]", "hakparentes")]:
            if body.count(o) != body.count(c):
                self.fail(f, "R2", 0, f"obalanserade {name}: {body.count(o)} vs {body.count(c)}",
                          "balansera parenteserna")
        # dubbla H1
        h1 = re.findall(r"^#\s+.+", body, re.M)
        if len(h1) > 1:
            self.fail(f, "R2", 0, f"{len(h1)} H1-rubriker (max 1)", "demota resten till H2")
        # dubblerade rubriker (exakt samma rubriktext i rad, utan innehåll emellan)
        prev_h = None
        for ln in lines:
            m = re.match(r"^(#{1,6})\s+(.+)$", ln)
            if m:
                if m.group(0).strip() == prev_h:
                    self.fail(f, "R2", 0, f"dubblerad rubrik: '{m.group(0).strip()}'", "ta bort en av dem")
                prev_h = m.group(0).strip()
            elif ln.strip():
                prev_h = None
        # blankrader mellan numrerade listobjekt
        for m in re.finditer(r"^\d+\.\s+.*\n\s*\n\d+\.\s+", body, re.M):
            self.fail(f, "R2", 0, "blankrad mellan numrerade listobjekt: ta bort blankraden",
                      "LISTBLANK")
        # stjärnlistor (utom Alpha-Architect)
        if self.repo_name != "Alpha-Architect":
            for m in re.finditer(r"^\*\s+", body, re.M):
                self.fail(f, "R2", 0, "stjärnlista (ska vara '- ')", "ersätt '* ' med '- '")
        # kodblock stängda?
        if body.count("```") % 2 != 0:
            self.fail(f, "R2", 0, "ostängt kodblock (```)", "stäng kodblocket")
        # ** balans
        if body.count("**") % 2 != 0:
            self.fail(f, "R2", 0, "obalanserade ** (fetstil)", "balansera **")

    # -- R3: em/en-dash -----------------------------------------------------
    def check_dashes(self, f, body):
        for i, ln in enumerate(body.splitlines(), 1):
            if "—" in ln:
                self.fail(f, "R3", i, "em-dash (—): ersätt med colon eller komma", "EMDASH")
            elif "–" in ln:
                self.fail(f, "R3", i, "en-dash (–): ersätt med bindestreck eller komma", "ENDASH")

    # -- R4: frontmatter ----------------------------------------------------
    def check_frontmatter(self, f, fm, body, repo_files_seen):
        if fm is None:
            self.fail(f, "R4", 0, "saknar frontmatter (---)", "lägg till giltig frontmatter")
            return
        missing = [k for k in REQUIRED_FM if k not in fm]
        if missing:
            self.fail(f, "R4", 0, f"saknade frontmatter-nycklar: {', '.join(missing)}",
                      "lägg till nycklarna (meta_title/meta_description kan kopiera title/description)")
        title = str(fm.get("title", "")).strip()
        desc = str(fm.get("description", "")).strip()
        slug = str(fm.get("slug", "")).strip()
        if not title:
            self.fail(f, "R4", 0, "title saknas", "sätt title")
        if not desc:
            self.fail(f, "R4", 0, "description saknas", "skriv en unik description")
        elif title and desc.lower().strip() == title.lower().strip():
            self.fail(f, "R4", 0, "description är identisk med title", "skriv en unik description")
        elif desc in repo_files_seen:
            self.fail(f, "R4", 0, "description duplicerad i repot", "gör description unik")
        else:
            repo_files_seen.add(desc)
        stem = pathlib.Path(f).stem
        if slug:
            if self.repo_name == "Alpha-Architect":
                ok_slug = stem.endswith(slug)  # datum-prefix YYYY-MM-DD-<slug>
            else:
                ok_slug = slug == stem
            if not ok_slug:
                self.fail(f, "R4", 0, f"slug '{slug}' matchar inte filnamn '{stem}'",
                          "synka slug och filnamn")
        date_s = str(fm.get("date", "")).strip().strip('"')
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_s):
            self.fail(f, "R4", 0, f"datum ej ISO: '{date_s}'", "använd YYYY-MM-DD")
        else:
            try:
                d = datetime.date.fromisoformat(date_s)
                if d > datetime.date.today():
                    self.fail(f, "R4", 0, f"datum {date_s} ligger efter kördatum", "sätt datum till idag eller tidigare")
            except ValueError:
                self.fail(f, "R4", 0, f"datum ogiltigt: '{date_s}'", "använd YYYY-MM-DD")
        if str(fm.get("draft", "")).strip().lower() not in ("true", "false"):
            self.fail(f, "R4", 0, "draft måste vara true/false", "sätt draft: true eller false")

    # -- R5: ordantal -------------------------------------------------------
    def check_words(self, f, body):
        words = len(re.findall(r"\S+", body))
        if words < 800:
            self.fail(f, "R5", 0, f"{words} ord (<800)", "utöka till minst 1500 ord (800 lägsta)")
        elif words < 1500:
            self.warn(f, "R5", 0, f"{words} ord (800-1499, ok vid kvalitetsförbättring)")

    # -- R6: språk/brand ----------------------------------------------------
    def check_voice(self, f, body, lang):
        low = body.lower()
        for phrase in VETO_PHRASES:
            if phrase in low:
                self.fail(f, "R6", 0, f"förbjuden fras: '{phrase}'", "skriv om")
        for m in re.finditer(r"(?m)^Genom att\b", body):
            self.fail(f, "R6", m.start(), "stycke börjar med 'Genom att'", "skriv om meningen")
        for h in (HEDGE_SV if lang == "sv" else HEDGE_EN):
            for m in re.finditer(r"\b" + re.escape(h) + r"\b", low):
                self.fail(f, "R6", m.start(), f"hedging-ord: '{h}'", "skriv absolut")
                break
        for ban in BRAND_BANS.get(self.repo_name, []):
            pat, desc = ban
            for m in re.finditer(pat, low):
                self.fail(f, "R6", 0, f"brand-veto: {desc}", "skriv om/radera")
                break

    # -- R7: SEK ------------------------------------------------------------
    def check_sek(self, f, body):
        has_currency = any(re.search(p, body) for p in CURRENCY)
        if has_currency and "SEK" not in body:
            self.fail(f, "R7", 0, "valuta utan SEK-konvertering (USD/EUR/GBP/$/€/£)",
                      "konvertera till SEK eller lägg explicit konvertering")

    # -- R8/R9: CTA ---------------------------------------------------------
    def check_cta(self, f, body, lines):
        cta_hits = []
        for pat in CTA_PATTERNS:
            for m in re.finditer(pat, body, re.I):
                line_no = body[: m.start()].count("\n") + 1
                cta_hits.append((line_no, m.group(0)))
        if len(cta_hits) > 1:
            self.fail(f, "R8", cta_hits[0][0], f"{len(cta_hits)} CTA/kvalificeringsgränser (max 1)",
                      "behåll en CTA")
        last_heading_i = None
        for i, ln in enumerate(lines):
            if re.match(r"^##\s+", ln):
                last_heading_i = i
        if last_heading_i is not None:
            after = lines[last_heading_i + 1:]
            for (ln, _txt) in cta_hits:
                if ln > last_heading_i + 1:
                    self.fail(f, "R9", ln, "CTA efter sista rubriken", "flytta CTA före sista rubriken")
            for j, aln in enumerate(after, last_heading_i + 2):
                if re.match(r"^#{1,6}\s+", aln):
                    self.fail(f, "R9", j, "rubrik efter sista rubriken", "ta bort eller flytta före")
                if re.search(r"<!--|-->|TODO:|FIXME:|arbetskommentar|work in progress", aln):
                    self.fail(f, "R9", j, "arbetskommentar efter sista rubriken", "radera")

    # -- R10: sista rubrik --------------------------------------------------
    def check_last_heading(self, f, body, lang):
        headings = re.findall(r"^##\s+.+$", body, re.M)
        if not headings:
            self.fail(f, "R10", 0, "ingen ##-rubrik finns", "avsluta med ## Summary/## Sammanfattning")
            return
        last = headings[-1].strip()
        want = "## Sammanfattning" if lang == "sv" else "## Summary"
        if last != want:
            self.fail(f, "R10", 0, f"sista rubrik '{last}' (ska vara '{want}' för {lang})",
                      f"byt till {want}")

    # -- main ---------------------------------------------------------------
    def run(self):
        content = self.repo / "content"
        if not content.exists():
            return 2, "saknar content/", 0
        md_files = [p for p in content.rglob("*.md")
                    if "_duplicates" not in str(p) and p.name.lower() not in ("readme.md", "license.md")]
        seen_descs = set()
        for f in sorted(md_files):
            text = f.read_text(encoding="utf-8", errors="replace")
            fm, body = parse_frontmatter(text)
            # R1: läckage (kör på hela texten inkl. frontmatter)
            for i, ln in enumerate(text.splitlines(), 1):
                for pat in LEAK_PATTERNS:
                    if re.search(pat, ln, re.I):
                        self.fail(f, "R1", i, f"promptläckage/meta: mönster '{pat}'", "radera raden")
                for pat in BLEED_PATTERNS:
                    if re.search(pat, ln):
                        self.fail(f, "R1", i, f"genereringsartefakt: '{pat}'", "skriv om narrativt")
            if fm is not None:
                lang = detect_language(body)
                self.check_frontmatter(f, fm, body, seen_descs)
                self.check_words(f, body)
                self.check_structure(f, body)
                self.check_dashes(f, body)
                self.check_voice(f, body, lang)
                self.check_sek(f, body)
                lines = body.splitlines()
                self.check_cta(f, body, lines)
                self.check_last_heading(f, body, lang)
        # --fix-applikation (efter analys)
        if self.do_fix:
            self.apply_fixes()
        blocks = sum(1 for r in self.results if r[1] == "BLOCK")
        warns = sum(1 for r in self.results if r[1] == "WARN")
        return 0 if blocks == 0 else 1, f"{len(md_files)} filer, {blocks} BLOCK, {warns} WARN", len(md_files)

    def apply_fixes(self):
        """Säkra mekaniska fixar: R3 (em/en-dash), R2 (I:s, #Header, listblankrader), R10 (rubriknamn)."""
        targets = {}
        for r in self.results:
            if r[5] in ("EMDASH", "ENDASH", "LISTBLANK"):
                targets.setdefault(r[0], set()).add(r[5])
            if r[2] == "R10":
                targets.setdefault(r[0], set()).add("R10")
        for f, codes in targets.items():
            p = pathlib.Path(f)
            t = p.read_text(encoding="utf-8")
            orig = t
            for code in codes:
                if code == "EMDASH":
                    t = t.replace("—", ", ")
                elif code == "ENDASH":
                    t = t.replace("–", "-")
                elif code == "LISTBLANK":
                    t = re.sub(r"^(\d+\.\s+.*)\n\s*\n(\d+\.\s+)", r"\1\n\2", t, flags=re.M)
            t = re.sub(r"\bI: s\b", "is", t)
            t = re.sub(r"\bI:s\b", "is", t)
            t = re.sub(r"^(#{1,6})([^ #])", r"\1 \2", t, flags=re.M)
            if "R10" in codes:
                fm, body = parse_frontmatter(t)
                if fm is not None:
                    lang = detect_language(body)
                    want = "## Sammanfattning" if lang == "sv" else "## Summary"
                    known = ["## Summary", "## Sammanfattning", "## Slutsats", "## Slutord",
                             "## Conclusion", "## Conclusions", "## Key Takeaways", "## Konklusion",
                             "## Sammanfattning ", "## Summary "]
                    lines = t.splitlines()
                    for i in range(len(lines) - 1, -1, -1):
                        if re.match(r"^##\s+", lines[i]):
                            h = lines[i].strip()
                            if h in known and h != want:
                                lines[i] = want
                            break
                    t = "\n".join(lines)
            if t != orig:
                p.write_text(t, encoding="utf-8")
                self.fixed_files += 1
        for i, r in enumerate(self.results):
            if r[5] in ("EMDASH", "ENDASH", "LISTBLANK"):
                self.results[i] = (r[0], "FIXED", r[2], r[3], r[4] + " (fixad)", None)
            elif r[2] == "R10":
                self.results[i] = (r[0], "FIXED", r[2], r[3], r[4] + " (fixad)", None)

    def report(self, out: pathlib.Path):
        by_rule = {}
        for r in self.results:
            by_rule.setdefault(r[2], []).append(r)
        lines = [f"# QA-rapport: {self.repo_name}", "",
                 f"Kördatum: {datetime.date.today().isoformat()}",
                 f"Totalt: {len(self.results)} träffar "
                 f"({sum(1 for r in self.results if r[1]=='BLOCK')} BLOCK, "
                 f"{sum(1 for r in self.results if r[1]=='WARN')} WARN, "
                 f"{sum(1 for r in self.results if r[1]=='FIXED')} FIXED)", ""]
        for rule in sorted(by_rule):
            hits = by_rule[rule]
            lines.append(f"## {rule}: {len(hits)}")
            for f, sev, rl, ln, msg, _fix in hits[:40]:
                lines.append(f"- [{sev}] {pathlib.Path(f).name}:{ln} {msg}")
            if len(hits) > 40:
                lines.append(f"- …och {len(hits)-40} till")
            lines.append("")
        out.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--report")
    ap.add_argument("--file")
    args = ap.parse_args()
    repo = pathlib.Path(args.repo).expanduser()
    if not repo.exists():
        print("Repo finns inte:", repo)
        sys.exit(2)
    a = Audit(repo, do_fix=args.fix)
    if args.file:
        f = repo / args.file
        if not f.exists():
            print("Fil finns inte:", f)
            sys.exit(2)
        text = f.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(text)
        for i, ln in enumerate(text.splitlines(), 1):
            for pat in LEAK_PATTERNS:
                if re.search(pat, ln, re.I):
                    a.fail(f, "R1", i, f"promptläckage: '{pat}'", "radera")
        if fm is None:
            a.fail(f, "R4", 0, "saknar frontmatter", "lägg till")
        else:
            lang = detect_language(body)
            a.check_frontmatter(f, fm, body, set())
            a.check_words(f, body)
            a.check_structure(f, body)
            a.check_dashes(f, body)
            a.check_voice(f, body, lang)
            a.check_sek(f, body)
            a.check_cta(f, body, body.splitlines())
            a.check_last_heading(f, body, lang)
        if args.fix:
            a.apply_fixes()
        for r in a.results:
            print(f"[{r[1]}] {r[2]} {pathlib.Path(r[0]).name}:{r[3]} {r[4]}")
        if args.report:
            a.report(pathlib.Path(args.report))
        sys.exit(0 if all(r[1] != "BLOCK" for r in a.results) else 1)
    code, summary, n = a.run()
    print(f"{repo.name}: {summary}")
    if args.report:
        a.report(pathlib.Path(args.report))
        print(f"Rapport: {args.report}")
    if a.do_fix:
        print(f"Fixade {a.fixed_files} filer mekaniskt.")
    sys.exit(code)


if __name__ == "__main__":
    main()
