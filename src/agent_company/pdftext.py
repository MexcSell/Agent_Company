"""The text of one PDF, run in the L3 sandbox by `research.extract_pdf` (G-R-12): no network, limited memory, tasks
and time, a scratch directory as its only writable path. Standalone (only pypdf), started as
`python -I pdftext.py <pdf> <out.json>`; writes {"title", "text", "pages"} or {"error"} to <out.json>."""

import json
import sys

MAX_PAGES = 300
MAX_CHARS = 200_000


def extract(path: str) -> dict:
    import pypdf

    reader = pypdf.PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(""):
        return {"error": "the PDF is encrypted"}
    parts, size = [], 0
    for page in reader.pages[:MAX_PAGES]:
        text = page.extract_text() or ""
        parts.append(text)
        size += len(text)
        if size >= MAX_CHARS:
            break
    meta = reader.metadata or {}
    return {"title": str(meta.get("/Title") or "").strip()[:500], "text": "\n\n".join(parts)[:MAX_CHARS],
            "pages": len(reader.pages)}


def main() -> int:
    src, out = sys.argv[1], sys.argv[2]
    try:
        result = extract(src)
    except Exception as e:  # noqa: BLE001 - any parser failure is a result, not a crash
        result = {"error": f"{type(e).__name__}: {e}"[:300]}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
