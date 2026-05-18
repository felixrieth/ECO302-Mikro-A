# ECO302 Mikro A Exam Dashboard

Local Streamlit dashboard for browsing old ECO302 Mikroökonomik A exam PDFs by year, term, and question number.

## Setup

1. Keep the exam PDFs in the `exams/` folder.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
streamlit run app.py
```

On first launch, the app builds `data/questions.json` from all PDFs in `exams/`. Use **Rebuild question index** in the sidebar after adding, renaming, replacing PDFs, or when parser metadata changes.

## Project Structure

```text
app.py
parser.py
data/questions.json
data/parser.log
exams/
requirements.txt
README.md
```

## Parsing Notes

- Year and term are inferred from filenames such as `Mikro A_2021_2.pdf`, which becomes `2021_T2`.
- PDFs with names containing `Loesung`, `Lösung`, or `solution` are treated as solution files and merged into the matching question keys.
- Duplicate PDFs with `Duplikat` in the filename are skipped.
- Parser warnings and failures are written to `data/parser.log`; one failed PDF does not stop the rest of the index build.
- Solutions are hidden by default in the app and shown only after clicking **Show solution**.
- The main question view renders the original PDF crop when possible. Extracted text is kept as a fallback in **Extracted text backup**.
