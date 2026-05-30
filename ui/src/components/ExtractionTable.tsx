import { REQUIRED_FIELDS, type ExtractionResult } from "../api/types";

function confidenceClass(confidence: number | null) {
  if (confidence === null || confidence < 0.6) return "bg-red-100 text-red-800";
  if (confidence < 0.85) return "bg-yellow-100 text-yellow-800";
  return "bg-green-100 text-green-800";
}

export function ExtractionTable({ extraction }: { extraction?: ExtractionResult | null }) {
  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <h2 className="mb-3 text-base font-semibold">Extracted fields</h2>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-neutral-200 text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-neutral-500">
              <th className="py-2 pr-4">Field</th>
              <th className="py-2 pr-4">Value</th>
              <th className="py-2 pr-4">Confidence</th>
              <th className="py-2 pr-4">Source snippet</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100">
            {REQUIRED_FIELDS.map((fieldName) => {
              const field = extraction?.fields[fieldName];
              const confidence = field?.is_present ? field.confidence : null;
              return (
                <tr key={fieldName}>
                  <td className="py-3 pr-4 font-medium">{fieldName}</td>
                  <td className="py-3 pr-4">
                    {field?.is_present ? field.value : "Not present in document."}
                  </td>
                  <td className="py-3 pr-4">
                    <span className={`rounded-full px-2 py-1 text-xs ${confidenceClass(confidence)}`}>
                      {confidence === null ? "missing" : confidence.toFixed(2)}
                    </span>
                  </td>
                  <td className="py-3 pr-4">
                    {field?.source_snippet ? (
                      <details>
                        <summary className="cursor-pointer text-neutral-600">View snippet</summary>
                        <p className="mt-2 max-w-xl whitespace-pre-wrap rounded bg-neutral-50 p-2 text-xs">
                          {field.source_snippet}
                        </p>
                      </details>
                    ) : (
                      <span className="text-neutral-400">No evidence snippet.</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
