Function-calling keeps the query surface constrained to audited repository tools.
The LLM chooses tool names and typed arguments, but never writes raw SQL.
Every answer includes evidence: tool name, arguments, and returned data.
If no curated tool can answer, the agent says so instead of inventing a number.
This is safer for operators because counts and rankings are grounded in stored runs.
