import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Crumb = { label: string; to?: string };
export type PageMeta = { title: string; crumbs?: Crumb[] };

const Ctx = createContext<{ meta: PageMeta; set: (m: PageMeta) => void }>({
  meta: { title: "" },
  set: () => {},
});

export function PageMetaProvider({ children }: { children: ReactNode }) {
  const [meta, set] = useState<PageMeta>({ title: "" });
  return <Ctx.Provider value={{ meta, set }}>{children}</Ctx.Provider>;
}

export const usePageMetaValue = () => useContext(Ctx).meta;

/** Pages declare their title and breadcrumbs for the top bar and the document title. */
export function usePageMeta(title: string, crumbs?: Crumb[]) {
  const { set } = useContext(Ctx);
  const key = JSON.stringify(crumbs ?? []);
  useEffect(() => {
    set({ title, crumbs: JSON.parse(key) as Crumb[] });
    document.title = title ? `${title} | NovaCRM Onboarding` : "NovaCRM Onboarding";
  }, [title, key, set]);
}
