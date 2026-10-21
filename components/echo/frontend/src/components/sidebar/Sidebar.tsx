import { useState } from "react";
import {
  FileText,
  Headphones,
  Inbox,
  ListFilter,
  Layers3,
  Plus,
  Video,
} from "lucide-react";
import { FolderPickerModal } from "./FolderPickerModal";
import { SidebarHeader, WorkspaceBottom } from "./SidebarHeader";
import { useSidebarCollapsed } from "./useSidebarCollapsed";
import type { LibraryFilter } from "./libraryFilter";
import "./Sidebar.css";

export type { LibraryFilter } from "./libraryFilter";
interface SidebarProps {
  filter: LibraryFilter;
  counts: Record<LibraryFilter, number>;
  activeJobs: number;
  totalJobs: number;
  jobsSelected: boolean;
  refreshing: boolean;
  installMode: boolean;
  onFilter: (filter: LibraryFilter) => void;
  onAdd: () => void;
  onOpenJobs: () => void;
  onRefresh: () => void;
  onSwitchRoot: (
    path: string,
  ) => Promise<{ library_root: string; item_count: number; invalid_item_count: number }>;
  onExit: () => Promise<{ status: string }>;
}
const NAV = [
  { id: "all" as const, label: "全部内容", Icon: Inbox },
  { id: "article" as const, label: "文章", Icon: FileText },
  { id: "video" as const, label: "视频", Icon: Video },
  { id: "audio" as const, label: "音频", Icon: Headphones },
  { id: "mixed" as const, label: "混合内容", Icon: Layers3 },
];
export function Sidebar({
  filter,
  counts,
  activeJobs,
  totalJobs,
  jobsSelected,
  refreshing,
  installMode,
  onFilter,
  onAdd,
  onOpenJobs,
  onRefresh,
  onSwitchRoot,
  onExit,
}: SidebarProps) {
  const { collapsed, toggle } = useSidebarCollapsed();
  const [pickerOpen, setPickerOpen] = useState(false);
  return (
    <aside
      className={`sidebar workspace-nav${collapsed ? " sidebar-collapsed" : ""}`}
      data-collapsed={collapsed}
    >
      <SidebarHeader collapsed={collapsed} />
      <button className="workspace-add" type="button" onClick={onAdd}>
        <Plus size={18} />
        {!collapsed && <span>添加内容</span>}
      </button>
      <nav className="workspace-links" aria-label="资料库导航">
        {NAV.map(({ id, label, Icon }) => (
          <button
            key={id}
            className={filter === id ? "is-active" : ""}
            type="button"
            onClick={() => onFilter(id)}
            aria-pressed={filter === id}
            title={collapsed ? label : undefined}
          >
            <Icon size={17} />
            {!collapsed && (
              <>
                <span>{label}</span>
                <small>{counts[id]}</small>
              </>
            )}
          </button>
        ))}
      </nav>
      <button
        className={`workspace-section${activeJobs > 0 ? " has-active-jobs" : ""}${jobsSelected ? " is-active" : ""}`}
        type="button"
        onClick={onOpenJobs}
        aria-label={`提取任务，${activeJobs} 个进行中`}
        aria-current={jobsSelected ? "page" : undefined}
      >
        <ListFilter size={16} />
        {!collapsed && (
          <>
            <span>提取任务</span>
            <small>{activeJobs > 0 ? `${activeJobs} 进行中` : totalJobs}</small>
          </>
        )}
      </button>
      <WorkspaceBottom
        collapsed={collapsed}
        refreshing={refreshing}
        onToggle={toggle}
        onRefresh={onRefresh}
        onPickFolder={() => setPickerOpen(true)}
        installMode={installMode}
        onExit={onExit}
      />
      {pickerOpen && (
        <FolderPickerModal
          onClose={() => setPickerOpen(false)}
          onSelect={async (path) => {
            await onSwitchRoot(path);
            setPickerOpen(false);
          }}
        />
      )}
    </aside>
  );
}
