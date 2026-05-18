from __future__ import annotations

import io
import re
import unicodedata
from pathlib import Path

import streamlit as st

from parser import EXAMS_DIR, LOG_FILE, QUESTIONS_JSON, load_questions, parse_all_pdfs

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


st.set_page_config(page_title="ECO302 Mikro A Exam Dashboard", layout="wide")

MANNHEIM_LOGO_URL = "https://upload.wikimedia.org/wikipedia/commons/thumb/4/48/Uni-mannheim.svg/500px-Uni-mannheim.svg.png"

st.markdown(
    """
    <style>
    [data-testid="stSidebar"][aria-expanded="true"] {
        min-width: 13.5rem !important;
        max-width: 13.5rem !important;
    }
    [data-testid="stSidebar"][aria-expanded="false"] {
        min-width: 0 !important;
        max-width: 0 !important;
        width: 0 !important;
        margin-left: 0 !important;
    }
    [data-testid="stSidebar"][aria-expanded="false"] [data-testid="stSidebarContent"] {
        padding: 0 !important;
        width: 0 !important;
        min-width: 0 !important;
        max-width: 0 !important;
        overflow: hidden !important;
    }
    [data-testid="stSidebar"] [data-testid="stSidebarContent"] {
        padding: 1rem 0.75rem;
    }
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] .stButton button,
    [data-testid="stSidebar"] .stSelectbox div {
        font-size: 0.85rem;
    }
    .block-container {
        padding: 1.5rem 0.75rem 1.25rem 0.75rem;
        max-width: none;
        width: 100%;
    }
    .search-shell {
        padding: 0 0 0.75rem 0;
        background: transparent;
        border-bottom: 1px solid rgba(49, 51, 63, 0.12);
    }
    .app-title-row {
        display: flex;
        align-items: center;
        gap: 1rem;
        margin: 0 0 0.45rem 0;
    }
    .app-title-row img {
        width: clamp(150px, 18vw, 240px);
        height: auto;
        flex: 0 0 auto;
    }
    .app-title-row h1 {
        margin: 0 !important;
    }
    div[data-testid="stTextInput"]:has(input[aria-label="Search all questions"]) {
        margin-top: 0.35rem;
    }
    .result-meta {
        color: rgba(49, 51, 63, 0.7);
        font-size: 0.86rem;
        margin-top: -0.45rem;
        margin-bottom: 0.35rem;
    }
    h1 {
        font-size: 1.45rem !important;
        line-height: 1.25 !important;
        margin-bottom: 0.2rem !important;
    }
    h2, h3 {
        margin-top: 0.5rem !important;
        margin-bottom: 0.55rem !important;
    }
    div[data-testid="stImage"] {
        width: 100%;
        margin: 0;
    }
    div[data-testid="stImage"] img {
        width: 100%;
        max-width: 100%;
        height: auto;
        display: block;
    }
    div[data-testid="stVerticalBlock"] {
        gap: 0.55rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def has_pdf_regions(questions: dict) -> bool:
    return any(question.get("pdf_regions") for question in questions.values())


def ensure_questions() -> dict:
    """Load the question index, or build it if it is missing or empty."""
    if QUESTIONS_JSON.exists():
        questions = load_questions(QUESTIONS_JSON)
        if questions and has_pdf_regions(questions):
            return questions

    with st.spinner("Building question index from PDFs..."):
        return parse_all_pdfs(EXAMS_DIR, QUESTIONS_JSON)


def normalize_search(text: str) -> str:
    text = text.lower()
    text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", text).strip()


def search_tokens(query: str) -> list[str]:
    normalized = normalize_search(query)
    return [token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) > 1]


def re_like_query(query: str) -> str | None:
    """Convert queries like "2021 T2 Aufgabe 3", "2022 Termin 2 Aufgabe 7", or "2021 T2 3"."""
    query = normalize_search(query)

    year = re.search(r"\b(20\d{2})\b", query)
    term = re.search(r"\b(?:t|termin)\s*([12])\b", query)
    task = re.search(r"\b(?:aufgabe|frage|question|exercise|a)\s*(\d{1,2})\b", query)

    # Compact exam lookup: "2021 T2 4".
    if year and term and not task:
        remainder = query
        remainder = remainder.replace(year.group(0), " ", 1)
        remainder = remainder.replace(term.group(0), " ", 1)
        numbers = re.findall(r"\b\d{1,2}\b", remainder)
        if numbers:
            task_number = numbers[-1]
            return f"{year.group(1)}_T{term.group(1)}_A{int(task_number)}"

    # Numeric shorthand: "2021 2 4".
    shorthand = re.search(r"\b(20\d{2})\D+([12])\D+(\d{1,2})\b", query)
    if shorthand and not term and not task:
        return f"{shorthand.group(1)}_T{shorthand.group(2)}_A{int(shorthand.group(3))}"

    if year and term and task:
        return f"{year.group(1)}_T{term.group(1)}_A{int(task.group(1))}"
    return None


def ranked_search(query: str, questions: dict, limit: int = 12) -> list[tuple[str, int]]:
    tokens = search_tokens(query)
    if not tokens:
        return []

    ranked: list[tuple[str, int]] = []
    for key, question in questions.items():
        key_text = normalize_search(key.replace("_", " "))
        meta_text = normalize_search(
            f"{question.get('year', '')} {question.get('term', '')} "
            f"aufgabe {question.get('question_number', '')} termin {question.get('term', '')[-1:]}"
        )
        question_text = normalize_search(question.get("question_text", ""))
        choice_text = normalize_search(" ".join(question.get("answer_choices", {}).values()))
        haystack = f"{key_text} {meta_text} {question_text} {choice_text}"

        if not all(token in haystack for token in tokens):
            continue

        score = 0
        for token in tokens:
            if token in key_text:
                score += 80
            if token in meta_text:
                score += 50
            if token in question_text:
                score += 12 + min(question_text.count(token), 6)
            if token in choice_text:
                score += 6 + min(choice_text.count(token), 4)

        ranked.append((key, score))

    ranked.sort(
        key=lambda item: (
            -item[1],
            questions[item[0]]["year"],
            questions[item[0]]["term"],
            questions[item[0]]["question_number"],
        )
    )
    return ranked[:limit]


def sorted_values(questions: dict, field: str) -> list:
    values = {question[field] for question in questions.values()}
    return sorted(values, key=lambda value: int(value[1:]) if field == "term" else value)


def question_label(key: str, question: dict) -> str:
    return f"{question['year']} / {question['term']} / Aufgabe {question['question_number']}"


def render_pdf_regions(question: dict) -> list[bytes]:
    """Render stored PDF crop regions as PNG images."""
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed. Run: pip install -r requirements.txt")

    source_path = EXAMS_DIR / question["source_file"]
    if not source_path.exists():
        raise FileNotFoundError(f"Source PDF not found: {source_path}")

    images: list[bytes] = []
    with fitz.open(source_path) as doc:
        regions = question.get("pdf_regions") or []
        if not regions:
            page_index = max(0, int(question.get("page_number") or 1) - 1)
            page_index = min(page_index, len(doc) - 1)
            regions = [{"page_index": page_index, "rect": None, "cropped": False}]

        for region in regions:
            page_index = int(region.get("page_index", 0))
            if page_index < 0 or page_index >= len(doc):
                continue

            page = doc[page_index]
            rect_values = region.get("rect")
            clip = None
            if rect_values:
                rect = fitz.Rect(rect_values)
                crop_margin = 14
                rect = fitz.Rect(
                    rect.x0 - crop_margin,
                    rect.y0 - crop_margin,
                    rect.x1 + crop_margin,
                    rect.y1 + crop_margin,
                )
                clip = rect & page.rect
                if clip.is_empty or clip.height < 20 or clip.width < 20:
                    clip = None

            pixmap = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip, alpha=False)
            images.append(pixmap.tobytes("png"))

    return images


@st.cache_data(show_spinner=False)
def render_pdf_regions_cached(source_file: str, regions: tuple, page_number: int | None) -> list[bytes]:
    question = {
        "source_file": source_file,
        "pdf_regions": [dict(region_items) for region_items in regions],
        "page_number": page_number,
    }
    return render_pdf_regions(question)


def region_cache_key(question: dict) -> tuple:
    regions = []
    for region in question.get("pdf_regions", []):
        normalized_items = []
        for key, value in sorted(region.items()):
            if isinstance(value, list):
                value = tuple(value)
            normalized_items.append((key, value))
        regions.append(tuple(normalized_items))
    return tuple(regions)


def show_extracted_text(question: dict) -> None:
    st.markdown(question.get("question_text", "_No question text extracted._"))

    choices = question.get("answer_choices", {})
    if choices:
        st.markdown("**Antwortmöglichkeiten**")
        for label in ["a", "b", "c", "d", "e"]:
            if label in choices:
                st.markdown(f"**{label})** {choices[label]}")
    else:
        st.warning("No answer choices were extracted for this question.")


def set_selected_key(key: str) -> None:
    st.session_state.selected_key = key


def first_question_key(questions: dict) -> str:
    return min(
        questions,
        key=lambda key: (
            questions[key]["year"],
            questions[key]["term"],
            questions[key]["question_number"],
        ),
    )


questions = ensure_questions()

if not questions:
    st.warning("No questions found. Put PDF files in the exams/ folder and rebuild the question index.")
    if LOG_FILE.exists():
        st.info(f"Parser log: {LOG_FILE}")
    st.stop()

if "selected_key" not in st.session_state or st.session_state.selected_key not in questions:
    st.session_state.selected_key = first_question_key(questions)

st.markdown('<div class="search-shell">', unsafe_allow_html=True)
st.markdown(
    f"""
    <div class="app-title-row">
        <img src="{MANNHEIM_LOGO_URL}" alt="Universität Mannheim logo">
        <h1>ECO302 Mikroökonomik A</h1>
    </div>
    """,
    unsafe_allow_html=True,
)
search_query = st.text_input(
    "Search all questions",
    placeholder="2021 T2 Aufgabe 3, 2023 T1 14, Cobb Douglas, Edgeworth...",
    label_visibility="collapsed",
    key="global_search",
)
st.markdown("</div>", unsafe_allow_html=True)

exact_key = re_like_query(search_query) if search_query else None
keyword_results: list[tuple[str, int]] = []
if search_query:
    if exact_key and exact_key in questions:
        st.session_state.selected_key = exact_key
    elif exact_key and exact_key not in questions:
        st.warning(f"No indexed question found for `{exact_key}`.")
    else:
        keyword_results = ranked_search(search_query, questions)
        if keyword_results:
            st.caption(f"{len(keyword_results)} best matches")
            for key, score in keyword_results:
                question_result = questions[key]
                preview = question_result.get("question_text", "").replace("\n", " ")
                if len(preview) > 180:
                    preview = preview[:177].rstrip() + "..."
                if st.button(question_label(key, question_result), key=f"result_{key}", use_container_width=True):
                    set_selected_key(key)
                    st.rerun()
                st.markdown(f'<div class="result-meta">{preview}</div>', unsafe_allow_html=True)
        else:
            st.info("No matching questions found.")

if st.sidebar.button("Rebuild question index", use_container_width=True):
    with st.spinner("Rebuilding question index from PDFs..."):
        questions = parse_all_pdfs(EXAMS_DIR, QUESTIONS_JSON)
    st.session_state.selected_key = first_question_key(questions) if questions else None
    st.success(f"Rebuilt index with {len(questions)} questions.")

st.sidebar.divider()
st.sidebar.markdown("**Select**")

current_question = questions[st.session_state.selected_key]
years = sorted_values(questions, "year")
selected_year = st.sidebar.selectbox("Year", years, index=years.index(current_question["year"]))

terms = sorted(
    {question["term"] for question in questions.values() if question["year"] == selected_year},
    key=lambda term: int(term[1:]),
)
current_term = current_question["term"] if current_question["year"] == selected_year and current_question["term"] in terms else terms[0]
selected_term = st.sidebar.selectbox("Term", terms, index=terms.index(current_term))

numbers = sorted(
    {
        question["question_number"]
        for question in questions.values()
        if question["year"] == selected_year and question["term"] == selected_term
    }
)
current_number = (
    current_question["question_number"]
    if current_question["year"] == selected_year
    and current_question["term"] == selected_term
    and current_question["question_number"] in numbers
    else numbers[0]
)
selected_number = st.sidebar.selectbox("Aufgabe", numbers, index=numbers.index(current_number))
sidebar_key = f"{selected_year}_{selected_term}_A{selected_number}"
if sidebar_key in questions:
    st.session_state.selected_key = sidebar_key

selected_key = st.session_state.selected_key
question = questions[selected_key]
solution = question.get("solution", "")

st.header(question_label(selected_key, question))

try:
    regions = region_cache_key(question)
    pdf_images = render_pdf_regions_cached(question["source_file"], regions, question.get("page_number"))
except Exception as exc:
    pdf_images = []
    st.warning(f"Could not render original PDF view. Showing extracted text fallback instead. ({exc})")

if pdf_images:
    for image in pdf_images:
        st.image(io.BytesIO(image), use_container_width=True)
else:
    show_extracted_text(question)

with st.expander("Extracted text backup"):
    show_extracted_text(question)

if solution:
    if st.button("Show solution"):
        st.success(f"Correct answer(s): {solution}")
else:
    st.info("No solution was extracted for this question.")

with st.sidebar.expander("Index details"):
    st.write(f"Questions: {len(questions)}")
    st.write(f"PDF folder: `{Path(EXAMS_DIR).name}/`")
    st.write(f"JSON: `{QUESTIONS_JSON}`")
    if LOG_FILE.exists():
        st.write(f"Parser log: `{LOG_FILE}`")
