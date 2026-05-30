import type { FieldValidationStatus, ValidationResult } from "../api/types";

function statusClass(status: FieldValidationStatus) {
  if (status === "MATCH") return "bg-green-100 text-green-800";
  if (status === "UNCERTAIN") return "bg-yellow-100 text-yellow-800";
  return "bg-red-100 text-red-800";
}

export function ValidationTable({ validation }: { validation?: ValidationResult | null }) {
  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-base font-semibold">Validation results</h2>
        {validation ? (
          <span className="text-xs text-neutral-500">
            Confidence {validation.validator_confidence.toFixed(2)}
          </span>
        ) : null}
      </div>
      {!validation ? (
        <p className="text-sm text-neutral-500">No validation result yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-neutral-200 text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-neutral-500">
                <th className="py-2 pr-4">Field</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Found</th>
                <th className="py-2 pr-4">Expected</th>
                <th className="py-2 pr-4">Rule</th>
                <th className="py-2 pr-4">Reasoning</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {validation.field_results.map((field) => (
                <tr key={`${field.field_name}-${field.expected_rule}`}>
                  <td className="py-3 pr-4 font-medium">{field.field_name}</td>
                  <td className="py-3 pr-4">
                    <span className={`rounded-full px-2 py-1 text-xs ${statusClass(field.status)}`}>
                      {field.status}
                    </span>
                  </td>
                  <td className="py-3 pr-4">{field.found_value ?? "Not present"}</td>
                  <td className="py-3 pr-4">{field.expected_value ?? "None"}</td>
                  <td className="py-3 pr-4 text-xs text-neutral-600">{field.expected_rule}</td>
                  <td className="py-3 pr-4 text-neutral-700">{field.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
