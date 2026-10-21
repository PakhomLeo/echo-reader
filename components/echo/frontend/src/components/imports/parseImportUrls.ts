export const MAX_IMPORT_URLS = 50;

export interface ParsedImportUrls {
  urls: string[];
  inputCount: number;
  duplicateCount: number;
  errors: string[];
}

export function parseImportUrls(input: string): ParsedImportUrls {
  const entries = input.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
  const urls: string[] = [];
  const errors: string[] = [];
  const seen = new Set<string>();
  let duplicateCount = 0;

  entries.forEach((entry, index) => {
    let parsed: URL;
    try {
      parsed = new URL(entry);
    } catch {
      errors.push(`第 ${index + 1} 个链接不是有效的绝对网址`);
      return;
    }
    if (parsed.protocol !== "https:") {
      errors.push(`第 ${index + 1} 个链接必须使用 HTTPS`);
      return;
    }
    if (parsed.username || parsed.password) {
      errors.push(`第 ${index + 1} 个链接不能包含用户名或密码`);
      return;
    }
    const canonical = parsed.href;
    if (seen.has(canonical)) {
      duplicateCount += 1;
      return;
    }
    seen.add(canonical);
    urls.push(canonical);
  });

  if (entries.length === 0) errors.push("请至少输入一个链接");
  if (urls.length > MAX_IMPORT_URLS) {
    errors.push(`一次最多导入 ${MAX_IMPORT_URLS} 个不同链接，当前为 ${urls.length} 个`);
  }
  return { urls, inputCount: entries.length, duplicateCount, errors };
}
