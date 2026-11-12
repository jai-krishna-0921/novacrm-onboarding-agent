import { useQueryClient } from "@tanstack/react-query";
import { Info, Pencil, Plus, Search, Users } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { enc } from "@/lib/api";
import { cn } from "@/lib/cn";
import { ago, fmtDateTime } from "@/lib/format";
import { aeSaveError, normalisePhone, phoneHint, validateAe, type AeErrors, type AeForm } from "@/lib/phone";
import { patch, post, qk, useAes } from "@/lib/queries";
import type { Ae } from "@/lib/schemas";
import { TipBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { EmptyState, ErrorBox } from "@/components/ui/empty-state";
import { Field, Input } from "@/components/ui/input";
import { Mono } from "@/components/ui/misc";
import { clampPage, Pagination, usePaging } from "@/components/ui/pagination";
import { Select } from "@/components/ui/select";
import { Sheet } from "@/components/ui/sheet";
import { SkeletonRows } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { SortTh, sortBy, Table, Td, Th, Tr, type Sort } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { Tip } from "@/components/ui/tooltip";
import { usePageMeta } from "@/components/layout/page-meta";

type Key = "name" | "email" | "updated";

export function AccountExecutivesPage() {
  usePageMeta("Account Executives");
  const q = useAes();
  const [search, setSearch] = useState("");
  const [show, setShow] = useState("");
  const [sort, setSort] = useState<Sort<Key>>({ key: "name", dir: "asc" });
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Ae | null>(null);
  const [toggling, setToggling] = useState<Ae | null>(null);
  const paging = usePaging("aes");

  const rows = useMemo(() => {
    const s = search.trim().toLowerCase();
    const list = (q.data ?? []).filter(
      (a) =>
        (!show || (show === "active" ? a.active : !a.active)) &&
        (!s || a.name.toLowerCase().includes(s) || a.email.toLowerCase().includes(s)),
    );
    return sortBy(list, sort, (a, k) =>
      k === "name" ? a.name.toLowerCase() : k === "email" ? a.email : a.updated_at,
    );
  }, [q.data, search, show, sort]);
  const page = clampPage(paging.page, rows.length, paging.size);
  const shown = rows.slice(page * paging.size, (page + 1) * paging.size);
  const activeCount = (q.data ?? []).filter((a) => a.active).length;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start gap-3 rounded-card border border-accent-line bg-accent-soft px-4 py-3 text-sm text-ink-2">
        <Info className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
        <p>
          Only <span className="font-medium text-ink">active</span> AEs can trigger onboarding. A deal email
          from an unknown or inactive sender is not processed, and the voice agent calls the phone number
          listed here to confirm the tier.
        </p>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative w-full sm:w-64">
            <Search
              className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-ink-3"
              aria-hidden
            />
            <Input
              type="search"
              aria-label="Search AEs"
              placeholder="Name or email"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                paging.setPage(0);
              }}
              className="pl-8"
            />
          </div>
          <Select
            label="Show"
            allLabel="All AEs"
            value={show}
            onChange={(v) => {
              setShow(v);
              paging.setPage(0);
            }}
            options={[
              { value: "active", label: "Active only" },
              { value: "inactive", label: "Inactive only" },
            ]}
            className="w-40"
          />
        </div>
        <Button variant="primary" onClick={() => setAdding(true)}>
          <Plus className="size-4" aria-hidden />
          Add AE
        </Button>
      </div>

      <Card className="overflow-hidden">
        {q.isPending ? (
          <SkeletonRows rows={5} className="p-5" />
        ) : q.isError ? (
          <div className="p-5">
            <ErrorBox error={q.error} />
          </div>
        ) : !shown.length ? (
          <EmptyState
            icon={Users}
            title={q.data.length ? "No AEs match" : "No AEs yet"}
            action={
              q.data.length ? null : (
                <Button variant="primary" onClick={() => setAdding(true)}>
                  <Plus className="size-4" aria-hidden />
                  Add AE
                </Button>
              )
            }
          >
            {q.data.length
              ? "Try another search or filter."
              : "Add the account executives whose deal emails should start onboarding."}
          </EmptyState>
        ) : (
          <Table>
            <thead>
              <tr>
                <SortTh label="Name" k="name" sort={sort} onSort={setSort} />
                <SortTh
                  label="Email"
                  k="email"
                  sort={sort}
                  onSort={setSort}
                  className="hidden md:table-cell"
                />
                <Th className="hidden sm:table-cell">Phone</Th>
                <Th>Active</Th>
                <SortTh
                  label="Updated"
                  k="updated"
                  sort={sort}
                  onSort={setSort}
                  className="hidden text-right lg:table-cell"
                />
                <Th className="w-12">
                  <span className="sr-only">Edit</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {shown.map((a) => (
                <Tr key={a.email} className={cn(!a.active && "text-ink-3")}>
                  <Td>
                    <div className={cn("font-medium", a.active ? "text-ink" : "text-ink-3")}>{a.name}</div>
                    <div className="text-[13px] text-ink-3 md:hidden">{a.email}</div>
                  </Td>
                  <Td className="hidden md:table-cell">{a.email}</Td>
                  <Td className="hidden sm:table-cell">
                    <Tip content="Masked to the last 4 digits. The full number is only used to place the call.">
                      <span tabIndex={0}>
                        <Mono>{a.phone_masked || "-"}</Mono>
                      </span>
                    </Tip>
                  </Td>
                  <Td>
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={a.active}
                        onCheckedChange={() => setToggling(a)}
                        aria-label={`${a.active ? "Deactivate" : "Activate"} ${a.name}`}
                      />
                      <TipBadge
                        tone={a.active ? "ok" : "idle"}
                        tip={
                          a.active
                            ? "Deal emails from this AE start onboarding."
                            : "Deal emails from this AE are ignored."
                        }
                        className="hidden sm:inline-flex"
                      >
                        {a.active ? "Active" : "Inactive"}
                      </TipBadge>
                    </div>
                  </Td>
                  <Td className="hidden text-right lg:table-cell">
                    <Tip content={fmtDateTime(a.updated_at)}>
                      <span tabIndex={0} className="text-[13px] text-ink-3">
                        {a.updated_at ? ago(a.updated_at) : "-"}
                      </span>
                    </Tip>
                  </Td>
                  <Td>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Edit ${a.name}`}
                      onClick={() => setEditing(a)}
                    >
                      <Pencil className="size-4" aria-hidden />
                    </Button>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}
        <Pagination
          total={rows.length}
          page={page}
          size={paging.size}
          onPage={paging.setPage}
          onSize={paging.setSize}
          noun="AEs"
        />
      </Card>
      <p className="text-[13px] text-ink-3">
        {activeCount} of {q.data?.length ?? 0} AEs active.
      </p>

      <AddAeDialog open={adding} onOpenChange={setAdding} />
      <EditAeSheet ae={editing} onClose={() => setEditing(null)} />
      <ToggleDialog ae={toggling} onClose={() => setToggling(null)} />
    </div>
  );
}

function PhoneField({
  value,
  onChange,
  error,
  label = "Phone",
}: {
  value: string;
  onChange: (v: string) => void;
  error?: string;
  label?: string;
}) {
  const hint = phoneHint(value);
  return (
    <Field
      label={label}
      error={error}
      hint={value.trim() ? hint.text : "Include the country code, for example +91 97899 98350."}
      hintTone={hint.ok ? "ok" : "muted"}
    >
      {(p) => (
        <Input
          {...p}
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="+91 97899 98350"
        />
      )}
    </Field>
  );
}

function AddAeDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const empty: AeForm = { email: "", name: "", phone: "", active: true };
  const [f, setF] = useState<AeForm>(empty);
  const [errors, setErrors] = useState<AeErrors>({});
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof AeForm, v: string | boolean) => {
    setF((x) => ({ ...x, [k]: v }));
    setErrors((e) => ({ ...e, [k]: undefined }));
  };
  const close = (o: boolean) => {
    if (busy) return;
    onOpenChange(o);
    if (!o) {
      setF(empty);
      setErrors({});
      setFormError("");
    }
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const errs = validateAe(f);
    setErrors(errs);
    setFormError("");
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      await post("/aes", {
        email: f.email.trim().toLowerCase(),
        name: f.name.trim(),
        phone: normalisePhone(f.phone),
        active: f.active,
      });
      toast({
        title: "AE added",
        body: `${f.name.trim()} can now trigger onboarding${f.active ? "" : " once activated"}.`,
      });
      await qc.invalidateQueries({ queryKey: qk.aes });
      setBusy(false);
      close(false);
    } catch (err) {
      const r = aeSaveError(err);
      if (r.field) setErrors((x) => ({ ...x, [r.field!]: r.message }));
      else setFormError(r.message);
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title="Add an Account Executive"
      description="Deal emails from this address start onboarding while the AE is active."
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-4 pb-2">
        <Field label="Work email" error={errors.email}>
          {(p) => (
            <Input
              {...p}
              type="email"
              autoComplete="email"
              value={f.email}
              onChange={(e) => set("email", e.target.value)}
              placeholder="name@novacrm.com"
            />
          )}
        </Field>
        <Field label="Full name" error={errors.name}>
          {(p) => (
            <Input {...p} autoComplete="name" value={f.name} onChange={(e) => set("name", e.target.value)} />
          )}
        </Field>
        <PhoneField value={f.phone} onChange={(v) => set("phone", v)} error={errors.phone} />
        <label className="flex items-center justify-between gap-3 rounded-ctl border border-line px-3 py-2.5">
          <span>
            <span className="block text-sm font-medium text-ink">Active</span>
            <span className="block text-[12.5px] text-ink-3">Only active AEs can trigger onboarding.</span>
          </span>
          <Switch checked={f.active} onCheckedChange={(v) => set("active", v)} aria-label="Active" />
        </label>
        {formError ? <ErrorBox error={formError} /> : null}
        <div className="flex justify-end gap-2 border-t border-line pt-4">
          <Button variant="secondary" onClick={() => close(false)} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={busy}>
            Add AE
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function EditAeSheet({ ae, onClose }: { ae: Ae | null; onClose: () => void }) {
  return (
    <Sheet
      open={!!ae}
      onOpenChange={(o) => !o && onClose()}
      title={ae?.name ?? "Edit AE"}
      description={ae?.email}
    >
      {ae ? <EditAeForm key={ae.email} ae={ae} onDone={onClose} /> : null}
    </Sheet>
  );
}

function EditAeForm({ ae, onDone }: { ae: Ae; onDone: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [name, setName] = useState(ae.name);
  const [phone, setPhone] = useState("");
  const [errors, setErrors] = useState<AeErrors>({});
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const errs = validateAe(
      { email: ae.email, name, phone, active: ae.active },
      { requireEmail: false, requirePhone: false },
    );
    setErrors(errs);
    setFormError("");
    if (Object.keys(errs).length) return;
    const body: Record<string, string> = {};
    if (name.trim() !== ae.name) body.name = name.trim();
    if (phone.trim()) body.phone = normalisePhone(phone);
    if (!Object.keys(body).length) {
      onDone();
      return;
    }
    setBusy(true);
    try {
      await patch(`/aes/${enc(ae.email)}`, body);
      toast({ title: "AE updated", body: name.trim() });
      await qc.invalidateQueries({ queryKey: qk.aes });
      onDone();
    } catch (err) {
      const r = aeSaveError(err);
      if (r.field) setErrors((x) => ({ ...x, [r.field!]: r.message }));
      else setFormError(r.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      <Field label="Work email" hint="The email is the AE's id and cannot be changed. Add a new AE instead.">
        {(p) => <Input {...p} value={ae.email} disabled />}
      </Field>
      <Field label="Full name" error={errors.name}>
        {(p) => (
          <Input
            {...p}
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setErrors((x) => ({ ...x, name: undefined }));
            }}
          />
        )}
      </Field>
      <div>
        <div className="mb-1.5 text-[13px] text-ink-3">
          Current phone <Mono className="text-ink-2">{ae.phone_masked || "-"}</Mono>
        </div>
        <PhoneField
          label="New phone (leave empty to keep)"
          value={phone}
          onChange={(v) => {
            setPhone(v);
            setErrors((x) => ({ ...x, phone: undefined }));
          }}
          error={errors.phone}
        />
      </div>
      {formError ? <ErrorBox error={formError} /> : null}
      <div className="flex justify-end gap-2 border-t border-line pt-4">
        <Button variant="secondary" onClick={onDone} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={busy}>
          Save changes
        </Button>
      </div>
    </form>
  );
}

function ToggleDialog({ ae, onClose }: { ae: Ae | null; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const next = ae ? !ae.active : false;
  const go = async () => {
    if (!ae) return;
    setBusy(true);
    setError("");
    try {
      await patch(`/aes/${enc(ae.email)}`, { active: next });
      toast({ title: next ? "AE activated" : "AE deactivated", body: ae.name });
      await qc.invalidateQueries({ queryKey: qk.aes });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={!!ae}
      onOpenChange={(o) => !o && !busy && (setError(""), onClose())}
      title={next ? `Activate ${ae?.name ?? ""}?` : `Deactivate ${ae?.name ?? ""}?`}
      description={
        next
          ? "New deal emails from this AE will start onboarding."
          : "New deal emails from this AE will be ignored. Runs already in progress are not affected."
      }
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Keep as is
          </Button>
          <Button variant={next ? "primary" : "danger"} onClick={go} loading={busy}>
            {next ? "Activate" : "Deactivate"}
          </Button>
        </>
      }
    >
      {error ? <ErrorBox error={error} /> : null}
    </Dialog>
  );
}
