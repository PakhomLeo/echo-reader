import type { Root } from "hast";
import { visit } from "unist-util-visit";

/**
 * 在 HAST 阶段为每个 img 元素按出现顺序写入 occurrence 编号。
 * react-markdown 会把 node.properties 合并进 React 元素 props。
 */
export function rehypeImageIndex() {
  return (tree: Root) => {
    let occurrence = 0;
    visit(tree, (node) => {
      if (node.type === "element" && node.tagName === "img") {
        node.properties = {
          ...node.properties,
          "data-occurrence": String(occurrence++),
        };
      }
    });
  };
}
