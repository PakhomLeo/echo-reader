import { useEffect, useMemo, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { ImportModal } from "../components/imports/ImportModal";
import { ContentList } from "../components/sidebar/ContentList";
import { Sidebar, type LibraryFilter } from "../components/sidebar/Sidebar";
import {
  countLibraryItems,
  matchesLibraryFilter,
} from "../components/sidebar/libraryFilter";
import { useImports } from "../hooks/useImports";
import { useItems } from "../hooks/useItems";
import { getAppConfig, shutdownApp } from "../api/items";
import "./AppShell.css";

export function AppShell() {
  const { items, loading, error, refreshing, refresh, switchRoot } = useItems();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<LibraryFilter>("all");
  const [importOpen, setImportOpen] = useState(false);
  const [installMode, setInstallMode] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const imports = useImports(() => void refresh());
  const handleSwitchRoot = async (path: string) => {
    navigate("/");
    const result = await switchRoot(path);
    setQuery("");
    setFilter("all");
    return result;
  };
  const counts = useMemo(() => countLibraryItems(items), [items]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return items.filter(
      (i) =>
        matchesLibraryFilter(i, filter) &&
        (!needle ||
          [i.source_title, i.source_author, i.folder_name].some((v) =>
            v?.toLocaleLowerCase().includes(needle),
          )),
    );
  }, [items, filter, query]);
  useEffect(() => {
    let current = true;
    void getAppConfig().then((config) => {
      if (current) setInstallMode(config.install_mode);
    }).catch(() => undefined);
    return () => { current = false; };
  }, []);
  return (
    <div
      className={`app-shell${location.pathname.startsWith("/items/") ? " shell-reading" : ""}${location.pathname === "/imports" ? " shell-tasks" : ""}`}
    >
      <Sidebar
        filter={filter}
        counts={counts}
        activeJobs={imports.activeJobs}
        totalJobs={imports.total}
        jobsSelected={location.pathname === "/imports"}
        refreshing={refreshing}
        onFilter={(next) => { setFilter(next); navigate("/"); }}
        onAdd={() => setImportOpen(true)}
        onOpenJobs={() => navigate("/imports")}
        onRefresh={() => void refresh()}
        onSwitchRoot={handleSwitchRoot}
        installMode={installMode}
        onExit={shutdownApp}
      />
      {location.pathname !== "/imports" && <ContentList
        items={filtered}
        loading={loading}
        error={error}
        query={query}
        filter={filter}
        onQuery={setQuery}
        onFilter={setFilter}
        onReload={() => void refresh()}
        onAdd={() => {
          setQuery("");
          setFilter("all");
          setImportOpen(true);
        }}
      />}
      <main className="app-shell-main">
        <Outlet context={{ openImport: () => setImportOpen(true), imports }} />
      </main>
      {importOpen && (
        <ImportModal
          capabilities={imports.capabilities}
          online={imports.online}
          submitting={imports.submitting}
          error={imports.error}
          onClose={() => setImportOpen(false)}
          onSubmit={imports.submit}
          onViewTasks={() => {
            setImportOpen(false);
            navigate("/imports");
          }}
        />
      )}
    </div>
  );
}
