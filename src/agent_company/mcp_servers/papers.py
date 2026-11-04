"""Agent Company's research-papers MCP server: arXiv, OpenAlex and Semantic Scholar, read-only, no key needed.

Tools: search_papers, get_paper, citing_papers. Optional environment: S2_API_KEY (a Semantic Scholar key raises its rate limit),
OPENALEX_EMAIL (OpenAlex's "polite pool"). Run by mcp_hub.py (Agents → Tools & MCP → catalog → Research papers).
"""
import json, re, sys, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stdio import ToolError, serve, tool  # noqa: E402

import os  # noqa: E402
UA = "Agent Company-research-papers/1.0 (MCP server)"
TIMEOUT = 25
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

def get(url, headers=None, raw=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read(4_000_000)
                return body if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2: time.sleep(2 + 3 * attempt); continue
            if e.code == 404: raise ToolError("Not found.")
            raise ToolError(f"{urllib.parse.urlsplit(url).netloc} answered HTTP {e.code}.")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ToolError(f"Couldn't reach {urllib.parse.urlsplit(url).netloc}: {getattr(e, 'reason', e)}")

def clip(s, n): s = re.sub(r"\s+", " ", s or "").strip(); return s if len(s) <= n else s[:n - 1].rstrip() + "…"

def arxiv_id(s):
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?|([a-z\-]+(?:\.[A-Z]{2})?/\d{7})", s or "")
    return (m.group(1) or m.group(3)) if m else None

# ---------- sources
def _arxiv(params):
    root = ET.fromstring(get("http://export.arxiv.org/api/query?" + urllib.parse.urlencode(params), raw=True))
    out = []
    for e in root.findall("a:entry", ATOM):
        aid = arxiv_id(e.findtext("a:id", "", ATOM))
        if not aid: continue
        pdf = next((l.get("href") for l in e.findall("a:link", ATOM) if l.get("title") == "pdf"), f"https://arxiv.org/pdf/{aid}")
        out.append({"id": f"arXiv:{aid}", "title": clip(e.findtext("a:title", "", ATOM), 300), "year": int((e.findtext("a:published", "", ATOM) or "0")[:4] or 0) or None,
                    "authors": [a.findtext("a:name", "", ATOM) for a in e.findall("a:author", ATOM)], "venue": "arXiv " + ((e.find("arxiv:primary_category", ATOM).get("term") if e.find("arxiv:primary_category", ATOM) is not None else "")),
                    "abstract": e.findtext("a:summary", "", ATOM), "url": f"https://arxiv.org/abs/{aid}", "pdf": pdf,
                    "doi": e.findtext("arxiv:doi", None, ATOM), "citations": None, "source": "arXiv"})
    return out

def _abstract(inv):
    if not inv: return ""
    words = sorted((p, w) for w, ps in inv.items() for p in ps)
    return " ".join(w for _, w in words)

def _oa_row(w):
    ids = w.get("ids") or {}
    doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
    aid = arxiv_id((ids.get("arxiv") or "")) or next((arxiv_id(l.get("landing_page_url") or "") for l in w.get("locations") or [] if "arxiv" in (l.get("landing_page_url") or "")), None)
    loc = w.get("primary_location") or {}
    return {"id": f"doi:{doi}" if doi else w["id"].rsplit("/", 1)[-1], "openalex": w["id"].rsplit("/", 1)[-1], "title": clip(w.get("title") or w.get("display_name"), 300),
            "year": w.get("publication_year"), "authors": [a["author"]["display_name"] for a in w.get("authorships") or [] if a.get("author")],
            "venue": ((loc.get("source") or {}).get("display_name")) or "", "abstract": _abstract(w.get("abstract_inverted_index")),
            "url": loc.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else w["id"]), "pdf": (w.get("best_oa_location") or {}).get("pdf_url"),
            "doi": doi, "arxiv": aid, "citations": w.get("cited_by_count"), "source": "OpenAlex"}

def _openalex(path, params):
    if os.getenv("OPENALEX_EMAIL"): params = {**params, "mailto": os.environ["OPENALEX_EMAIL"]}
    return get(f"https://api.openalex.org/{path}?" + urllib.parse.urlencode(params))

def _s2(path, params):
    h = {"x-api-key": os.environ["S2_API_KEY"]} if os.getenv("S2_API_KEY") else {}
    return get(f"https://api.semanticscholar.org/graph/v1/{path}?" + urllib.parse.urlencode(params), h)

S2_FIELDS = "title,authors,year,venue,citationCount,abstract,externalIds,url,openAccessPdf,tldr"
def _s2_row(p):
    x = p.get("externalIds") or {}
    return {"id": f"doi:{x['DOI']}" if x.get("DOI") else f"arXiv:{x['ArXiv']}" if x.get("ArXiv") else f"s2:{p.get('paperId')}", "title": clip(p.get("title"), 300),
            "year": p.get("year"), "authors": [a.get("name") for a in p.get("authors") or []], "venue": p.get("venue") or "",
            "abstract": p.get("abstract") or ((p.get("tldr") or {}).get("text") or ""), "url": p.get("url"), "pdf": (p.get("openAccessPdf") or {}).get("url"),
            "doi": x.get("DOI"), "arxiv": x.get("ArXiv"), "citations": p.get("citationCount"), "source": "Semantic Scholar"}

# ---------- tools
def fmt(rows, abstract=500):
    if not rows: return "No papers found."
    out = []
    for i, r in enumerate(rows, 1):
        au = r["authors"] or []
        authors = ", ".join(au[:5]) + (f" and {len(au) - 5} more" if len(au) > 5 else "")
        meta = " · ".join(x for x in [str(r["year"] or ""), r["venue"], f"{r['citations']} citations" if r.get("citations") is not None else "", r["source"]] if x)
        out.append(f"{i}. {r['title']}\n   id: {r['id']}\n   {authors}\n   {meta}\n   {r['url']}" + (f"\n   PDF: {r['pdf']}" if r.get("pdf") else "")
                   + (f"\n   {clip(r['abstract'], abstract)}" if r.get("abstract") and abstract else ""))
    return "\n\n".join(out)

def search_papers(a):
    q = clip(a.get("query"), 300)
    if not q: raise ToolError("Give a query, e.g. 'retrieval augmented generation evaluation'.")
    n, src, since = max(1, min(int(a.get("limit") or 6), 10)), a.get("source") or "all", int(a.get("year_from") or 0)
    rows, notes = [], []
    if src in ("all", "openalex"):
        p = {"search": q, "per-page": n}
        if since: p["filter"] = f"from_publication_date:{since}-01-01"
        try: rows += [_oa_row(w) for w in _openalex("works", p).get("results") or []]
        except ToolError as e: notes.append(f"OpenAlex: {e}")
    if src in ("all", "arxiv"):
        try: rows += [r for r in _arxiv({"search_query": "all:" + " AND all:".join(re.findall(r"[\w\-]+", q)[:8]), "start": 0, "max_results": n, "sortBy": "relevance"})
                      if not since or (r["year"] or 0) >= since]
        except ToolError as e: notes.append(f"arXiv: {e}")
    if src == "semantic_scholar":
        p = {"query": q, "limit": n, "fields": S2_FIELDS}
        if since: p["year"] = f"{since}-"
        try: rows += [_s2_row(x) for x in _s2("paper/search", p).get("data") or []]
        except ToolError as e: notes.append(f"Semantic Scholar: {e}")
    seen, uniq = set(), []
    for r in rows:  # the same paper from two sources: keep the first (OpenAlex has citations)
        k = (r.get("doi") or "").lower() or (r.get("arxiv") or "") or re.sub(r"\W+", "", (r["title"] or "").lower())
        alt = re.sub(r"\W+", "", (r["title"] or "").lower())
        if k in seen or alt in seen: continue
        seen.update({k, alt}); uniq.append(r)
    if not uniq and notes: raise ToolError("Nothing could be searched. " + " ".join(notes))
    return fmt(uniq[: n * 2]) + ("\n\n(" + " ".join(notes) + ")" if notes else "")

def _lookup(pid):
    pid = (pid or "").strip()
    if not pid: raise ToolError("Give a paper id: an arXiv id (2401.01234), a DOI (10.1145/…), an OpenAlex id (W…) or an id from search_papers.")
    low = pid.lower()
    if low.startswith("s2:"): return _s2_row(_s2(f"paper/{pid[3:]}", {"fields": S2_FIELDS}))
    doi = re.sub(r"^(doi:|https?://(dx\.)?doi\.org/)", "", pid, flags=re.I) if re.search(r"10\.\d{4,9}/", pid) else None
    if doi: return _oa_row(_openalex(f"works/doi:{doi}", {}))
    if re.fullmatch(r"w\d+", low): return _oa_row(_openalex(f"works/{pid.upper()}", {}))
    aid = arxiv_id(pid)
    if aid:
        got = _arxiv({"id_list": aid, "max_results": 1})
        if not got: raise ToolError(f"arXiv has no paper {aid}.")
        r = got[0]
        try:  # citations and what cites it come from OpenAlex, found by the arXiv DOI
            oa = _oa_row(_openalex(f"works/doi:10.48550/arXiv.{aid}", {}))
            r.update(citations=oa["citations"], openalex=oa["openalex"])
        except ToolError: pass
        return r
    raise ToolError(f"“{pid}” doesn't look like an arXiv id, a DOI or an OpenAlex id.")

def get_paper(a):
    r = _lookup(a.get("id"))
    return fmt([r], abstract=4000) + ("\n\nUse citing_papers with this id for the papers that cite it." if r.get("openalex") or r.get("doi") else "")

def citing_papers(a):
    r = _lookup(a.get("id"))
    oid = r.get("openalex")
    if not oid and r.get("doi"): oid = _oa_row(_openalex(f"works/doi:{r['doi']}", {}))["openalex"]
    if not oid: raise ToolError("OpenAlex doesn't know this paper, so its citations can't be listed.")
    n = max(1, min(int(a.get("limit") or 8), 20))
    got = _openalex("works", {"filter": f"cites:{oid}", "sort": "cited_by_count:desc", "per-page": n}).get("results") or []
    return f"Papers citing “{r['title']}” (most cited first):\n\n" + fmt([_oa_row(w) for w in got], abstract=250)

TOOLS = [
    tool("search_papers", "Search research papers (OpenAlex and arXiv by default; Semantic Scholar on request). Returns titles, ids, authors, "
         "year, venue, citation counts, links and abstracts. Cite papers by their links; don't invent papers.",
         {"query": {"type": "string", "description": "What the papers are about."},
          "source": {"type": "string", "enum": ["all", "openalex", "arxiv", "semantic_scholar"], "description": "Where to search (default all)."},
          "year_from": {"type": "integer", "description": "Only papers from this year on."},
          "limit": {"type": "integer", "description": "Papers per source, 1 to 10 (default 6)."}}, ["query"], "Search papers"),
    tool("get_paper", "One paper in full: title, authors, year, venue, citations, links and the whole abstract.",
         {"id": {"type": "string", "description": "An arXiv id, a DOI, an OpenAlex id, or an id from search_papers."}}, ["id"], "Read a paper"),
    tool("citing_papers", "The papers that cite a paper, most cited first: what built on it.",
         {"id": {"type": "string", "description": "The paper's id (as for get_paper)."}, "limit": {"type": "integer", "description": "1 to 20 (default 8)."}}, ["id"], "Papers citing it"),
]

if __name__ == "__main__":
    serve("axor-papers", "1.0", TOOLS, {"search_papers": search_papers, "get_paper": get_paper, "citing_papers": citing_papers})
