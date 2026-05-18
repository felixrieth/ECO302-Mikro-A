"""PDF parser for ECO302 Mikro A old exams.

The parser is intentionally conservative: it extracts text with PyMuPDF, splits
exam PDFs into "Aufgabe" blocks, extracts answer choices a-e, and merges in
solutions found either inline or in matching solution PDFs.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

try:
    import fitz  # PyMuPDF
except ImportError:  # Streamlit shows this clearly before requirements are installed.
    fitz = None


ROOT = Path(__file__).resolve().parent
EXAMS_DIR = ROOT / "exams"
DATA_DIR = ROOT / "data"
QUESTIONS_JSON = DATA_DIR / "questions.json"
LOG_FILE = DATA_DIR / "parser.log"

QUESTION_HEADER_RE = re.compile(
    r"(?im)^\s*(?:Aufgabe|Question|Problem|Exercise)\s+(\d{1,2})\b[.:)]?\s*"
)
FALLBACK_QUESTION_HEADER_RE = re.compile(r"(?m)^\s*(\d{1,2})\s*[\).]\s+")
ANSWER_CHOICE_RE = re.compile(
    r"(?ims)(?:^|\n)\s*(?:\(([a-eA-E])\)|([a-eA-E])[\).:])\s+"
    r"(.+?)(?=(?:\n\s*(?:\([a-eA-E]\)|[a-eA-E][\).:])\s+)"
    r"|(?:\n\s*(?:Correct answers?|Correct answer|Korrekte Antworten?|Richtige Antworten?|"
    r"L[oö]sung|Antwort)\b)|\Z)"
)
SOLUTION_RE = re.compile(
    r"(?is)\b(?:Correct answers?|Correct answer|Korrekte Antworten?|Richtige Antworten?|"
    r"L[oö]sung(?:en)?|Antwort(?:en)?)\s*[:\-]?\s*"
    r"(?:\(|\[)?\s*([a-e](?:\s*[,;/&+ ]\s*[a-e])*)\s*(?:\)|\])?"
)


def setup_logging() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        force=True,
    )


def infer_year_term(pdf_path: Path) -> tuple[str | None, str | None]:
    """Infer ("2021", "T2") from filenames such as "Mikro A_2021_2.pdf"."""
    match = re.search(r"(20\d{2})\D+([12])(?:\D|$)", pdf_path.stem)
    if not match:
        return None, None
    return match.group(1), f"T{match.group(2)}"


def is_solution_pdf(pdf_path: Path) -> bool:
    name = pdf_path.stem.lower()
    return any(token in name for token in ("loesung", "lösung", "solution"))


def is_duplicate_pdf(pdf_path: Path) -> bool:
    return "duplikat" in pdf_path.stem.lower() or "duplicate" in pdf_path.stem.lower()


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract text page by page with page-break markers."""
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed. Run: pip install -r requirements.txt")

    parts: list[str] = []
    with fitz.open(pdf_path) as doc:
        for page_number, page in enumerate(doc, start=1):
            try:
                text = page.get_text("text", sort=True)
            except Exception as exc:  # Keep parsing the rest of the document.
                logging.warning("Could not extract page %s from %s: %s", page_number, pdf_path.name, exc)
                continue
            parts.append(text)
    return "\n\n".join(parts)


def extract_pdf_pages(pdf_path: Path) -> list[str]:
    """Extract raw text per page so question text parsing can still use page breaks."""
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed. Run: pip install -r requirements.txt")

    pages: list[str] = []
    with fitz.open(pdf_path) as doc:
        for page_number, page in enumerate(doc, start=1):
            try:
                pages.append(page.get_text("text", sort=True))
            except Exception as exc:
                logging.warning("Could not extract page %s from %s: %s", page_number, pdf_path.name, exc)
                pages.append("")
    return pages


def normalize_text(text: str) -> str:
    """Normalize line/page breaks while preserving German text and math symbols."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00ad", "")  # soft hyphen
    text = re.sub(r"([A-Za-zÄÖÜäöüß])- *\n *([A-Za-zÄÖÜäöüß])", r"\1\2", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def answer_choice_count(text: str) -> int:
    return len(re.findall(r"(?im)^\s*(?:\([a-e]\)|[a-e][\).:])\s+", text))


def clean_fragment(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip(" \n\t:.-")


def split_question_blocks(text: str) -> list[tuple[int, str]]:
    matches = list(QUESTION_HEADER_RE.finditer(text))
    if not matches:
        matches = list(FALLBACK_QUESTION_HEADER_RE.finditer(text))

    blocks: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        number = int(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        if block:
            blocks.append((number, block))
    return blocks


def locate_question_regions(pdf_path: Path) -> dict[int, list[dict[str, Any]]]:
    """Locate crop rectangles for each question in the original PDF.

    The old exams usually start each task with a line like "4. Betrachten ...".
    We first collect those numbered lines, then discard false positives such as
    cover-page dates or numbered instructions by requiring answer choices below.
    """
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed. Run: pip install -r requirements.txt")

    raw_candidates: list[dict[str, Any]] = []
    header_re = re.compile(r"^\s*(?:Aufgabe\s*)?(\d{1,2})\s*[\).]\s+", re.IGNORECASE)

    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc):
            page_dict = page.get_text("dict", sort=True)
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    line_text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                    match = header_re.match(line_text)
                    if not match:
                        continue
                    raw_candidates.append(
                        {
                            "question_number": int(match.group(1)),
                            "page_index": page_index,
                            "y0": float(line["bbox"][1]),
                            "line_text": line_text,
                        }
                    )

        valid_candidates: list[dict[str, Any]] = []
        for index, candidate in enumerate(raw_candidates):
            page = doc[candidate["page_index"]]
            next_on_page = next(
                (
                    other
                    for other in raw_candidates[index + 1 :]
                    if other["page_index"] == candidate["page_index"]
                ),
                None,
            )
            y1 = next_on_page["y0"] if next_on_page else page.rect.y1
            clip = fitz.Rect(page.rect.x0, candidate["y0"], page.rect.x1, y1)
            segment_text = page.get_text("text", clip=clip, sort=True)
            if answer_choice_count(segment_text) >= 2:
                valid_candidates.append(candidate)

        regions: dict[int, list[dict[str, Any]]] = {}
        for index, candidate in enumerate(valid_candidates):
            next_candidate = valid_candidates[index + 1] if index + 1 < len(valid_candidates) else None
            page_index = candidate["page_index"]
            start_y = max(doc[page_index].rect.y0, candidate["y0"] - 8)

            if next_candidate and next_candidate["page_index"] == page_index:
                rects = [
                    fitz.Rect(
                        45,
                        start_y,
                        doc[page_index].rect.x1 - 35,
                        max(start_y + 30, next_candidate["y0"] - 8),
                    )
                ]
            elif next_candidate and next_candidate["page_index"] > page_index:
                current_rect = fitz.Rect(45, start_y, doc[page_index].rect.x1 - 35, doc[page_index].rect.y1 - 35)
                current_text = doc[page_index].get_text("text", clip=current_rect, sort=True)
                rects = [current_rect]

                if answer_choice_count(current_text) < 5:
                    for middle_page in range(page_index + 1, next_candidate["page_index"]):
                        rects.append(
                            fitz.Rect(
                                45,
                                doc[middle_page].rect.y0 + 35,
                                doc[middle_page].rect.x1 - 35,
                                doc[middle_page].rect.y1 - 35,
                            )
                        )
                    next_page = doc[next_candidate["page_index"]]
                    rects.append(
                        fitz.Rect(
                            45,
                            next_page.rect.y0 + 35,
                            next_page.rect.x1 - 35,
                            max(next_page.rect.y0 + 65, next_candidate["y0"] - 8),
                        )
                    )
            else:
                rects = [fitz.Rect(45, start_y, doc[page_index].rect.x1 - 35, doc[page_index].rect.y1 - 35)]

            regions[candidate["question_number"]] = [
                {
                    "page_index": rect_page,
                    "page_number": rect_page + 1,
                    "rect": [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)],
                    "cropped": True,
                }
                for rect_page, rect in zip(
                    range(page_index, page_index + len(rects)),
                    rects,
                )
            ]

    if not regions:
        logging.warning("No reliable PDF crop regions found in %s", pdf_path.name)
    return regions


def extract_solution(text: str) -> str:
    match = SOLUTION_RE.search(text)
    if not match:
        return ""
    letters = re.findall(r"[a-e]", match.group(1).lower())
    return ", ".join(dict.fromkeys(letters))


def remove_solution_lines(text: str) -> str:
    return SOLUTION_RE.sub("", text).strip()


def extract_answer_choices(block: str) -> tuple[str, dict[str, str]]:
    matches = list(ANSWER_CHOICE_RE.finditer(block))
    choices: dict[str, str] = {}

    if not matches:
        return clean_fragment(remove_solution_lines(block)), choices

    question_text = clean_fragment(remove_solution_lines(block[: matches[0].start()]))
    for match in matches:
        label = (match.group(1) or match.group(2)).lower()
        choice_text = clean_fragment(remove_solution_lines(match.group(3)))
        if choice_text:
            choices[label] = choice_text

    return question_text, choices


def parse_exam_pdf(pdf_path: Path) -> dict[str, dict[str, Any]]:
    year, term = infer_year_term(pdf_path)
    if not year or not term:
        logging.warning("Skipping %s: could not infer year and term from filename", pdf_path.name)
        return {}

    page_texts = extract_pdf_pages(pdf_path)
    text = normalize_text("\n\n".join(page_texts))
    blocks = split_question_blocks(text)
    regions = locate_question_regions(pdf_path)
    if not blocks:
        logging.warning("No question blocks found in %s", pdf_path.name)
        return {}

    parsed: dict[str, dict[str, Any]] = {}
    for question_number, block in blocks:
        question_text, answer_choices = extract_answer_choices(block)
        solution = extract_solution(block)
        key = f"{year}_{term}_A{question_number}"

        if not question_text:
            logging.warning("%s: Aufgabe %s has no question text", pdf_path.name, question_number)
        if len(answer_choices) < 2:
            logging.warning(
                "%s: Aufgabe %s has only %s extracted answer choices",
                pdf_path.name,
                question_number,
                len(answer_choices),
            )

        parsed[key] = {
            "year": year,
            "term": term,
            "question_number": question_number,
            "question_text": question_text,
            "answer_choices": answer_choices,
            "solution": solution,
            "source_file": pdf_path.name,
            "pdf_regions": regions.get(question_number, []),
            "page_number": regions.get(question_number, [{}])[0].get("page_number") if regions.get(question_number) else None,
        }
    return parsed


def parse_solution_pdf(pdf_path: Path) -> dict[str, str]:
    """Return solutions keyed as 2021_T2_A3 from a solution PDF."""
    year, term = infer_year_term(pdf_path)
    if not year or not term:
        logging.warning("Skipping solution %s: could not infer year and term", pdf_path.name)
        return {}

    text = normalize_text(extract_pdf_text(pdf_path))
    solutions: dict[str, str] = {}

    for question_number, block in split_question_blocks(text):
        solution = extract_solution(block)
        if solution:
            solutions[f"{year}_{term}_A{question_number}"] = solution

    # Some solution PDFs are compact answer keys: "1 d", "2 a,c", etc.
    compact_re = re.compile(r"(?im)^\s*(?:Aufgabe\s*)?(\d{1,2})\s*[:.)-]?\s*([a-e](?:\s*[,;/&+ ]\s*[a-e])*)\s*$")
    for match in compact_re.finditer(text):
        letters = ", ".join(dict.fromkeys(re.findall(r"[a-e]", match.group(2).lower())))
        if letters:
            solutions[f"{year}_{term}_A{int(match.group(1))}"] = letters

    if not solutions:
        logging.warning("No solutions found in %s", pdf_path.name)
    return solutions


def parse_all_pdfs(exams_dir: Path = EXAMS_DIR, output_path: Path = QUESTIONS_JSON) -> dict[str, dict[str, Any]]:
    """Parse all PDFs under exams_dir and write the JSON question index."""
    setup_logging()
    exams_dir.mkdir(exist_ok=True)
    output_path.parent.mkdir(exist_ok=True)

    questions: dict[str, dict[str, Any]] = {}
    solution_maps: list[dict[str, str]] = []

    pdfs = sorted(exams_dir.glob("*.pdf"))
    if not pdfs:
        logging.warning("No PDFs found in %s", exams_dir)

    for pdf_path in pdfs:
        try:
            if is_duplicate_pdf(pdf_path):
                logging.info("Skipping duplicate PDF %s", pdf_path.name)
                continue
            if is_solution_pdf(pdf_path):
                solution_maps.append(parse_solution_pdf(pdf_path))
            else:
                questions.update(parse_exam_pdf(pdf_path))
        except Exception as exc:
            logging.exception("Failed to parse %s: %s", pdf_path.name, exc)

    for solution_map in solution_maps:
        for key, solution in solution_map.items():
            if key in questions and solution:
                questions[key]["solution"] = solution

    ordered = dict(sorted(questions.items(), key=lambda item: (item[1]["year"], item[1]["term"], item[1]["question_number"])))
    output_path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote %s questions to %s", len(ordered), output_path)
    return ordered


def load_questions(path: Path = QUESTIONS_JSON) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    parsed = parse_all_pdfs()
    print(f"Parsed {len(parsed)} questions into {QUESTIONS_JSON}")
