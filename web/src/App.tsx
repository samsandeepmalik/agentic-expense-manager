import { useState } from "react";
import { Categories } from "./components/Categories";
import { Chat } from "./components/Chat";
import { Connect } from "./components/Connect";
import { Dashboard } from "./components/Dashboard";

type Tab = "dashboard" | "chat" | "categories" | "connect";

const TABS: { id: Tab; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "chat", label: "Chat" },
  { id: "categories", label: "Categories" },
  { id: "connect", label: "Connect" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="app">
      <header>
        <div className="brand">💰 Expense Manager</div>
        <nav>
          {TABS.map((item) => (
            <button
              key={item.id}
              className={tab === item.id ? "active" : ""}
              onClick={() => {
                setTab(item.id);
                if (item.id === "dashboard") setRefreshKey((key) => key + 1);
              }}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </header>
      <main>
        {tab === "dashboard" && <Dashboard refreshKey={refreshKey} />}
        {tab === "chat" && <Chat />}
        {tab === "categories" && <Categories />}
        {tab === "connect" && <Connect />}
      </main>
    </div>
  );
}
