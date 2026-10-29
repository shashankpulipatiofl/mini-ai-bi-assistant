"""
Vectorless RAG over the data dictionary (docs/data_dictionary.md).

Used for questions ABOUT the dataset ("what columns are there?",
"what does status mean?") - questions that are answered from documentation,
not by calculating numbers.

RAG = Retrieval-Augmented Generation:
  1. RETRIEVE  the most relevant sections of the document
  2. AUGMENT   the LLM prompt with only those sections
  3. GENERATE  an answer that must be based on those sections

"Vectorless" because retrieval uses simple keyword matching instead of
embeddings + a vector database. The document is small (~10 sections), so
keyword search is enough, easy to explain, and needs no extra libraries.
"""

import re
from pathlib import Path

from app import llm

DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "data_dictionary.md"
TOP_K = 3  # number of sections given to the LLM

STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "in", "on", "to", "and",
    "or", "for", "what", "which", "how", "does", "do", "did", "me", "tell", "about",
    "explain", "this", "that", "there", "it", "its", "by", "with", "be", "can",
    "i", "you", "we", "my", "our", "please", "give", "show", "list", "diff",
    "different", "all", "data", "dataset", "mean", "means",
}

# Synonyms so that e.g. "attributes" also finds the "Columns" section
SYNONYMS = {
    "attribute": "column", "field": "column", "variable": "column", "feature": "column",
    "income": "revenue", "calculated": "revenue", "calculate": "revenue",
    "invalid": "rejected", "missing": "rejected", "removed": "rejected",
}

DOCS_PROMPT = """You answer questions about a sales dataset using ONLY the documentation
sections given to you. Do not use outside knowledge and do not invent facts or numbers.
If the sections do not contain the answer, set "answer" to null.
Keep the answer short and clear (a few sentences or a short list).

Reply with JSON only: {"answer": "<answer or null>"}
"""


def tokenize(text):
    """Lowercase words, remove stop words, map synonyms, strip a plural 's'."""
    tokens = set()
    for word in re.findall(r"[a-z_]+", text.lower()):
        if word in STOP_WORDS or len(word) < 3:
            continue
        if word.endswith("s") and len(word) > 3:
            word = word[:-1]
        tokens.add(SYNONYMS.get(word, word))
    return tokens


def load_sections(path=DOC_PATH):
    """Split the markdown document into sections at every '## ' heading."""
    text = Path(path).read_text(encoding="utf-8")
    sections = []
    for block in re.split(r"\n(?=## )", text):
        lines = block.strip().splitlines()
        if not lines or not lines[0].startswith("## "):
            continue  # skip the document title / intro
        title = lines[0][3:].strip()
        body = "\n".join(lines[1:]).strip()
        sections.append({"title": title, "text": body})
    return sections


def retrieve(question, k=TOP_K):
    """
    Score each section by how many question keywords it contains
    (a match in the title counts double) and return the top k sections.
    Sections with score 0 are never returned.
    """
    q_tokens = tokenize(question)
    scored = []
    for section in load_sections():
        title_tokens = tokenize(section["title"])
        body_tokens = tokenize(section["text"])
        score = len(q_tokens & body_tokens) + 2 * len(q_tokens & title_tokens)
        if score > 0:
            scored.append((score, section))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [section for _, section in scored[:k]]


def answer_from_docs(question):
    """
    Full RAG step. Returns (answer_or_None, retrieved_sections).
    answer is None when nothing relevant was retrieved, or when the LLM says
    the retrieved sections do not contain the answer.
    """
    sections = retrieve(question)
    if not sections:
        return None, []

    context = "\n\n".join(f"### {s['title']}\n{s['text']}" for s in sections)
    data = llm.call_llm(DOCS_PROMPT, f"Documentation sections:\n{context}\n\nQuestion: {question}")
    return data.get("answer"), sections
