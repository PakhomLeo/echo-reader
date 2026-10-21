import { describe, expect, it } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ContentModeSwitch } from "../src/components/media/ContentModeSwitch";
import { resolveImageUrl, isSafeHref } from "../src/components/markdown/urls";
import { ExternalLink } from "../src/components/markdown/ExternalLink";
import { MediaController } from "../src/hooks/useMediaController";
import { AdditionalMediaList } from "../src/components/media/AdditionalMediaList";
import { MiniPlayer } from "../src/components/media/MiniPlayer";
import { WorkspaceBottom } from "../src/components/sidebar/SidebarHeader";
import {
  countLibraryItems,
  matchesLibraryFilter,
} from "../src/components/sidebar/libraryFilter";
import type { ItemSummary } from "../src/api/types";

describe("ContentModeSwitch 显示规则", () => {
  it("同时有文章和转录时显示两个选项", () => {
    render(<ContentModeSwitch hasArticle hasTranscript mode="article" onModeChange={() => {}} />);
    expect(screen.getByRole("tab", { name: "文章" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "时间线文本" })).toBeInTheDocument();
  });

  it("只有文章时不显示切换", () => {
    render(<ContentModeSwitch hasArticle hasTranscript={false} mode="article" onModeChange={() => {}} />);
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("只有转录时不显示切换", () => {
    render(<ContentModeSwitch hasArticle={false} hasTranscript mode="transcript" onModeChange={() => {}} />);
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("两者都没有时不显示切换", () => {
    render(<ContentModeSwitch hasArticle={false} hasTranscript={false} mode="article" onModeChange={() => {}} />);
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("默认选中文章模式", () => {
    render(<ContentModeSwitch hasArticle hasTranscript mode="article" onModeChange={() => {}} />);
    expect(screen.getByRole("tab", { name: "文章" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("图片 URL 转换", () => {
  it("相对路径转换为 API 地址", () => {
    expect(resolveImageUrl("id-1", "images/0001.webp")).toBe("/api/v1/items/id-1/assets/images/0001.webp");
  });

  it("http 链接保持不变", () => {
    expect(resolveImageUrl("id-1", "https://example.com/a.png")).toBe("https://example.com/a.png");
  });

  it("拒绝 data: 与危险协议", () => {
    expect(resolveImageUrl("id-1", "data:image/png;base64,xxx")).toBeNull();
    expect(resolveImageUrl("id-1", "javascript:alert(1)")).toBeNull();
  });
});

describe("ExternalLink 安全属性", () => {
  it("外部链接带 target 与 rel", () => {
    const { container } = render(<ExternalLink href="https://example.com">链接</ExternalLink>);
    const a = container.querySelector("a")!;
    expect(a).toHaveAttribute("target", "_blank");
    expect(a).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("javascript: 协议被拒绝（渲染为 span）", () => {
    const { container } = render(<ExternalLink href="javascript:alert(1)">危险</ExternalLink>);
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("span")).not.toBeNull();
  });

  it("相对链接不打开新窗口", () => {
    const { container } = render(<ExternalLink href="other-page">相对</ExternalLink>);
    const a = container.querySelector("a")!;
    expect(a).not.toHaveAttribute("target");
  });
});

describe("isSafeHref", () => {
  it("允许 http/https/相对路径", () => {
    expect(isSafeHref("https://a.com")).toBe(true);
    expect(isSafeHref("http://a.com")).toBe(true);
    expect(isSafeHref("images/1.webp")).toBe(true);
  });

  it("拒绝危险协议", () => {
    expect(isSafeHref("javascript:void(0)")).toBe(false);
    expect(isSafeHref("data:text/html,<script>")).toBe(false);
    expect(isSafeHref("file:///etc/passwd")).toBe(false);
    expect(isSafeHref("vbscript:msgbox(1)")).toBe(false);
  });
});

describe("MediaController 倍速持久化", () => {
  it("设置倍速写入 localStorage", () => {
    localStorage.clear();
    const c = new MediaController();
    c.setPlaybackRate(1.5);
    expect(localStorage.getItem("reader.media.playbackRate")).toBe("1.5");
  });

  it("创建时读取已保存的倍速", () => {
    localStorage.setItem("reader.media.playbackRate", "2");
    const c = new MediaController();
    expect(c.getSnapshot().playbackRate).toBe(2);
  });

  it("非法值回落默认 1.0", () => {
    localStorage.setItem("reader.media.playbackRate", "99");
    const c = new MediaController();
    expect(c.getSnapshot().playbackRate).toBe(1);
  });
});

describe("MediaController snapshot", () => {
  it("状态变化同步更新迷你播放器", () => {
    const controller = new MediaController();
    render(<MiniPlayer controller={controller} />);
    expect(screen.getByRole("button", { name: "播放" })).toBeInTheDocument();
    act(() => controller.updateState("playing"));
    expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();
    act(() => controller.updateState("paused"));
    expect(screen.getByRole("button", { name: "播放" })).toBeInTheDocument();
  });

  it("每次通知都发布新的不可变完整快照", () => {
    const controller = new MediaController();
    const before = controller.getSnapshot();
    controller.setVolume(0.4);
    const after = controller.getSnapshot();
    expect(after).not.toBe(before);
    expect(after.volume).toBe(0.4);
    expect(Object.isFrozen(after)).toBe(true);
  });
});

describe("AdditionalMediaList", () => {
  it("按媒体类型渲染带无障碍名称的原生控件", () => {
    const controller = new MediaController();
    render(
      <AdditionalMediaList
        controller={controller}
        items={[
          {
            id: "inline-video",
            role: "inline",
            type: "video",
            title: "正文视频",
            description: "视频说明",
            url: "/api/v1/items/a/media/inline-video",
            mime_type: "video/mp4",
          },
          {
            id: "attachment-audio",
            role: "attachment",
            type: "audio",
            title: null,
            description: null,
            url: "/api/v1/items/a/media/attachment-audio",
            mime_type: "audio/mpeg",
          },
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "页面中的其他媒体" })).toBeInTheDocument();
    expect(screen.getByLabelText("正文视频")).toHaveAttribute("controls");
    expect(screen.getByText("视频说明")).toBeInTheDocument();
    expect(screen.getByLabelText("媒体 2")).toHaveAttribute("controls");
  });

  it("空列表不渲染章节", () => {
    const { container } = render(<AdditionalMediaList items={[]} controller={new MediaController()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("播放附加媒体会暂停主媒体", () => {
    const controller = new MediaController();
    const primary = document.createElement("audio");
    Object.defineProperty(primary, "paused", { configurable: true, value: false });
    const pause = vi.spyOn(primary, "pause").mockImplementation(() => undefined);
    controller.setElement(primary);
    render(<AdditionalMediaList controller={controller} items={[{
      id: "other", role: "inline", type: "audio", title: "其他音频",
      description: null, url: "/other.mp3", mime_type: "audio/mpeg",
    }]} />);
    fireEvent.play(screen.getByLabelText("其他音频"));
    expect(pause).toHaveBeenCalledOnce();
  });
});

describe("安装模式退出入口", () => {
  const props = {
    collapsed: false,
    refreshing: false,
    onToggle: vi.fn(),
    onRefresh: vi.fn(),
    onPickFolder: vi.fn(),
  };

  it("普通开发模式不显示", () => {
    render(<WorkspaceBottom {...props} installMode={false} onExit={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "退出 Echo Reader" })).not.toBeInTheDocument();
  });

  it("安装模式确认后调用退出，并展示服务拒绝原因", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const onExit = vi.fn(async () => { throw new Error("仍有导入任务进行中"); });
    render(<WorkspaceBottom {...props} installMode onExit={onExit} />);
    fireEvent.click(screen.getByRole("button", { name: "退出 Echo Reader" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("仍有导入任务进行中");
    expect(onExit).toHaveBeenCalledOnce();
  });
});

describe("mixed 资料库筛选", () => {
  const items: ItemSummary[] = [
    {
      id: "mixed",
      folder_name: "mixed",
      source_type: "mixed",
      has_article: true,
      has_transcript: false,
      has_media: true,
      updated_at: "2026-08-11T00:00:00Z",
      source_title: "混合内容",
      source_author: null,
      published_at: null,
    },
    {
      id: "video",
      folder_name: "video",
      source_type: "video",
      has_article: false,
      has_transcript: false,
      has_media: true,
      updated_at: "2026-08-11T00:00:00Z",
      source_title: "视频",
      source_author: null,
      published_at: null,
    },
  ];

  it("mixed 有独立计数且不会混入视频筛选", () => {
    expect(countLibraryItems(items)).toMatchObject({
      all: 2,
      article: 1,
      video: 1,
      audio: 0,
      mixed: 1,
    });
    expect(matchesLibraryFilter(items[0], "mixed")).toBe(true);
    expect(matchesLibraryFilter(items[0], "video")).toBe(false);
  });
});
