import type { CustomerSummary } from "../api/types";

interface UploadBarProps {
  customers: CustomerSummary[];
  selectedCustomerId: string;
  selectedFile: File | null;
  isRunning: boolean;
  progress: number;
  onCustomerChange: (customerId: string) => void;
  onFileChange: (file: File | null) => void;
  onRun: () => void;
}

export function UploadBar({
  customers,
  selectedCustomerId,
  selectedFile,
  isRunning,
  progress,
  onCustomerChange,
  onFileChange,
  onRun
}: UploadBarProps) {
  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-3 md:flex-row md:items-end">
        <label className="flex-1 text-sm font-medium text-neutral-700">
          Document
          <input
            className="mt-2 block w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm"
            type="file"
            accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff"
            onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
          />
        </label>

        <label className="w-full text-sm font-medium text-neutral-700 md:w-72">
          Customer
          <select
            className="mt-2 block w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm"
            value={selectedCustomerId}
            onChange={(event) => onCustomerChange(event.target.value)}
          >
            <option value="">Select customer</option>
            {customers.map((customer) => (
              <option key={customer.customer_id} value={customer.customer_id}>
                {customer.name}
              </option>
            ))}
          </select>
        </label>

        <button
          className="rounded-md bg-neutral-900 px-4 py-2 text-sm font-semibold text-white"
          disabled={!selectedFile || !selectedCustomerId || isRunning}
          onClick={onRun}
        >
          {isRunning ? "Running..." : "Run pipeline"}
        </button>
      </div>

      {selectedFile ? (
        <p className="mt-3 text-xs text-neutral-500">
          Selected: {selectedFile.name} ({Math.ceil(selectedFile.size / 1024)} KB)
        </p>
      ) : null}

      {isRunning || progress > 0 ? (
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-neutral-200">
          <div className="h-full bg-neutral-900 transition-all" style={{ width: `${progress}%` }} />
        </div>
      ) : null}
    </section>
  );
}
