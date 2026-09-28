try:
    import rank_bm25
    print("rank_bm25 OK")
except ImportError as e:
    print(f"rank_bm25 MISSING: {e}")

try:
    import pdfplumber
    print("pdfplumber OK")
except ImportError as e:
    print(f"pdfplumber MISSING: {e}")

try:
    from sentence_transformers import CrossEncoder
    print("CrossEncoder OK")
except ImportError as e:
    print(f"CrossEncoder MISSING: {e}")

try:
    import chromadb
    print("chromadb OK")
except ImportError as e:
    print(f"chromadb MISSING: {e}")