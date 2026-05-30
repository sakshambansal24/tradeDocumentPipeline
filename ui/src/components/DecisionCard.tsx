import type { RouterDecision } from "../api/types";

function decisionClass(decision: string) {
  if (decision === "AUTO_APPROVE") return "bg-green-100 text-green-900";
  if (decision === "HUMAN_REVIEW") return "bg-yellow-100 text-yellow-900";
  return "bg-red-100 text-red-900";
}

export function DecisionCard({ decision }: { decision?: RouterDecision | null }) {
  if (!decision) {
    return (
      <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
        <h2 className="text-base font-semibold">Decision</h2>
        <p className="mt-2 text-sm text-neutral-500">No routing decision yet.</p>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-base font-semibold">Decision</h2>
        <span className={`rounded-full px-3 py-1 text-sm font-semibold ${decisionClass(decision.decision)}`}>
          {decision.decision.replace("_", " ").toLowerCase()}
        </span>
      </div>
      <p className="mt-3 text-sm text-neutral-700">{decision.reasoning}</p>

      {decision.decision === "AMEND" && decision.drafted_message ? (
        <div className="mt-4 rounded-md border border-neutral-200 bg-neutral-50 p-4">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold">Draft amendment email</h3>
            <button
              className="rounded border border-neutral-300 px-2 py-1 text-xs"
              onClick={() => navigator.clipboard.writeText(decision.drafted_message ?? "")}
            >
              Copy
            </button>
          </div>
          <pre className="whitespace-pre-wrap text-sm leading-6 text-neutral-800">
            {decision.drafted_message}
          </pre>
        </div>
      ) : null}

      {decision.risk_flags.length > 0 ? (
        <div className="mt-4">
          <h3 className="text-sm font-semibold">Risk flags</h3>
          <ul className="mt-2 list-disc pl-5 text-sm text-neutral-700">
            {decision.risk_flags.map((flag) => (
              <li key={flag}>{flag}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
