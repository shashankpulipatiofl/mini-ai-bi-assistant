"""
Manual check of the REAL LLM against the 5 example questions.

The automated tests use a fake LLM. This script uses the real Groq model
(needs GROQ_API_KEY in .env) and checks that the expected numbers appear in
the rows returned by the generated SQL.

Run from the project folder:   python -m scripts.check_examples
"""

from dotenv import load_dotenv

load_dotenv()

from app import database, qa_service  # noqa: E402

# question -> a value that must appear in the result rows
EXPECTED = {
    "What is the total revenue?": 1439200.0,
    "Which product generated the highest revenue?": "Monitor",
    "What is the monthly revenue trend?": "2026-06",
    "Which region generated the highest sales?": "South",
    "How did revenue change between March and April?": 240700.0,
}


def main():
    database.load_csv_to_db()
    passed = 0
    for question, expected in EXPECTED.items():
        try:
            result = qa_service.answer_question(question)
            values = [value for row in result["rows"] for value in row]
            ok = expected in values
            print(f"[{'PASS' if ok else 'FAIL'}] {question}")
            print(f"       SQL:    {result['sql']}")
            print(f"       Answer: {result['answer']}")
        except Exception as exc:  # show the error and keep going
            ok = False
            print(f"[FAIL] {question}\n       Error: {exc}")
        passed += ok
    print(f"\n{passed}/{len(EXPECTED)} example questions correct")


if __name__ == "__main__":
    main()
