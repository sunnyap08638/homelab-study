"""Extract a small page range in a separate process with bounded memory."""

import json
import logging
import sys


def extract(path, first, last, limit):
    from pypdf import PdfReader

    logging.disable(logging.CRITICAL)
    with open(path, "rb") as stream:
        reader = PdfReader(stream, root_object_recovery_limit=2000, xref_stream_maximum_entries=500_000)
        if reader.is_encrypted:
            raise ValueError("Use a PDF without password protection.")
        if first > len(reader.pages):
            raise ValueError("The starting page is outside this PDF.")
        excerpts = []
        remaining = limit + 1
        clipped = False
        for index in range(first - 1, min(last, len(reader.pages))):
            text = (reader.pages[index].extract_text() or "").strip()
            if not text:
                continue
            if excerpts:
                remaining -= 2
            excerpts.append(text[:max(0, remaining)])
            remaining -= len(text)
            if remaining <= 0:
                clipped = True
                break
        result = "\n\n".join(excerpts).strip()
        if not result:
            raise ValueError("No readable text was found. Use a searchable PDF or paste an OCR excerpt.")
        return {"text": result[:limit], "clipped": clipped or len(result) > limit}


if __name__ == "__main__":
    if sys.platform.startswith("linux"):
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (144 * 1024 ** 2,) * 2)
    try:
        outcome = extract(sys.argv[1], *(int(value) for value in sys.argv[2:]))
    except ImportError:
        outcome = {"error": "PDF support is unavailable on this server."}
    except ValueError as error:
        # Library parsing errors can contain content from the uploaded file.
        outcome = {"error": str(error) if type(error) is ValueError and str(error) in {
            "Use a PDF without password protection.", "The starting page is outside this PDF.",
            "No readable text was found. Use a searchable PDF or paste an OCR excerpt.",
        } else "Could not read the selected PDF pages."}
    except Exception:
        outcome = {"error": "Could not read the selected PDF pages."}
    print(json.dumps(outcome))
