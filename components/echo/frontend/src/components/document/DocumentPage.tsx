import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { saveArticle } from "../../api/items";
import { useDocument } from "../../hooks/useDocument";
import { useFeatures } from "../../hooks/useFeatures";
import { useMediaController } from "../../hooks/useMedia";
import { useTranscript } from "../../hooks/useTranscript";
import { ArticleView } from "../markdown/ArticleView";
import { MarkdownEditor } from "../markdown/MarkdownEditor";
import { MiniPlayer } from "../media/MiniPlayer";
import { AdditionalMediaList } from "../media/AdditionalMediaList";
import { StickyMediaCard } from "../media/StickyMediaCard";
import type { ContentMode } from "../media/ContentModeSwitch";
import { ContentModeSwitch } from "../media/ContentModeSwitch";
import { FollowPlaybackControl } from "../transcript/FollowPlaybackControl";
import { TranscriptView } from "../transcript/TranscriptView";
import { DocumentEmptyState } from "./DocumentEmptyState";
import { DocumentErrorState } from "./DocumentErrorState";
import { DocumentHeader } from "./DocumentHeader";
import { EnrichmentActions } from "./EnrichmentActions";
import { OcrPanel } from "./OcrPanel";
import "./DocumentPage.css";

interface ArticleOverride {
  markdown: string;
  version: string;
}

export function DocumentPage() {
  const { itemId } = useParams<{ itemId: string }>();
  const navigate = useNavigate();
  const [refreshKey, setRefreshKey] = useState(0);
  const state = useDocument(itemId, refreshKey);
  const features = useFeatures();
  const { controller, mediaProps } = useMediaController();
  const [mode, setMode] = useState<ContentMode>("article");
  const [follow, setFollow] = useState(false);
  const [editingArticle, setEditingArticle] = useState(false);
  const [articleOverride, setArticleOverride] =
    useState<ArticleOverride | null>(null);

  const scrollerRef = useRef<HTMLDivElement>(null);
  const scrollPositions = useRef<Record<ContentMode, number>>({
    article: 0,
    transcript: 0,
  });
  const currentModeRef = useRef<ContentMode>(mode);
  const restoreFrameRef = useRef<number | null>(null);
  const restoreTimerRef = useRef<number | null>(null);

  // itemId 变化：重置模式、滚动位置与媒体状态
  useEffect(() => {
    setMode("article");
    setFollow(false);
    setEditingArticle(false);
    setArticleOverride(null);
    scrollPositions.current = { article: 0, transcript: 0 };
    currentModeRef.current = "article";
    controller.reset();
  }, [itemId, controller]);

  // 在 React 替换正文前记录位置，避免较短内容先把 scrollTop 自动压低。
  const handleModeChange = useCallback((nextMode: ContentMode) => {
    const el = scrollerRef.current;
    if (el) scrollPositions.current[currentModeRef.current] = el.scrollTop;
    currentModeRef.current = nextMode;
    if (restoreFrameRef.current !== null)
      window.cancelAnimationFrame(restoreFrameRef.current);
    if (restoreTimerRef.current !== null)
      window.clearTimeout(restoreTimerRef.current);
    setMode(nextMode);
    restoreFrameRef.current = window.requestAnimationFrame(() => {
      if (scrollerRef.current)
        scrollerRef.current.scrollTop = scrollPositions.current[nextMode];
      restoreFrameRef.current = null;
    });
    restoreTimerRef.current = window.setTimeout(() => {
      if (scrollerRef.current)
        scrollerRef.current.scrollTop = scrollPositions.current[nextMode];
      restoreTimerRef.current = null;
    }, 0);
  }, []);

  const transcriptEnabled =
    state.status === "ready" && state.detail.transcript.available;
  const {
    data: transcript,
    loading: transcriptLoading,
    error: transcriptError,
    reload: reloadTranscript,
  } = useTranscript(itemId ?? "", transcriptEnabled);

  const handleSaveArticle = useCallback(
    async (markdown: string) => {
      if (state.status !== "ready") return;
      const result = await saveArticle(
        state.detail.id,
        markdown,
        articleOverride?.version ?? state.detail.article.version ?? "",
      );
      setArticleOverride({ markdown, version: result.version });
      setEditingArticle(false);
    },
    [state, articleOverride],
  );

  const handleImageDeleted = useCallback(
    (result: { markdown: string; version: string }) => {
      setArticleOverride({
        markdown: result.markdown,
        version: result.version,
      });
    },
    [],
  );

  const effectiveArticle =
    articleOverride ??
    (state.status === "ready"
      ? {
          markdown: state.detail.article.markdown ?? "",
          available: state.detail.article.available,
        }
      : null);

  if (state.status === "loading") {
    return <DocumentEmptyState message="加载中…" />;
  }
  if (state.status === "not_found") {
    return <DocumentErrorState message="内容不存在或已被移除" />;
  }
  if (state.status === "network_error") {
    return <DocumentErrorState message={state.message} />;
  }

  const { detail } = state;
  const effectiveMode: ContentMode = !detail.article.available
    ? "transcript"
    : mode;

  return (
    <>
      <div className="document-page-scroller" ref={scrollerRef}>
        <article className="document-page">
          <button
            type="button"
            className="reader-back"
            onClick={() => navigate("/")}
          >
            <ArrowLeft size={17} />
            返回列表
          </button>
          <DocumentHeader
            detail={detail}
            editingArticle={editingArticle}
            onEditArticle={() => setEditingArticle(true)}
            extraActions={
              <EnrichmentActions
                itemId={detail.id}
                features={features}
                hasArticle={detail.article.available}
                hasImages={/!\[[^\]]*\]\([^)]+\)/.test(effectiveArticle?.markdown ?? "")}
                onCompleted={() => setRefreshKey((value) => value + 1)}
              />
            }
          />
          {detail.media && (
            <StickyMediaCard
              media={detail.media}
              controller={controller}
              mediaProps={mediaProps}
            />
          )}
          <AdditionalMediaList items={detail.media_items} controller={controller} />
          {detail.transcript.available && (
            <div className="document-content-toolbar">
              <ContentModeSwitch
                hasArticle={detail.article.available}
                hasTranscript={detail.transcript.available}
                mode={effectiveMode}
                onModeChange={handleModeChange}
              />
              {effectiveMode === "transcript" && (
                <FollowPlaybackControl
                  follow={follow}
                  onToggle={() => setFollow(!follow)}
                />
              )}
            </div>
          )}
          <div className="document-body">
            {effectiveMode === "article" ? (
              editingArticle && effectiveArticle ? (
                <MarkdownEditor
                  itemId={detail.id}
                  initialValue={effectiveArticle.markdown}
                  onSave={handleSaveArticle}
                  onCancel={() => setEditingArticle(false)}
                />
              ) : (
                <ArticleView
                  itemId={detail.id}
                  markdown={effectiveArticle?.markdown ?? ""}
                  articleVersion={
                    articleOverride?.version ?? detail.article.version
                  }
                  onImageDeleted={handleImageDeleted}
                />
              )
            ) : transcript ? (
              <TranscriptView
                itemId={detail.id}
                data={transcript}
                controller={controller}
                follow={follow}
                onReload={reloadTranscript}
              />
            ) : transcriptLoading ? (
              <DocumentEmptyState message="加载转录…" />
            ) : transcriptError ? (
              <DocumentErrorState message={transcriptError} />
            ) : detail.article.available && detail.transcript.available ? (
              <DocumentEmptyState message="加载转录…" />
            ) : (
              <DocumentEmptyState message="该内容暂无转录" />
            )}
          </div>
          {effectiveMode === "article" && !editingArticle && (
            <OcrPanel itemId={detail.id} version={refreshKey} />
          )}
        </article>
        {detail.media?.type === "audio" && (
          <MiniPlayer controller={controller} />
        )}
      </div>
    </>
  );
}
