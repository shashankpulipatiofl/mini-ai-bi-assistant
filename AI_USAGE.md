# How I Used AI on This Project

## AI tools I used

I used two kinds of AI here, and I want to keep them separate because they did different jobs.

1. **Claude (Anthropic)** was my coding assistant. I used it to plan the structure, write the first version of the code, suggest test cases and draft the README.
2. **A Groq-hosted LLM (`openai/gpt-oss-120b`)** runs inside the app itself. It decides what kind of question the user asked, writes the SQL for data questions, turns the result rows into a short answer, and answers questions about the dataset from the data dictionary.

I didn't use any agent framework like LangChain. The task was small, and I wanted to be able to point at every step in the code and explain what it does.

## How I used AI during development

I started by giving Claude the challenge document and the CSV and asking for a simple plan that fits the 3–4 hour target. We settled on FastAPI for the API, SQLite for the database and one plain HTML page for the UI. I picked SQLite because it comes with Python and doesn't need a server.

Before writing any code I went through the CSV myself. A few things stood out: the dates are in DD-MM-YYYY format, one row has quantity 0, one row has no unit price, and one order is marked as Returned. I decided how to handle each of these myself. Bad rows are rejected and saved with a reason, nothing is guessed, and returned orders stay in the table but don't count towards revenue.

After that, Claude wrote the first version of each module and I went through them one by one. Where I didn't understand something, I asked why it was done that way, and I simplified it if a simpler way worked. For example, I used Python's built-in `csv` and `sqlite3` modules instead of pandas or an ORM.

My first version mixed two approaches. The five example questions were answered by keyword rules with fixed SQL, and only other questions went to the LLM. When I tested it, it felt like the "AI" part was barely used. The rules also broke easily: "Which product had the highest revenue in the North region?" matched the "top product" rule and gave the overall top product, ignoring North. So I removed the rules completely. Now every question goes through the LLM, and I put the safety checks around it instead.

Later, while testing with a real API key, I asked "explain the different attributes in the dataset" and the app refused, because no SQL query can answer that. So I added a routing step where the LLM first decides whether a question is about the data, about the dataset itself, or out of scope. Dataset questions are answered with a small RAG over a data dictionary I wrote (`docs/data_dictionary.md`). Retrieval is plain keyword matching, no embeddings. The document is only about ten sections, so I didn't think a vector database was worth the extra setup.

For testing, I didn't want the tests to depend on an internet connection or a paid API, so the LLM is replaced with a fake in the tests. To check that the expected numbers were really correct, I calculated the total revenue separately with a few lines of plain Python over the CSV and compared. It came out to 1,439,200 both ways.

## Problems I ran into and how I debugged them

**Date format.** The CSV dates are DD-MM-YYYY, which doesn't sort or group properly as text. I convert them to YYYY-MM-DD when loading, and I added an `order_month` column so monthly questions are easy.

**Missing unit price (TXN0051).** This would either crash the `float()` conversion or produce a NULL revenue. I added a validation step that rejects the row and saves the reason. You can see rejected rows at `/api/data-quality`.

**Quantity 0 (TXN0034).** An order with zero units isn't a real sale, so this one is rejected too.

**Returned order inflating revenue.** At first I wasn't sure whether to include TXN0033. A returned order isn't real revenue, so I added "status must be Completed" as a rule in the LLM prompt.

**The LLM re-calculating revenue.** I was worried the model might forget the discount when it calculates revenue. So I calculate a `revenue` column once at load time, and the LLM only has to do `SUM(revenue)`.

**Keyword rules giving wrong answers.** This is the North region example above. I found it by trying rephrased questions by hand, and it's the main reason I switched to LLM-only.

**The model I planned to use was retired.** I first used `llama-3.3-70b-versatile`, then found on Groq's deprecations page that it had been shut down. I switched to `openai/gpt-oss-120b` and made the model configurable through `GROQ_MODEL` in `.env`.

**Broken SQL from the LLM.** Sometimes a model writes a query with a typo in a column name. Now, if SQLite returns an error, I send the error back to the LLM once and let it fix the query. I tested this with a fake LLM that deliberately writes `revenu` instead of `revenue`.

**Retrieval picking the wrong section.** For "how is revenue calculated?", my keyword search returned "Regions and sales channels" first. When I printed the retrieved sections and thought about the scoring, the reason was clear: I had mapped the word "sales" to "revenue" as a synonym, so that section got extra points. I removed that synonym and added a test that checks the top result.

**401 Unauthorized from Groq.** The page showed a 401 error when I ran it. The `.env` file still had the placeholder text instead of my real key. I fixed the file, and I also changed the error message so it now says clearly that the key was rejected, instead of showing a long raw HTTP error.

**Setup problems on my Mac.** A few things went wrong when I ran it locally:

- I unzipped an older copy of the project by mistake, which I noticed because the test names didn't match.
- Terminal had lost permission to read my Downloads folder, so commands failed with "Operation not permitted".
- Anaconda's own `uvicorn` was running instead of the one in my virtual environment.

I moved the project out of Downloads, gave Terminal folder access again, and now I start everything with `python -m ...`, which always uses the virtual environment.

## What I would improve with another day

- Turn it into a proper agent: give the LLM `run_sql` and `search_docs` as tools and let it decide the steps, so it can handle multi-step questions like "which region grew the most from March to June?"
- Build a small evaluation set of around 30 questions with expected results, so I can measure how accurate the real model is, not just whether the pipeline works.
- Use a real SQL parser such as `sqlglot` for validation instead of regex.
- Automatically check that every number in the answer text actually appears in the result rows.
- Move RAG retrieval to BM25 or embeddings if the documentation grows beyond a few pages.
- Add charts for trend questions, and simple memory so follow-up questions like "and in April?" work.
- Add a Dockerfile and a GitHub Actions workflow that runs the tests on every push.
