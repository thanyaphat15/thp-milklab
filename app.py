"""Sandwich Cloud Kitchen RAG Chatbot (S4 Pivot).

Run locally: streamlit run app.py
Deploy: push to GitHub then Actions deploys to HuggingFace Space
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from time import perf_counter

import faiss
import numpy as np
import streamlit as st
from dotenv import load_dotenv
from google import genai
from sentence_transformers import SentenceTransformer

TRACE_FILE = Path(__file__).parent / "traces.jsonl"
KB_FILE = Path(__file__).parent / "sandwich_kb.md"
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
GEMINI_MODEL = "gemini-2.5-flash"


def ensure_env() -> None:
    load_dotenv()


def write_trace_log(entry: dict) -> None:
    TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with TRACE_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_span(trace_id: str, span_name: str, details: dict | None = None, status: str = "ok") -> dict:
    payload = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "trace_id": trace_id,
        "span": span_name,
        "status": status,
        "details": details or {},
    }
    write_trace_log(payload)
    return payload


def read_trace_entries(trace_id: str) -> list[dict]:
    if not TRACE_FILE.exists():
        return []
    entries: list[dict] = []
    with TRACE_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("trace_id") == trace_id:
                entries.append(record)
    return entries


def format_trace_summary(trace_id: str) -> dict:
    events = read_trace_entries(trace_id)
    return {
        "trace_id": trace_id,
        "event_count": len(events),
        "events": events,
    }


@st.cache_resource
def load_index() -> tuple[SentenceTransformer, faiss.IndexFlatIP, list[str]]:
    """Load knowledge base, chunk text, embed with sentence-transformers, and cache the FAISS index."""
    ensure_env()

    raw_text = KB_FILE.read_text(encoding="utf-8")
    chunks = [chunk.strip()
              for chunk in raw_text.split("\n\n") if chunk.strip()]
    model = SentenceTransformer(EMBEDDING_MODEL)
    embeddings = model.encode(
        chunks, convert_to_numpy=True, normalize_embeddings=True)
    embeddings = np.array(embeddings, dtype="float32")
    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)
    return model, index, chunks


def retrieve_top_k(
    query: str,
    k: int = 4,
    trace_id: str | None = None,
    model: SentenceTransformer | None = None,
    index: faiss.IndexFlatIP | None = None,
    chunks: list[str] | None = None,
) -> list[str]:
    """Retrieve top-k chunks for the query using cosine similarity over FAISS."""
    trace_id = trace_id or str(uuid.uuid4())
    if model is None or index is None or chunks is None:
        model, index, chunks = load_index()

    start = perf_counter()
    log_span(trace_id, "retrieve_top_k.start", {"query": query})
    query_embedding = model.encode(
        [query], convert_to_numpy=True, normalize_embeddings=True).astype("float32")
    top_k = min(k, len(chunks))
    similarity_scores, indices = index.search(query_embedding, top_k)
    elapsed_ms = (perf_counter() - start) * 1000

    ordered_chunks = []
    scores: list[float] = []
    for idx, score in zip(indices[0].tolist(), similarity_scores[0].tolist()):
        if idx >= 0 and idx < len(chunks):
            ordered_chunks.append(chunks[idx])
            scores.append(score)

    log_span(
        trace_id,
        "retrieve_top_k.end",
        {
            "query": query,
            "top_k": top_k,
            "scores": scores,
            "elapsed_ms": elapsed_ms,
        },
    )
    return ordered_chunks


def generate_answer(query: str, context_chunks: list[str], trace_id: str | None = None) -> str:
    """Generate an answer from Gemini using retrieved context and observability tracing."""
    trace_id = trace_id or str(uuid.uuid4())
    ensure_env()
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets.get("GOOGLE_API_KEY")
        except Exception:
            api_key = None

    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not set in environment or Streamlit secrets")

    client = genai.Client(api_key=api_key)
    context_text = "\n\n".join(context_chunks)
    prompt = (
        "คุณคือผู้ช่วยตอบคำถามและแนะนำสินค้าของร้าน Sandwich Cloud Kitchen (ร้านแซนด์วิชขนมปังหนานุ่ม ไส้แน่น สไตล์คาเฟ่เกาหลี/ญี่ปุ่น Pre-order & Catering) จากข้อมูลด้านล่างเท่านั้น\n"
        "คำแนะนำในการตอบ:\n"
        "- ตอบด้วยน้ำเสียงสุภาพ เป็นมิตร น่ารับประทาน\n"
        "- หากลูกค้าถามหาเมนูแนะนำ เมนูยอดนิยม หรือถามว่ามีเมนูอะไรบ้าง ให้แนะนำและสรุปรายการเมนูพร้อมราคาจาก context ที่มีให้อย่างครบถ้วน\n"
        "- ถ้าคำถามไม่มีข้อมูลใน context เลย ให้ตอบว่า 'ขอโทษครับ/ค่ะ ฉันไม่มีข้อมูลในส่วนนี้'\n"
        "- อย่าแต่งข้อมูลหรือเดาราคาเกินกว่าที่มีใน context\n\n"
        f"Context:\n{context_text}\n\n"
        f"คำถาม: {query}\n"
        "คำตอบ:")

    log_span(trace_id, "generate_answer.start", {
             "query": query, "context_chunks": len(context_chunks)})
    start = perf_counter()
    response = client.models.generate_content(
        model=GEMINI_MODEL, contents=prompt)
    answer = (response.text or "").strip()
    elapsed_ms = (perf_counter() - start) * 1000
    log_span(
        trace_id,
        "generate_answer.end",
        {
            "query": query,
            "answer_length": len(answer),
            "elapsed_ms": elapsed_ms,
        },
    )
    return answer


def main() -> None:
    st.set_page_config(page_title="Sandwich Cloud Kitchen RAG", page_icon="🥪")
    st.title("🥪 Sandwich Cloud Kitchen Chatbot")
    st.caption("ถามเรื่องเมนู รอบจัดส่ง บริการจัดเลี้ยง และการแพ้อาหารได้เลย ตอบจาก sandwich_kb.md")

    model, index, chunks = load_index()
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    if prompt := st.chat_input("ถามเรื่องเมนู รอบส่ง หรือบริการจัดเลี้ยงได้เลย"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        trace_id = str(uuid.uuid4())
        with st.chat_message("assistant"):
            with st.spinner("กำลังค้นข้อมูล..."):
                context = retrieve_top_k(
                    prompt,
                    k=3,
                    trace_id=trace_id,
                    model=model,
                    index=index,
                    chunks=chunks,
                )
                answer = generate_answer(prompt, context, trace_id=trace_id)
            st.write(answer)
            with st.expander("Source chunks"):
                for i, c in enumerate(context, 1):
                    st.markdown(f"**[{i}]** {c}")
            with st.expander("Trace"):
                st.json(format_trace_summary(trace_id))
        st.session_state.messages.append(
            {"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
