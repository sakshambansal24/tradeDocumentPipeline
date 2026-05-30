import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { askQuery } from "../api/client";

export function QueryPanel() {
  const [question, setQuestion] = useState("");
  const queryMutation = useMutation({
    mutationFn: askQuery
  });

  return (
    <aside className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold">Grounded query</h2>
      <p className="mt-1 text-xs text-neutral-500">
        Ask about stored runs. Answers include tool evidence.
      </p>
      <form
        className="mt-3 space-y-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (question.trim()) queryMutation.mutate(question.trim());
        }}
      >
        <textarea
          className="min-h-24 w-full rounded-md border border-neutral-300 p-2 text-sm"
          placeholder="How many shipments were flagged this week?"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button className="rounded-md bg-neutral-900 px-3 py-2 text-sm font-semibold text-white">
          Ask
        </button>
      </form>

      {queryMutation.isError ? (
        <p className="mt-3 text-sm text-red-700">{(queryMutation.error as Error).message}</p>
      ) : null}

      {queryMutation.data ? (
        <div className="mt-4">
          <div className="rounded-md bg-neutral-50 p-3 text-sm font-medium">
            {queryMutation.data.answer}
          </div>
          <details className="mt-3">
            <summary className="cursor-pointer text-sm font-semibold">Evidence</summary>
            <pre className="mt-2 max-h-80 overflow-auto rounded bg-neutral-900 p-3 text-xs text-white">
              {JSON.stringify(queryMutation.data.evidence, null, 2)}
            </pre>
          </details>
        </div>
      ) : null}
    </aside>
  );
}
