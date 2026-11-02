export function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function message(error: unknown): string { return error instanceof Error ? error.message : String(error); }

export function download(filename: string, value: unknown): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' }));
  const link = element('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function table(headers: string[], rows: (string | number)[][], caption: string): HTMLElement {
  const wrapper = element('div', 'table-scroll');
  const node = element('table');
  node.append(element('caption', 'sr-only', caption));
  const head = element('thead');
  const headerRow = element('tr');
  for (const text of headers) { const cell = element('th', '', text); cell.scope = 'col'; headerRow.append(cell); }
  head.append(headerRow);
  node.append(head);
  const body = element('tbody');
  for (const row of rows) { const tr = element('tr'); for (const value of row) tr.append(element('td', '', String(value))); body.append(tr); }
  if (!rows.length) { const tr = element('tr'); const td = element('td', 'muted', '暂无明细'); td.colSpan = headers.length; tr.append(td); body.append(tr); }
  node.append(body);
  wrapper.append(node);
  return wrapper;
}
