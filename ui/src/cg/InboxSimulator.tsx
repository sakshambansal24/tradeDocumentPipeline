import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { simulateUploadedMail } from "../api/client";
import type { CustomerSummary } from "../api/types";

type AttachmentDraft = {
  id: number;
  filename: string;
  file: File | null;
};

const DEFAULT_ATTACHMENTS: AttachmentDraft[] = [
  { id: 1, filename: "bill_of_lading.pdf", file: null },
  { id: 2, filename: "commercial_invoice.pdf", file: null }
];

export function InboxSimulator({ customers }: { customers: CustomerSummary[] }) {
  const queryClient = useQueryClient();
  const [customerId, setCustomerId] = useState(customers[0]?.customer_id ?? "acme_corp");
  const [sender, setSender] = useState("supplier@example.com");
  const [recipient, setRecipient] = useState("cg@gocomet.local");
  const [subject, setSubject] = useState("Shipment docs - ACME shipment for review");
  const [body, setBody] = useState(
    "Dear CG Team,\n\nPlease find attached the shipment documents for review.\n\nRegards,\nSupplier"
  );
  const [attachments, setAttachments] = useState<AttachmentDraft[]>(DEFAULT_ATTACHMENTS);
  const canSend =
    sender.trim() &&
    recipient.trim() &&
    subject.trim() &&
    body.trim() &&
    customerId.trim() &&
    attachments.length > 0 &&
    attachments.every((attachment) => attachment.filename.trim() && attachment.file);

  const mutation = useMutation({
    mutationFn: () => {
      return simulateUploadedMail({
        emailId: `ui-sim-${Date.now()}`,
        sender,
        recipient,
        subject,
        customerId,
        attachments: attachments.map((attachment) => ({
          filename: attachment.filename,
          file: attachment.file as File
        })),
        body
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["shipments"] });
    }
  });

  return (
    <section className="rounded-lg border border-dashed border-blue-300 bg-blue-50/60 p-4">
      <div className="mb-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-blue-700">
          Local mail thread simulator
        </p>
        <p className="text-sm text-blue-950">
          Send a local MIME email into the CG mailbox. The mail listener reads the thread,
          extracts attachments, and starts the pipeline.
        </p>
        <div className="mt-2 grid gap-2 text-xs text-blue-900 md:grid-cols-3">
          <span className="rounded bg-white/70 px-2 py-1">Inbox: inbox/mail/incoming</span>
          <span className="rounded bg-white/70 px-2 py-1">Processed: inbox/mail/processed</span>
          <span className="rounded bg-white/70 px-2 py-1">Replies: inbox/mail/sent</span>
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-sm">
          <span className="mb-1 block font-medium text-neutral-700">From</span>
          <input
            className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2"
            onChange={(event) => setSender(event.target.value)}
            placeholder="supplier@example.com"
            value={sender}
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium text-neutral-700">To</span>
          <input
            className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2"
            onChange={(event) => setRecipient(event.target.value)}
            placeholder="cg@gocomet.local"
            value={recipient}
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium text-neutral-700">Subject</span>
          <input
            className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2"
            onChange={(event) => setSubject(event.target.value)}
            placeholder="Shipment docs - ACME shipment for review"
            value={subject}
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium text-neutral-700">Customer</span>
          <select
            className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2"
            value={customerId}
            onChange={(event) => setCustomerId(event.target.value)}
          >
            {(customers.length ? customers : [{ customer_id: "acme_corp", name: "ACME Corp" }]).map(
              (customer) => (
                <option key={customer.customer_id} value={customer.customer_id}>
                  {customer.name ?? customer.customer_id}
                </option>
              )
            )}
          </select>
        </label>
      </div>

      <label className="mt-3 block text-sm">
        <span className="mb-1 block font-medium text-neutral-700">Email body</span>
        <textarea
          className="min-h-[120px] w-full rounded-md border border-neutral-300 bg-white px-3 py-2"
          onChange={(event) => setBody(event.target.value)}
          placeholder="Write the supplier email body here..."
          value={body}
        />
      </label>

      <div className="mt-4 rounded-lg border border-blue-200 bg-white/60">
        <div className="flex items-center justify-between gap-3 border-b border-blue-100 px-3 py-2">
          <div>
            <h3 className="text-sm font-semibold text-blue-950">Attachments</h3>
            <p className="text-xs text-blue-800">
              Upload documents and set the filenames that will appear in the email.
            </p>
          </div>
          <button
            className="rounded-md border border-blue-300 px-3 py-2 text-xs font-semibold text-blue-800 hover:bg-blue-50"
            onClick={() => {
              setAttachments((current) => [
                ...current,
                {
                  id: Date.now(),
                  filename: `shipment_document_${current.length + 1}.pdf`,
                  file: null
                }
              ]);
            }}
            type="button"
          >
            Add document
          </button>
        </div>
        <div className="space-y-3 p-3">
          {attachments.map((attachment, index) => (
            <div className="grid gap-2 md:grid-cols-[1fr_1fr_auto]" key={attachment.id}>
              <label className="text-sm">
                <span className="mb-1 block font-medium text-neutral-700">
                  Attachment name {index + 1}
                </span>
                <input
                  className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2"
                  onChange={(event) =>
                    setAttachments((current) =>
                      current.map((candidate) =>
                        candidate.id === attachment.id
                          ? { ...candidate, filename: event.target.value }
                          : candidate
                      )
                    )
                  }
                  placeholder="commercial_invoice.pdf"
                  value={attachment.filename}
                />
              </label>
              <label className="text-sm">
                <span className="mb-1 block font-medium text-neutral-700">Upload document</span>
                <input
                  accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff"
                  className="w-full rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm"
                  onChange={(event) =>
                    setAttachments((current) =>
                      current.map((candidate) =>
                        candidate.id === attachment.id
                          ? {
                              ...candidate,
                              file: event.target.files?.[0] ?? null,
                              filename: event.target.files?.[0]?.name ?? candidate.filename
                            }
                          : candidate
                      )
                    )
                  }
                  type="file"
                />
              </label>
              <button
                className="self-end rounded-md border border-red-200 px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50"
                disabled={attachments.length === 1}
                onClick={() =>
                  setAttachments((current) =>
                    current.filter((candidate) => candidate.id !== attachment.id)
                  )
                }
                type="button"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-4 flex justify-end">
        <button
          className="rounded-md bg-blue-700 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-800 disabled:opacity-60"
          disabled={mutation.isPending || !canSend}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending ? "Sending..." : "Send local email"}
        </button>
      </div>
      {mutation.data ? (
        <div className="mt-3 rounded-md bg-white/70 p-3 text-sm text-blue-900">
          <p>{mutation.data.message} It may appear after the mail listener&apos;s next pass.</p>
          <p className="mt-1 font-mono text-xs">Message-ID: {mutation.data.message_id}</p>
          <p className="mt-1 font-mono text-xs">Mailbox file: {mutation.data.mailbox_path}</p>
        </div>
      ) : null}
      {mutation.isError ? (
        <p className="mt-3 text-sm text-red-700">{(mutation.error as Error).message}</p>
      ) : null}
    </section>
  );
}
