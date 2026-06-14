import type { Category } from "../api";

/**
 * Render <option>s for a category <select>, grouping sub-categories indented
 * under their parent (parent, then "  ↳ child"). The option VALUE is always the
 * category name, because the transaction API references categories by name.
 * `cats` should already be filtered (e.g. by type) by the caller.
 */
export function categoryOptions(cats: Category[]) {
  const childrenOf = (id: number) => cats.filter((c) => c.parent_id === id);
  const rendered = new Set<number>();
  const out = cats
    .filter((c) => c.parent_id === 0)
    .flatMap((top) => {
      rendered.add(top.id);
      const kids = childrenOf(top.id);
      kids.forEach((k) => rendered.add(k.id));
      return [
        <option key={top.id} value={top.name}>{top.name}</option>,
        ...kids.map((k) => (
          <option key={k.id} value={k.name}>{"  ↳ "}{k.name}</option>
        )),
      ];
    });
  // Orphans: children whose parent was filtered out (e.g. different type) —
  // render flat so they remain selectable.
  const orphans = cats.filter((c) => !rendered.has(c.id));
  return [
    ...out,
    ...orphans.map((c) => <option key={c.id} value={c.name}>{c.name}</option>),
  ];
}
