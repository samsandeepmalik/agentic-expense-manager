import { useMemo, useState, useEffect } from "react";
import type { Category } from "../api";

interface Props {
  categories: Category[];
  type: "income" | "expense";
  valueId: number | null;
  onChange: (categoryId: number | null) => void;
  /** Always render the sub-category slot, even before a parent is picked or
   *  when the parent has no children — so the sub-category step is discoverable
   *  (e.g. in the bulk recategorize bar). Disabled until a parent with
   *  children is selected. */
  alwaysShowSub?: boolean;
}

export function CategoryPicker({ categories, type, valueId, onChange, alwaysShowSub = false }: Props) {
  const tops = useMemo(
    () => categories.filter((c) => c.parent_id === 0 && c.type === type),
    [categories, type]
  );

  // Derive initial top + sub from valueId
  const deriveState = (id: number | null): { topId: number | ""; subId: number | "" } => {
    if (id === null) return { topId: "", subId: "" };
    const cat = categories.find((c) => c.id === id);
    if (!cat) return { topId: "", subId: "" };
    if (cat.parent_id === 0) return { topId: cat.id, subId: "" };
    return { topId: cat.parent_id, subId: cat.id };
  };

  const initial = deriveState(valueId);
  const [topId, setTopId] = useState<number | "">(initial.topId);
  const [subId, setSubId] = useState<number | "">(initial.subId);

  // Sync when valueId changes from outside (edit mode re-init)
  useEffect(() => {
    const d = deriveState(valueId);
    setTopId(d.topId);
    setSubId(d.subId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [valueId]);

  const children = useMemo(
    () => (topId !== "" ? categories.filter((c) => c.parent_id === topId) : []),
    [categories, topId]
  );
  const hasChildren = children.length > 0;

  const emit = (newTopId: number | "", newSubId: number | "") => {
    const effective = newSubId !== "" ? newSubId : newTopId !== "" ? newTopId : null;
    onChange(effective as number | null);
  };

  const handleTop = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value === "" ? "" : Number(e.target.value);
    setTopId(val as number | "");
    setSubId("");
    emit(val as number | "", "");
  };

  const handleSub = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value === "" ? "" : Number(e.target.value);
    setSubId(val as number | "");
    emit(topId, val as number | "");
  };

  return (
    <div style={{ display: "flex", gap: 8 }}>
      <select value={topId} onChange={handleTop} style={{ flex: 1 }}>
        <option value="">Category…</option>
        {tops.map((c) => (
          <option key={c.id} value={c.id}>{c.name}</option>
        ))}
      </select>
      {(hasChildren || alwaysShowSub) && (
        <select value={subId} onChange={handleSub} disabled={!hasChildren}
                title={!hasChildren
                  ? (topId === "" ? "Pick a category first" : "No sub-categories")
                  : undefined}
                style={{ flex: 1 }}>
          <option value="">
            {topId === "" ? "Sub-category…" : hasChildren ? "— none —" : "no sub-categories"}
          </option>
          {children.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      )}
    </div>
  );
}
