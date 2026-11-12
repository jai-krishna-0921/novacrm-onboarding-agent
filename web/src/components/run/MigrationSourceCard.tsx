import { useQueryClient } from "@tanstack/react-query";
import { DatabaseZap, Unplug } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, enc } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { qk, useMigrationSource } from "@/lib/queries";
import { MigrationSource } from "@/lib/schemas";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { Mono } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";

type Form = { host: string; port: string; database: string; username: string; password: string };
const EMPTY: Form = { host: "", port: "5432", database: "", username: "", password: "" };

/** The customer's own (old CRM) database for this run. The password lives only in local form state and the PUT
 * body: never in a query key, URL, log or toast, and it is cleared as soon as the request finishes. */
export function MigrationSourceCard({ runId }: { runId: string }) {
  const q = useMigrationSource(runId);
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState<Form>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<MigrationSource | null>(null);

  const refresh = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: qk.migSource(runId) }),
      qc.invalidateQueries({ queryKey: qk.run(runId) }),
      qc.invalidateQueries({ queryKey: qk.migSummary }),
    ]);

  const set = (k: keyof Form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const port = Number(form.port);
    if (!form.host.trim() || !form.database.trim() || !form.username.trim() || !form.password) {
      setError("Fill in host, database, username and password.");
      return;
    }
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      setError("Port must be a number between 1 and 65535.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const out = await api(`/runs/${enc(runId)}/migration-source`, MigrationSource, {
        method: "PUT",
        body: {
          host: form.host.trim(),
          port,
          database: form.database.trim(),
          username: form.username.trim(),
          password: form.password,
        },
      });
      setSaved(out);
      setForm(EMPTY);
      qc.setQueryData(qk.migSource(runId), out);
      toast({ title: "Customer database connected", body: "The next migration check reads it." });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setForm((f) => ({ ...f, password: "" }));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    try {
      await api(`/runs/${enc(runId)}/migration-source`, MigrationSource, { method: "DELETE" });
      setSaved(null);
      toast({ title: "Customer database disconnected", body: "The check uses the sample export again." });
    } catch (err) {
      toast({
        title: "Could not disconnect",
        body: err instanceof Error ? err.message : String(err),
        tone: "bad",
      });
    }
    await refresh();
  };

  const src = q.data;
  return (
    <Card>
      <CardHeader
        title="Customer database (source)"
        description={
          src?.configured
            ? "The migration check compares this database with what was imported into NovaCRM."
            : "Connect the customer's old CRM database (read-only user). Until then the check uses the sample export."
        }
        actions={
          src?.configured ? (
            <ConfirmDialog
              title="Disconnect the customer database?"
              description="The saved credentials are deleted. The migration check goes back to the sample export."
              confirmLabel="Disconnect"
              tone="danger"
              onConfirm={disconnect}
              trigger={(open) => (
                <Button variant="danger-ghost" size="sm" onClick={open}>
                  <Unplug className="size-4" aria-hidden />
                  Disconnect
                </Button>
              )}
            />
          ) : null
        }
      />
      <CardBody>
        {q.isPending ? (
          <Skeleton className="h-10 w-full" />
        ) : src?.configured ? (
          <div className="flex flex-col gap-1.5 text-sm text-ink-2">
            <div className="flex flex-wrap items-center gap-2">
              <DatabaseZap className="size-4 text-ok" aria-hidden />
              <Mono>{src.display}</Mono>
            </div>
            <span className="text-[13px] text-ink-3">
              Connected{src.by ? ` by ${src.by}` : ""}
              {src.saved_at ? ` at ${fmtDateTime(String(src.saved_at))}` : ""}
            </span>
            {saved?.rows != null ? (
              <span className="text-[13px] text-ink-3">
                Read {saved.rows} contacts
                {saved.columns?.length ? ` · columns: ${saved.columns.join(", ")}` : ""}
              </span>
            ) : null}
          </div>
        ) : (
          <form className="grid gap-3 sm:grid-cols-6" onSubmit={save} autoComplete="off" noValidate>
            <Field label="Host" className="sm:col-span-4">
              {(p) => <Input {...p} value={form.host} onChange={set("host")} placeholder="db.customer.com" />}
            </Field>
            <Field label="Port" className="sm:col-span-2">
              {(p) => <Input {...p} value={form.port} onChange={set("port")} inputMode="numeric" />}
            </Field>
            <Field label="Database" className="sm:col-span-2">
              {(p) => <Input {...p} value={form.database} onChange={set("database")} />}
            </Field>
            <Field label="Username" className="sm:col-span-2">
              {(p) => <Input {...p} value={form.username} onChange={set("username")} autoComplete="off" />}
            </Field>
            <Field label="Password" className="sm:col-span-2">
              {(p) => (
                <Input
                  {...p}
                  type="password"
                  autoComplete="off"
                  value={form.password}
                  onChange={set("password")}
                />
              )}
            </Field>
            <div className="flex flex-wrap items-center gap-3 sm:col-span-6">
              <Button type="submit" variant="primary" loading={busy}>
                Test connection & save
              </Button>
              {error ? (
                <p role="alert" className="text-[13px] text-bad">
                  {error}
                </p>
              ) : null}
            </div>
          </form>
        )}
      </CardBody>
    </Card>
  );
}
