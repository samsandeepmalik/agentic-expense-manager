import { useEffect, useState } from "react";
import { deleteJson, getJson, postJson, type Category } from "../api";

export function Categories() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [name, setName] = useState("");
  const [type, setType] = useState<"income" | "expense">("expense");
  const [percent, setPercent] = useState("100");
  const [error, setError] = useState("");

  const load = () =>
    getJson<Category[]>("/api/categories")
      .then(setCategories)
      .catch((err) => setError(String(err)));

  useEffect(() => {
    load();
  }, []);

  async function save() {
    if (!name.trim()) return;
    try {
      await postJson("/api/categories", {
        name: name.trim(),
        type,
        percent: Number(percent) || 100,
      });
      setName("");
      setPercent("100");
      await load();
    } catch (err) {
      setError(String(err));
    }
  }

  async function remove(categoryName: string) {
    await deleteJson(`/api/categories/${encodeURIComponent(categoryName)}`);
    await load();
  }

  async function updatePercent(category: Category, value: string) {
    const parsed = Number(value);
    if (Number.isNaN(parsed)) return;
    await postJson("/api/categories", { ...category, percent: parsed });
    await load();
  }

  return (
    <div className="panel">
      <h2>Categories</h2>
      <p className="hint">
        Percent = counting formula: how much of each transaction's total counts
        toward summaries (default 100%). E.g. set Dining to 50 to count half.
      </p>
      {error && <div className="error">{error}</div>}

      <div className="category-form">
        <input
          placeholder="Category name"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <select value={type} onChange={(event) => setType(event.target.value as "income" | "expense")}>
          <option value="expense">expense</option>
          <option value="income">income</option>
        </select>
        <input
          type="number"
          min={0}
          max={100}
          value={percent}
          onChange={(event) => setPercent(event.target.value)}
          style={{ width: 80 }}
        />
        <button onClick={save}>Save</button>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Name</th><th>Type</th><th>Percent</th><th></th></tr>
          </thead>
          <tbody>
            {categories.map((category) => (
              <tr key={category.name}>
                <td>{category.name}</td>
                <td><span className={`tag ${category.type}`}>{category.type}</span></td>
                <td>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    defaultValue={category.percent}
                    style={{ width: 80 }}
                    onBlur={(event) => updatePercent(category, event.target.value)}
                  />
                  %
                </td>
                <td>
                  <button className="link danger" onClick={() => remove(category.name)}>
                    delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
