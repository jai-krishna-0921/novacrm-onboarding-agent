import { useState } from "react";
import { fmtDateTime, fmtInt, fmtMs, fmtTime, fmtUsd } from "@/lib/format";
import { qs, useToolCalls } from "@/lib/queries";
import type { RunDetail, ToolCallRow } from "@/lib/schemas";
import { Badge, TipBadge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { KeyValues, Mono, Stat } from "@/components/ui/misc";
import { clampPage, Pagination, usePaging } from "@/components/ui/pagination";
import { SkeletonRows } from "@/components/ui/skeleton";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { Tip } from "@/components/ui/tooltip";
import { ToolCallSheet, toolTone } from "./ToolCallSheet";

export function AiToolsTab({ data }: { data: RunDetail }) {
  const llm = data.llm;
  const models = [...new Set(llm.by_node.map((r) => r.model).filter(Boolean))] as string[];
  const llmPaging = usePaging("run-llm");
  const llmRows = [...llm.by_node].reverse();
  const lp = clampPage(llmPaging.page, llmRows.length, llmPaging.size);
  const toolPaging = usePaging("run-tools");
  const q = qs({ run_id: data.run.id, limit: toolPaging.size, offset: toolPaging.offset });
  const tools = useToolCalls(q, true);
  const [open, setOpen] = useState<ToolCallRow | null>(null);

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Stat label="LLM calls" value={fmtInt(llm.calls)} />
        <Stat label="Tokens in / out" value={`${fmtInt(llm.input_tokens)} / ${fmtInt(llm.output_tokens)}`} />
        <Stat
          label="LLM cost (est.)"
          value={fmtUsd(llm.cost_usd)}
          tip="Estimated from list prices per million tokens. Not a bill."
        />
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm text-ink-3">
        Models used:
        {models.length ? (
          models.map((m) => (
            <Badge key={m} tone="accent" mono>
              {m}
            </Badge>
          ))
        ) : (
          <span>none yet</span>
        )}
      </div>

      <section>
        <h3 className="mb-2 text-[14px] font-semibold text-ink">LLM calls ({llmRows.length})</h3>
        {llmRows.length ? (
          <div className="-mx-5">
            <Table>
              <thead>
                <tr>
                  <Th>Time</Th>
                  <Th>Agent / step</Th>
                  <Th className="hidden md:table-cell">Model</Th>
                  <Th className="hidden lg:table-cell">Prompt</Th>
                  <Th className="text-right">Tokens in / out</Th>
                  <Th className="text-right">Latency</Th>
                  <Th className="text-right">Cost</Th>
                </tr>
              </thead>
              <tbody>
                {llmRows.slice(lp * llmPaging.size, (lp + 1) * llmPaging.size).map((r, i) => (
                  <Tr key={`${r.ts}-${i}`}>
                    <Td className="whitespace-nowrap">
                      <Tip content={fmtDateTime(r.ts)}>
                        <span tabIndex={0}>{fmtTime(r.ts)}</span>
                      </Tip>
                    </Td>
                    <Td>
                      <Mono>
                        {r.agent} / {r.node}
                      </Mono>
                    </Td>
                    <Td className="hidden md:table-cell">
                      <Mono className="text-ink-2">{r.model}</Mono>
                    </Td>
                    <Td className="hidden lg:table-cell">
                      <Mono className="text-ink-2">{r.prompt_version}</Mono>
                    </Td>
                    <Td className="tabular text-right whitespace-nowrap">
                      {fmtInt(r.input_tokens)} / {fmtInt(r.output_tokens)}
                    </Td>
                    <Td className="tabular text-right whitespace-nowrap">{fmtMs(r.latency_ms)}</Td>
                    <Td className="tabular text-right whitespace-nowrap">
                      <Tip content={r.stop_reason ? `Stop reason: ${r.stop_reason}` : "Estimated cost"}>
                        <span tabIndex={0}>{fmtUsd(r.cost_usd, 5)}</span>
                      </Tip>
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
            <Pagination
              total={llmRows.length}
              page={lp}
              size={llmPaging.size}
              onPage={llmPaging.setPage}
              onSize={llmPaging.setSize}
              noun="calls"
            />
          </div>
        ) : (
          <p className="text-sm text-ink-3">No LLM calls yet.</p>
        )}
      </section>

      <section>
        <h3 className="mb-1 text-[14px] font-semibold text-ink">
          Tool calls ({tools.data?.total ?? data.tool_calls.length})
        </h3>
        <p className="mb-2 text-[13px] text-ink-3">
          Every external action goes through the tool registry. Select a row for its input and output.
        </p>
        <div className="-mx-5">
          {tools.isPending ? (
            <SkeletonRows rows={3} className="px-5" />
          ) : tools.data?.rows.length ? (
            <>
              <Table>
                <thead>
                  <tr>
                    <Th>Time</Th>
                    <Th className="hidden sm:table-cell">Agent</Th>
                    <Th>Tool</Th>
                    <Th>Result</Th>
                    <Th className="text-right">Duration</Th>
                  </tr>
                </thead>
                <tbody>
                  {tools.data.rows.map((t) => (
                    <Tr key={t.id ?? t.ts} onActivate={() => setOpen(t)} current={open?.id === t.id}>
                      <Td className="whitespace-nowrap">{fmtTime(t.ts)}</Td>
                      <Td className="hidden sm:table-cell">{t.agent}</Td>
                      <Td>
                        <Mono>{t.tool}</Mono>
                      </Td>
                      <Td>
                        <TipBadge
                          tone={toolTone(t.status)}
                          tip={t.attempt && t.attempt > 1 ? `Attempt ${t.attempt}` : "First attempt"}
                        >
                          {t.status}
                        </TipBadge>
                      </Td>
                      <Td className="tabular text-right">{fmtMs(t.latency_ms)}</Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
              <Pagination
                total={tools.data.total}
                page={toolPaging.page}
                size={toolPaging.size}
                onPage={toolPaging.setPage}
                onSize={toolPaging.setSize}
                noun="calls"
              />
            </>
          ) : (
            <EmptyState title="No tool calls yet" />
          )}
        </div>
      </section>

      <KeyValues
        items={[
          ["Run id", <Mono key="id">{data.run.id}</Mono>],
          [
            "Gmail message",
            data.run.gmail_message_id ? <Mono key="g">{data.run.gmail_message_id}</Mono> : null,
          ],
        ]}
      />
      <ToolCallSheet row={open} onClose={() => setOpen(null)} showRunLink={false} />
    </div>
  );
}
