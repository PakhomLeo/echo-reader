import { EditorState, type Extension } from "@codemirror/state";
import { EditorView, keymap, lineNumbers } from "@codemirror/view";
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from "@codemirror/commands";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";

export interface EditorHandle {
  getValue: () => string;
  insertAtCursor: (text: string) => void;
  destroy: () => void;
}

/**
 * 动态加载并挂载 CodeMirror 编辑器。
 * 此模块被动态 import，确保 CodeMirror 不在初始页面包中。
 */
export function createEditor(
  host: HTMLElement,
  doc: string,
  handlers: {
    onPasteImage: (file: File) => void;
  },
): EditorHandle {
  const pasteImageHandler: Extension = EditorView.domEventHandlers({
    paste: (event) => {
      const items = event.clipboardData?.items;
      if (!items) return false;
      let imageFile: File | null = null;
      for (const item of items) {
        if (item.kind === "file" && item.type.startsWith("image/")) {
          imageFile = item.getAsFile();
          if (imageFile) break;
        }
      }
      if (imageFile) {
        event.preventDefault();
        handlers.onPasteImage(imageFile);
        return true;
      }
      return false;
    },
  });

  const state = EditorState.create({
    doc,
    extensions: [
      lineNumbers(),
      history(),
      keymap.of([
        ...defaultKeymap,
        ...historyKeymap,
        ...searchKeymap,
        indentWithTab,
      ]),
      markdown({ base: markdownLanguage }),
      highlightSelectionMatches(),
      EditorView.lineWrapping,
      pasteImageHandler,
      EditorView.theme({
        "&": { fontSize: "15px", height: "100%" },
        ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.7" },
        ".cm-content": {
          padding: "16px 0",
          caretColor: "var(--color-text-primary)",
        },
        ".cm-gutters": {
          backgroundColor: "transparent",
          borderRight: "1px solid var(--color-border)",
          color: "var(--color-text-tertiary)",
        },
        "&.cm-focused": { outline: "none" },
        ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
          background: "rgba(53, 107, 214, 0.18) !important",
        },
        ".cm-activeLine": { backgroundColor: "rgba(25, 25, 24, 0.03)" },
      }),
    ],
  });

  const view = new EditorView({ state, parent: host });

  return {
    getValue: () => view.state.doc.toString(),
    insertAtCursor: (text: string) => {
      view.dispatch(view.state.replaceSelection(text));
    },
    destroy: () => view.destroy(),
  };
}
