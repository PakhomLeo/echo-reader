import { useState } from "react";
import { ChevronLeft, ChevronRight, FolderOpen, Power, RefreshCw } from "lucide-react";

export function SidebarHeader({ collapsed }: { collapsed: boolean }) {
  return (
    <div className="workspace-brand">
      <span className="workspace-mark">E</span>
      {!collapsed && <span>Echo</span>}
    </div>
  );
}

interface WorkspaceBottomProps {
  collapsed: boolean;
  refreshing: boolean;
  onToggle: () => void;
  onRefresh: () => void;
  onPickFolder: () => void;
  installMode: boolean;
  onExit: () => Promise<{ status: string }>;
}
export function WorkspaceBottom({
  collapsed,
  refreshing,
  onToggle,
  onRefresh,
  onPickFolder,
  installMode,
  onExit,
}: WorkspaceBottomProps) {
  const [exitError, setExitError] = useState<string | null>(null);
  const [exiting, setExiting] = useState(false);
  const exit = async () => {
    if (!window.confirm("退出 Echo Reader？正在导入的任务会阻止退出。")) return;
    setExiting(true);
    setExitError(null);
    try {
      await onExit();
    } catch (error) {
      setExiting(false);
      setExitError(error instanceof Error ? error.message : "无法退出 Echo Reader");
    }
  };
  return (
    <div className="workspace-bottom">
      {exitError && <p className="sidebar-action-error" role="alert">{exitError}</p>}
      <button
        type="button"
        onClick={onPickFolder}
        title="选择文件夹"
        aria-label="选择文件夹"
      >
        <FolderOpen size={17} />
        {!collapsed && <span>切换资料库</span>}
      </button>
      {installMode && <button
        type="button"
        onClick={() => void exit()}
        disabled={exiting}
        title="退出 Echo Reader"
        aria-label="退出 Echo Reader"
      >
        <Power size={17} />
        {!collapsed && <span>{exiting ? "正在退出" : "退出 Echo Reader"}</span>}
      </button>}
      <button
        type="button"
        onClick={onRefresh}
        disabled={refreshing}
        title="刷新资料库"
        aria-label="刷新资料库"
      >
        <RefreshCw size={17} className={refreshing ? "is-spinning" : ""} />
        {!collapsed && <span>{refreshing ? "刷新中" : "刷新资料库"}</span>}
      </button>
      <button
        type="button"
        onClick={onToggle}
        title={collapsed ? "展开侧边栏" : "折叠侧边栏"}
        aria-label={collapsed ? "展开侧边栏" : "折叠侧边栏"}
      >
        {collapsed ? <ChevronRight size={17} /> : <ChevronLeft size={17} />}{" "}
        {!collapsed && <span>折叠侧栏</span>}
      </button>
    </div>
  );
}
